"""What the review step shows and what the user answers (decisions 0004, 0005).

Plain data, no UI: Studio, the CLI, Telegram or Slack render a `ReviewRequest` and send
back a `ReviewAnswer`. The request is the `interrupt()` payload, so it must stay JSON.
"""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewFile(BaseModel):
    """One proposed file change.

    Attributes:
        id: Short handle for answers, e.g. "f3".
        path: Vault-relative path.
        change: "new" (file is created) or "changed".
        digest: The `FileDiff` digest; approval is for exactly this change.
        diff: Unified diff to show.
        editable: Whether an "edit" answer may replace its text (wiki pages only; not
            the raw note or code-owned `90_meta/*.json`).
        after_text: The proposed full text, the starting point for an edit.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    path: str
    change: Literal["new", "changed"]
    digest: str
    diff: str
    editable: bool = False
    after_text: str = ""


class ReviewFact(BaseModel):
    """A fact the agent filed on a proposed page, with the note line it cites.

    Attributes:
        path: The page it goes to (a `ReviewFile` path).
        section: Its section heading, e.g. "## Decisions".
        text: The filed text.
        line: The note line it cites.
        quote: That line as written, without bullet, checkbox or task ID.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    section: str
    text: str
    line: int
    quote: str = ""


class ReviewWin(BaseModel):
    """A task done in this note; picked ones go to the impact page as wins.

    Attributes:
        text: The task as written (no rewording).
        project: Its project slug, if mapped.
        chosen: Whether it is picked (an earlier "answer").
    """

    model_config = ConfigDict(frozen=True)

    id: str
    line: int
    text: str
    project: str | None = None
    chosen: bool = False


class ReviewTask(BaseModel):
    """A task with no project by rules 1–3: the model's proposal or the user's answer.

    Attributes:
        line: The task's line in the note.
        text: The task as written.
        project: The project it will be stored with, or None.
        by: "model" (a proposal; in `tasks.json` unless answered), "user" (an
            earlier answer) or None (nothing proposed).
        why: The model's reason, for a proposal.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    line: int
    text: str
    project: str | None = None
    by: Literal["model", "user"] | None = None
    why: str = ""


class ReviewProject(BaseModel):
    """A project a task may be answered with: a page in the vault or new in this review.

    Attributes:
        slug: The project's slug, e.g. "project-atlas".
        title: Its page title, for showing.
    """

    model_config = ConfigDict(frozen=True)

    slug: str
    title: str


class ReviewRequest(BaseModel):
    """Everything the user needs to approve or reject one ingest.

    Attributes:
        note: The ingested note.
        files: Proposed changes, one per file.
        warnings: Problems found along the way.
        revisions_left: How many more "revise" answers are allowed.
        can_revise: False when the note has no prose for the model to read again.
        edited: Paths whose text is the user's own (from "edit" answers).
        wins: Tasks done in this note; picked ones become wins.
        tasks: Tasks with no project by rules 1–3, with the model's proposals; the
            proposals are in `tasks.json` unless answered (decision 0007).
        projects: The projects a task answer may name, by title; the same list the
            answer is checked against.
        facts: What the agent filed, per page, with the note lines cited.
        note_date: The note's date, if it has one.
        task_ids_added: How many task IDs this ingest appends to the note.
        error: Why the previous answer was not accepted, if it wasn't.
    """

    model_config = ConfigDict(frozen=True)

    note: str
    files: tuple[ReviewFile, ...]
    warnings: tuple[str, ...] = ()
    revisions_left: int = 0
    can_revise: bool = False
    edited: tuple[str, ...] = ()
    wins: tuple[ReviewWin, ...] = ()
    tasks: tuple[ReviewTask, ...] = ()
    projects: tuple[ReviewProject, ...] = ()
    facts: tuple[ReviewFact, ...] = ()
    note_date: datetime.date | None = None
    task_ids_added: int = 0
    error: str | None = None


Action = Literal["approve", "reject", "revise", "edit", "answer"]

# Which optional fields each action takes ("reject" is the file list of an approval).
_FIELDS: dict[str, set[str]] = {
    "approve": {"reject"},
    "reject": set(),
    "revise": {"feedback"},
    "edit": {"file", "text"},
    "answer": {"wins", "tasks"},
}
_REQUIRED: dict[str, set[str]] = {
    "revise": {"feedback"},
    "edit": {"file", "text"},
}


class ReviewAnswer(BaseModel):
    """The user's answer: one action, with the fields that action takes.

    Examples:
        {"action": "approve"}                                  everything
        {"action": "approve", "reject": ["f2"]}                all but f2
        {"action": "reject"}                                   nothing is written
        {"action": "revise", "feedback": "Also note X"}        re-read the note
        {"action": "edit", "file": "f4", "text": "..."}        f4's full new text
        {"action": "answer", "wins": ["w2"],
         "tasks": {"m1": "project-atlas", "m2": "none"}}       wins, tasks

    Attributes:
        action: What to do.
        reject: approve: file IDs to leave out.
        feedback: revise: what the model got wrong or missed.
        file: edit: the file ID.
        text: edit: the file's complete new text.
        wins: answer: win IDs to put on the impact page; replaces earlier picks.
        tasks: answer: task ID → a project slug (existing or proposed in this
            review) or "none" (no project; not proposed again).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action
    reject: tuple[str, ...] = Field(default=())
    feedback: str | None = None
    file: str | None = None
    text: str | None = None
    wins: tuple[str, ...] | None = None
    tasks: dict[str, str] | None = None

    @model_validator(mode="after")
    def _fields_match_action(self) -> ReviewAnswer:
        given = {
            name
            for name in ("reject", "feedback", "file", "text", "tasks")
            if getattr(self, name) not in (None, (), "", {})
        }
        if self.wins is not None:  # [] is an answer too: pick none
            given.add("wins")
        if extra := sorted(given - _FIELDS[self.action]):
            raise ValueError(f'{", ".join(extra)} not allowed with "{self.action}"')
        if missing := sorted(_REQUIRED.get(self.action, set()) - given):
            raise ValueError(f'"{self.action}" needs {", ".join(missing)}')
        if self.action == "answer" and not given:
            raise ValueError('"answer" needs wins or tasks')
        return self
