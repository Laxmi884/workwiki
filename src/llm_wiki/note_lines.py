"""Which lines of a daily note are prose, and keys to tell new lines from ingested ones.

Prose is what the agent may cite: not tasks (code tracks those), blank lines, empty
bullets ("- " from the template), `##` headings or fenced code. A line's key ignores
indentation and spacing, so the ledger (phase 3a) recognises a line it has seen.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Sequence

from llm_wiki.daily_syntax import is_fence
from llm_wiki.domain.common import SectionKind
from llm_wiki.domain.daily import DailyNote, Section

# Sections whose prose the agent reads; Carried over and Tasks hold only tasks (code).
PROSE_SECTIONS = frozenset(
    {SectionKind.MEETINGS, SectionKind.LOG, SectionKind.HELPED, SectionKind.LEARNED}
)

# A blank line, or a bullet with nothing after it: "-", "- ", "*", "1.".
_EMPTY_BULLET = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s*)?$")


def prose_lines(section: Section, task_lines: set[int]) -> list[tuple[int, str]]:
    """Return (file line, text) for the section's prose lines."""
    lines: list[tuple[int, str]] = []
    in_fence = False
    for i, text in enumerate(section.lines):
        line_no = section.start_line + i
        if is_fence(text):
            in_fence = not in_fence
            continue
        if in_fence or _EMPTY_BULLET.match(text) or line_no in task_lines:
            continue
        if i == 0 and section.heading:  # the `##` heading itself
            continue
        lines.append((line_no, text.rstrip()))
    return lines


def line_key(text: str) -> str:
    """Return a short key for a prose line: same words, same key (indent/spacing ignored)."""
    normal = " ".join(text.split())
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:16]


def new_lines(
    lines: Sequence[tuple[int, str]], seen: Collection[str]
) -> list[tuple[int, str]]:
    """Return the lines whose key isn't in `seen` (not ingested before)."""
    return [(n, text) for n, text in lines if line_key(text) not in seen]


def task_lines(note: DailyNote) -> set[int]:
    """Return the file lines of the note's tasks."""
    return {task.line_no for task in note.tasks}


def has_new_prose(
    section: Section, tasks: set[int], seen: Collection[str] = ()
) -> bool:
    """Return whether the section has new prose lines (worth a revise round)."""
    return section.kind in PROSE_SECTIONS and bool(
        new_lines(prose_lines(section, tasks), seen)
    )
