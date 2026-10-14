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
from langgraph.types import Send

from llm_wiki import extraction, ledger, routing, task_tracking
from llm_wiki.config import get_settings
from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.diffs import VaultReader, render_diffs, vault_reader
from llm_wiki.domain.changes import RAW_DAILY_FOLDER, ReplaceFile, sha256_text
from llm_wiki.domain.daily import DailyNote
from llm_wiki.entity_index import load_index
from llm_wiki.ingest.state import ExtractInput, IngestContext, IngestState
from llm_wiki.models import choose_model_spec, make_chat_model


def vault_path(runtime: Runtime[IngestContext]) -> Path:
    """Return the run's vault: context `vault_path`, else `WIKI_VAULT_PATH`."""
    context = runtime.context or {}
    if context.get("vault_path"):
        return Path(context["vault_path"]).expanduser().resolve()
    return get_settings().vault_path


def chat_model(runtime: Runtime[IngestContext]) -> BaseChatModel:
    """Return the run's model: context `model`, else `WIKI_MODEL` (decision 0003)."""
    context = runtime.context or {}
    spec = choose_model_spec(context.get("model"), get_settings().model)
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
    """Fold the note's tasks into `90_meta/tasks.json` (status, project mapping)."""
    vault = vault_path(runtime)
    read = vault_reader(vault)
    index, problem = task_tracking.load_task_index(read(task_tracking.TASK_INDEX_PATH))
    if index is None:
        return {"warnings": [problem]}

    updated, warnings = task_tracking.update_task_index(
        index, state["note"], state.get("task_ids", {}), load_index(vault)
    )
    edits = list(state.get("task_edits", []))
    if updated != index:
        text = task_tracking.dump_task_index(updated)
        edits.append(ReplaceFile(path=task_tracking.TASK_INDEX_PATH, text=text))
    return {"task_edits": edits, "warnings": warnings}


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
    """Send each section with new prose to `extract` in parallel; none → `route`."""
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
    return sends or "route"


def route(state: IngestState, runtime: Runtime[IngestContext]) -> dict[str, Any]:
    """Turn all facts into page edits (rebuilt from scratch each round)."""
    vault = vault_path(runtime)
    read = vault_reader(vault)
    edits, warnings = routing.route_facts(
        state["note"],
        state.get("facts", {}),
        load_index(vault),
        lambda path: read(path) is not None,
    )
    return {"fact_edits": edits, "warnings": warnings}


def render(state: IngestState, runtime: Runtime[IngestContext]) -> dict[str, Any]:
    """Render task, fact and ledger edits into one diff per file (nothing is written).

    The ledger records the note's hash *after* its task IDs are added, so once the
    diffs are applied the next run sees the note as unchanged.
    """
    read = vault_reader(vault_path(runtime))
    edits = [*state.get("task_edits", []), *state.get("fact_edits", [])]
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
