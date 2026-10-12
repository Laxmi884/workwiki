"""Daily-note Markdown syntax: split a note body into `##` sections (decision 0002)."""

from __future__ import annotations

import re

from llm_wiki.domain.daily import Section, SectionKind

# "## Name", exactly level 2, optional closing hashes ("## Tasks ##").
_HEADING_RE = re.compile(r"^##[ \t]+(?P<name>.*?)(?:[ \t]+#+)?[ \t]*$")

_FENCE_PREFIXES = ("```", "~~~")

_KIND_BY_NAME: dict[str, SectionKind] = {
    "carried over": SectionKind.CARRIED_OVER,
    "tasks": SectionKind.TASKS,
    "meetings": SectionKind.MEETINGS,
    "log": SectionKind.LOG,
    "helped": SectionKind.HELPED,
    "learned": SectionKind.LEARNED,
}


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
        if line.lstrip().startswith(_FENCE_PREFIXES):
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
