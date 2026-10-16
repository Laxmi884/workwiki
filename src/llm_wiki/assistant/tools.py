"""Code tools for the assistant: deterministic answers from code-owned data (0008).

The model never parses `90_meta/tasks.json`: `find_tasks` filters the task index and
returns one plain line per task, with the ID, status, project, people and where the
task was last seen, so answers about tasks can cite exact facts.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Literal

from langchain.tools import tool
from langchain_core.tools import BaseTool

from llm_wiki.domain.task_index import TaskIndex, TaskRecord
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.task_tracking import TASK_INDEX_PATH, load_task_index

StatusFilter = Literal["open", "done", "cancelled", "all"]
MAX_TASKS = 60

_OPEN = frozenset({TaskStatus.OPEN, TaskStatus.IN_PROGRESS})
_BOX = {
    TaskStatus.OPEN: "[ ]",
    TaskStatus.IN_PROGRESS: "[/]",
    TaskStatus.DONE: "[x]",
    TaskStatus.CANCELLED: "[-]",
}


def _wanted(record: TaskRecord, status: StatusFilter) -> bool:
    if status == "all":
        return True
    if status == "open":
        return record.status in _OPEN
    return record.status.value == status


def task_lines(
    index: TaskIndex,
    *,
    project: str | None = None,
    person: str | None = None,
    status: StatusFilter = "open",
    since: datetime.date | None = None,
) -> list[str]:
    """Return one line per matching task, oldest first.

    `project="none"` selects tasks with no project. `since` keeps tasks last seen on or
    after that date.
    """
    rows = []
    for task_id, r in index.tasks.items():
        if not _wanted(r, status):
            continue
        if project == "none" and r.project is not None:
            continue
        if project not in (None, "none") and r.project != project:
            continue
        if person is not None and person not in r.people:
            continue
        if since is not None and r.last_seen < since:
            continue
        rows.append((r.first_seen, task_id, r))
    lines = []
    for _, task_id, r in sorted(rows, key=lambda row: (row[0], row[1])):
        stem = r.source.rsplit("/", 1)[-1].removesuffix(".md")
        bits = [task_id, r.project or "no project"]
        bits += list(r.people)
        bits.append(f"since {r.first_seen}")
        if r.last_seen != r.first_seen:
            bits.append(f"last seen {r.last_seen}")
        if r.tags:
            bits.append(" ".join(f"#{t}" for t in r.tags))
        lines.append(
            f"- {_BOX[r.status]} {r.text} ({'; '.join(bits)}; [[{stem}]] line {r.line})"
        )
    return lines


def make_find_tasks(read: Callable[[str], str | None]) -> BaseTool:
    """Return the `find_tasks` tool over the vault's task index (read through `read`)."""

    @tool
    def find_tasks(
        project: str | None = None,
        person: str | None = None,
        status: StatusFilter = "open",
        since: str | None = None,
    ) -> str:
        """List tracked tasks from the task index (code-owned, exact).

        Use it for any question about tasks: what is open, what was done, what is
        blocked, what is on a project or with a person.

        Args:
            project: A project slug (e.g. "project-atlas"), or "none" for tasks with no
                project. Omit for all.
            person: A person slug (e.g. "person-sam-ortiz"). Omit for all.
            status: "open" (open or in progress), "done", "cancelled" or "all".
            since: "YYYY-MM-DD": only tasks last seen on or after this date.
        """
        index, problem = load_task_index(read(TASK_INDEX_PATH))
        if index is None:
            return f"The task index can't be read: {problem}"
        try:
            day = datetime.date.fromisoformat(since) if since else None
        except ValueError:
            return f"since must be YYYY-MM-DD, not {since!r}"
        lines = task_lines(
            index, project=project, person=person, status=status, since=day
        )
        if not lines:
            return "No tasks match."
        more = len(lines) - MAX_TASKS
        shown = lines[:MAX_TASKS]
        tail = [f"... and {more} more; narrow the filters"] if more > 0 else []
        return "\n".join([f"{len(lines)} tasks:", *shown, *tail])

    return find_tasks
