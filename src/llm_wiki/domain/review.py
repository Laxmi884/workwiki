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
    """

    model_config = ConfigDict(frozen=True)

    id: str
    path: str
    change: Literal["new", "changed"]
    digest: str
    diff: str


class ReviewDecision(BaseModel):
    """A decision found in the note (3c adds "make page")."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    line: int


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
        decisions: Decisions found (shown; answers come in 3c).
        inbox: Unmatched facts (shown; answers come in 3c).
        warnings: Problems found along the way.
        error: Why the previous answer was not accepted, if it wasn't.
    """

    model_config = ConfigDict(frozen=True)

    note: str
    files: tuple[ReviewFile, ...]
    decisions: tuple[ReviewDecision, ...] = ()
    inbox: tuple[ReviewInboxItem, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None


class ReviewAnswer(BaseModel):
    """The user's answer.

    Examples:
        {"action": "approve"}                     everything
        {"action": "approve", "reject": ["f2"]}   all but f2
        {"action": "reject"}                      nothing is written

    Attributes:
        action: "approve" or "reject".
        reject: File IDs to leave out of an approval.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["approve", "reject"]
    reject: tuple[str, ...] = Field(default=())

    @model_validator(mode="after")
    def _reject_only_with_approve(self) -> ReviewAnswer:
        if self.action == "reject" and self.reject:
            raise ValueError('"reject" lists files only with "action": "approve"')
        return self
