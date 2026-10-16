"""Origins, quarterly paths and picked wins on the fixture vault (pure: no writes)."""

import datetime

import pytest

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.diffs import render_diffs, vault_reader
from llm_wiki.domain.changes import AppendToSection, CreateFile
from llm_wiki.domain.task_index import DoneTask
from llm_wiki.routing import decision_log_path, impact_path, origin_of, win_edits

NOTE = "00_raw/daily/2026-10-12.md"
ATLAS = "10_projects/project-atlas.md"
IMPACT = "40_impact/2026/impact-2026-q4.md"


@pytest.fixture(scope="module")
def note(fixture_vault):
    return parse_daily_note(NOTE, (fixture_vault / NOTE).read_text())


@pytest.fixture(scope="module")
def exists(fixture_vault):
    read = vault_reader(fixture_vault)
    return lambda path: read(path) is not None


def _appends(edits, path):
    return [
        (e.heading, e.lines[0])
        for e in edits
        if isinstance(e, AppendToSection) and e.path == path
    ]


def test_origin_has_date_meeting_and_quote(note):
    o = origin_of(note, 23)
    assert o.date == datetime.date(2026, 10, 12)
    assert o.meeting == (
        "Atlas weekly sync with [[person-alex-chen]] and [[person-priya-nair]]"
    )
    assert o.quote == "Decided to keep the old BI tool until Q1 #decision"
    assert origin_of(note, 29).meeting == "1:1 with [[person-sam-ortiz]]"
    assert origin_of(note, 34).meeting is None  # Log has no meetings
    assert o.bullet("Kept.") == (
        "- 2026-10-12 · Atlas weekly sync with [[person-alex-chen]] and "
        "[[person-priya-nair]]: Kept. ([[2026-10-12]])"
    )


def test_quarterly_paths():
    day = datetime.date(2026, 10, 12)
    assert impact_path(day) == IMPACT
    assert decision_log_path(day) == "30_decisions/2026/decisions-2026-q4.md"
    assert impact_path(datetime.date(2027, 3, 31)) == "40_impact/2027/impact-2027-q1.md"


def test_picked_win_goes_to_impact_page_and_project(note, exists, fixture_vault):
    read = vault_reader(fixture_vault)
    wins = [
        DoneTask(
            line=13,
            task_id="t-aaaaaa",
            text="Fix login redirect bug",
            project="project-atlas",
        )
    ]
    edits = win_edits(note, wins, exists)
    assert isinstance(edits[0], CreateFile) and edits[0].path == IMPACT
    assert _appends(edits, IMPACT) == [
        (
            "## Wins",
            "- 2026-10-12: Fix login redirect bug ([[project-atlas]]) ([[2026-10-12]])",
        )
    ]
    assert _appends(edits, ATLAS) == [
        ("## Wins", "- 2026-10-12: Fix login redirect bug ([[2026-10-12]])")
    ]
    diffs, warnings = render_diffs(edits, read)
    assert warnings == [] and {d.path for d in diffs} == {IMPACT, ATLAS}


def test_win_without_a_project_page_stays_on_the_impact_page(note, exists):
    wins = [
        DoneTask(line=9, task_id="t-b4k9e1", text="Reply to Priya", project=None),
        DoneTask(line=13, task_id="t-c", text="Fix bug", project="project-gone"),
    ]
    edits = win_edits(note, wins, exists)
    assert {e.path for e in edits} == {IMPACT}
    assert win_edits(note, [], exists) == []
