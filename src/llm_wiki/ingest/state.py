"""Ingest graph state and per-run context (decision 0004)."""

from __future__ import annotations

import operator
from typing import Annotated

from typing_extensions import TypedDict

from llm_wiki.domain.changes import Edit, FileDiff
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.entities import Entity
from llm_wiki.domain.facts import NameScan, SectionFacts
from llm_wiki.domain.task_index import DoneTask


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
        skills_dir: Skills folder for the 0006 spike's ingest agent (default: its own).
    """

    vault_path: str
    model: str
    skills_dir: str


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
        task_projects: Task line → its project slug or None (track_tasks).
        done_tasks: Tasks this note marks done for the first time (track_tasks).
        name_scan: People and project groups in task lines and headings (find_names).
        facts: Section key (its start line, e.g. "20") → facts (extract).
        feedback: The user's revise messages, oldest first (review).
        revisions: How many revise rounds have run (at most `review.MAX_REVISIONS`).
        decision_pages: Note lines of decisions that get their own page (review).
        overrides: Path → the user's own text for that page (review "edit"); a
            revise or make_page clears them.
        fact_edits: Edits from all facts, rebuilt each round (route).
        diffs: One proposed diff per changed file (render).
        review_action: The user's last answer: "approve" or "reject" (the run ends),
            or "revise", "make_page", "edit", "answer" (it loops back).
        approved: Digests of the approved diffs; only these may be written (3d).
        written: Paths `apply` wrote (or found already up to date).
        commit: The vault git commit of those paths, if any (3e).
        apply_conflicts: Approved paths that changed since the review; nothing was
            written and the run re-renders for a fresh review.
        chosen_wins: Lines of done tasks the user picked as wins (review "answer").
        new_entities: Pages the user asked for ("new"); proposed, not yet written.
        new_aliases: Slug → names the user said are that entity.
        ignored_names: Names the user said are not people (this run).
        warnings: Problems found anywhere; nodes append.
    """

    note_path: str
    note: DailyNote
    note_sha256: str
    seen_lines: list[str]
    skip_reason: str
    task_ids: dict[int, str]
    task_edits: list[Edit]
    task_projects: dict[int, str | None]
    done_tasks: list[DoneTask]
    name_scan: NameScan
    facts: Annotated[dict[str, SectionFacts], merge_facts]
    feedback: list[str]
    revisions: int
    decision_pages: list[int]
    overrides: dict[str, str]
    fact_edits: list[Edit]
    diffs: list[FileDiff]
    review_action: str
    approved: list[str]
    written: list[str]
    commit: str | None
    apply_conflicts: list[str]
    chosen_wins: list[int]
    new_entities: list[Entity]
    new_aliases: dict[str, list[str]]
    ignored_names: list[str]
    warnings: Annotated[list[str], operator.add]


class IngestInput(TypedDict):
    """What a run starts with (the Studio input form).

    Attributes:
        note_path: Vault-relative note path, e.g. "00_raw/daily/2026-10-12.md".
    """

    note_path: str


class FindNamesInput(TypedDict):
    """What `find_names` gets (sent alongside the extracts).

    Attributes:
        note: The parsed note.
        task_projects: Task line → project slug or None; None lines may be grouped.
    """

    note: DailyNote
    task_projects: dict[int, str | None]


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
