"""Ingest graph state and per-run context (decision 0004)."""

from __future__ import annotations

import operator
from typing import Annotated

from typing_extensions import TypedDict

from llm_wiki.domain.changes import Edit
from llm_wiki.domain.daily import DailyNote


class IngestContext(TypedDict, total=False):
    """Per-run settings (Studio assistant or `context=`); missing keys use `WIKI_*`.

    Attributes:
        vault_path: Vault folder, e.g. "~/WorkDev".
        model: "provider:model" for extraction (decision 0003).
    """

    vault_path: str
    model: str


class IngestState(TypedDict, total=False):
    """What flows between ingest nodes. Facts, feedback and diffs arrive in 2d/2e.

    Attributes:
        note_path: Input: vault-relative note path, e.g. "00_raw/daily/2026-10-12.md".
        note: The parsed note (load_note).
        note_sha256: Hash of the note text, for the ingest ledger (phase 3).
        task_ids: Line number → newly assigned task ID (assign_ids).
        task_edits: ID appends and the task index rewrite (assign_ids, track_tasks).
        warnings: Problems found anywhere; nodes append.
    """

    note_path: str
    note: DailyNote
    note_sha256: str
    task_ids: dict[int, str]
    task_edits: list[Edit]
    warnings: Annotated[list[str], operator.add]
