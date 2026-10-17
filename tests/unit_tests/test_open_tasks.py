"""The open-tasks page (decision 0012): code renders it from the task index (pure)."""

import datetime

from llm_wiki.domain.task_index import TaskIndex, TaskRecord
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.entity_index import build_index
from llm_wiki.open_tasks import OPEN_TASKS_PATH, render_open_tasks

D14, D15 = datetime.date(2026, 10, 14), datetime.date(2026, 10, 15)


def _task(
    text, status=TaskStatus.OPEN, project=None, people=(), tags=(), day=D14, line=5
):
    return TaskRecord(
        text=text,
        status=status,
        project=project,
        people=people,
        tags=tags,
        first_seen=day,
        last_seen=day,
        source=f"00_raw/daily/{day.isoformat()}.md",
        line=line,
    )


INDEX = TaskIndex(
    tasks={
        "t-aaaaaa": _task(
            "Fix the delq report", project="project-delq", people=("person-eugene",)
        ),
        "t-bbbbbb": _task("Add jfrog CI", tags=("blocked",), line=9),
        "t-cccccc": _task(
            "Old done work", status=TaskStatus.DONE, project="project-delq"
        ),
        "t-dddddd": _task("Dropped idea", status=TaskStatus.CANCELLED),
        "t-eeeeee": _task(
            "Write the macro",
            status=TaskStatus.IN_PROGRESS,
            project="project-delq",
            day=D15,
            line=3,
        ),
    }
)
PAGES = build_index(
    [
        ("10_projects/project-delq.md", "---\ntitle: delq analysis\n---\n# delq\n"),
        ("20_people/person-eugene.md", "# Eugene\n"),
    ]
)


def test_open_and_in_progress_tasks_by_project_and_by_person():
    assert OPEN_TASKS_PATH == "05_generated/open-tasks.md"
    assert render_open_tasks(INDEX, PAGES) == (
        "# Open tasks\n"
        "\n"
        "_Written by code from the task index (`90_meta/tasks.json`) on every ingest; "
        "edits here are overwritten. Tick tasks in your daily notes._\n"
        "\n"
        "## By project\n"
        "\n"
        "### [[project-delq|delq analysis]]\n"
        "- Fix the delq report · since 2026-10-14 · [[2026-10-14#^t-aaaaaa|note]]\n"
        "- Write the macro (in progress) · since 2026-10-15 · "
        "[[2026-10-15#^t-eeeeee|note]]\n"
        "\n"
        "### No project\n"
        "- Add jfrog CI #blocked · since 2026-10-14 · [[2026-10-14#^t-bbbbbb|note]]\n"
        "\n"
        "## By person\n"
        "\n"
        "### [[person-eugene|Eugene]]\n"
        "- Fix the delq report · since 2026-10-14 · [[2026-10-14#^t-aaaaaa|note]]\n"
    )


def test_no_open_tasks():
    text = render_open_tasks(TaskIndex(), PAGES)
    assert text.endswith("_No open tasks._\n") and "## By project" not in text


def test_a_project_without_a_page_is_linked_by_its_slug():
    # e.g. a new project page the user left out in a review
    lone = TaskIndex(tasks={"t-ffffff": _task("Ping Zed", project="project-zed")})
    assert "### [[project-zed|project-zed]]\n" in render_open_tasks(lone, PAGES)
