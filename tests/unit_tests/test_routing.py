"""Routing facts to pages on the fixture vault (pure: no model, no writes)."""

import datetime

import pytest

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.diffs import render_diffs, vault_reader
from llm_wiki.domain.changes import AppendToSection, CreateFile
from llm_wiki.domain.entities import EntityKind
from llm_wiki.domain.facts import (
    Decision,
    Help,
    Learning,
    PersonNote,
    ProjectUpdate,
    SectionFacts,
    Win,
)
from llm_wiki.entity_index import load_index
from llm_wiki.routing import (
    INBOX_PATH,
    decision_slug,
    origin_of,
    resolve,
    route_facts,
)

NOTE = "00_raw/daily/2026-10-12.md"
DECISION_LOG = "30_decisions/2026/decisions-2026-q4.md"
ATLAS = "10_projects/project-atlas.md"
IMPACT = "40_impact/2026/impact-2026-q4.md"


@pytest.fixture(scope="module")
def note(fixture_vault):
    return parse_daily_note(NOTE, (fixture_vault / NOTE).read_text())


@pytest.fixture(scope="module")
def entities(fixture_vault):
    return load_index(fixture_vault)


@pytest.fixture(scope="module")
def exists(fixture_vault):
    read = vault_reader(fixture_vault)
    return lambda path: read(path) is not None


def _route(note, entities, exists, **facts):
    return route_facts(note, {"20": SectionFacts(**facts)}, entities, exists)


def _appends(edits, path):
    return [
        (e.heading, e.lines[0])
        for e in edits
        if isinstance(e, AppendToSection) and e.path == path
    ]


# --- origin and names ----------------------------------------------------------------


def test_origin_has_date_meeting_and_quote(note):
    o = origin_of(note, 23)
    assert o.date == datetime.date(2026, 10, 12)
    assert o.meeting == (
        "Atlas weekly sync with [[person-alex-chen]] and [[person-priya-nair]]"
    )
    assert o.quote == "Decided to keep the old BI tool until Q1 #decision"
    assert origin_of(note, 29).meeting == "1:1 with [[person-sam-ortiz]]"
    assert origin_of(note, 34).meeting is None  # Log has no meetings


@pytest.mark.parametrize(
    ("name", "kind", "expected"),
    [
        ("person-sam-ortiz", EntityKind.PERSON, "person-sam-ortiz"),
        ("[[person-sam-ortiz]]", EntityKind.PERSON, "person-sam-ortiz"),
        ("Sam", EntityKind.PERSON, "person-sam-ortiz"),  # alias
        ("Atlas", EntityKind.PROJECT, "project-atlas"),
        ("project-atlas", EntityKind.PERSON, None),  # wrong kind
        ("Jordan", EntityKind.PERSON, None),  # no page
        ("person-jordan-lee", EntityKind.PERSON, "person-jordan-lee"),  # linked
        ("person-made-up", EntityKind.PERSON, None),  # model-invented slug
        (None, EntityKind.PROJECT, None),
    ],
)
def test_resolve(entities, name, kind, expected):
    assert resolve(name, kind, entities, linked={"person-jordan-lee"}) == expected


# --- where each fact goes ------------------------------------------------------------


def test_decision_is_a_block_in_the_quarter_log_and_a_line_on_the_project(
    note, entities, exists
):
    decision = Decision(
        line=23,
        title="Keep the old BI tool until Q1",
        rationale="Migration is blocked on the data export.",
        project="project-atlas",
        people=["person-alex-chen", "Priya"],
    )
    edits, warnings = _route(note, entities, exists, decisions=[decision])
    assert warnings == []
    log = next(e for e in edits if e.path == DECISION_LOG)
    assert isinstance(log, CreateFile) and "# Decisions 2026 Q4" in log.text
    ((heading, *block),) = [
        (e.heading, *e.lines)
        for e in edits
        if isinstance(e, AppendToSection) and e.path == DECISION_LOG
    ]
    assert heading == "## 2026-10-12"
    assert block == [
        "",
        "### Keep the old BI tool until Q1",
        "- **When:** 2026-10-12, Atlas weekly sync with [[person-alex-chen]] and "
        "[[person-priya-nair]]",
        "- **People:** [[person-alex-chen]], [[person-priya-nair]]",
        "- **Project:** [[project-atlas]]",
        "- **Rationale:** Migration is blocked on the data export.",
        '- **Source:** [[2026-10-12]]: "Decided to keep the old BI tool until Q1 '
        '#decision"',
    ]
    ((heading, line),) = _appends(edits, ATLAS)
    assert heading == "## Decisions"
    assert line.startswith("- 2026-10-12 · Atlas weekly sync with [[person-alex-chen]]")
    assert "Keep the old BI tool until Q1 (see [[decisions-2026-q4]])" in line


