"""Daily note domain types (decision 0002)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SectionKind(StrEnum):
    """The fixed daily-note sections. Each one feeds a different extractor."""

    CARRIED_OVER = "carried_over"
    TASKS = "tasks"
    MEETINGS = "meetings"
    LOG = "log"  # also any unknown section, and text before the first heading
    HELPED = "helped"
    LEARNED = "learned"


class Section(BaseModel):
    """One `##` section of a daily note.

    Attributes:
        kind: Which extractor the section feeds.
        heading: The heading text as written ("" for text before the first heading).
        start_line: 1-based file line of `lines[0]`; line i is file line start_line + i.
        lines: The section's lines without line endings, heading line included.
    """

    model_config = ConfigDict(frozen=True)

    kind: SectionKind
    heading: str
    start_line: int = Field(ge=1)
    lines: tuple[str, ...]
