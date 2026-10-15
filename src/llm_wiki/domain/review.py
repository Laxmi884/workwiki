"""What the review step shows and what the user answers (decisions 0004, 0005).

Plain data, no UI: Studio, the CLI, Telegram or Slack render a `ReviewRequest` and send
back a `ReviewAnswer`. The request is the `interrupt()` payload, so it must stay JSON.
"""

from __future__ import annotations

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


class ReviewDecision(BaseModel):
    """A decision found in the note.

    Attributes:
        page: Whether it gets its own page (a "make_page" answer).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    line: int
    page: bool = False


class ReviewPerson(BaseModel):
    """A name with no page: answer "new", "ignore" or an existing person slug.

    Attributes:
        name: As written, e.g. "Eugene".
        lines: Note lines naming them.
        context: The first of those lines, to recognise them by.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    lines: tuple[int, ...]
    context: str


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


class ReviewInboxItem(BaseModel):
    """A fact whose person, project or topic matched no page (3c adds answers)."""

    model_config = ConfigDict(frozen=True)

    id: str
    text: str


class ReviewRequest(BaseModel):
    """Everything the user needs to approve or reject one ingest.

    Attributes:
        note: The ingested note.
        files: Proposed changes, one per file.
        decisions: Decisions found; "make_page" gives one its own page.
        inbox: Unmatched facts (shown; answers come in 3c-2).
        warnings: Problems found along the way.
        revisions_left: How many more "revise" answers are allowed.
        can_revise: False when the note has no prose for the model to read again.
        edited: Paths whose text is the user's own (from "edit" answers).
        people: Names with no page, to answer with "answer".
        wins: Tasks done in this note; picked ones become wins.
        error: Why the previous answer was not accepted, if it wasn't.
    """

    model_config = ConfigDict(frozen=True)

    note: str
    files: tuple[ReviewFile, ...]
    decisions: tuple[ReviewDecision, ...] = ()
    inbox: tuple[ReviewInboxItem, ...] = ()
    warnings: tuple[str, ...] = ()
    revisions_left: int = 0
    can_revise: bool = False
    edited: tuple[str, ...] = ()
    people: tuple[ReviewPerson, ...] = ()
    wins: tuple[ReviewWin, ...] = ()
    error: str | None = None


Action = Literal["approve", "reject", "revise", "make_page", "edit", "answer"]

# Which optional fields each action takes ("reject" is the file list of an approval).
_FIELDS: dict[str, set[str]] = {
    "approve": {"reject"},
    "reject": set(),
    "revise": {"feedback"},
    "make_page": {"decisions"},
    "edit": {"file", "text"},
    "answer": {"people", "wins"},
}
_REQUIRED: dict[str, set[str]] = {
    "revise": {"feedback"},
    "make_page": {"decisions"},
    "edit": {"file", "text"},
}


class ReviewAnswer(BaseModel):
    """The user's answer: one action, with the fields that action takes.

    Examples:
        {"action": "approve"}                                  everything
        {"action": "approve", "reject": ["f2"]}                all but f2
        {"action": "reject"}                                   nothing is written
        {"action": "revise", "feedback": "Also note X"}        re-read the note
        {"action": "make_page", "decisions": ["d1"]}           d1 gets its own page
        {"action": "edit", "file": "f4", "text": "..."}        f4's full new text
        {"action": "answer", "people": {"p1": "new", "p2": "person-sam-ortiz",
         "p3": "ignore"}, "wins": ["w2"]}                      questions

    Attributes:
        action: What to do.
        reject: approve: file IDs to leave out.
        feedback: revise: what the model got wrong or missed.
        decisions: make_page: decision IDs.
        file: edit: the file ID.
        text: edit: the file's complete new text.
        people: answer: person question ID → "new" (make a page), "ignore" (not a
            person) or an existing person slug (the name becomes its alias).
        wins: answer: win IDs to put on the impact page; replaces earlier picks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action
    reject: tuple[str, ...] = Field(default=())
    feedback: str | None = None
    decisions: tuple[str, ...] = Field(default=())
    file: str | None = None
    text: str | None = None
    people: dict[str, str] | None = None
    wins: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _fields_match_action(self) -> ReviewAnswer:
        given = {
            name
            for name in ("reject", "feedback", "decisions", "file", "text", "people")
            if getattr(self, name) not in (None, (), "", {})
        }
        if self.wins is not None:  # [] is an answer too: pick none
            given.add("wins")
        if extra := sorted(given - _FIELDS[self.action]):
            raise ValueError(f'{", ".join(extra)} not allowed with "{self.action}"')
        if missing := sorted(_REQUIRED.get(self.action, set()) - given):
            raise ValueError(f'"{self.action}" needs {", ".join(missing)}')
        if self.action == "answer" and not given:
            raise ValueError('"answer" needs people or wins')
        return self
