import datetime

import pytest

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.entity_index import build_index
from llm_wiki.task_tracking import (
    TASK_INDEX_PATH,
    assign_ids,
    dump_task_index,
    load_task_index,
    map_project,
    update_task_index,
)

OPEN, DONE, CANCELLED = TaskStatus.OPEN, TaskStatus.DONE, TaskStatus.CANCELLED

ATLAS_PAGE = (
    "10_projects/project-atlas.md",
    "---\naliases: [Atlas, atlas beta]\n---\n",
)
ENTITIES = build_index([ATLAS_PAGE])


def _note(day, body, folder="00_raw/daily"):
    return parse_daily_note(f"{folder}/{day}.md", f"---\ndate: {day}\n---\n{body}")


# --- assign_ids --------------------------------------------------------------------


def test_assign_ids_only_for_tasks_without_one():
    note = _note("2026-10-12", "## Tasks\n- [ ] a\n- [ ] b ^t-aaaaaa\n- [ ] c\n")
    ids, edits = assign_ids(note, existing=set())
    assert sorted(ids) == [5, 7]  # file lines of "a" and "c"
    assert len(set(ids.values())) == 2
    assert [(e.path, e.line_no, e.task_id) for e in edits] == [
        (note.path, 5, ids[5]),
        (note.path, 7, ids[7]),
    ]


def test_assign_ids_is_repeatable_for_the_same_note():
    note = _note("2026-10-12", "## Tasks\n- [ ] a\n- [ ] b\n")
    assert assign_ids(note, set())[0] == assign_ids(note, set())[0]


def test_assign_ids_avoids_existing_ids():
    note = _note("2026-10-12", "## Tasks\n- [ ] a\n")
    first = assign_ids(note, set())[0][5]
    again = assign_ids(note, existing={first})[0][5]
    assert again != first


# --- map_project -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("## Tasks\n- [ ] Ship [[project-atlas]]\n", ("project-atlas", "link")),
        ("## Tasks\n- [ ] Ship [[project-zed]]\n", ("project-zed", "link")),  # no page
        ("## Tasks\n- [ ] Ping [[person-sam]]\n", (None, None)),  # not a project
        (
            "## Tasks\n- Plan [[project-atlas]]\n    - [ ] sub\n",
            ("project-atlas", "context"),
        ),
        (
            "## Meetings\n### Sync on [[project-atlas]]\n- [ ] follow up\n",
            ("project-atlas", "context"),
        ),
        ("## Tasks\n- [ ] fix the Atlas beta export\n", ("project-atlas", "alias")),
        ("## Tasks\n- [ ] read the Atlassian docs\n", (None, None)),
    ],
)
def test_map_project_rules(body, expected):
    task = _note("2026-10-12", body).tasks[0]
    assert map_project(task, ENTITIES) == expected


def test_link_on_the_task_beats_context():
    body = "## Meetings\n### Sync on [[project-atlas]]\n- [ ] ship [[project-zed]]\n"
    task = _note("2026-10-12", body).tasks[0]
    assert map_project(task, ENTITIES) == ("project-zed", "link")


def test_two_alias_matches_are_ambiguous():
    entities = build_index(
        [ATLAS_PAGE, ("10_projects/project-orbit.md", "---\naliases: [beta]\n---\n")]
    )
    task = _note("2026-10-12", "## Tasks\n- [ ] fix the atlas beta export\n").tasks[0]
    assert map_project(task, entities) == (None, None)


# --- update_task_index -------------------------------------------------------------


def _index_after(*notes):
    index = load_task_index(None)[0]
    for note in notes:
        index, _ = update_task_index(index, note, {}, ENTITIES)
    return index


def test_child_task_inherits_its_parent_tasks_project():
    note = _note(
        "2026-10-12",
        "## Tasks\n- [ ] Ship [[project-atlas]] ^t-aaaaaa\n"
        "    - [ ] Write notes ^t-bbbbbb\n"
        "        - [ ] Add screenshots ^t-cccccc\n",
    )
    tasks = _index_after(note).tasks
    assert [(tasks[i].project, tasks[i].mapped_by) for i in sorted(tasks)] == [
        ("project-atlas", "link"),
        ("project-atlas", "context"),
        ("project-atlas", "context"),  # grandchild, through its parent task
    ]


def _one(day, status_char, text="Review budget", folder="00_raw/daily"):
    return _note(day, f"## Tasks\n- [{status_char}] {text} ^t-q7m2xa\n", folder)


def _record(*notes):
    return _index_after(*notes).tasks["t-q7m2xa"]


def test_newer_note_sets_status_and_first_seen_stays():
    record = _record(_one("2026-10-12", " "), _one("2026-10-13", "x"))
    assert (record.status, record.first_seen, record.last_seen) == (
        DONE,
        datetime.date(2026, 10, 12),
        datetime.date(2026, 10, 13),
    )
    assert record.source == "00_raw/daily/2026-10-13.md"


def test_older_note_never_reverts_status():
    record = _record(_one("2026-10-13", "x"), _one("2026-10-12", " "))
    assert (record.status, record.first_seen, record.last_seen) == (
        DONE,
        datetime.date(2026, 10, 12),  # first_seen still moves back
        datetime.date(2026, 10, 13),
    )


def test_reopened_in_a_later_note():
    assert _record(_one("2026-10-12", "x"), _one("2026-10-13", " ")).status is OPEN


