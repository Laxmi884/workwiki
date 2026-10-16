"""Ingest nodes: note → task IDs and index → facts → page edits → diffs.

Each node reads the vault through `vault_reader` (no writes) and returns a partial
state update. The pure logic lives in `daily_syntax`, `task_tracking`, `extraction`
and `routing`.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Send, interrupt

from llm_wiki import (
    answers,
    extraction,
    ledger,
    names,
    review,
    routing,
    task_tracking,
    vault_git,
)
from llm_wiki.apply import ApplyConflict, apply_diffs
from llm_wiki.config import get_settings
from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.diffs import VaultReader, render_diffs, vault_reader
from llm_wiki.domain.changes import (
    RAW_DAILY_FOLDER,
    ReplaceFile,
    UserText,
    sha256_text,
)
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.entities import EntityKind
from llm_wiki.domain.review import ReviewAnswer, ReviewRequest
from llm_wiki.domain.task_index import DoneTask
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.entity_index import EntityIndex, load_index, with_proposed
from llm_wiki.ingest.state import (
    ExtractInput,
    FindNamesInput,
    IngestContext,
    IngestState,
)
from llm_wiki.models import ModelSpec, choose_model_spec, make_chat_model


def vault_path(runtime: Runtime[IngestContext]) -> Path:
    """Return the run's vault: context `vault_path`, else `WIKI_VAULT_PATH`."""
    context = runtime.context or {}
    if context.get("vault_path"):
        return Path(context["vault_path"]).expanduser().resolve()
    return get_settings().vault_path


def chat_model(runtime: Runtime[IngestContext]) -> BaseChatModel:
    """Return the run's model: context `model`, else `WIKI_MODEL` (decision 0003)."""
    context = runtime.context or {}
    return _build_model(choose_model_spec(context.get("model"), get_settings().model))


def fallback_models(runtime: Runtime[IngestContext]) -> list[BaseChatModel]:
    """Return the backup models (`WIKI_FALLBACK_MODELS`), tried in order on failure."""
    del runtime  # same signature as chat_model; fallbacks are process-wide for now
    return [_build_model(ModelSpec.parse(s)) for s in get_settings().fallback_models]


def _build_model(spec: ModelSpec) -> BaseChatModel:
    if spec.provider == "ollama":
        # Local models: no hidden "thinking" (minutes per call). Ollama's default
        # 4096-token window is too small for prompt + detailed answer; the output cap
        # is large and only stops a runaway answer.
        return make_chat_model(
            spec, temperature=0, reasoning=False, num_ctx=32768, num_predict=8192
        )
    return make_chat_model(spec, temperature=0)


def _daily_notes(vault: Path, read: VaultReader) -> list[DailyNote]:
    notes = []
    for file in sorted((vault / RAW_DAILY_FOLDER).glob("*.md")):
        rel = file.relative_to(vault).as_posix()
        text = read(rel)
        if text is not None:
            notes.append(parse_daily_note(rel, text))
    return notes


def _entities(state: IngestState, runtime: Runtime[IngestContext]) -> EntityIndex:
    """Return the vault's entity index plus the pages and aliases answered in review."""
    return with_proposed(
        load_index(vault_path(runtime)),
        state.get("new_entities", []),
        state.get("new_aliases", {}),
    )


def load_note(state: IngestState, runtime: Runtime[IngestContext]) -> dict[str, Any]:
    """Read and parse `note_path` and check the ledger. A missing note raises.

    Sets `skip_reason` (the run stops) for an unchanged note or a broken ledger, and
    `seen_lines` (already-ingested prose lines) for an edited one.
    """
    path = state["note_path"]
    read = vault_reader(vault_path(runtime))
    text = read(path)
    if text is None:
        raise FileNotFoundError(f"note not found in vault: {path}")
    note = parse_daily_note(path, text)
    sha = sha256_text(text)
    update: dict[str, Any] = {
        "note": note,
        "note_sha256": sha,
        "warnings": [f"{path}: {w}" for w in note.warnings],
    }

    known, problem = ledger.load_ledger(read(ledger.LEDGER_PATH))
    entry = known.notes.get(path) if known is not None else None
    if problem is not None:
        update["skip_reason"] = problem
    elif entry is not None and entry.sha256 == sha:
        update["skip_reason"] = f"{path}: already ingested and unchanged"
    if "skip_reason" in update:
        update["warnings"] = [*update["warnings"], update["skip_reason"]]
    update["seen_lines"] = list(entry.lines) if entry is not None else []
    return update


