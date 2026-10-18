"""The phase 6 eval without a model: corpus, scoring, and two days end to end, scripted."""

import argparse
import json
import subprocess

from langchain_core.messages import AIMessage

from llm_wiki.ingest import nodes
from tests.eval import ingest_eval, qa_eval, run
from tests.eval.corpus import (
    load_labels,
    load_preferences,
    load_questions,
    make_vault,
    note_text,
)
from tests.eval.score import (
    key_score,
    oracle_answers,
    oracle_feedback,
    page_names,
    score,
    unsupported_numbers,
    vault_checks,
)

from .scripted_model import AgentRounds, ToolScript, call

DAY = "2026-11-24"


def test_corpus_resolves_and_the_vault_starts_clean(tmp_path):
    labels, prefs = load_labels(), load_preferences()
    assert len(labels["days"]) == 20 and [p["id"] for p in prefs][0] == "P1"
    days = labels["days"].values()
    assert sum(len(d["facts"]) for d in days) == 82
    assert sum(len(d["chore_lines"]) for d in days) == 11
    questions = load_questions()
    assert len(questions) == 30
    assert sum(q["keys"] == "unanswerable" for q in questions) == 2
    vault = make_vault(tmp_path / "v")
    assert not list((vault / "00_raw/daily").glob("*.md"))
    assert (vault / "10_projects/project-hiring-q4.md").exists()
    assert (vault / "90_meta/rules.md").exists()
    assert not (vault / "__init__.py").exists()
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    )
    assert len(log.stdout.splitlines()) == 1


def _hit(labels, anchor, path, section, text="x"):
    line = next(
        f["line"] for f in labels["days"][DAY]["facts"] if f["anchor"] == anchor
    )
    return {"line": line, "path": path, "section": section, "text": text}


def test_score_counts_pages_prefs_and_misplaced():
    labels = load_labels()
    lines = note_text(DAY).splitlines()
    marta = "20_people/person-marta-ruiz.md"
    filed = [
        _hit(labels, "feedback on her first design doc", marta, "## 1:1 notes"),
        _hit(labels, "happier with on-call", marta, "## Notes"),
        _hit(labels, "happier with on-call", "40_impact/2026/impact-2026-q4.md", "## Wins"),
        _hit(labels, "weekly syncs on Tuesdays", "10_projects/project-atlas.md", "## Updates"),
    ]  # fmt: skip
    s = score(DAY, filed, labels, lines)
    assert (s["found"], s["right"], s["expected"]) == (3, 2, 5)
    assert s["right_page"] == round(2 / 5, 3)  # Tuesdays went to Atlas, not payments
    assert s["prefs"]["one_on_one"] == {"ok": 1, "of": 2}
    assert len(s["misplaced"]) == 2

    feedback = oracle_feedback(DAY, s, labels, {"one_on_one"}, lines)
    assert any("worth keeping" in f for f in feedback)
    assert any("belongs on payments" in f for f in feedback)
    assert any("rule about one on one" in f for f in feedback)
    untaught = oracle_feedback(DAY, s, labels, set(), lines)
    assert not any("rule about" in f for f in untaught)


def test_numbers_missing_from_the_cited_line_are_flagged():
    lines = ["- p95 is 1.8s, target under 1s"]
    filed = [
        {"line": 1, "path": "p.md", "text": "p95 1.8s (target 1s)."},
        {"line": 1, "path": "p.md", "text": "p95 dropped to 0.9s."},
    ]
    assert unsupported_numbers(filed, lines) == [
        {"line": 1, "path": "p.md", "numbers": ["0.9"]}
    ]


def test_vault_checks_current_state_candidates_and_bookkeeping(tmp_path):
    labels = load_labels()
    vault = make_vault(tmp_path / "v")
    note = f"00_raw/daily/{DAY}.md"
    (vault / note).write_text(note_text(DAY))
    (vault / "10_projects/project-payments-v2.md").write_text(
        "# Payments v2\n\n## Current state\n- Target: April\n"
    )
    (vault / "20_people/person-lena.md").write_text("# Lena\n")
    out = vault_checks(vault, DAY, note, labels)
    assert out["state"] == {"ok": 1, "of": 1}
    assert out["candidate_pages"] == ["person-lena.md"]
    tasks = out["bookkeeping"]
    assert tasks["tasks"] > 0 and len(tasks["without_id"]) == tasks["tasks"]
    assert not tasks["in_ledger"]  # nothing ingested


def test_key_score():
    assert key_score("GA moved to Dec 9.", [["dec 9"], ["go/no-go"]]) == 0.5
    assert key_score("The notes don’t say.", "unanswerable") == 1.0
    assert key_score("Lena Park.", "unanswerable") == 0.0
    assert (
        key_score("We decided **not** to add a `cache`.", [["not to add a cache"]])
        == 1.0
    )


