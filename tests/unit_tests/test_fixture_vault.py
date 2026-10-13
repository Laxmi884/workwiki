"""The parser and entity index run end to end on the fixture vault (tests/fixtures/vault)."""

import datetime

import pytest

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.domain.common import SectionKind
from llm_wiki.domain.entities import EntityKind
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.entity_index import load_index
from llm_wiki.tasks_syntax import parse_task_line

OPEN, DONE, WIP, CANCELLED = (
    TaskStatus.OPEN,
    TaskStatus.DONE,
    TaskStatus.IN_PROGRESS,
    TaskStatus.CANCELLED,
)
CARRIED, TASKS, MEETINGS, LOG = (
    SectionKind.CARRIED_OVER,
    SectionKind.TASKS,
    SectionKind.MEETINGS,
    SectionKind.LOG,
)
ATLAS = ("project-atlas",)


def _note(vault, rel):
    return parse_daily_note(rel, (vault / rel).read_text(encoding="utf-8"))


def _daily_paths(vault):
    return sorted(
        p.relative_to(vault).as_posix() for p in (vault / "00_raw/daily").glob("*.md")
    )


# --- entity index --------------------------------------------------------------


def test_index_has_every_entity_page_and_warns_about_the_stray_one(fixture_vault):
    index = load_index(fixture_vault)
    assert list(index.entities) == [
        "project-atlas",
        "concept-feature-flags",
        "person-alex-chen",
        "person-priya-nair",
        "person-sam-ortiz",
    ]
    assert len(index.warnings) == 1
    assert "Meeting with Alex.md: file name is not an entity slug" in index.warnings[0]


def test_only_jordan_has_no_page_yet(fixture_vault):
    """Links to missing pages are what phase 2 proposes as new entities."""
    index = load_index(fixture_vault)
    broken = {
        link
        for rel in _daily_paths(fixture_vault)
        for task in _note(fixture_vault, rel).tasks
        for link in task.links
        if index.get(link) is None
    }
    assert broken == {"person-jordan-lee"}


# --- daily notes ---------------------------------------------------------------


def test_clean_note(fixture_vault):
    note = _note(fixture_vault, "00_raw/daily/2026-10-12.md")
    assert (note.date, note.warnings) == (datetime.date(2026, 10, 12), ())
    got = [(t.line_no, t.status, t.section, t.id, t.links, t.tags) for t in note.tasks]
    assert got == [
        (8, OPEN, CARRIED, "t-q7m2xa", ATLAS, ()),
        (9, DONE, CARRIED, "t-b4k9e1", (), ()),
        (12, OPEN, TASKS, None, ATLAS, ()),
        (13, DONE, TASKS, None, (), ()),
        (14, WIP, TASKS, None, (), ()),
        (15, OPEN, TASKS, None, (), ()),
        (16, OPEN, TASKS, None, ("person-alex-chen",), ("blocked",)),
        (17, OPEN, TASKS, None, ("person-sam-ortiz",), ()),
        (18, CANCELLED, TASKS, None, (), ()),
        (25, OPEN, MEETINGS, None, ATLAS, ()),
        (26, OPEN, MEETINGS, None, (), ()),
        (30, OPEN, MEETINGS, None, (), ()),
    ]


def test_messy_note_still_parses(fixture_vault):
    note = _note(fixture_vault, "00_raw/daily/2026-10-13.md")
    assert note.date == datetime.date(2026, 10, 14)  # frontmatter wins
    assert len(note.warnings) == 1 and "date drift" in note.warnings[0]
    assert [s.kind for s in note.sections] == [CARRIED, TASKS, LOG]  # "## tasks"
    got = [(t.line_no, t.status, t.section, t.id, t.parent) for t in note.tasks]
    assert got == [
        (8, DONE, CARRIED, "t-q7m2xa", None),  # was open on 2026-10-12
        (9, WIP, CARRIED, "t-r8n3wd", None),
        (12, OPEN, TASKS, None, None),
        (13, OPEN, TASKS, None, None),  # "- [ ]" on line 14 is not a task
        (16, OPEN, TASKS, None, "Follow-ups from standup"),  # tab indent
        (19, OPEN, LOG, None, None),  # unknown heading → Log
    ]


def test_alias_without_link_maps_by_rule_3(fixture_vault):
    index = load_index(fixture_vault)
    task = _note(fixture_vault, "00_raw/daily/2026-10-13.md").tasks[3]
    assert (task.text, task.links) == ("Fix the atlas beta export bug #blocked", ())
    found = index.alias_mentions(task.text, EntityKind.PROJECT)
    assert [e.slug for e in found] == ["project-atlas"]


def test_template_has_no_tasks(fixture_vault):
    note = _note(fixture_vault, "50_templates/daily.md")
    assert note.tasks == ()
    assert any("invalid YAML" in w for w in note.warnings)  # unrendered {{date}}


@pytest.mark.parametrize(
    "rel", ["00_raw/daily/2026-10-12.md", "00_raw/daily/2026-10-13.md"]
)
def test_line_numbers_point_at_the_task(fixture_vault, rel):
    lines = (fixture_vault / rel).read_text(encoding="utf-8").splitlines()
    for task in _note(fixture_vault, rel).tasks:
        parsed = parse_task_line(lines[task.line_no - 1])
        assert parsed is not None
        assert (parsed.text, parsed.task_id) == (task.text, task.id)