def test_make_page_moves_the_details_to_the_decisions_own_page(note, entities, exists):
    decision = Decision(
        line=23,
        title="Keep the old BI tool until Q1",
        rationale="Migration is blocked on the data export.",
        project="project-atlas",
    )
    facts = {"20": SectionFacts(decisions=[decision])}
    edits, warnings = route_facts(note, facts, entities, exists, decision_pages=[23])
    assert warnings == []
    page = "30_decisions/decision-keep-the-old-bi-tool-until-q1.md"
    (created,) = [e for e in edits if isinstance(e, CreateFile) and e.path == page]
    assert created.text.startswith(
        "---\ntype: decision\ntitle: Keep the old BI tool until Q1\n"
        "date: 2026-10-12\nstatus: decided\n---\n# Keep the old BI tool until Q1\n\n"
        "- **When:** 2026-10-12, Atlas weekly sync"
    )
    assert "- **Rationale:** Migration is blocked on the data export." in created.text
    assert created.text.endswith("## Context\n\n## Consequences\n")

    ((_, *block),) = [
        (e.heading, *e.lines)
        for e in edits
        if isinstance(e, AppendToSection) and e.path == DECISION_LOG
    ]
    assert block[1] == "### Keep the old BI tool until Q1"
    assert block[3:] == ["- **Page:** [[decision-keep-the-old-bi-tool-until-q1]]"]
    ((_, line),) = _appends(edits, ATLAS)
    assert "(see [[decision-keep-the-old-bi-tool-until-q1]])" in line


def test_decision_slug_is_short_and_free():
    taken = {"decision-ship-it", "decision-ship-it-2"}
    assert decision_slug("Ship it!", taken.__contains__) == "decision-ship-it-3"
    long = decision_slug(
        "one two three four five six seven eight nine", lambda s: False
    )
    assert long == "decision-one-two-three-four-five-six-seven-eight"
    assert decision_slug("???", lambda s: False) == "decision-untitled"


def test_page_for_a_line_without_a_decision_is_warned(note, entities, exists):
    _, warnings = route_facts(note, {}, entities, exists, decision_pages=[23])
    assert warnings == [
        f"{NOTE}:23: no decision found on this line any more; no page made"
    ]


def test_two_decisions_share_the_date_heading_and_no_rationale_is_said(
    note, entities, exists, fixture_vault
):
    edits, _ = _route(
        note,
        entities,
        exists,
        decisions=[
            Decision(line=23, title="Keep the old BI tool until Q1"),
            Decision(line=22, title="Move the beta to Oct 20"),
        ],
    )
    diffs, warnings = render_diffs(edits, vault_reader(fixture_vault))
    assert warnings == []
    (log,) = [d for d in diffs if d.path == DECISION_LOG]
    assert log.after_text.count("## 2026-10-12") == 1
    assert "### Keep the old BI tool until Q1\n" in log.after_text
    assert "### Move the beta to Oct 20\n" in log.after_text
    assert "- **Rationale:** Not recorded in the note." in log.after_text
    assert "**Project:**" not in log.after_text  # none given, so no empty field


