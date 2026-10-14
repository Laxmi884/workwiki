"""Extraction on the fixture note with a scripted model (no real model calls)."""

from types import SimpleNamespace

import pytest

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.domain.common import SectionKind
from llm_wiki.domain.facts import Decision, PersonNote, ProjectUpdate, SectionFacts
from llm_wiki.entity_index import load_index
from llm_wiki.extraction import (
    build_messages,
    extract_section,
    needs_model,
    prose_lines,
    tagged_lines,
    task_lines,
)
from llm_wiki.ingest import nodes
from llm_wiki.ingest.state import merge_facts

from .scripted_model import ScriptedModel

NOTE = "00_raw/daily/2026-10-12.md"


@pytest.fixture(scope="module")
def note(fixture_vault):
    return parse_daily_note(NOTE, (fixture_vault / NOTE).read_text())


@pytest.fixture(scope="module")
def entities(fixture_vault):
    return load_index(fixture_vault)


def _section(note, kind):
    (section,) = [s for s in note.sections if s.kind is kind]
    return section


DECISION = Decision(line=23, title="Keep the old BI tool until Q1")
UPDATE = ProjectUpdate(line=22, project="project-atlas", note="Beta moved to Oct 20.")
SAM = PersonNote(line=29, person="person-sam-ortiz", note="Wants to own search.")
GOOD = SectionFacts(decisions=[DECISION], project_updates=[UPDATE], person_notes=[SAM])


# --- what the model is shown -------------------------------------------------------


def test_prose_lines_leave_out_heading_tasks_blanks_and_code(note):
    tasks = task_lines(note)
    meetings = prose_lines(_section(note, SectionKind.MEETINGS), tasks)
    assert [n for n, _ in meetings] == [21, 22, 23, 24, 28, 29]  # tasks: 25, 26, 30
    log = prose_lines(_section(note, SectionKind.LOG), tasks)
    assert [n for n, _ in log] == [33, 34, 35, 40]  # fenced 36-39 left out


def test_only_prose_sections_need_the_model(note):
    tasks = task_lines(note)
    wanted = {s.kind for s in note.sections if needs_model(s, tasks)}
    assert wanted == {
        SectionKind.MEETINGS,
        SectionKind.LOG,
        SectionKind.HELPED,
        SectionKind.LEARNED,
    }


def test_tagged_lines(note):
    tasks = task_lines(note)
    meetings = prose_lines(_section(note, SectionKind.MEETINGS), tasks)
    log = prose_lines(_section(note, SectionKind.LOG), tasks)
    assert tagged_lines(meetings) == {"decision": [23], "win": []}
    assert tagged_lines(log) == {"decision": [], "win": [34]}


def test_prompt_has_numbered_lines_entities_and_required_tags(note, entities):
    meetings = _section(note, SectionKind.MEETINGS)
    lines = prose_lines(meetings, task_lines(note))
    _, human = build_messages(meetings, lines, entities)
    assert (
        "  23 | - Decided to keep the old BI tool until Q1 #decision" in human.content
    )
    assert "- person-sam-ortiz: " in human.content
    assert "line 23 is tagged #decision" in human.content
    assert "Open ticket" not in human.content  # a task line


def test_revise_prompt_has_previous_answer_and_feedback(note, entities):
    meetings = _section(note, SectionKind.MEETINGS)
    _, human = build_messages(
        meetings,
        prose_lines(meetings, task_lines(note)),
        entities,
        previous=GOOD,
        feedback=["Sam's note is about the search feature, say so"],
    )
    assert "Keep the old BI tool until Q1" in human.content
    assert "Sam's note is about the search feature" in human.content


# --- the model call and the checks ---------------------------------------------------


def _extract(note, entities, model, kind=SectionKind.MEETINGS):
    return extract_section(model, _section(note, kind), task_lines(note), entities)


def test_good_answer_is_used_as_is(note, entities):
    model = ScriptedModel(GOOD)
    assert _extract(note, entities, model) == (GOOD, [])
    assert len(model.calls) == 1


def test_missing_tagged_line_is_sent_back_once(note, entities):
    missed = SectionFacts(project_updates=[UPDATE], person_notes=[SAM])
    model = ScriptedModel(missed, GOOD)
    assert _extract(note, entities, model) == (GOOD, [])
    retry = model.calls[1][-1].content
    assert "line 23 is tagged #decision but has no item in `decisions`" in retry


def test_still_missing_after_retry_becomes_a_warning(note, entities):
    missed = SectionFacts(project_updates=[UPDATE])
    facts, warnings = _extract(note, entities, ScriptedModel(missed, missed))
    assert facts == missed  # what it did find is kept
    assert warnings == [
        "section 'Meetings': line 23 is tagged #decision but has no item in `decisions`"
    ]


def test_facts_citing_lines_outside_the_section_are_dropped(note, entities):
    stray = ProjectUpdate(line=34, project="project-atlas", note="from the Log")
    answer = SectionFacts(decisions=[DECISION], project_updates=[UPDATE, stray])
    model = ScriptedModel(answer)
    facts, warnings = _extract(note, entities, model)
    assert facts.project_updates == [UPDATE]
    assert "project_updates fact cites line 34, not in section" in warnings[0]
    assert len(model.calls) == 1  # code repaired it: no retry


def test_invalid_output_twice_gives_empty_facts_and_a_warning(note, entities):
    model = ScriptedModel(ValueError("bad json"), ValueError("bad json"))
    facts, warnings = _extract(note, entities, model)
    assert facts == SectionFacts()
    assert "invalid output (bad json)" in warnings[0]


# --- node and reducer ----------------------------------------------------------------


def test_extract_node_returns_facts_under_the_section_key(
    note, fixture_vault, monkeypatch
):
    model = ScriptedModel(GOOD)
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: model)
    runtime = SimpleNamespace(context={"vault_path": str(fixture_vault)})
    update = nodes.extract({"note": note, "section_line": 20}, runtime)
    assert update == {"facts": {"20": GOOD}, "warnings": []}


def test_merge_facts_adds_sections_and_replaces_a_rerun():
    first = merge_facts({}, {"20": SectionFacts()})
    both = merge_facts(first, {"32": GOOD})
    assert merge_facts(both, {"20": GOOD}) == {"20": GOOD, "32": GOOD}


def test_repeated_facts_are_removed_and_reported(note, entities):
    looping = SectionFacts(decisions=[DECISION], project_updates=[UPDATE] * 4)
    model = ScriptedModel(looping)
    facts, warnings = _extract(note, entities, model)
    assert facts.project_updates == [UPDATE]
    assert warnings == ["section 'Meetings': project_updates: 3 repeated facts removed"]
    assert len(model.calls) == 1


def test_schema_caps_each_list_so_local_models_stop():
    schema = SectionFacts.model_json_schema()
    assert schema["properties"]["project_updates"]["maxItems"] == 20
