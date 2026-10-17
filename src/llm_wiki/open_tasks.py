"""The open-tasks page (decision 0012): one code-written view of the task index.

Pure. `track_tasks` proposes the page's whole text with the task index on every ingest,
so it is approved and committed like any other file. The text depends only on the index
and page titles (no clock): an unchanged index gives the same page, hence no diff.

    ### [[project-delq|delq analysis]]
    - Fix the delq report · since 2026-10-14 · [[2026-10-14#^t-aaaaaa|note]]

Each task links to its line in the daily note by block ID (decision 0001). Person and
project pages link here back through Obsidian's backlinks.
"""

from __future__ import annotations

from collections.abc import Iterable

from llm_wiki.domain.changes import OPEN_TASKS_PATH
from llm_wiki.domain.task_index import TaskIndex, TaskRecord
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.entity_index import EntityIndex

__all__ = ["OPEN_TASKS_PATH", "render_open_tasks"]

_HEADER = (
    "# Open tasks\n\n"
    "_Written by code from the task index (`90_meta/tasks.json`) on every ingest; "
    "edits here are overwritten. Tick tasks in your daily notes._\n"
)
_OPEN = (TaskStatus.OPEN, TaskStatus.IN_PROGRESS)


def _line(task_id: str, task: TaskRecord) -> str:
    stem = task.source.rsplit("/", 1)[-1].removesuffix(".md")
    doing = " (in progress)" if task.status is TaskStatus.IN_PROGRESS else ""
    tags = "".join(f" #{t}" for t in task.tags)
    return (
        f"- {task.text}{doing}{tags} · since {task.first_seen.isoformat()} · "
        f"[[{stem}#^{task_id}|note]]"
    )


def _title(slug: str, entities: EntityIndex) -> str:
    entity = entities.get(slug)
    return entity.title if entity else slug


def _groups(
    keyed: Iterable[tuple[str, str, TaskRecord]], entities: EntityIndex
) -> list[str]:
    """Return "### <page>" blocks for (slug, task id, task), pages by title."""
    by_slug: dict[str, list[tuple[str, TaskRecord]]] = {}
    for slug, task_id, task in keyed:
        by_slug.setdefault(slug, []).append((task_id, task))
    ordered = sorted(by_slug.items(), key=lambda kv: _title(kv[0], entities).casefold())
    return [
        f"### [[{slug}|{_title(slug, entities)}]]\n"
        + "".join(f"{_line(*t)}\n" for t in tasks)
        for slug, tasks in ordered
    ]


def render_open_tasks(index: TaskIndex, entities: EntityIndex) -> str:
    """Return the page: open and in-progress tasks by project, then by person.

    Tasks are in the order they were first seen (then note and line); tasks with no
    project close the project part under "No project".
    """
    open_tasks = sorted(
        ((tid, t) for tid, t in index.tasks.items() if t.status in _OPEN),
        key=lambda item: (item[1].first_seen, item[1].source, item[1].line),
    )
    if not open_tasks:
        return f"{_HEADER}\n_No open tasks._\n"
    blocks = _groups(
        ((t.project, tid, t) for tid, t in open_tasks if t.project), entities
    )
    loose = [_line(tid, t) for tid, t in open_tasks if not t.project]
    if loose:
        blocks.append("### No project\n" + "".join(f"{line}\n" for line in loose))
    people = _groups(
        ((slug, tid, t) for tid, t in open_tasks for slug in t.people), entities
    )
    out = f"{_HEADER}\n## By project\n\n" + "\n".join(blocks)
    if people:
        out += "\n## By person\n\n" + "\n".join(people)
    return out
