"""Daily note domain types (decision 0002)."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict, Field

from llm_wiki.domain.common import SectionKind
from llm_wiki.domain.tasks import Task

__all__ = ["DailyNote", "Section", "SectionKind"]


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


class DailyNote(BaseModel):
    """A parsed daily note.

    Attributes:
        path: Vault-relative path, e.g. "00_raw/daily/2026-10-12.md".
        date: From frontmatter, else the filename; None if neither is a valid date.
        template: Frontmatter `template`, "daily-v1" if missing.
        sections: The note's `##` sections in file order.
        tasks: Every task in the note, with context, in file order.
        warnings: Problems found while parsing (bad frontmatter, date drift, ...).
    """

    model_config = ConfigDict(frozen=True)

    path: str
    date: datetime.date | None
    template: str
    sections: tuple[Section, ...]
    tasks: tuple[Task, ...]
    warnings: tuple[str, ...] = ()
