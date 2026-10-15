"""Spike 0006 round 2: the corpus, the scoring and the scripted reviewer (no models)."""

from llm_wiki.ingest_agent.experiment import (
    load_corpus,
    make_vault,
    oracle_feedback,
    score,
    vault_checks,
)

DAY = "2026-11-24"


def test_corpus_lines_resolve_and_seed_vault_is_clean(tmp_path):
    labels, prefs = load_corpus()
    assert len(labels["days"]) == 20 and [p["id"] for p in prefs][0] == "P1"
    vault = make_vault(tmp_path / "v")
    assert not list((vault / "00_raw/daily").glob("*.md"))
    assert (vault / "10_projects/project-hiring-q4.md").exists()
    assert (vault / "90_meta/rules.md").exists()


def _hit(labels, anchor, path, section):
    line = next(
        f["line"] for f in labels["days"][DAY]["facts"] if f["anchor"] == anchor
    )
    return {"line": line, "path": path, "section": section, "text": "x"}


def test_score_counts_pages_prefs_and_misplaced():
    labels, _ = load_corpus()
    filed = [
        _hit(
            labels,
            "feedback on her first design doc",
            "20_people/person-marta-ruiz.md",
            "## 1:1 notes",
        ),
        _hit(
            labels, "happier with on-call", "20_people/person-marta-ruiz.md", "## Notes"
        ),
        _hit(
            labels,
            "happier with on-call",
            "40_impact/2026/impact-2026-q4.md",
            "## Wins",
        ),
        _hit(
            labels,
            "weekly syncs on Tuesdays",
            "10_projects/project-atlas.md",
            "## Updates",
        ),
    ]
    s = score(DAY, filed, labels, active={"one_on_one"})
    assert (s["found"], s["expected"]) == (3, 5)
    assert s["right_page"] == round(2 / 5, 3)  # Tuesdays went to Atlas, not payments
    assert s["prefs"]["one_on_one"] == {"ok": 1, "of": 2, "active": True}
    assert len(s["misplaced"]) == 2

    feedback = oracle_feedback(DAY, s, labels, {"one_on_one"}, ["x"] * 40)
    assert any("worth keeping" in f for f in feedback)
    assert any("belongs on payments" in f for f in feedback)
    assert any("rule about one on one" in f for f in feedback)


def test_vault_checks_current_state_and_candidate_pages(tmp_path):
    labels, _ = load_corpus()
    vault = make_vault(tmp_path / "v")
    (vault / "10_projects/project-payments-v2.md").write_text(
        "# Payments v2\n\n## Current state\n- Target: April\n"
    )
    (vault / "20_people/person-lena.md").write_text("# Lena\n")
    out = vault_checks(vault, DAY, labels, active={"state"})
    assert out["state"] == {"ok": 1, "of": 1, "active": True}
    assert out["candidate_pages"] == ["person-lena.md"]