def after_load(state: IngestState) -> str:
    """Stop here if `load_note` found nothing to do, else carry on."""
    return END if state.get("skip_reason") else "assign_ids"


def assign_ids(state: IngestState, runtime: Runtime[IngestContext]) -> dict[str, Any]:
    """Propose an ID for every task without one, unique across the vault."""
    vault = vault_path(runtime)
    read = vault_reader(vault)
    index, _ = task_tracking.load_task_index(read(task_tracking.TASK_INDEX_PATH))
    existing = set(index.tasks) if index is not None else set()
    existing |= task_tracking.ids_in_notes(_daily_notes(vault, read))
    ids, edits = task_tracking.assign_ids(state["note"], existing)
    return {"task_ids": ids, "task_edits": edits}


def track_tasks(state: IngestState, runtime: Runtime[IngestContext]) -> dict[str, Any]:
    """Fold the note's tasks into `90_meta/tasks.json` (status, project, people).

    Runs again after an "answer" in the review, with the answered pages known, so the
    index is rebuilt from the vault's copy each time (the old rewrite is replaced).
    Also lists each task's project and the tasks done for the first time (wins).
    """
    read = vault_reader(vault_path(runtime))
    index, problem = task_tracking.load_task_index(read(task_tracking.TASK_INDEX_PATH))
    if index is None:
        return {"warnings": [problem]}

    note, new_ids = state["note"], state.get("task_ids", {})
    updated, warnings = task_tracking.update_task_index(
        index, note, new_ids, _entities(state, runtime)
    )
    edits = [
        e
        for e in state.get("task_edits", [])
        if e.path != task_tracking.TASK_INDEX_PATH
    ]
    if updated != index:
        text = task_tracking.dump_task_index(updated)
        edits.append(ReplaceFile(path=task_tracking.TASK_INDEX_PATH, text=text))

    projects: dict[int, str | None] = {}
    done: list[DoneTask] = []
    for task in note.tasks:
        task_id = task.id or new_ids.get(task.line_no)
        if task_id is None or task_id not in updated.tasks:
            continue
        record = updated.tasks[task_id]
        projects[task.line_no] = record.project
        old = index.tasks.get(task_id)
        if task.status is TaskStatus.DONE and (
            old is None or old.status is not TaskStatus.DONE
        ):
            done.append(
                DoneTask(
                    line=task.line_no,
                    task_id=task_id,
                    text=task.text,
                    project=record.project,
                )
            )
    return {
        "task_edits": edits,
        "task_projects": projects,
        "done_tasks": done,
        "warnings": warnings,
    }


def find_names(
    state: FindNamesInput, runtime: Runtime[IngestContext]
) -> dict[str, Any]:
    """Ask the model for people and project groups in task lines and headings."""
    note = state["note"]
    scan, warnings = names.scan_names(
        chat_model(runtime),
        note,
        state["task_projects"],
        load_index(vault_path(runtime)),
    )
    return {"name_scan": scan, "warnings": [f"{note.path}: {w}" for w in warnings]}


def extract(state: ExtractInput, runtime: Runtime[IngestContext]) -> dict[str, Any]:
    """Extract one prose section's facts with the model (2e sends one per section)."""
    note = state["note"]
    (section,) = [s for s in note.sections if s.start_line == state["section_line"]]
    facts, warnings = extraction.extract_section(
        chat_model(runtime),
        section,
        extraction.task_lines(note),
        load_index(vault_path(runtime)),
        previous=state.get("previous"),
        feedback=state.get("feedback", []),
        seen=frozenset(state.get("seen_lines", [])),
    )
    return {
        "facts": {str(section.start_line): facts},
        "warnings": [f"{note.path}: {w}" for w in warnings],
    }


def fan_out(state: IngestState) -> list[Send] | str:
    """Send prose sections to `extract` and task lines to `find_names`, in parallel.

    `find_names` runs once per run; with nothing to send, go to `route`. After an
    "answer" the facts stand: straight to `route`.
    """
    if state.get("review_action") == "answer":
        return "route"
    note = state["note"]
    tasks = extraction.task_lines(note)
    seen = frozenset(state.get("seen_lines", []))
    facts = state.get("facts", {})
    sends = [
        Send(
            "extract",
            {
                "note": note,
                "section_line": section.start_line,
                "previous": facts.get(str(section.start_line)),
                "feedback": state.get("feedback", []),
                "seen_lines": state.get("seen_lines", []),
            },
        )
        for section in note.sections
        if extraction.needs_model(section, tasks, seen)
    ]
    if "name_scan" not in state and names.scan_lines(note):
        payload = {"note": note, "task_projects": state.get("task_projects", {})}
        sends.append(Send("find_names", payload))
    return sends or "route"