def test_win_goes_to_impact_page_and_project(note, entities, exists):
    win = Win(line=34, text="Found the login redirect root cause", project="Atlas")
    edits, _ = _route(note, entities, exists, wins=[win])
    assert isinstance(edits[0], CreateFile) and edits[0].path == IMPACT
    assert _appends(edits, IMPACT) == [
        (
            "## Wins",
            "- 2026-10-12: Found the login redirect root cause ([[project-atlas]]) "
            "([[2026-10-12]])",
        )
    ]
    assert _appends(edits, ATLAS)[0][0] == "## Wins"


def test_help_with_minutes_goes_to_impact_and_the_person(note, entities, exists):
    helped = Help(line=43, person="Priya", what="Paired on CI cache config", minutes=30)
    edits, _ = _route(note, entities, exists, helps=[helped])
    assert _appends(edits, IMPACT) == [
        (
            "## Help given",
            "- 2026-10-12: Helped [[person-priya-nair]]: Paired on CI cache config "
            "(30 min) ([[2026-10-12]])",
        )
    ]
    assert _appends(edits, "20_people/person-priya-nair.md")[0][0] == "## Help given"


def test_person_note_and_update_on_their_pages(note, entities, exists):
    edits, _ = _route(
        note,
        entities,
        exists,
        person_notes=[PersonNote(line=29, person="Sam", note="Wants to own search.")],
        project_updates=[
            ProjectUpdate(
                line=22, project="project-atlas", note="Beta moved to Oct 20."
            )
        ],
    )
    assert _appends(edits, "20_people/person-sam-ortiz.md") == [
        (
            "## Notes",
            "- 2026-10-12 · 1:1 with [[person-sam-ortiz]]: Wants to own search. "
            "([[2026-10-12]])",
        )
    ]
    assert _appends(edits, ATLAS)[0][0] == "## Updates"


def test_unmatched_names_go_to_the_inbox_not_lost(note, entities, exists):
    edits, _ = _route(
        note,
        entities,
        exists,
        learnings=[
            Learning(line=47, topic="Cookie Domain attribute", summary="Must match."),
            Learning(line=48, topic="concept-feature-flags", summary="Need expiry."),
        ],
        person_notes=[PersonNote(line=29, person="Jordan", note="New hire.")],
    )
    inbox = _appends(edits, INBOX_PATH)
    assert [heading for heading, _ in inbox] == ["## 2026-10-12", "## 2026-10-12"]
    assert "note about 'Jordan': New hire." in inbox[0][1]
    assert "learning (Cookie Domain attribute): Must match." in inbox[1][1]
    assert sum(isinstance(e, CreateFile) and e.path == INBOX_PATH for e in edits) == 1
    concept = _appends(edits, "15_concepts/concept-feature-flags.md")
    assert concept[0][0] == "## Notes"


def test_linked_person_without_a_page_gets_a_stub(fixture_vault, entities, exists):
    path = "00_raw/daily/2026-10-13.md"
    messy = parse_daily_note(path, (fixture_vault / path).read_text())
    note = PersonNote(line=12, person="person-jordan-lee", note="Joining in November.")
    edits, _ = route_facts(
        messy, {"11": SectionFacts(person_notes=[note])}, entities, exists
    )
    stub, append = edits
    assert isinstance(stub, CreateFile)
    assert stub.path == "20_people/person-jordan-lee.md"
    assert "title: Jordan Lee" in stub.text and "# Jordan Lee" in stub.text
    assert isinstance(append, AppendToSection) and append.path == stub.path


def test_routed_edits_render_cleanly_on_the_fixture_vault(
    note, entities, exists, fixture_vault
):
    edits, _ = _route(
        note,
        entities,
        exists,
        decisions=[Decision(line=23, title="Keep the old BI tool until Q1")],
        wins=[Win(line=34, text="Root cause found", project="project-atlas")],
        project_updates=[
            ProjectUpdate(line=22, project="project-atlas", note="Beta moved.")
        ],
    )
    diffs, warnings = render_diffs(edits, vault_reader(fixture_vault))
    assert warnings == []
    atlas = next(d for d in diffs if d.path == ATLAS)
    win = "## Wins\n- 2026-10-12: Root cause found ([[2026-10-12]])\n"
    assert win in atlas.after_text
    assert "## Updates\n- 2026-10-12 · Atlas weekly sync" in atlas.after_text
