"""C1/I1 (whole-project review 2026-10-18): a run's inputs come from whoever calls the
Agent Server, so the graphs check them: the vault must be one this process serves, and
ingest reads only daily notes."""

import tempfile
from pathlib import Path

import pytest

from llm_wiki.assistant.graph import run_choice
from llm_wiki.backend import NoteNotFound
from llm_wiki.backend.local import LocalBackend
from llm_wiki.config import VaultNotAllowed
from llm_wiki.ingest import nodes
from llm_wiki.rollover_graph import graph as rollover
from llm_wiki.runner import IngestRunner

from .scripted_model import AgentRounds


@pytest.fixture
def elsewhere():
    """A folder outside every allowed vault root (not under pytest's temp folder)."""
    with tempfile.TemporaryDirectory(prefix="not-a-vault-") as d:
        yield Path(d)


def test_ingest_refuses_a_vault_this_process_does_not_serve(elsewhere, monkeypatch):
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([]))
    runner = IngestRunner.in_memory(context={"vault_path": str(elsewhere)})
    with pytest.raises(VaultNotAllowed):
        runner.start("00_raw/daily/2026-10-12.md")


def test_the_assistant_and_rollover_refuse_it_too(elsewhere):
    with pytest.raises(VaultNotAllowed):
        run_choice({"vault_path": str(elsewhere)})
    with pytest.raises(VaultNotAllowed):
        rollover.invoke({"date": "2026-10-19"}, context={"vault_path": str(elsewhere)})
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize(
    "path",
    [
        "10_projects/project-atlas.md",
        "00_raw/daily/../../10_projects/project-atlas.md",
        "/etc/hosts",
        "00_raw/daily/2026-10-12.txt",
    ],
)
def test_ingest_reads_only_daily_notes(vault_copy, monkeypatch, path):
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([]))
    page = vault_copy / "10_projects/project-atlas.md"
    before = page.read_text()
    backend = LocalBackend.in_memory(vault_copy)
    with pytest.raises(NoteNotFound):
        backend.start_ingest(path)
    assert page.read_text() == before and backend.pending() == []
