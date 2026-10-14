"""Code-only ingest nodes: read the note, assign task IDs, update the task index.

Each node reads the vault through `vault_reader` (no writes) and returns a partial
state update. The pure logic lives in `daily_syntax` and `task_tracking`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from llm_wiki import task_tracking
from llm_wiki.config import get_settings
from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.diffs import VaultReader, vault_reader
from llm_wiki.domain.changes import RAW_DAILY_FOLDER, ReplaceFile, sha256_text
from llm_wiki.domain.daily import DailyNote
from llm_wiki.entity_index import load_index
from llm_wiki.ingest.state import IngestContext, IngestState


def vault_path(runtime: Runtime[IngestContext]) -> Path:
    """Return the run's vault: context `vault_path`, else `WIKI_VAULT_PATH`."""
    context = runtime.context or {}
    if context.get("vault_path"):
        return Path(context["vault_path"]).expanduser().resolve()
    return get_settings().vault_path


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