@pytest.mark.parametrize(("first", "second"), [("x", " "), (" ", "x"), ("-", " ")])
def test_same_date_other_note_closed_wins(first, second):
    other = _one("2026-10-12", second, folder="00_raw/old")
    assert _record(_one("2026-10-12", first), other).status in {DONE, CANCELLED}


def test_reingesting_the_same_note_takes_its_new_content():
    record = _record(_one("2026-10-12", "x"), _one("2026-10-12", " "))
    assert record.status is OPEN  # same note, edited: its latest content counts


def test_project_is_sticky_but_a_later_link_overrides():
    first = _one("2026-10-12", " ", "Fix the atlas beta export")  # alias → atlas
    reworded = _one("2026-10-13", " ", "Fix the export")  # no rule matches
    relinked = _one("2026-10-14", " ", "Fix the export for [[project-zed]]")
    sticky = _record(first, reworded)
    assert (sticky.project, sticky.mapped_by) == ("project-atlas", "alias")
    overridden = _record(first, reworded, relinked)
    assert (overridden.project, overridden.mapped_by) == ("project-zed", "link")


def test_people_and_tags_are_recorded_for_unmapped_tasks():
    text = "Book 1:1 with [[person-sam-ortiz]] about [[concept-flags]] #blocked"
    record = _record(_one("2026-10-12", " ", text))
    assert record.project is None
    assert (record.people, record.tags) == (("person-sam-ortiz",), ("blocked",))


PEOPLE = build_index(
    [
        ATLAS_PAGE,
        (
            "20_people/person-joanna-reed.md",
            "---\ntitle: Joanna Reed\naliases: [Joanna]\n---\n",
        ),
        ("20_people/person-eugene-park.md", "---\naliases: [Eugene]\n---\n"),
        ("20_people/person-sam-ortiz.md", "---\naliases: [Sam]\n---\n"),
        ("20_people/person-sam-lee.md", "---\naliases: [Sam]\n---\n"),
    ]
)


def test_people_from_names_parent_and_meeting_heading():
    note = _note(
        "2026-10-14",
        "## Tasks\n"
        "- [ ] Report changes for Eugene\n"
        "## Meetings\n"
        "### 1:1 with Joanna\n"
        "- AI use cases with [[person-sam-ortiz]]\n"
        "    - [ ] Write the use-case doc\n"
        "- [ ] Give heat maps to Eugene\n",
    )
    ids = assign_ids(note, set())[0]
    index, warnings = update_task_index(load_task_index(None)[0], note, ids, PEOPLE)
    people = {r.text: r.people for r in index.tasks.values()}
    assert people == {
        "Report changes for Eugene": ("person-eugene-park",),
        "Write the use-case doc": ("person-sam-ortiz", "person-joanna-reed"),
        "Give heat maps to Eugene": ("person-eugene-park", "person-joanna-reed"),
    }
    assert warnings == []


def test_link_comes_first_and_a_shared_name_is_warned_not_linked():
    note = _note("2026-10-14", "## Tasks\n- [ ] Ask Sam and [[person-joanna-reed]]\n")
    ids = assign_ids(note, set())[0]
    index, warnings = update_task_index(load_task_index(None)[0], note, ids, PEOPLE)
    (record,) = index.tasks.values()
    assert record.people == ("person-joanna-reed",)
    assert warnings == [f"{note.path}:5: 'sam' names more than one person; not linked"]


def test_people_and_tags_follow_the_note_that_sets_the_status():
    blocked = _one("2026-10-12", " ", "Ping [[person-sam-ortiz]] #blocked")
    unblocked = _one("2026-10-13", " ", "Ping [[person-sam-ortiz]]")
    assert _record(blocked, unblocked).tags == ()  # newer note dropped the tag
    assert _record(unblocked, blocked).tags == ()  # an older note can't bring it back


def test_note_without_date_changes_nothing():
    note = parse_daily_note("00_raw/daily/scratch.md", "## Tasks\n- [ ] a ^t-aaaaaa\n")
    index, warnings = update_task_index(load_task_index(None)[0], note, {}, ENTITIES)
    assert index.tasks == {}
    assert "no date" in warnings[0]


def test_new_ids_from_assign_ids_are_used():
    note = _note("2026-10-12", "## Tasks\n- [ ] a\n")
    ids, _ = assign_ids(note, set())
    index, _ = update_task_index(load_task_index(None)[0], note, ids, ENTITIES)
    assert list(index.tasks) == [ids[5]]


# --- load / dump -------------------------------------------------------------------


def test_dump_is_deterministic_and_round_trips():
    body = "## Tasks\n- [ ] b ^t-bbbbbb\n- [ ] a ^t-aaaaaa\n"
    index = _index_after(_note("2026-10-12", body))
    text = dump_task_index(index)
    assert text.index('"t-aaaaaa"') < text.index('"t-bbbbbb"')  # sorted by ID
    assert text.endswith("}\n")
    assert '"first_seen": "2026-10-12"' in text
    assert load_task_index(text) == (index, None)


@pytest.mark.parametrize("text", ["{not json", '{"version": 2, "tasks": {}}'])
def test_broken_index_is_reported_not_replaced(text):
    index, problem = load_task_index(text)
    assert index is None
    assert TASK_INDEX_PATH in problem and "not tracked" in problem
