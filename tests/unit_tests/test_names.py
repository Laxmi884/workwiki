"""The name scan: which lines are sent, and what code keeps from the answer."""

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.domain.facts import NameScan, PersonMention, ProjectGroup
from llm_wiki.entity_index import build_index
from llm_wiki.names import build_messages, check_scan, scan_lines, scan_names

from .scripted_model import BySection, ScriptedModel

NOTE = parse_daily_note(
    "00_raw/daily/2026-10-14.md",
    "---\ndate: 2026-10-14\n---\n"  # lines 1-3
    "## Tasks\n"  # 4
    "- [ ] Report changes for Eugene\n"  # 5
    "- [ ] Validate loss history workflow\n"  # 6
    "- [x] Loss history macro\n"  # 7
    "## Meetings\n"  # 8
    "### 1:1 with Joanna\n"  # 9
    "- AI use cases\n"  # 10
    "- [ ] Give heat maps to Joanna and Sam\n",  # 11
)
ENTITIES = build_index(
    [("20_people/person-sam-ortiz.md", "---\ntitle: Sam Ortiz\naliases: [Sam]\n---\n")]
)
LINES = scan_lines(NOTE)
UNMAPPED = {5, 6, 7, 11}


def test_scan_lines_are_tasks_and_meeting_headings():
    assert [n for n, _ in LINES] == [5, 6, 7, 9, 11]  # not the prose bullet on 10


def test_prompt_marks_unlinked_tasks_and_lists_known_people():
    (_, human) = build_messages(LINES, {6}, ENTITIES)
    assert "- person-sam-ortiz: Sam Ortiz (also: Sam)" in human.content
    assert "   6 * - [ ] Validate loss history workflow" in human.content
    assert "   5 | - [ ] Report changes for Eugene" in human.content


def test_check_keeps_only_written_unknown_names_and_unlinked_tasks():
    scan = NameScan(
        people=[
            PersonMention(name="Joanna", lines=[11]),
            PersonMention(name="joanna", lines=[9]),  # same name: merged
            PersonMention(name="Eugene", lines=[5]),
            PersonMention(name="Sam", lines=[11]),  # has a page
            PersonMention(name="Priya", lines=[11]),  # not in the line
            PersonMention(name="Gitlab", lines=[5]),  # not in the line either
        ],
        projects=[
            ProjectGroup(name="Loss History", lines=[6, 7, 9]),  # 9 is a heading
            ProjectGroup(name="Ghost", lines=[42]),
        ],
    )
    checked, dropped = check_scan(scan, LINES, UNMAPPED, ENTITIES, ignore=["eugene"])
    assert [(p.name, p.lines) for p in checked.people] == [("Joanna", [9, 11])]
    assert [(g.name, g.lines) for g in checked.projects] == [("Loss History", [6, 7])]
    assert dropped == [
        "person 'Priya': not written in lines [11]",
        "person 'Gitlab': not written in lines [5]",
        "project 'Ghost': no unlinked task lines",
    ]


def test_scan_names_uses_the_model_once_and_checks_the_answer():
    model = BySection(
        {}, NameScan(), names=NameScan(people=[PersonMention(name="Eugene", lines=[5])])
    )
    scan, warnings = scan_names(model, NOTE, {5: None, 6: None}, ENTITIES)
    assert [p.name for p in scan.people] == ["Eugene"] and warnings == []
    assert len(model.name_calls) == 1


def test_invalid_scan_output_gives_an_empty_scan_and_a_warning():
    scan, warnings = scan_names(
        ScriptedModel(ValueError("bad json")), NOTE, {}, ENTITIES
    )
    assert scan == NameScan()
    assert warnings == ["name scan: invalid output (bad json)"]
