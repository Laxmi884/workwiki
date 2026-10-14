"""Ingest nodes: note → task IDs and index → facts → page edits → diffs.

Each node reads the vault through `vault_reader` (no writes) and returns a partial
state update. The pure logic lives in `daily_syntax`, `task_tracking`, `extraction`
and `routing`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.runtime import Runtime
from langgraph.types import Send

from llm_wiki import extraction, routing, task_tracking
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
    """Read and parse `note_path`. A missing note is a caller error and raises."""
    path = state["note_path"]
    text = vault_reader(vault_path(runtime))(path)
    if text is None:
        raise FileNotFoundError(f"note not found in vault: {path}")
    note = parse_daily_note(path, text)
    return {
        "note": note,
        "note_sha256": sha256_text(text),
        "warnings": [f"{path}: {w}" for w in note.warnings],
    }


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
    )
    return {
        "facts": {str(section.start_line): facts},
        "warnings": [f"{note.path}: {w}" for w in warnings],
    }


def fan_out(state: IngestState) -> list[Send] | str:
    """Send each prose section to `extract` in parallel; none → straight to `route`."""
    note = state["note"]
    tasks = extraction.task_lines(note)
    facts = state.get("facts", {})
    sends = [
        Send(
            "extract",
            {
                "note": note,
                "section_line": section.start_line,
                "previous": facts.get(str(section.start_line)),
                "feedback": state.get("feedback", []),
            },
        )
        for section in note.sections
        if extraction.needs_model(section, tasks)
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
    """Render task and fact edits into one diff per file (nothing is written)."""
    edits = [*state.get("task_edits", []), *state.get("fact_edits", [])]
    diffs, warnings = render_diffs(edits, vault_reader(vault_path(runtime)))
    return {"diffs": diffs, "warnings": warnings}
