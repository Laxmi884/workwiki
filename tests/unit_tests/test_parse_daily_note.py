import datetime

import pytest

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.domain.common import SectionKind
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.tasks_syntax import parse_task_line

PATH = "00_raw/daily/2026-10-12.md"

NOTE = """\
---
type: daily
template: daily-v1
date: 2026-10-12
---

## Tasks
- [ ] Email Alex about Q3 ^t-k3x9q2
- [x] Big launch
    - [ ] Write release notes
        - [/] Draft changelog
    - [ ] Ping design
- [ ] Top level again

## Meetings
### Roadmap sync with [[project-atlas]]
- Discussed launch timing
\t- [ ] Send slides to Alex
- [ ] Book follow-up
### 1:1 with [[person-sam]]
- [ ] Review Sam's PR

## Log
```
- [ ] not a task, inside a code block
```
Some prose
- [-] Old idea
"""

ROADMAP = "Roadmap sync with [[project-atlas]]"
OPEN, DONE, WIP, CANCELLED = (
    TaskStatus.OPEN,
    TaskStatus.DONE,
    TaskStatus.IN_PROGRESS,
    TaskStatus.CANCELLED,
)
TASKS, MEETINGS, LOG = SectionKind.TASKS, SectionKind.MEETINGS, SectionKind.LOG


def test_tasks_with_context():
    note = parse_daily_note(PATH, NOTE)
    got = [
        (t.line_no, t.text, t.id, t.status, t.section, t.subheading, t.parent)
        for t in note.tasks
    ]
    assert got == [
        (8, "Email Alex about Q3", "t-k3x9q2", OPEN, TASKS, None, None),
        (9, "Big launch", None, DONE, TASKS, None, None),
        (10, "Write release notes", None, OPEN, TASKS, None, "Big launch"),
        (11, "Draft changelog", None, WIP, TASKS, None, "Write release notes"),
        (12, "Ping design", None, OPEN, TASKS, None, "Big launch"),
        (13, "Top level again", None, OPEN, TASKS, None, None),
        (
            18,
            "Send slides to Alex",
            None,
            OPEN,
            MEETINGS,
            ROADMAP,
            "Discussed launch timing",
        ),
        (19, "Book follow-up", None, OPEN, MEETINGS, ROADMAP, None),
        (21, "Review Sam's PR", None, OPEN, MEETINGS, "1:1 with [[person-sam]]", None),
        (28, "Old idea", None, CANCELLED, LOG, None, None),
    ]
    assert all(t.source_path == PATH for t in note.tasks)


def test_task_line_numbers_point_at_the_task():
    note = parse_daily_note(PATH, NOTE)
    file_lines = NOTE.splitlines()
    for task in note.tasks:
        parsed = parse_task_line(file_lines[task.line_no - 1])
        assert parsed is not None
        assert (parsed.text, parsed.task_id) == (task.text, task.id)


def test_note_fields():
    note = parse_daily_note(PATH, NOTE)
    assert note.path == PATH
    assert note.date == datetime.date(2026, 10, 12)
    assert note.template == "daily-v1"
    assert [s.kind for s in note.sections] == [TASKS, MEETINGS, LOG]
    assert note.warnings == ()


def test_tasks_carry_links_and_tags_from_their_own_line():
    text = (
        "## Meetings\n"
        "### Sync with [[person-sam]]\n"
        "- [ ] Get sign-off from [[person-alex|Alex]] #blocked ^t-k3x9q2\n"
        "- [ ] Plain follow-up\n"
    )
    first, second = parse_daily_note(PATH, text).tasks
    assert (first.links, first.tags, first.id) == (
        ("person-alex",),
        ("blocked",),
        "t-k3x9q2",
    )
    # The ### heading's link stays in `subheading`; it is not copied to the task.
    assert (second.links, second.tags) == ((), ())


def test_subheading_clears_parent():
    note = parse_daily_note(
        PATH, "## Meetings\n- Old topic\n### New meeting\n    - [ ] a\n"
    )
    assert [(t.subheading, t.parent) for t in note.tasks] == [("New meeting", None)]


def test_new_section_clears_subheading_and_parent():
    text = "## Meetings\n### Sync\n- Topic\n## Tasks\n    - [ ] a\n"
    task = parse_daily_note(PATH, text).tasks[0]
    assert (task.section, task.subheading, task.parent) == (TASKS, None, None)


def test_empty_template_checkbox_is_not_a_task():
    assert parse_daily_note(PATH, "## Tasks\n- [ ]\n").tasks == ()


def test_no_frontmatter_uses_filename_date_and_default_template():
    note = parse_daily_note(PATH, "## Tasks\n- [ ] a\n")
    assert (note.date, note.template, note.warnings) == (
        datetime.date(2026, 10, 12),
        "daily-v1",
        (),
    )
    assert note.tasks[0].line_no == 2


@pytest.mark.parametrize(
    "front_date",
    ['"2026-10-12"', "2026-10-12 09:30:00"],  # quoted string, YAML datetime
)
def test_frontmatter_date_forms(front_date):
    note = parse_daily_note(PATH, f"---\ndate: {front_date}\n---\n")
    assert note.date == datetime.date(2026, 10, 12)
    assert note.warnings == ()


def test_date_drift_keeps_frontmatter_date_and_warns():
    note = parse_daily_note(PATH, "---\ndate: 2026-10-11\n---\n")
    assert note.date == datetime.date(2026, 10, 11)
    assert len(note.warnings) == 1 and "date drift" in note.warnings[0]


def test_no_date_anywhere_is_none_with_warning():
    note = parse_daily_note("00_raw/daily/scratch.md", "---\ndate: someday\n---\n")
    assert note.date is None
    assert any("not a date" in w for w in note.warnings)
    assert any("no date" in w for w in note.warnings)


def test_unknown_template_warns_but_parses():
    note = parse_daily_note(PATH, "---\ntemplate: daily-v7\n---\n## Tasks\n- [ ] a\n")
    assert note.template == "daily-v7"
    assert any("unknown template" in w for w in note.warnings)
    assert len(note.tasks) == 1


def test_broken_frontmatter_still_parses_with_right_line_numbers():
    text = "---\ndate: {{date:YYYY-MM-DD}}\n---\n## Tasks\n- [ ] a\n"
    note = parse_daily_note(PATH, text)
    assert note.date == datetime.date(2026, 10, 12)  # from the filename
    assert any("invalid YAML" in w for w in note.warnings)
    assert note.tasks[0].line_no == 5
