"""Ingest graph state and per-run context (decisions 0004, 0006)."""

from __future__ import annotations

import operator
from typing import Annotated

from typing_extensions import TypedDict

from llm_wiki.domain.changes import Edit, FileDiff
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.task_index import DoneTask, TaskProposal


class IngestContext(TypedDict, total=False):
    """Per-run settings (Studio assistant or `context=`); missing keys use `WIKI_*`.

    Attributes:
        vault_path: Vault folder, e.g. "~/WorkDev".
        model: "provider:model" for the ingest agent (decision 0003).
        skills_dir: Skills folder for the ingest agent (default: its own).
    """

    vault_path: str
    model: str
    skills_dir: str


class IngestState(TypedDict, total=False):
    """What flows between ingest nodes.

    Attributes:
        note_path: Input: vault-relative note path, e.g. "00_raw/daily/2026-10-12.md".
        vault_path: The vault this run reads and writes (absolute), set by load_note;
            the runner refuses to resume the thread for another vault (0009).
        note: The parsed note (load_note).
        note_sha256: Hash of the note text, for the ingest ledger.
        seen_lines: Keys of prose lines already ingested (ledger); only others give facts.
        skip_reason: Set when there is nothing to do (unchanged note, broken ledger).
        task_ids: Line number → newly assigned task ID (assign_ids).
        task_edits: ID appends and the task index rewrite (assign_ids, track_tasks).
        task_projects: Task line → its project slug or None, by rules 1–3 and the
            index only (track_tasks).
        open_tasks: Task line → rule-3 candidates (or ()) for tasks nothing mapped
            yet; the model may propose a project for these (decision 0007).
        task_proposals: The model's checked task → project proposals (agent step).
        chosen_projects: Task line → the user's review answer: a project slug, or
            None for "no project" (sticky).
        done_tasks: Tasks this note marks done for the first time (track_tasks).
        feedback: The user's revise messages, oldest first (review).
        revisions: How many revise rounds have run (at most `review.MAX_REVISIONS`).
        overrides: Path → the user's own text for that page (review "edit"); a
            revise or answer clears them.
        fact_edits: The agent's checked edits plus picked wins, rebuilt each round.
        diffs: One proposed diff per changed file (render).
        review_action: The user's last answer: "approve" or "reject" (the run ends),
            or "revise", "edit", "answer" (it loops back).
        approved: Digests of the approved diffs; only these may be written (3d).
        written: Paths `apply` wrote (or found already up to date).
        commit: The vault git commit of those paths, if any (3e).
        apply_conflicts: Approved paths that changed since the review; nothing was
            written and the run re-renders for a fresh review.
        chosen_wins: Lines of done tasks the user picked as wins (review "answer").
        warnings: Problems found anywhere; nodes append.
        round_warnings: What the agent step says about its latest round (replaced each
            round, so a revise doesn't repeat old notes); the review shows both.
    """

    note_path: str
    vault_path: str
    note: DailyNote
    note_sha256: str
    seen_lines: list[str]
    skip_reason: str
    task_ids: dict[int, str]
    task_edits: list[Edit]
    task_projects: dict[int, str | None]
    open_tasks: dict[int, list[str]]
    task_proposals: list[TaskProposal]
    chosen_projects: dict[int, str | None]
    done_tasks: list[DoneTask]
    feedback: list[str]
    revisions: int
    overrides: dict[str, str]
    fact_edits: list[Edit]
    diffs: list[FileDiff]
    review_action: str
    approved: list[str]
    written: list[str]
    commit: str | None
    apply_conflicts: list[str]
    chosen_wins: list[int]
    warnings: Annotated[list[str], operator.add]
    round_warnings: list[str]


class IngestInput(TypedDict):
    """What a run starts with (the Studio input form).

    Attributes:
        note_path: Vault-relative note path, e.g. "00_raw/daily/2026-10-12.md".
    """

    note_path: str
