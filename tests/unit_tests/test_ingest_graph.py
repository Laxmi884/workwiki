"""The whole ingest graph on the fixture vault, with a fake model (nothing is written)."""

import importlib

import pytest

from llm_wiki.domain.facts import (
    Decision,
    Help,
    Learning,
    PersonNote,
    ProjectUpdate,
    SectionFacts,
    Win,
)
from llm_wiki.ingest import nodes

from .scripted_model import BySection

# `llm_wiki.ingest.graph` the module, not a re-exported attribute.
ingest_graph = importlib.import_module("llm_wiki.ingest.graph")

ANSWERS = {
    "Meetings": SectionFacts(
        decisions=[
            Decision(
                line=23, title="Keep the old BI tool until Q1", project="project-atlas"
            )
        ],
        project_updates=[
            ProjectUpdate(
                line=22, project="project-atlas", note="Beta moved to Oct 20."
            )
        ],
        person_notes=[
            PersonNote(line=29, person="person-sam-ortiz", note="Wants to own search.")
        ],
    ),
    "Log": SectionFacts(
        wins=[Win(line=34, text="Found the login redirect root cause")]
    ),
    "Helped": SectionFacts(
        helps=[Help(line=43, person="person-priya-nair", what="CI cache", minutes=30)]
    ),
    "Learned": SectionFacts(
        learnings=[
            Learning(line=47, topic="Cookie Domain attribute", summary="Must match."),
            Learning(line=48, topic="concept-feature-flags", summary="Need expiry."),
        ]
    ),
}


def _run(vault, model, monkeypatch, note="00_raw/daily/2026-10-12.md"):
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: model)
    return ingest_graph.graph.invoke(
        {"note_path": note}, context={"vault_path": str(vault)}
    )


def test_note_to_diffs_end_to_end(fixture_vault, monkeypatch):
    model = BySection(ANSWERS, default=SectionFacts())
    state = _run(fixture_vault, model, monkeypatch)

    assert len(model.calls) == 4  # Meetings, Log, Helped, Learned, in parallel
    assert set(state["facts"]) == {"20", "32", "42", "46"}
    assert state["warnings"] == []
    assert sorted(d.path for d in state["diffs"]) == [
        "00_raw/daily/2026-10-12.md",  # task IDs
        "05_generated/inbox.md",  # the cookie learning: no concept page
        "10_projects/project-atlas.md",
        "15_concepts/concept-feature-flags.md",
        "20_people/person-priya-nair.md",
        "20_people/person-sam-ortiz.md",
        "30_decisions/2026/decisions-2026-q4.md",
        "40_impact/2026/impact-2026-q4.md",
        "90_meta/tasks.json",
    ]


def test_note_without_prose_skips_the_model(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "00_raw/daily").mkdir(parents=True)
    (vault / "00_raw/daily/2026-10-15.md").write_text(
        "---\ndate: 2026-10-15\n---\n## Tasks\n- [ ] only a task\n"
    )

    def no_model(runtime):
        pytest.fail("no prose, so no model call")

    monkeypatch.setattr(nodes, "chat_model", no_model)
    state = ingest_graph.graph.invoke(
        {"note_path": "00_raw/daily/2026-10-15.md"},
        context={"vault_path": str(vault)},
    )
    assert state.get("facts", {}) == {}
    assert [d.path for d in state["diffs"]] == [
        "00_raw/daily/2026-10-15.md",
        "90_meta/tasks.json",
    ]
