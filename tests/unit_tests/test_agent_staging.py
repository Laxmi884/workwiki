"""Spike 0006: the checks on what the ingest agent stages, and one scripted agent run."""

import pytest
from langchain_core.messages import AIMessage

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.diffs import vault_reader
from llm_wiki.domain.changes import AppendToSection, CreateFile
from llm_wiki.entity_index import load_index
from llm_wiki.extraction import line_key
from llm_wiki.ingest import nodes
from llm_wiki.ingest_agent import staging
from llm_wiki.ingest_agent.graph import builder as agent_builder
from llm_wiki.ingest_agent.staging import StagingContext, StagingError
from llm_wiki.runner import IngestRunner

from .scripted_model import ToolScript, call

NOTE = "00_raw/daily/2026-10-12.md"


@pytest.fixture
def ctx(fixture_vault):
    read = vault_reader(fixture_vault)
    note = parse_daily_note(NOTE, read(NOTE))
    return StagingContext(
        note, staging.citable_lines(note), load_index(fixture_vault), read
    )


def fact(ctx, staged=(), filed=(), **kw):
    return staging.stage_fact(ctx, list(staged), list(filed), **kw)


def test_citable_lines_skip_headings_blanks_and_code(ctx):
    lines = staging.citable_lines(ctx.note)
    assert 22 in lines and 25 in lines  # prose and a nested task
    assert not {20, 21, 27, 36, 37, 38, 39} & set(lines)  # headings, blank, fence


def test_seen_lines_cannot_be_cited(ctx):
    seen = {line_key(ctx.citable[29])}
    assert 29 not in staging.citable_lines(ctx.note, seen)


def test_person_note_gets_its_origin_from_code(ctx):
    edits, filed = fact(
        ctx,
        target="person-sam-ortiz",
        section="Notes",
        text="Wants to own the search feature.",
        line=29,
    )
    assert edits == [
        AppendToSection(
            path="20_people/person-sam-ortiz.md",
            heading="## Notes",
            lines=(
                "- 2026-10-12 · 1:1 with [[person-sam-ortiz]]: "
                "Wants to own the search feature. ([[2026-10-12]])",
            ),
        )
    ]
    assert (filed.line, filed.path) == (29, "20_people/person-sam-ortiz.md")


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"line": 21}, "can't be cited"),  # a ### heading
        ({"line": 37}, "can't be cited"),  # fenced code
        ({"line": 999}, "can't be cited"),
        ({"target": "../../00_raw/daily/x"}, "not a target"),
        ({"target": "person-nobody"}, "has no page"),
        ({"target": "impact", "section": "Notes"}, "not allowed"),
        ({"section": "Current state"}, "update_summary"),
        ({"section": "## Notes <b>"}, "not a short"),
        ({"text": "Talked to [[person-ghost]]."}, "has no page"),
        ({"text": "x" * 401}, "keep it under"),
    ],
)
def test_refusals(ctx, kw, reason):
    args = {
        "target": "person-sam-ortiz",
        "section": "Notes",
        "text": "Wants to own search.",
        "line": 29,
    } | kw
    with pytest.raises(StagingError, match=reason):
        fact(ctx, **args)


def test_one_fact_per_line_per_page(ctx):
    args = {"target": "person-sam-ortiz", "section": "Notes", "line": 29}
    edits, filed = fact(ctx, text="Wants search.", **args)
    with pytest.raises(StagingError, match="already filed"):
        fact(ctx, edits, [filed], text="Wants to own search.", **args)


def test_decision_log_and_impact_are_created_once(ctx):
    edits, filed = fact(
        ctx,
        target="decision-log",
        section="Decisions",
        text="Not recorded in the note.",
        line=23,
        title="Keep the old BI tool until Q1",
    )
    assert isinstance(edits[0], CreateFile)
    assert edits[0].path == "30_decisions/2026/decisions-2026-q4.md"
    assert edits[1].lines[1] == "### Keep the old BI tool until Q1"

    win1, f1 = fact(ctx, target="impact", section="Wins", text="Found it.", line=34)
    win2, _ = fact(
        ctx, win1, [f1], target="impact", section="Help given", text="Paired.", line=43
    )
    assert [type(e) for e in win1] == [CreateFile, AppendToSection]
    assert [type(e) for e in win2] == [AppendToSection]


