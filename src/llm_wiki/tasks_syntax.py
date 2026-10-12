"""Task line syntax and parsing utilities."""

from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass

from llm_wiki.domain.tasks import TaskStatus

# indent, bullet (- * + or "1."), one-char checkbox, then the rest of the line.
_TASK_LINE_RE = re.compile(
    r"""
    ^(?P<indent>[ \t]*)
    (?:[-*+]|\d+\.)[ \t]+
    \[(?P<status>[^\]])\][ \t]+
    (?P<rest>.*)
    """,
    re.VERBOSE,
)

# Block ID at the very end of the rest: "^t-" + 6 lowercase base36 chars.
_TASK_ID_RE = re.compile(r"(?:^|\s)\^(?P<id>t-[0-9a-z]{6})$")

# A bare task ID, without the caret.
_ID_FORMAT_RE = re.compile(r"t-[0-9a-z]{6}")

# Checkbox char -> status. Unknown chars (Tasks plugin custom statuses) count as open.
_STATUS_BY_CHAR: dict[str, TaskStatus] = {
    " ": TaskStatus.OPEN,
    "x": TaskStatus.DONE,
    "X": TaskStatus.DONE,
    "/": TaskStatus.IN_PROGRESS,
    "-": TaskStatus.CANCELLED,
}

_ID_ALPHABET = string.digits + string.ascii_lowercase  # base36


@dataclass(frozen=True, slots=True)
class ParsedTaskLine:
    """Represents a parsed task line from a note."""

    indent: str  # leading whitespace, kept as-is ("" or "    " or "\t")
    status_char: str  # the raw char inside [ ]
    text: str  # without bullet, checkbox and ID
    task_id: str | None  # the task ID, e.g. "t-abcdef"


def parse_task_line(line: str) -> ParsedTaskLine | None:
    """Parse one Markdown line. Return None if it is not a task with text."""
    m = _TASK_LINE_RE.match(line)
    if m is None:
        return None

    rest = m.group("rest").strip()
    id_match = _TASK_ID_RE.search(rest)
    if id_match is None:
        text, task_id = rest, None
    else:
        text, task_id = rest[: id_match.start()].strip(), id_match.group("id")

    if not text:
        return None

    return ParsedTaskLine(
        indent=m.group("indent"),
        status_char=m.group("status"),
        text=text,
        task_id=task_id,
    )


def status_from_char(char: str) -> TaskStatus:
    """Map the char inside [ ] to a status; unknown chars are open."""
    return _STATUS_BY_CHAR.get(char, TaskStatus.OPEN)


def new_task_id(existing: set[str], rand: random.Random | None = None) -> str:
    """Return a random "t-xxxxxx" ID that is not in `existing`.

    Pass a seeded `random.Random` in tests to get repeatable IDs.
    """
    if rand is None:
        rand = random.Random()
    while True:
        task_id = "t-" + "".join(rand.choices(_ID_ALPHABET, k=6))
        if task_id not in existing:
            return task_id


def append_id(line: str, task_id: str) -> str:
    """Return `line` with " ^<task_id>" added at the end, and nothing else changed.

    Trailing spaces are dropped; a trailing newline is kept. Raises ValueError if
    the ID is malformed, the line is not a task, or the task already has an ID.
    """
    if not _ID_FORMAT_RE.fullmatch(task_id):
        raise ValueError(f"malformed task ID: {task_id!r}")

    body = line.rstrip("\r\n")
    ending = line[len(body) :]

    parsed = parse_task_line(body)
    if parsed is None:
        raise ValueError(f"not a task line: {line!r}")
    if parsed.task_id is not None:
        raise ValueError(f"task already has ID {parsed.task_id}: {line!r}")

    return f"{body.rstrip()} ^{task_id}{ending}"
