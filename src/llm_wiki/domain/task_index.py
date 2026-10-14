"""The task index (`90_meta/tasks.json`): one record per task ID, owned by code."""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from llm_wiki.domain.entities import SLUG_PATTERN
from llm_wiki.domain.tasks import TaskStatus

# How a task's project was chosen (decision 0002 rules 1–3); None if unmapped.
MappedBy = Literal["link", "context", "alias"]


class TaskRecord(BaseModel):
    """The latest known state of one task across all daily notes.

    Attributes:
        text: Task text from the note that set the status.
        status: From the newest note; on the same date, done/cancelled wins.
        project: Project slug, or None if no rule matched.
        mapped_by: Which rule set `project` ("link", "context", "alias").
        people: Person slugs linked on the task line, e.g. ("person-sam-ortiz",).
        tags: Tags on the task line, e.g. ("blocked",).
        first_seen: Date of the earliest note containing the task.
        last_seen: Date of the note that set `status`.
        source: Vault path of that note.
        line: 1-based line of the task in `source`.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    status: TaskStatus
    project: str | None = Field(default=None, pattern=SLUG_PATTERN)
    mapped_by: MappedBy | None = None
    people: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    first_seen: datetime.date
    last_seen: datetime.date
    source: str
    line: int = Field(ge=1)


class TaskIndex(BaseModel):
    """All tracked tasks, keyed by task ID."""

    model_config = ConfigDict(frozen=True)

    version: Literal[1] = 1
    tasks: dict[str, TaskRecord] = Field(default_factory=dict)
