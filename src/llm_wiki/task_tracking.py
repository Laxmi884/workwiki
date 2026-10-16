"""Task IDs, task → project mapping, and the task index (decisions 0001, 0002, 0004, 0007).

All pure: notes, the entity index and the index text come in; IDs, edits and a new
index come out. The ingest nodes do the reading.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable, Mapping

import pydantic

from llm_wiki.domain.changes import AppendId
from llm_wiki.domain.common import SectionKind
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.entities import EntityKind
from llm_wiki.domain.task_index import MappedBy, TaskIndex, TaskRecord
from llm_wiki.domain.tasks import Task, TaskStatus
from llm_wiki.entity_index import EntityIndex, kind_of
from llm_wiki.inline_syntax import find_links
from llm_wiki.tasks_syntax import new_task_id

TASK_INDEX_PATH = "90_meta/tasks.json"

_CLOSED = frozenset({TaskStatus.DONE, TaskStatus.CANCELLED})


# --- task IDs --------------------------------------------------------------------


def ids_in_notes(notes: Iterable[DailyNote]) -> set[str]:
    """Return every task ID written in `notes`."""
    return {task.id for note in notes for task in note.tasks if task.id is not None}


def assign_ids(
    note: DailyNote, existing: set[str]
) -> tuple[dict[int, str], list[AppendId]]:
    """Give each task without an ID a new one, unique against `existing` and the note.

    The generator is seeded from the note path, so re-running on the same note
    proposes the same IDs (a revise round doesn't reshuffle them).

    Returns:
        (line number → new ID, one `AppendId` edit per new ID)
    """
    rand = random.Random(note.path)
    used = set(existing) | ids_in_notes([note])
    ids: dict[int, str] = {}
    edits: list[AppendId] = []
    for task in note.tasks:
        if task.id is not None:
            continue
        task_id = new_task_id(used, rand)
        used.add(task_id)  # before generating the next one
        ids[task.line_no] = task_id
        edits.append(AppendId(path=note.path, line_no=task.line_no, task_id=task_id))
    return ids, edits


# --- project mapping (decision 0002, rules 1–3; rule 4 in 0007) -----------------

# Line → (project slug or None, "model" | "user"): rule-4 choices from the review.
Chosen = Mapping[int, tuple[str | None, MappedBy]]


def _project_links(text: str | None) -> list[str]:
    return [
        slug for slug in find_links(text or "") if kind_of(slug) is EntityKind.PROJECT
    ]


def map_project(
    task: Task, entities: EntityIndex, inherited: str | None = None
) -> tuple[str | None, MappedBy | None]:
    """Return (project slug, rule) for a task, or (None, None).

    1. "link": a `[[project-…]]` on the task line.
    2. "context": a project link in the parent bullet, else the parent task's project
       (`inherited`), else a project link in the `###` heading.
    3. "alias": exactly one project alias in the task text (two or more: ambiguous).
    """
    if links := _project_links(task.text):
        return links[0], "link"
    if links := _project_links(task.parent):
        return links[0], "context"
    if inherited is not None:
        return inherited, "context"
    if links := _project_links(task.subheading):
        return links[0], "context"
    found = entities.alias_mentions(task.text, EntityKind.PROJECT)
    if len(found) == 1:
        return found[0].slug, "alias"
    return None, None


def alias_candidates(task: Task, entities: EntityIndex) -> tuple[str, ...]:
    """Return the projects whose aliases the task names, if two or more (rule 3 → 4)."""
    found = entities.alias_mentions(task.text, EntityKind.PROJECT)
    return tuple(e.slug for e in found) if len(found) > 1 else ()


# --- people ----------------------------------------------------------------------


def task_people(task: Task, entities: EntityIndex) -> tuple[tuple[str, ...], list[str]]:
    """Return (person slugs, ambiguous names) for a task.

    People come from, in order: `[[person-…]]` links on the task line, person names
    (title or alias) in the task text, then the same in the parent bullet and the `###`
    heading ("### 1:1 with Joanna" gives every task under it Joanna). A name shared by
    two people links neither.
    """
    slugs = [s for s in task.links if kind_of(s) is EntityKind.PERSON]
    ambiguous: list[str] = []
    for text in (task.text, task.parent, task.subheading):
        if not text:
            continue
        slugs += [s for s in find_links(text) if kind_of(s) is EntityKind.PERSON]
        found, unclear = entities.name_mentions(text, EntityKind.PERSON)
        slugs += [e.slug for e in found]
        ambiguous += unclear
    return tuple(dict.fromkeys(slugs)), list(dict.fromkeys(ambiguous))


# --- the index -------------------------------------------------------------------


def load_task_index(text: str | None) -> tuple[TaskIndex | None, str | None]:
    """Parse `tasks.json`. Missing file: empty index. Broken file: (None, problem).

    A broken index is never replaced: overwriting it would lose every record.
    """
    if text is None:
        return TaskIndex(), None
    try:
        return TaskIndex.model_validate_json(text), None
    except pydantic.ValidationError as exc:
        problem = (
            f"{TASK_INDEX_PATH} is not a valid task index ({exc.error_count()} errors)"
        )
        return None, f"{problem}; tasks not tracked"


def dump_task_index(index: TaskIndex) -> str:
    """Serialise deterministically (sorted keys, 2-space indent) so diffs stay small."""
    data = index.model_dump(mode="json")
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _status_from_this_note(old: TaskRecord, task: Task, note: DailyNote) -> bool:
    """Return whether this note sets the status (newest date; same date: closed wins)."""
    assert note.date is not None
    if note.date > old.last_seen or note.path == old.source:
        return True
    if note.date == old.last_seen:  # another note with the same date
        return not (old.status in _CLOSED and task.status not in _CLOSED)
    return False  # an older note never changes the status


def _merge(
    old: TaskRecord | None,
    task: Task,
    note: DailyNote,
    project: str | None,
    mapped_by: MappedBy | None,
    people: tuple[str, ...],
) -> TaskRecord:
    assert note.date is not None
    seen = TaskRecord(
        text=task.text,
        status=task.status,
        project=project,
        mapped_by=mapped_by,
        people=people,
        tags=task.tags,
        first_seen=note.date,
        last_seen=note.date,
        source=note.path,
        line=task.line_no,
    )
    if old is None:
        return seen

    current = seen if _status_from_this_note(old, task, note) else old

    # Project: sticky once set (a user's "none" too); an explicit link in a note at
    # least as new overrides.
    if (mapped_by == "link" and note.date >= old.last_seen) or (
        old.mapped_by is None and mapped_by is not None
    ):
        mapping = {"project": project, "mapped_by": mapped_by}
    else:
        mapping = {"project": old.project, "mapped_by": old.mapped_by}

    return current.model_copy(
        update={"first_seen": min(old.first_seen, note.date), **mapping}
    )


def update_task_index(
    index: TaskIndex,
    note: DailyNote,
    new_ids: Mapping[int, str],
    entities: EntityIndex,
    chosen: Chosen | None = None,
) -> tuple[TaskIndex, list[str]]:
    """Fold one note's tasks into the index. `new_ids` maps line → ID from `assign_ids`.

    `chosen` (line → (project or None, "model" | "user")) maps tasks rules 1–3 left
    open: the model's proposals and the user's review answers (decision 0007). It
    never overrides a rule or a mapping already in the index.

    Returns the new index and warnings. A note without a date changes nothing.
    """
    if note.date is None:
        return index, [f"{note.path}: no date, so its tasks are not tracked"]

    tasks = dict(index.tasks)
    warnings: list[str] = []
    project_of: dict[tuple[SectionKind | None, str], str] = {}  # this note's tasks
    for task in note.tasks:
        task_id = task.id or new_ids.get(task.line_no)
        if task_id is None:
            continue
        inherited = project_of.get((task.section, task.parent or ""))
        project, mapped_by = map_project(task, entities, inherited)
        if project is not None:
            project_of[(task.section, task.text)] = project
        elif chosen and task.line_no in chosen:
            project, mapped_by = chosen[task.line_no]
        people, ambiguous = task_people(task, entities)
        warnings += [
            f"{note.path}:{task.line_no}: {name!r} names more than one person; not linked"
            for name in ambiguous
        ]
        tasks[task_id] = _merge(
            tasks.get(task_id), task, note, project, mapped_by, people
        )
    return index.model_copy(update={"tasks": tasks}), warnings


def open_tasks(
    index: TaskIndex,
    note: DailyNote,
    new_ids: Mapping[int, str],
    entities: EntityIndex,
) -> dict[int, tuple[str, ...]]:
    """Return line → rule-3 candidates for the note's tasks with no mapping yet.

    `index` already holds this note (rules 1–3 applied). A task is open when nothing
    mapped it, not even a user's "none"; its candidates are the projects whose aliases
    it names when there are two or more, else () (any project).
    """
    out: dict[int, tuple[str, ...]] = {}
    for task in note.tasks:
        record = index.tasks.get(task.id or new_ids.get(task.line_no, ""))
        if record is not None and record.mapped_by is None:
            out[task.line_no] = alias_candidates(task, entities)
    return out
