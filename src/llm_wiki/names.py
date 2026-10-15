"""Names in task lines and meeting headings: one small model call, checked by code.

Tasks never go to `extract` (code handles them), so a person named only in a task or a
`###` heading ("Give heat maps to Eugene", "### 1:1 with Joanna") would never reach the
review. This step asks the model for those names and for project groups of unlinked
tasks (decision 0002 rule 4); code keeps only names written in the cited lines that no
page has yet, and only task lines that really have no project.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.entities import EntityKind
from llm_wiki.domain.facts import NameScan, PersonMention, ProjectGroup
from llm_wiki.entity_index import EntityIndex, contains_phrase, kind_of, name_key

SYSTEM_PROMPT = """\
You read the task lines and meeting headings of a work daily note and return JSON \
matching the schema.

- people: humans named in the lines, first names or full names, exactly as written. \
Tools, systems, products, reports, teams and acronyms are not people (e.g. "Gitlab", \
"PowerBI", "ELTV"). Leave out people in the known entities.
- projects: group the tasks marked `*` (they have no project yet) by the piece of work \
they belong to. Name each group in one to four words taken from the task wording, or \
use a known project slug if one fits. Leave one-off chores out.
- Never invent names. Leave a list empty when nothing fits."""


def scan_lines(note: DailyNote) -> list[tuple[int, str]]:
    """Return (file line, text) for every task line and `###` heading, in note order."""
    tasks = {task.line_no for task in note.tasks}
    found = []
    for section in note.sections:
        for i, text in enumerate(section.lines):
            line_no = section.start_line + i
            if line_no in tasks or text.startswith("### "):
                found.append((line_no, text.strip()))
    return found


def build_messages(
    lines: list[tuple[int, str]],
    unmapped: Collection[int],
    entities: EntityIndex,
) -> list[BaseMessage]:
    """Build the prompt: numbered lines (unlinked tasks marked `*`), known entities."""
    known = "\n".join(
        f"- {e.slug}: {e.title}"
        + (f" (also: {', '.join(e.aliases)})" if e.aliases else "")
        for e in entities.entities.values()
        if e.kind in (EntityKind.PERSON, EntityKind.PROJECT)
    )
    numbered = "\n".join(
        f"{n:>4} {'*' if n in unmapped else '|'} {text}" for n, text in lines
    )
    return [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(f"Known entities:\n{known or '(none)'}\n\nLines:\n{numbered}"),
    ]


def is_known_person(name: str, entities: EntityIndex) -> bool:
    """Return whether `name` already means a person page (slug, title or alias)."""
    if kind_of(name) is not None:
        return True  # a slug, not a plain name: routing handles it
    if any(e.kind is EntityKind.PERSON for e in entities.lookup(name)):
        return True
    found, ambiguous = entities.name_mentions(name, EntityKind.PERSON)
    return bool(found or ambiguous)


def check_scan(
    scan: NameScan,
    lines: list[tuple[int, str]],
    unmapped: Collection[int],
    entities: EntityIndex,
    ignore: Collection[str] = (),
) -> tuple[NameScan, list[str]]:
    """Keep what the lines support. Returns (checked scan, what was dropped).

    People: the name must be written in one of its cited lines and match no page or
    ignored name; repeats are merged. Projects: only task lines without a project.
    """
    text_of = dict(lines)
    ignored = {name_key(n) for n in ignore}
    dropped: list[str] = []

    people: dict[str, PersonMention] = {}
    for p in scan.people:
        name = " ".join(p.name.split())
        cited = sorted({n for n in p.lines if n in text_of})
        written = [n for n in cited if name and contains_phrase(text_of[n], name)]
        if not written:
            dropped.append(f"person {p.name!r}: not written in lines {p.lines}")
            continue
        key = name_key(name)
        if is_known_person(name, entities) or key in ignored:
            continue
        if key in people:
            written = sorted({*people[key].lines, *written})
            name = people[key].name
        people[key] = PersonMention(name=name, lines=written)

    projects: dict[str, ProjectGroup] = {}
    for g in scan.projects:
        name = " ".join(g.name.split())
        tasks = sorted({n for n in g.lines if n in unmapped})
        if not name or not tasks:
            dropped.append(f"project {g.name!r}: no unlinked task lines")
            continue
        key = name_key(name)
        if key in projects:
            tasks = sorted({*projects[key].lines, *tasks})
        projects[key] = ProjectGroup(name=name, lines=tasks)

    ordered = sorted(people.values(), key=lambda p: p.lines[0])
    groups = sorted(projects.values(), key=lambda g: g.lines[0])
    return NameScan(people=ordered, projects=groups), dropped


def scan_names(
    model: BaseChatModel,
    note: DailyNote,
    task_projects: Mapping[int, str | None],
    entities: EntityIndex,
    ignore: Collection[str] = (),
) -> tuple[NameScan, list[str]]:
    """Ask the model for names and project groups; return the checked scan and warnings.

    One attempt: invalid output gives an empty scan and a warning (the review still
    works, just without these questions).
    """
    lines = scan_lines(note)
    unmapped = {n for n, project in task_projects.items() if project is None}
    structured = model.with_structured_output(NameScan, include_raw=True)
    result = structured.invoke(build_messages(lines, unmapped, entities))
    parsed = result.get("parsed")
    if not isinstance(parsed, NameScan):
        return NameScan(), [
            f"name scan: invalid output ({result.get('parsing_error')})"
        ]
    checked, dropped = check_scan(parsed, lines, unmapped, entities, ignore)
    return checked, [f"name scan: {d}" for d in dropped]
