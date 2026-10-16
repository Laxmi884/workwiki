"""Where code puts things, and how a line says where it came from.

- Every filed line carries its origin, taken from the note by code: date, meeting (the
  `###` heading above the line) and a link to the note (`Origin.bullet`).
- Quarterly pages live in year folders: the decision log
  (`30_decisions/2026/decisions-2026-q4.md`) and the impact page for the brag sheet
  (`40_impact/2026/impact-2026-q4.md`). They are not entity pages (the index reads only
  the top level of each entity folder).
- Done tasks the user picks as wins in the review go on the impact page and, if the
  task has a project page, on that page too (`win_edits`, no model involved).
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from llm_wiki.domain.changes import AppendToSection, CreateFile, Edit
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.task_index import DoneTask
from llm_wiki.entity_index import path_for

# Section headings on entity and impact pages.
UPDATES, DECISIONS, WINS, NOTES, HELP_GIVEN = (
    "## Updates",
    "## Decisions",
    "## Wins",
    "## Notes",
    "## Help given",
)


@dataclass(frozen=True)
class Origin:
    """Where a fact came from, read from the note by code.

    Attributes:
        date: The note's date.
        note: The note's file stem, for the `[[2026-10-12]]` link.
        meeting: The `###` heading above the line, without "### ", if any.
        quote: The line as written, without the bullet.
    """

    date: datetime.date
    note: str
    meeting: str | None
    quote: str

    def bullet(self, text: str) -> str:
        """Return `- 2026-10-12 · <meeting>: <text> ([[2026-10-12]])`."""
        where = f"{self.date} · {self.meeting}" if self.meeting else f"{self.date}"
        return f"- {where}: {text} ([[{self.note}]])"


def origin_of(note: DailyNote, line: int) -> Origin:
    """Return the origin of file line `line` (which must be in one of the sections)."""
    assert note.date is not None
    stem = note.path.rsplit("/", 1)[-1].removesuffix(".md")
    for section in note.sections:
        offset = line - section.start_line
        if 0 <= offset < len(section.lines):
            meeting = None
            for text in reversed(section.lines[1 : offset + 1]):
                if text.startswith("### "):
                    meeting = text[4:].strip()
                    break
            quote = re.sub(r"^\s*[-*+]\s+", "", section.lines[offset]).strip()
            return Origin(note.date, stem, meeting, quote)
    raise ValueError(f"line {line} is not in {note.path}")


def _quarter(date: datetime.date) -> str:
    return f"{date.year}-q{(date.month - 1) // 3 + 1}"


def impact_path(date: datetime.date) -> str:
    """Return the quarter's impact page, e.g. "40_impact/2026/impact-2026-q4.md"."""
    return f"40_impact/{date.year}/impact-{_quarter(date)}.md"


def decision_log_path(date: datetime.date) -> str:
    """Return the quarter's decision log, e.g. "30_decisions/2026/decisions-2026-q4.md"."""
    return f"30_decisions/{date.year}/decisions-{_quarter(date)}.md"


def win_edits(
    note: DailyNote, wins: Sequence[DoneTask], exists: Callable[[str], bool]
) -> list[Edit]:
    """Return edits that put the picked done tasks on the quarter's impact page.

    Each win is the task as written, with its project linked; it also goes under
    "## Wins" on the project page when that page exists (`exists(path)`: in the vault
    or created in this run). The impact page is created once if missing.
    """
    if note.date is None or not wins:
        return []
    path = impact_path(note.date)
    edits: list[Edit] = []
    if not exists(path):
        quarter = f"{note.date.year} Q{(note.date.month - 1) // 3 + 1}"
        edits.append(
            CreateFile(path=path, text=f"---\ntype: impact\n---\n# Impact {quarter}\n")
        )
    for t in wins:
        o = origin_of(note, t.line)
        suffix = f" ([[{t.project}]])" if t.project else ""
        edits.append(
            AppendToSection(path=path, heading=WINS, lines=(o.bullet(t.text + suffix),))
        )
        if t.project and exists(path_for(t.project)):
            edits.append(
                AppendToSection(
                    path=path_for(t.project), heading=WINS, lines=(o.bullet(t.text),)
                )
            )
    return edits