def route(state: IngestState, runtime: Runtime[IngestContext]) -> dict[str, Any]:
    """Turn all facts into page edits (rebuilt from scratch each round)."""
    vault = vault_path(runtime)
    read = vault_reader(vault)
    new_entities = state.get("new_entities", [])
    edits = answers.entity_edits(
        new_entities, state.get("new_aliases", {}), load_index(vault)
    )
    chosen = set(state.get("chosen_wins", []))
    routed, warnings = routing.route_facts(
        state["note"],
        state.get("facts", {}),
        _entities(state, runtime),
        lambda path: read(path) is not None,
        decision_pages=state.get("decision_pages", []),
        task_wins=[t for t in state.get("done_tasks", []) if t.line in chosen],
    )
    return {"fact_edits": edits + routed, "warnings": warnings}


def render(state: IngestState, runtime: Runtime[IngestContext]) -> dict[str, Any]:
    """Render task, fact, user-text and ledger edits into one diff per file.

    Nothing is written. The user's own text (review "edit") is applied last, so it
    replaces what the other edits produced for that page. The ledger records the note's
    hash *after* its task IDs are added, so once the diffs are applied the next run
    sees the note as unchanged.
    """
    read = vault_reader(vault_path(runtime))
    edits = [
        *state.get("task_edits", []),
        *state.get("fact_edits", []),
        *(UserText(path=p, text=t) for p, t in state.get("overrides", {}).items()),
    ]
    diffs, warnings = render_diffs(edits, read)

    note = state["note"]
    note_after = [d.after_text for d in diffs if d.path == note.path]
    sha = sha256_text(note_after[0]) if note_after else state["note_sha256"]
    known, _ = ledger.load_ledger(read(ledger.LEDGER_PATH))  # valid: load_note checked
    assert known is not None
    now = datetime.datetime.now(datetime.UTC)
    text = ledger.dump_ledger(ledger.record(known, note, sha, now))
    ledger_diffs, ledger_warnings = render_diffs(
        [ReplaceFile(path=ledger.LEDGER_PATH, text=text)], read
    )
    return {"diffs": diffs + ledger_diffs, "warnings": warnings + ledger_warnings}


def _can_revise(state: IngestState) -> bool:
    note = state["note"]
    tasks = extraction.task_lines(note)
    seen = frozenset(state.get("seen_lines", []))
    return any(extraction.needs_model(s, tasks, seen) for s in note.sections)


def review_changes(
    state: IngestState, runtime: Runtime[IngestContext]
) -> dict[str, Any]:
    """Pause for the user's review; ask again, with the reason, until the answer is valid.

    Runs from the top on every resume, so it has no side effects: it only reads state
    (and the vault's entity pages) and returns the update for the chosen action
    (`after_review` routes on it).
    """
    overrides = state.get("overrides", {})
    entities = _entities(state, runtime)
    people = review.people_questions(
        state["note"],
        state.get("name_scan"),
        state.get("facts", {}),
        entities,
        state.get("ignored_names", []),
    )
    known_people = [
        e.slug for e in entities.entities.values() if e.kind is EntityKind.PERSON
    ]
    request = review.build_request(
        state["note"].path,
        state.get("diffs", []),
        state.get("facts", {}),
        state.get("fact_edits", []),
        state.get("warnings", []),
        decision_pages=state.get("decision_pages", []),
        revisions=state.get("revisions", 0),
        can_revise=_can_revise(state),
        edited=list(overrides),
        people=people,
        done_tasks=state.get("done_tasks", []),
        chosen_wins=state.get("chosen_wins", []),
    )
    while True:
        raw = interrupt(request.model_dump(mode="json"))
        answer, error = review.check_answer(request, raw, known_people)
        if answer is not None:
            break
        request = request.model_copy(update={"error": error})

    update: dict[str, Any] = {"review_action": answer.action, "approved": []}
    dropped = [f"your edit of {p} was dropped by {answer.action}" for p in overrides]
    if answer.action in ("approve", "reject"):
        update["approved"] = review.approved_digests(request, answer)
    elif answer.action == "revise":
        assert answer.feedback is not None
        update |= {
            "feedback": [*state.get("feedback", []), answer.feedback],
            "revisions": state.get("revisions", 0) + 1,
            "overrides": {},
            "warnings": dropped,
        }
    elif answer.action == "make_page":
        lines = {d.line for d in request.decisions if d.id in answer.decisions}
        pages = sorted({*state.get("decision_pages", []), *lines})
        update |= {"decision_pages": pages, "overrides": {}, "warnings": dropped}
    elif answer.action == "answer":
        update |= _answered(state, request, answer, entities)
        update |= {"overrides": {}, "warnings": dropped}
    else:  # edit
        (target,) = [f for f in request.files if f.id == answer.file]
        assert answer.text is not None
        update["overrides"] = {**overrides, target.path: answer.text}
    return update


