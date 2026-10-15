"""Ingest graph state and per-run context (decision 0004)."""

from __future__ import annotations

import operator
from typing import Annotated

from typing_extensions import TypedDict

from llm_wiki.domain.changes import Edit, FileDiff
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.facts import SectionFacts


def merge_facts(
    old: dict[str, SectionFacts], new: dict[str, SectionFacts]
) -> dict[str, SectionFacts]:
    """Reducer: parallel extracts add their section's key; a re-run replaces its own."""
    return {**old, **new}


class IngestContext(TypedDict, total=False):
    """Per-run settings (Studio assistant or `context=`); missing keys use `WIKI_*`.

    Attributes:
        vault_path: Vault folder, e.g. "~/WorkDev".
        model: "provider:model" for extraction (decision 0003).
    """

    vault_path: str
    model: str


class IngestState(TypedDict, total=False):
    """What flows between ingest nodes.

    Attributes:
        note_path: Input: vault-relative note path, e.g. "00_raw/daily/2026-10-12.md".
        note: The parsed note (load_note).
        note_sha256: Hash of the note text, for the ingest ledger.
        seen_lines: Keys of prose lines already ingested (ledger); only others give facts.
        skip_reason: Set when there is nothing to do (unchanged note, broken ledger).
        task_ids: Line number → newly assigned task ID (assign_ids).
        task_edits: ID appends and the task index rewrite (assign_ids, track_tasks).
        facts: Section key (its start line, e.g. "20") → facts (extract).
        feedback: The user's revise messages (phase 3 review).
        fact_edits: Edits from all facts, rebuilt each round (route).
        diffs: One proposed diff per changed file (render).
        review_action: The user's answer, "approve" or "reject" (review).
        approved: Digests of the approved diffs; only these may be written (3d).
        warnings: Problems found anywhere; nodes append.
    """

    note_path: str
    note: DailyNote
    note_sha256: str
    seen_lines: list[str]
    skip_reason: str
    task_ids: dict[int, str]
    task_edits: list[Edit]
    facts: Annotated[dict[str, SectionFacts], merge_facts]
    feedback: list[str]
    fact_edits: list[Edit]
    diffs: list[FileDiff]
    review_action: str
    approved: list[str]
    warnings: Annotated[list[str], operator.add]


class IngestInput(TypedDict):
    """What a run starts with (the Studio input form).

    Attributes:
        note_path: Vault-relative note path, e.g. "00_raw/daily/2026-10-12.md".
    """

    note_path: str


class ExtractInput(TypedDict, total=False):
    """What one `extract` run gets (2e sends one per prose section).

    Attributes:
        note: The parsed note.
        section_line: Start line of the section to read; also its key in `facts`.
        previous: The section's facts from the last round, when revising.
        feedback: The user's revise messages.
        seen_lines: Keys of prose lines already ingested; they're context only.
    """

    note: DailyNote
    section_line: int
    previous: SectionFacts | None
    feedback: list[str]
    seen_lines: list[str]