def test_decision_log_needs_a_title(ctx):
    with pytest.raises(StagingError, match="title"):
        fact(ctx, target="decision-log", section="Decisions", text="x", line=23)


def test_new_page_must_be_named_in_the_line(ctx):
    page = staging.stage_page(
        ctx, [], slug="concept-cookie-domain", title="Cookie Domain", line=47
    )
    assert page.path == "15_concepts/concept-cookie-domain.md"
    with pytest.raises(StagingError, match="doesn't name"):
        staging.stage_page(ctx, [], slug="person-hana", title="Hana", line=47)
    with pytest.raises(StagingError, match="already has a page"):
        staging.stage_page(ctx, [], slug="person-sam-ortiz", title="Sam", line=29)
    with pytest.raises(StagingError, match="is not a"):
        staging.stage_page(ctx, [], slug="decision-x", title="Cookie", line=47)


def test_facts_can_go_on_a_proposed_page(ctx):
    page = staging.stage_page(
        ctx, [], slug="concept-cookie-domain", title="Cookie Domain", line=47
    )
    edits, _ = fact(
        ctx,
        [page],
        target="concept-cookie-domain",
        section="Notes",
        text="Must match the subdomain for SSO redirects.",
        line=47,
    )
    assert edits[0].path == "15_concepts/concept-cookie-domain.md"


def test_scripted_agent_run_reaches_the_review(vault_copy, monkeypatch):
    script = ToolScript(
        messages=iter(
            [
                call("read_file", file_path="/skills/wiki-filing/SKILL.md"),
                call("write_file", file_path="/vault/x.md", content="no"),
                call(
                    "file_fact",
                    target="person-sam-ortiz",
                    section="Notes",
                    text="Wants to own the search feature.",
                    line=29,
                ),
                call("file_fact", target="impact", section="Wins", text="x", line=21),
                call("ask_user", question="Is BI a project?", line=23),
                call("write_todos", todos=[]),
                AIMessage("Filed 1 fact."),
            ]
        )
    )
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: script)
    runner = IngestRunner.in_memory(
        context={"vault_path": str(vault_copy)}, graph_builder=agent_builder
    )
    result = runner.start(NOTE)
    assert result.status == "review"
    paths = [f.path for f in result.review.files]
    assert "20_people/person-sam-ortiz.md" in paths
    assert not any(p.endswith("x.md") for p in paths)
    assert any("agent refused: file_fact: line 21" in w for w in result.warnings)
    assert any("question line 23: Is BI a project?" in w for w in result.warnings)
    values = runner.graph.get_state(runner._config(result.thread_id)).values
    assert values["agent_stats"]["refusals"] == 1
    assert [
        {k: v for k, v in f.items() if k != "added"} for f in values["agent_filed"]
    ] == [
        {
            "line": 29,
            "path": "20_people/person-sam-ortiz.md",
            "section": "## Notes",
            "text": "Wants to own the search feature.",
        }
    ]
    assert not (vault_copy / "x.md").exists()
    done = runner.resume(result.thread_id, {"action": "approve"})
    assert done.status == "done"
    assert "20_people/person-sam-ortiz.md" in done.written
    sam = (vault_copy / "20_people/person-sam-ortiz.md").read_text()
    assert "Wants to own the search feature." in sam


