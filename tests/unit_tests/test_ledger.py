"""The ingest ledger and "only new lines" extraction (pure, scripted model)."""

import datetime

import pytest

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.domain.common import SectionKind
from llm_wiki.domain.facts import SectionFacts, Win
from llm_wiki.domain.ledger import IngestLedger
from llm_wiki.entity_index import build_index
from llm_wiki.extraction import (
    build_messages,
    extract_section,
    line_key,
    needs_model,
    new_lines,
    prose_lines,
    task_lines,
)
from llm_wiki.ledger import LEDGER_PATH, dump_ledger, load_ledger, prose_keys, record

from .scripted_model import ScriptedModel

NOW = datetime.datetime(2026, 10, 13, 9, 0, tzinfo=datetime.UTC)
SHA = "a" * 64
NOTE = parse_daily_note(
    "00_raw/daily/2026-10-12.md",
    "---\ndate: 2026-10-12\n---\n"
    "## Log\n"
    "- Morning: deep work\n"  # line 5
    "- Found the root cause #win\n"  # line 6
    "- [ ] a task, never a prose line\n"
    "## Learned\n"
    "- Cookies need the right domain\n",  # line 9
)
LOG = NOTE.sections[0]


# --- the ledger file -----------------------------------------------------------------


def test_missing_ledger_is_empty_and_dump_round_trips():
    empty, problem = load_ledger(None)
    assert (empty, problem) == (IngestLedger(), None)
    recorded = record(empty, NOTE, SHA, NOW)
    text = dump_ledger(recorded)
    assert '"ingested_at": "2026-10-13T09:00:00Z"' in text
    assert load_ledger(text) == (recorded, None)


@pytest.mark.parametrize("text", ["{broken", '{"version": 2, "notes": {}}'])
def test_broken_ledger_is_reported(text):
    ledger, problem = load_ledger(text)
    assert ledger is None
    assert LEDGER_PATH in problem and "fix or remove it" in problem


def test_record_keeps_every_prose_line_but_no_task_lines():
    entry = record(IngestLedger(), NOTE, SHA, NOW).notes[NOTE.path]
    assert set(entry.lines) == {
        line_key("- Morning: deep work"),
        line_key("- Found the root cause #win"),
        line_key("- Cookies need the right domain"),
    }
    assert set(entry.lines) == prose_keys(NOTE)


def test_record_adds_to_earlier_lines():
    first = record(IngestLedger(), NOTE, SHA, NOW)
    shorter = parse_daily_note(NOTE.path, "---\ndate: 2026-10-12\n---\n## Log\n- New\n")
    entry = record(first, shorter, "b" * 64, NOW).notes[NOTE.path]
    assert line_key("- Morning: deep work") in entry.lines  # deleted, still known
    assert line_key("- New") in entry.lines
    assert entry.sha256 == "b" * 64


def test_line_key_ignores_spacing_not_words():
    assert line_key("- Found  the root cause") == line_key(
        "    - Found the root cause "
    )
    assert line_key("- Found the root cause") != line_key("- Found the real cause")


# --- extraction with seen lines ------------------------------------------------------


def _seen(*texts):
    return frozenset(line_key(t) for t in texts)


def test_new_lines_and_needs_model():
    lines = prose_lines(LOG, task_lines(NOTE))
    seen = _seen("- Morning: deep work")
    assert new_lines(lines, seen) == [(6, "- Found the root cause #win")]
    everything = _seen("- Morning: deep work", "- Found the root cause #win")
    assert not needs_model(LOG, task_lines(NOTE), everything)
    assert needs_model(LOG, task_lines(NOTE), seen)


def test_prompt_marks_seen_lines_as_context_and_requires_only_new_tags():
    lines = prose_lines(LOG, task_lines(NOTE))
    _, human = build_messages(
        LOG, lines, build_index([]), seen=_seen("- Found the root cause #win")
    )
    assert "   5 | - Morning: deep work" in human.content
    assert "   6 ~ - Found the root cause #win" in human.content
    assert "Required" not in human.content  # the #win line was already ingested


def test_facts_citing_seen_lines_are_dropped_without_retry():
    seen = _seen("- Found the root cause #win")
    answer = SectionFacts(wins=[Win(line=6, text="Found the root cause")])
    model = ScriptedModel(answer)
    facts, warnings = extract_section(
        model, LOG, task_lines(NOTE), build_index([]), seen=seen
    )
    assert facts == SectionFacts()
    assert warnings == ["section 'Log': wins fact cites line 6, already ingested"]
    assert len(model.calls) == 1


def test_sections_are_unaffected_without_seen_lines():
    assert {s.kind for s in NOTE.sections if needs_model(s, task_lines(NOTE))} == {
        SectionKind.LOG,
        SectionKind.LEARNED,
    }
