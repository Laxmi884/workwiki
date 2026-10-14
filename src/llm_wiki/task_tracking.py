"""Task IDs, task → project mapping, and the task index (decisions 0001, 0002, 0004).

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


# --- project mapping (decision 0002, rules 1–3) ----------------------------------


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
) -> TaskRecord:
    assert note.date is not None
    seen = TaskRecord(
        text=task.text,
        status=task.status,
        project=project,
        mapped_by=mapped_by,
        people=tuple(s for s in task.links if kind_of(s) is EntityKind.PERSON),
        tags=task.tags,
        first_seen=note.date,
        last_seen=note.date,
        source=note.path,
        line=task.line_no,
    )
    if old is None:
        return seen

    current = seen if _status_from_this_note(old, task, note) else old

    # Project: sticky once set; an explicit link in a note at least as new overrides.
    if (mapped_by == "link" and note.date >= old.last_seen) or (
        old.project is None and project is not None
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
) -> tuple[TaskIndex, list[str]]:
    """Fold one note's tasks into the index. `new_ids` maps line → ID from `assign_ids`.

    Returns the new index and warnings. A note without a date changes nothing.
    """
    if note.date is None:
        return index, [f"{note.path}: no date, so its tasks are not tracked"]

    tasks = dict(index.tasks)
    project_of: dict[tuple[SectionKind | None, str], str] = {}  # this note's tasks
    for task in note.tasks:
        task_id = task.id or new_ids.get(task.line_no)
        if task_id is None:
            continue
        inherited = project_of.get((task.section, task.parent or ""))
        project, mapped_by = map_project(task, entities, inherited)
        if project is not None:
            project_of[(task.section, task.text)] = project
        tasks[task_id] = _merge(tasks.get(task_id), task, note, project, mapped_by)
    return index.model_copy(update={"tasks": tasks}), []
