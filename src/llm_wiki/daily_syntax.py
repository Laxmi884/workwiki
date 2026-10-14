"""Daily-note Markdown syntax: sections, tasks with context, `DailyNote` (decision 0002)."""

from __future__ import annotations

import datetime
import re
from pathlib import PurePosixPath
from typing import Any

from llm_wiki.domain.daily import DailyNote, Section, SectionKind
from llm_wiki.domain.tasks import Task
from llm_wiki.frontmatter import split_frontmatter
from llm_wiki.inline_syntax import find_links, find_tags
from llm_wiki.tasks_syntax import parse_task_line, status_from_char

# "## Name", exactly level 2, optional closing hashes ("## Tasks ##").
_HEADING_RE = re.compile(r"^##[ \t]+(?P<name>.*?)(?:[ \t]+#+)?[ \t]*$")

# "### Name", exactly level 3: a meeting title inside ## Meetings.
_SUBHEADING_RE = re.compile(r"^###[ \t]+(?P<name>.*?)(?:[ \t]+#+)?[ \t]*$")

# Any list item: "- x", "* x", "+ x", "1. x" (tasks included).
_LIST_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)(?:[-*+]|\d+\.)[ \t]+(?P<content>.*)$")

_FENCE_PREFIXES = ("```", "~~~")
_TAB_WIDTH = 4
_DEFAULT_TEMPLATE = "daily-v1"
_KNOWN_TEMPLATES = frozenset({"daily-v1"})

_KIND_BY_NAME: dict[str, SectionKind] = {
    "carried over": SectionKind.CARRIED_OVER,
    "tasks": SectionKind.TASKS,
    "meetings": SectionKind.MEETINGS,
    "log": SectionKind.LOG,
    "helped": SectionKind.HELPED,
    "learned": SectionKind.LEARNED,
}


def is_fence(line: str) -> bool:
    """Return whether the line opens or closes a fenced code block."""
    return line.lstrip().startswith(_FENCE_PREFIXES)


def _kind_for(heading: str) -> SectionKind:
    """Known headings match case- and spacing-insensitively; anything else is Log."""
    key = " ".join(heading.split()).casefold()
    return _KIND_BY_NAME.get(key, SectionKind.LOG)


def split_sections(body: str, body_start_line: int = 1) -> list[Section]:
    """Split a note body on `##` headings, keeping file line numbers.

    Text before the first heading becomes a Log section with heading "" (dropped if
    blank). `##` lines inside code fences are not headings. `###` lines stay inside
    their section.
    """
    sections: list[Section] = []
    heading: str | None = None  # None until the first heading: the preamble
    start = body_start_line
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if heading is None:
            if any(line.strip() for line in current):
                sections.append(
                    Section(
                        kind=SectionKind.LOG,
                        heading="",
                        start_line=start,
                        lines=tuple(current),
                    )
                )
        else:
            sections.append(
                Section(
                    kind=_kind_for(heading),
                    heading=heading,
                    start_line=start,
                    lines=tuple(current),
                )
            )

    for i, line in enumerate(body.splitlines()):
        if is_fence(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(line)
        if m is None:
            current.append(line)
            continue
        flush()
        heading = m.group("name")
        start = body_start_line + i
        current = [line]

    flush()
    return sections


def _section_tasks(section: Section, path: str) -> list[Task]:
    """Find the tasks in one section, with `###` heading, parent, links and tags."""
    tasks: list[Task] = []
    subheading: str | None = None
    open_bullets: list[tuple[int, str]] = []  # (indent width, text), innermost last
    in_fence = False

    for i, line in enumerate(section.lines):
        if is_fence(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        sub = _SUBHEADING_RE.match(line)
        if sub is not None:
            subheading = sub.group("name")
            open_bullets.clear()
            continue

        item = _LIST_ITEM_RE.match(line)
        if item is None:
            continue  # blank line or prose: nesting carries on

        width = len(item.group("indent").expandtabs(_TAB_WIDTH))
        while open_bullets and open_bullets[-1][0] >= width:
            open_bullets.pop()
        parent = open_bullets[-1][1] if open_bullets else None

        parsed = parse_task_line(line)
        if parsed is not None:
            tasks.append(
                Task(
                    id=parsed.task_id,
                    text=parsed.text,
                    status=status_from_char(parsed.status_char),
                    source_path=path,
                    line_no=section.start_line + i,
                    section=section.kind,
                    subheading=subheading,
                    parent=parent,
                    links=find_links(parsed.text),
                    tags=find_tags(parsed.text),
                )
            )
        label = parsed.text if parsed is not None else item.group("content").strip()
        open_bullets.append((width, label))

    return tasks


def _to_date(value: Any) -> datetime.date | None:
    """Return a date from a YAML value (date, datetime or ISO string), else None."""
    if isinstance(value, datetime.datetime):  # check first: datetime is a date subclass
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _resolve_date(
    path: str, metadata: dict[str, Any], warnings: list[str]
) -> datetime.date | None:
    """Frontmatter date wins, filename is the fallback; warn on drift or no date."""
    raw = metadata.get("date")
    front = _to_date(raw)
    if raw is not None and front is None:
        warnings.append(f"frontmatter date is not a date: {raw!r}")

    from_name = _to_date(PurePosixPath(path).stem)
    if front is not None and from_name is not None and front != from_name:
        warnings.append(
            f"date drift: frontmatter says {front}, filename says {from_name}"
        )

    date = front or from_name
    if date is None:
        warnings.append("no date in frontmatter or filename")
    return date


def parse_daily_note(path: str, text: str) -> DailyNote:
    """Parse a daily note's text. Never raises on messy notes; problems become warnings.

    `path` is vault-relative ("00_raw/daily/2026-10-12.md"); it is stored on each task
    and used as the date fallback. No file is read here.
    """
    warnings: list[str] = []
    split = split_frontmatter(text)
    if split.error is not None:
        warnings.append(split.error)
    metadata = split.metadata

    template = str(metadata.get("template", _DEFAULT_TEMPLATE))
    if template not in _KNOWN_TEMPLATES:
        warnings.append(f"unknown template {template!r}; parsed as {_DEFAULT_TEMPLATE}")

    date = _resolve_date(path, metadata, warnings)
    sections = split_sections(split.body, split.body_start_line)
    tasks = [task for section in sections for task in _section_tasks(section, path)]

    return DailyNote(
        path=path,
        date=date,
        template=template,
        sections=tuple(sections),
        tasks=tuple(tasks),
        warnings=tuple(warnings),
    )