def test_two_days_end_to_end_with_a_scripted_model(tmp_path, monkeypatch, capsys):
    """Real graph, real apply and git commit; the reviewer and the remember answer."""
    day1, day2 = "2026-11-04", "2026-11-05"
    rounds = AgentRounds(
        # day 1, first pass: one of five labelled facts
        [call("file_fact", target="project-atlas", section="Updates",
              text="Dashboard p95 is 1.8s, target under 1s.", line=17)],
        # day 1, revise: the decision the oracle said was missing
        [call("file_fact", target="decision-log", section="Decisions",
              text="No cache layer before GA: too risky this late.", line=16,
              title="No cache layer before GA")],
        # day 2, first pass: Sam's 1:1 line on Sam's page
        [call("file_fact", target="person-sam-ortiz", section="Notes",
              text="Wants to present the search design at the December all-hands.",
              line=17),
         call("ask_user", question="Who is Hana Kim: a new person page?", line=24)],
        # day 2, revise: the #win the oracle said was missing
        [call("file_fact", target="impact", section="Wins",
              text="The events index cut dashboard p95 from 1.8s to 1.1s.", line=21)],
    )  # fmt: skip
    monkeypatch.setattr(nodes, "chat_model", rounds)
    monkeypatch.setattr(nodes, "fallback_models", lambda runtime: [])
    vault = make_vault(tmp_path / "vault")

    seen = []
    rows = ingest_eval.run_ingest(
        vault, None, "oracle", [day1, day2], on_day=seen.append
    )

    assert seen == rows and all("error" not in r for r in rows), rows
    one, two = rows
    assert (one["first"]["found"], one["final"]["found"]) == (1, 2)
    assert one["revised"] and "worth keeping" in rounds.briefs[1]
    assert one["taught"] and "chores" not in one["active"]
    rules = (vault / "90_meta/rules.md").read_text()
    assert "Routine chores are never wins" in rules  # P1, via the review's answer
    assert "chores" in two["active"]
    assert (two["first"]["found"], two["final"]["found"]) == (1, 2)
    assert two["refusals"] == 0 and two["final"]["right"] == 2
    assert "cut dashboard p95" in rounds.briefs[3]  # the oracle's feedback
    assert two["first"]["asked"] == ["Hana Kim's API spec"]  # asked, not silent
    assert two["answers"] == {"q1": "File it on impact or person-hana."}
    assert "File it on impact or person-hana." in rounds.briefs[3]  # answer reached it
    assert "belongs on decision-log or project-atlas" in rounds.briefs[1]
    impact = next((vault / "40_impact").rglob("*.md")).read_text()
    assert "1.8s to 1.1s" in impact
    for r in rows:
        assert r["commit"] and f"00_raw/daily/{r['day']}.md" in r["written"]
        assert r["vault"]["bookkeeping"]["in_ledger"]
        assert not r["vault"]["bookkeeping"]["without_id"]

    summary = ingest_eval.summarize(rows)
    assert summary["failed"] == [] and summary["bookkeeping_problems"] == []
    assert summary["uncommitted_days"] == []
    assert summary["final"]["recall"]["ok"] == 4
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    )
    assert len(log.stdout.splitlines()) == 3  # seed + one commit per day

    questions = [q for q in load_questions() if q["q"].startswith(
        ("What was the original Atlas GA date", "What is Lena's last name")
    )]  # fmt: skip
    model = ToolScript(
        messages=iter([AIMessage("It was Nov 18."), AIMessage("The notes don't say.")])
    )
    answers = qa_eval.run_qa(vault, model, questions=questions)
    assert [a["score"] for a in answers] == [1.0, 1.0]
    assert qa_eval.summarize(answers)["unanswerable_ok"] == "1/1"

    # the report and the comparison of two runs
    report = {
        "meta": {"openrouter_usage_before": 1.0, "openrouter_usage_after": 1.25},
        "ingest": {"summary": summary, "days": rows},
        "qa": {"summary": qa_eval.summarize(answers), "rows": answers},
    }
    head = run.headline(report)
    assert head["final recall"] == round(4 / 9, 3) and head["cost $"] == 0.25
    assert head["qa key facts"] == 1.0
    worse = json.loads(json.dumps(report))
    worse["qa"]["rows"][0]["score"] = 0.0
    worse["ingest"]["days"][0]["final"]["missed"].append("p95 is 1.8s")
    for name, r in (("a", report), ("b", worse)):
        (tmp_path / name).mkdir()
        run.save(tmp_path / name, r)
    run.cmd_compare(argparse.Namespace(a=tmp_path / "a", b=tmp_path / "b"))
    printed = capsys.readouterr().out
    assert "2026-11-04: B missed ['p95 is 1.8s']" in printed
    assert "1.00 → 0.00  What was the original Atlas GA date?" in printed


def test_the_oracle_names_pages_as_the_vault_has_them(tmp_path):
    vault = make_vault(tmp_path / "v")
    (vault / "20_people/person-marta-ruiz.md").write_text("# Marta Ruiz\n")
    got = page_names(
        ["person-marta", "impact-2026", "30_decisions/decision-", "*", "payments"],
        vault,
    )
    assert got == ["person-marta-ruiz", "impact", "decision-log", "payments"]
    assert page_names(["*"], vault) == []


def test_the_oracle_answers_questions_from_the_labels():
    labels = load_labels()
    day = labels["days"]["2026-11-04"]
    fact_line = next(f["line"] for f in day["facts"] if "cache layer" in f["anchor"])
    chore_line = day["chore_lines"][0]
    questions = [
        {"id": "q1", "line": fact_line},
        {"id": "q2", "line": chore_line},
        {"id": "q3", "line": 12},  # a task line nobody labelled
    ]
    answers = oracle_answers("2026-11-04", questions, labels)
    assert answers["q1"].startswith("File it on decision-log")
    assert "chore" in answers["q2"] and answers["q3"].startswith("Leave it out")
    lines = note_text("2026-11-04").splitlines()
    s = score("2026-11-04", [], labels, lines, [{"line": fact_line}])
    assert s["asked"] == ["not to add a cache layer"] and s["found"] == 0