def test_finalize_fixes_repeats_from_parallel_calls(ctx):
    # Two calls in one model turn both see an empty staging.
    a, fa = fact(ctx, target="impact", section="Wins", text="Found it.", line=34)
    b, fb = fact(ctx, target="impact", section="Help given", text="Paired.", line=43)
    c, fc = fact(ctx, target="impact", section="Wins", text="Root cause.", line=34)
    edits, filed, problems = staging.finalize([*a, *b, *c], [fa, fb, fc])
    assert sum(isinstance(e, CreateFile) for e in edits) == 1
    assert [f.text for f in filed] == ["Found it.", "Paired."]
    assert not any(
        "Root cause." in line for e in edits for line in getattr(e, "lines", ())
    )
    assert problems == [
        "finalize: line 34 filed twice on 40_impact/2026/impact-2026-q4.md; kept the first"
    ]


def test_any_short_heading_on_entity_pages(ctx):
    edits, filed = fact(
        ctx,
        target="person-sam-ortiz",
        section="1:1 notes",
        text="Wants search.",
        line=29,
    )
    assert edits[0].heading == "## 1:1 notes" and filed.section == "## 1:1 notes"


def test_summary_replaces_current_state_and_last_one_wins(ctx):
    first = staging.stage_summary(
        ctx, [], target="project-atlas", lines=["Beta Oct 20."], cites=[22]
    )
    assert first.heading == "## Current state"
    assert first.lines == (
        "- Beta Oct 20.",
        "",
        "_Updated 2026-10-12 from [[2026-10-12]]_",
    )
    second = staging.stage_summary(
        ctx,
        [],
        target="project-atlas",
        lines=["Beta Oct 20.", "BI tool kept."],
        cites=[22, 23],
    )
    edits, _, _ = staging.finalize([first, second], [])
    assert edits == [second]
    with pytest.raises(StagingError, match="cite"):
        staging.stage_summary(ctx, [], target="project-atlas", lines=["x"], cites=[])
    with pytest.raises(StagingError, match="only person"):
        staging.stage_summary(ctx, [], target="impact", lines=["x"], cites=[22])
    with pytest.raises(StagingError, match="1 to 6"):
        staging.stage_summary(
            ctx, [], target="project-atlas", lines=["x"] * 7, cites=[22]
        )


def test_withdrawn_fact_and_its_line_are_dropped(ctx):
    a, fa = fact(ctx, target="person-sam-ortiz", section="Notes", text="A.", line=29)
    b, fb = fact(ctx, target="project-atlas", section="Updates", text="B.", line=22)
    edits, filed, problems = staging.finalize(
        [*a, *b], [fa, fb], [(29, "20_people/person-sam-ortiz.md", 2)]
    )
    assert filed == [fb] and edits == b and problems == []


def test_revise_keeps_the_filing_and_a_withdrawn_fact_can_be_filed_again(
    vault_copy, monkeypatch
):
    sam = {"target": "person-sam-ortiz", "line": 29}
    script = ToolScript(
        messages=iter(
            [
                # round 1
                call("file_fact", section="Notes", text="Wants search.", **sam),
                call(
                    "file_fact", target="impact", section="Wins", text="Fix.", line=34
                ),
                AIMessage("Filed."),
                # round 2 (revise): move Sam's note to "1:1 notes"
                call("withdraw_fact", **sam),
                call("file_fact", section="1:1 notes", text="Wants search.", **sam),
                AIMessage("Moved."),
            ]
        )
    )
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: script)
    runner = IngestRunner.in_memory(
        context={"vault_path": str(vault_copy)}, graph_builder=agent_builder
    )
    result = runner.start(NOTE)
    result = runner.resume(
        result.thread_id,
        {"action": "revise", "feedback": "1:1 notes go under 1:1 notes"},
    )
    values = runner.graph.get_state(runner._config(result.thread_id)).values
    assert [(f["line"], f["section"]) for f in values["agent_filed"]] == [
        (34, "## Wins"),  # kept from round 1
        (29, "## 1:1 notes"),
    ]
    assert values["agent_stats"]["refusals"] == 0
    sam_diff = next(f for f in result.review.files if f.path.endswith("sam-ortiz.md"))
    assert "## 1:1 notes" in sam_diff.diff and "## Notes\n+" not in sam_diff.diff