def _answered(
    state: IngestState,
    request: ReviewRequest,
    answer: ReviewAnswer,
    entities: EntityIndex,
) -> dict[str, Any]:
    """Turn an "answer" into state: new pages, aliases, ignored names, picked wins."""
    new_entities = list(state.get("new_entities", []))
    new_aliases = {k: list(v) for k, v in state.get("new_aliases", {}).items()}
    ignored = list(state.get("ignored_names", []))
    question = {p.id: p for p in request.people}
    for pid, value in (answer.people or {}).items():
        name = question[pid].name
        if value == "new":
            taken = {*entities.entities, *(e.slug for e in new_entities)}
            new_entities.append(
                answers.new_entity(EntityKind.PERSON, name, taken.__contains__)
            )
        elif value == "ignore":
            ignored.append(name)
        else:
            new_aliases.setdefault(value, []).append(name)
    update: dict[str, Any] = {
        "new_entities": new_entities,
        "new_aliases": new_aliases,
        "ignored_names": ignored,
    }
    if answer.wins is not None:
        picked = set(answer.wins)
        update["chosen_wins"] = [w.line for w in request.wins if w.id in picked]
    return update


def apply(state: IngestState, runtime: Runtime[IngestContext]) -> dict[str, Any]:
    """Write the approved diffs (3d) and commit them to the vault's git repo (3e).

    If approved files changed since the review, nothing is written. Changed wiki pages:
    `after_apply` re-renders from the current files for a fresh review. A changed note:
    the run ends, since its line numbers (task IDs, cited facts) may no longer fit; run
    ingest again to read it fresh.
    """
    try:
        written = apply_diffs(
            vault_path(runtime), state.get("diffs", []), state.get("approved", [])
        )
    except ApplyConflict as exc:
        changed = ", ".join(exc.paths)
        if state["note"].path in exc.paths:
            warning = f"nothing written: {changed} changed since the review; run again"
        else:
            warning = f"nothing written: {changed} changed since the review"
        return {
            "written": [],
            "apply_conflicts": list(exc.paths),
            "warnings": [warning],
        }
    commit, warnings = vault_git.commit_paths(
        vault_path(runtime), written, _commit_message(state, written)
    )
    return {
        "written": written,
        "apply_conflicts": [],
        "commit": commit,
        "warnings": warnings,
    }


def _commit_message(state: IngestState, written: list[str]) -> str:
    """Return "ingest 2026-10-14: 6 files" plus one line per file with its digest."""
    note = state["note"]
    digests = {d.path: d.digest for d in state.get("diffs", [])}
    body = "\n".join(f"- {p}  {digests.get(p, '')[:12]}" for p in written)
    return f"ingest {note.date or note.path}: {len(written)} files\n\n{body}\n"


def after_apply(state: IngestState) -> str:
    """Re-render after changed pages; end after success or a changed note."""
    conflicts = state.get("apply_conflicts", [])
    if not conflicts or state["note"].path in conflicts:
        return END
    return "render"


def after_review(state: IngestState) -> list[Send] | str:
    """Approve applies, reject ends; the other answers go back as far as they need.

    approve → apply (when at least one file is approved; else the run ends),

    revise → extract (the model reads the note again with the feedback),
    answer → track_tasks (answered names re-map tasks; facts stand),
    make_page → route (same facts, new places), edit → render (same edits + user text).
    """
    action = state.get("review_action")
    if action == "revise":
        return fan_out(state)
    if action == "answer":
        return "track_tasks"
    if action == "make_page":
        return "route"
    if action == "edit":
        return "render"
    if action == "approve" and state.get("approved"):
        return "apply"
    return END
