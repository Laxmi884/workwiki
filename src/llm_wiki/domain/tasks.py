"""Domain models and types for the LLM Wiki system."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    """Status of a task which is maintained in the task index."""

    OPEN = "open"  # Task exists but is not being worked on.
    IN_PROGRESS = "in_progress"  # The user is actively working on the task.
    DONE = "done"  # The task has been completed.
    CANCELLED = "cancelled"  # The task has been cancelled and will not be completed.


class Task(BaseModel):
    """A task from a note with optional block ID.

    Attributes:
        id: The optional task block ID, e.g. 't-abcdef'.
        text: The task text (without block ID).
        status: The status of the task (OPEN, IN_PROGRESS, DONE, CANCELLED).
        source_path: The path to the file containing the task.
        line_no: The 1-based line number of the task in its file.
    """

    model_config = ConfigDict(frozen=True)

    id: str | None = Field(default=None, pattern=r"^t-[0-9a-z]{6}$")
    text: str
    status: TaskStatus
    source_path: str
    line_no: int = Field(ge=1)
