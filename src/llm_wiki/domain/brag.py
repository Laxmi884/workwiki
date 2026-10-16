"""The review-writer's draft (decision 0008): evidence for a brag sheet, every item sourced."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# "[[2026-10-12]]" or "[[project-atlas]]": the daily note or page the item comes from.
SOURCE_PATTERN = r"^\[\[[^\[\]|#]+\]\]$"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class BragItem(BaseModel):
    """One accomplishment, with where it is written down.

    Attributes:
        kind: "win", "help" (help given to someone) or "decision" (one the user drove).
        text: What happened, in the vault's words; numbers exactly as written.
        date: The date of the line it comes from, YYYY-MM-DD.
        source: The daily note or page it comes from, as an Obsidian link.
        metric: A number or outcome the notes state for it, verbatim; None if none.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["win", "help", "decision"]
    text: str = Field(min_length=1, max_length=400)
    date: str = Field(pattern=DATE_PATTERN)
    source: str = Field(pattern=SOURCE_PATTERN)
    metric: str | None = None


class BragSection(BaseModel):
    """Items for one project, or for work outside any project (`project` None)."""

    model_config = ConfigDict(frozen=True)

    project: str | None = Field(
        default=None, description='Project slug, e.g. "project-atlas"; None: no project'
    )
    items: list[BragItem] = Field(default_factory=list)


class BragDraft(BaseModel):
    """The review-writer's structured result for a period.

    Attributes:
        period: The period covered, as asked, e.g. "2026 Q4" or "2026-07-01 to 2026-12-31".
        sections: Items grouped by project.
        gaps: What the user should fill in: items with no outcome or number, quiet
            stretches, projects with updates but no wins.
    """

    model_config = ConfigDict(frozen=True)

    period: str
    sections: list[BragSection] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
