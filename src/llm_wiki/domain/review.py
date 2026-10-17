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
            the raw note, the open-tasks page or anything in `90_meta/`).
        after_text: The proposed full text, the starting point for an edit; also
            sent for `90_meta/rules.md`, to read.
        required: Bookkeeping (note task IDs, task index, open-tasks page, ingest
            ledger): written with every approval, never left out (decision 0013).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    path: str
    change: Literal["new", "changed"]
    digest: str
    diff: str
    editable: bool = False
    after_text: str = ""
    required: bool = False


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


class ReviewQuestion(BaseModel):
    """A question the agent asked about one note line, answered in the review.

    Attributes:
        id: Short handle for answers, e.g. "q1".
        line: The note line it is about.
        quote: That line as written, without bullet, checkbox or task ID.
        text: The question.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    line: int
    quote: str = ""
    text: str


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


class ReviewPage(BaseModel):
    """An entity page a remembered alias may be added to: in the vault or new here.

    Attributes:
        slug: The page's slug, e.g. "person-sam-ortiz".
        title: Its title, for showing.
        names: Its slug, title and aliases (before this review's remembered ones),
            so an alias that already names a page can be refused.
    """

    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    names: tuple[str, ...] = ()


class ReviewAlias(BaseModel):
    """A name the user asked to remember for a page (an `aliases:` edit, 0009 R3).

    Attributes:
        name: The name, e.g. "Sammy".
        slug: The page it names.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    slug: str


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
        questions: The agent's questions; answered together as one "revise".
        rules: Rules the user asked to remember in this review; approved, they are
            added to `90_meta/rules.md`, which both agents read (0009 R3).
        aliases: Names the user asked to remember, each added to its page's
            `aliases:`; task mapping uses them in this review already.
        pages: The entity pages an alias may name, by title.
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
    questions: tuple[ReviewQuestion, ...] = ()
    rules: tuple[str, ...] = ()
    aliases: tuple[ReviewAlias, ...] = ()
    pages: tuple[ReviewPage, ...] = ()
    note_date: datetime.date | None = None
    task_ids_added: int = 0
    error: str | None = None


Action = Literal["approve", "reject", "revise", "edit", "answer"]

# Which optional fields each action takes ("reject" is the file list of an approval).
_FIELDS: dict[str, set[str]] = {
    "approve": {"reject"},
    "reject": set(),
    "revise": {"feedback", "remember", "answers"},
    "edit": {"file", "text"},
    "answer": {"wins", "tasks", "rules", "aliases"},
}
_REQUIRED: dict[str, set[str]] = {
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
        {"action": "answer", "rules": ["Skip lunch notes"],
         "aliases": {"Sammy": "person-sam-ortiz"}}            remember
        {"action": "revise", "feedback": "...", "remember": true}  and keep it as a rule
        {"action": "revise", "answers": {"q1": "Risk analytics work"}}  answer questions

    Attributes:
        action: What to do.
        reject: approve: file IDs to leave out (wiki pages only: bookkeeping files
            are written with every approval, decision 0013).
        feedback: revise: what the model got wrong or missed.
        file: edit: the file ID.
        text: edit: the file's complete new text.
        wins: answer: win IDs to put on the impact page; replaces earlier picks.
        tasks: answer: task ID → a project slug (existing or proposed in this
            review) or "none" (no project; not proposed again).
        rules: answer: the rules to remember; replaces the earlier list.
        aliases: answer: name → page slug, the names to remember; replaces the
            earlier list.
        remember: revise: also remember the feedback as a rule.
        answers: revise: question ID → the user's answer; code adds them to the
            feedback, so feedback is optional when answers are given.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action
    reject: tuple[str, ...] = Field(default=())
    feedback: str | None = None
    file: str | None = None
    text: str | None = None
    wins: tuple[str, ...] | None = None
    tasks: dict[str, str] | None = None
    rules: tuple[str, ...] | None = None
    aliases: dict[str, str] | None = None
    remember: bool = False
    answers: dict[str, str] | None = None

    @model_validator(mode="after")
    def _fields_match_action(self) -> ReviewAnswer:
        given = {
            name
            for name in ("reject", "feedback", "file", "text", "tasks", "answers")
            if getattr(self, name) not in (None, (), "", {})
        }
        # [] / {} are answers too: pick none, forget all
        given |= {
            n for n in ("wins", "rules", "aliases") if getattr(self, n) is not None
        }
        if self.remember:
            given.add("remember")
        if extra := sorted(given - _FIELDS[self.action]):
            raise ValueError(f'{", ".join(extra)} not allowed with "{self.action}"')
        if missing := sorted(_REQUIRED.get(self.action, set()) - given):
            raise ValueError(f'"{self.action}" needs {", ".join(missing)}')
        if self.action == "answer" and not given:
            raise ValueError('"answer" needs wins, tasks, rules or aliases')
        if self.action == "revise" and not given & {"feedback", "answers"}:
            raise ValueError('"revise" needs feedback or answers')
        if self.remember and "feedback" not in given:
            raise ValueError("remember keeps the feedback as a rule: give feedback")
        return self
