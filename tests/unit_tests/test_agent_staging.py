"""Spike 0006: the checks on what the ingest agent stages, and one scripted agent run."""

import dataclasses

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
        ({"line": 22}, "doesn't name person-sam-ortiz"),  # Atlas sync, not Sam
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

    d2, _ = fact(
        ctx,
        edits,
        [filed],
        target="decision-log",
        section="Decisions",
        text="Not recorded in the note.",
        line=18,
        title="Drop the dashboard migration",
    )
    assert [type(e) for e in d2] == [AppendToSection], "the log is created once"
    win, _ = fact(ctx, target="impact", section="Wins", text="Found it.", line=34)
    assert [type(e) for e in win] == [CreateFile, AppendToSection]


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
    assert result.review.warnings[-2:] == (
        "question line 23: Is BI a project?",
        "code refused 1 of the agent's calls (details in the trace)",
    )
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
    b, fb = fact(
        ctx, target="person-priya-nair", section="Help given", text="Paired.", line=43
    )
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


def test_existing_project_needs_its_name_in_the_line_parent_or_heading(ctx):
    # Line 22 is under "### Atlas weekly sync"; line 13's parent links project-atlas.
    fact(ctx, target="project-atlas", section="Updates", text="Beta Oct 20.", line=22)
    fact(ctx, target="project-atlas", section="Updates", text="Fixed it.", line=13)
    with pytest.raises(StagingError, match="doesn't name project-atlas"):
        fact(ctx, target="project-atlas", section="Updates", text="x", line=29)


def test_a_task_code_mapped_to_the_project_counts(fixture_vault):
    read = vault_reader(fixture_vault)
    note = parse_daily_note(NOTE, read(NOTE))
    mapped = StagingContext(
        note,
        staging.citable_lines(note),
        load_index(fixture_vault),
        read,
        task_projects={30: "project-atlas"},
    )
    fact(mapped, target="project-atlas", section="Updates", text="Plan.", line=30)


def test_a_proposed_project_also_needs_its_name_unless_feedback_names_it(ctx):
    page = staging.stage_page(ctx, [], slug="project-search", title="search", line=29)
    fact(ctx, [page], target="project-search", section="Updates", text="x", line=29)
    with pytest.raises(StagingError, match="doesn't name project-search"):
        fact(ctx, [page], target="project-search", section="Updates", text="x", line=33)
    told = dataclasses.replace(ctx, feedback=("line 33 is about Search too",))
    fact(told, [page], target="project-search", section="Updates", text="x", line=33)


def test_wins_need_a_win_tag(ctx):
    fact(ctx, target="impact", section="Wins", text="Root cause found.", line=34)
    with pytest.raises(StagingError, match="isn't tagged #win"):
        fact(ctx, target="impact", section="Wins", text="x", line=13)
    with pytest.raises(StagingError, match="isn't tagged #win"):
        fact(ctx, target="project-atlas", section="Wins", text="x", line=22)


def test_impact_takes_no_help_given(ctx):
    with pytest.raises(StagingError, match="not allowed on impact"):
        fact(ctx, target="impact", section="Help given", text="Paired.", line=43)


def test_finalize_drops_a_new_page_left_empty(ctx):
    edits, f = fact(ctx, target="impact", section="Wins", text="Found it.", line=34)
    kept, filed, problems = staging.finalize(edits, [f], [(34, f.path, 1)])
    assert kept == [] and filed == []
    assert problems == [
        "finalize: dropped 40_impact/2026/impact-2026-q4.md: a new page with nothing on it"
    ]


def test_picked_wins_reach_the_impact_page_without_the_model(vault_copy, monkeypatch):
    script = ToolScript(
        messages=iter(
            [
                call(
                    "file_fact",
                    target="person-sam-ortiz",
                    section="Notes",
                    text="Wants to own the search feature.",
                    line=29,
                ),
                AIMessage("Filed 1 fact."),
            ]
        )
    )
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: script)
    runner = IngestRunner.in_memory(
        context={"vault_path": str(vault_copy)}, graph_builder=agent_builder
    )
    first = runner.start(NOTE)
    assert not any(f.path.startswith("40_impact") for f in first.review.files)
    win = first.review.wins[0]

    picked = runner.resume(first.thread_id, {"action": "answer", "wins": [win.id]})
    impact = next(f for f in picked.review.files if f.path.startswith("40_impact"))
    assert win.text in impact.diff
    assert any(f.path.endswith("sam-ortiz.md") for f in picked.review.files)
    assert next(w for w in picked.review.wins if w.id == win.id).chosen

    done = runner.resume(first.thread_id, "approve")
    assert impact.path in done.written


def test_a_fact_may_name_a_page_proposed_in_the_same_turn(ctx):
    # Parallel calls: file_fact runs before (or beside) the propose_page it needs.
    sam = staging.stage_page(ctx, [], slug="person-sam", title="Sam", line=29)
    edits, f = fact(ctx, target="person-sam", section="Notes", text="x", line=29)
    assert staging.finalize([*edits, sam], [f], ctx=ctx)[1] == [f]

    kept, filed, problems = staging.finalize(edits, [f], ctx=ctx)
    assert kept == [] and filed == []
    assert problems == [
        "finalize: dropped line 29 on 20_people/person-sam.md: no page was proposed "
        "for it"
    ]
    # Filed before its page existed, on a line that doesn't name Sam: dropped.
    e2, f2 = fact(ctx, target="person-sam", section="Notes", text="x", line=22)
    problems = staging.finalize([*e2, sam], [f2], ctx=ctx)[2]
    assert problems == [
        "finalize: dropped line 22 on 20_people/person-sam.md: the line doesn't name "
        "that page",
        "finalize: dropped 20_people/person-sam.md: a new page with nothing on it",
    ]


def test_a_heading_may_be_cited_for_a_new_page_or_a_question(ctx):
    page = staging.stage_page(
        ctx, [], slug="project-atlas-sync", title="Atlas", line=21
    )
    assert page.path == "10_projects/project-atlas-sync.md"
    assert staging.check_question(ctx, "Who is on the sync?", 21).startswith("line 21")
    with pytest.raises(StagingError, match="can't be cited"):
        fact(ctx, target="project-atlas", section="Updates", text="x", line=21)


def test_existing_person_needs_their_name_in_the_line_parent_or_heading(ctx):
    fact(ctx, target="person-priya-nair", section="Help given", text="CI.", line=43)
    with pytest.raises(StagingError, match="doesn't name person-priya-nair"):
        fact(ctx, target="person-priya-nair", section="Notes", text="Owns CI.", line=47)


def test_review_lists_dropped_facts_and_counts_other_refusals():
    from llm_wiki.ingest_agent.graph import _for_the_user

    refusals = [
        "file_fact: line 8 doesn't name project-atlas",
        "ask_user: line 17 can't be cited",
        "finalize: dropped line 8 on 10_projects/p.md: the line doesn't name that page",
    ]
    assert _for_the_user(refusals, ["line 9: ad hoc?"]) == [
        "question line 9: ad hoc?",
        "the agent's question was refused: line 17 can't be cited",
        "not filed: dropped line 8 on 10_projects/p.md: the line doesn't name that page",
        "code refused 1 of the agent's calls (details in the trace)",
    ]
