"""3e: each applied review is one vault commit holding only the files apply wrote."""

import subprocess

import pytest

from llm_wiki.ingest import nodes
from llm_wiki.runner import IngestRunner
from llm_wiki.vault_git import commit_paths

from .scripted_model import AgentRounds, call

NOTE = "00_raw/daily/2026-10-12.md"


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    """Isolate git from the developer's config; give commits an identity."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def make_repo(path):
    git(path, "init", "-q")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "start")
    return path


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "a.md").write_text("a\n")
    (tmp_path / "mine.md").write_text("mine\n")
    return make_repo(tmp_path)


def test_commits_only_the_given_paths(repo):
    (repo / "a.md").write_text("a2\n")
    (repo / "new.md").write_text("n\n")
    (repo / "mine.md").write_text("my own unsaved edit\n")
    sha, warnings = commit_paths(repo, ["a.md", "new.md"], "ingest 2026-10-14: 2 files")
    assert warnings == [] and sha == git(repo, "rev-parse", "HEAD").strip()
    assert git(repo, "show", "--name-only", "--format=%s", "HEAD").split() == [
        "ingest",
        "2026-10-14:",
        "2",
        "files",
        "a.md",
        "new.md",
    ]
    assert "mine.md" in git(repo, "status", "--porcelain"), "user's edit stays out"


def test_user_staged_changes_stay_staged_and_uncommitted(repo):
    (repo / "mine.md").write_text("staged by me\n")
    git(repo, "add", "mine.md")
    (repo / "a.md").write_text("a2\n")
    commit_paths(repo, ["a.md"], "ingest")
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["a.md"]
    assert git(repo, "diff", "--cached", "--name-only").split() == ["mine.md"]


def test_nothing_new_makes_no_commit(repo):
    head = git(repo, "rev-parse", "HEAD")
    assert commit_paths(repo, ["a.md"], "ingest") == (None, [])
    assert git(repo, "rev-parse", "HEAD") == head


def test_not_a_repo_or_inside_another_repo_only_warns(tmp_path):
    (tmp_path / "a.md").write_text("a\n")
    sha, warnings = commit_paths(tmp_path, ["a.md"], "x")
    assert sha is None and "not a git repository" in warnings[0]
    outer = make_repo(tmp_path)
    inner = outer / "vault"
    inner.mkdir()
    (inner / "b.md").write_text("b\n")
    sha, warnings = commit_paths(inner, ["b.md"], "x")
    assert sha is None and "inside another git repository" in warnings[0]


@pytest.fixture
def model(monkeypatch):
    decision = call(
        "file_fact",
        target="decision-log",
        section="Decisions",
        text="Not recorded in the note.",
        line=23,
        title="Keep BI",
    )
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([decision]))


def test_approve_commits_the_applied_files(vault_copy, model):
    make_repo(vault_copy)
    runner = IngestRunner.in_memory(context={"vault_path": str(vault_copy)})
    done = runner.resume(runner.start(NOTE).thread_id, "approve")
    assert done.commit == git(vault_copy, "rev-parse", "HEAD").strip()
    subject, *files = git(
        vault_copy, "show", "--name-only", "--format=%s", "HEAD"
    ).split("\n")
    assert subject == f"ingest 2026-10-12: {len(done.written)} files"
    assert sorted(f for f in files if f) == sorted(done.written)
    assert git(vault_copy, "status", "--porcelain") == ""


def test_sqlite_runner_resumes_after_a_restart(vault_copy, model, tmp_path):
    db = tmp_path / "state" / "checkpoints.sqlite"
    context = {"vault_path": str(vault_copy)}
    started = IngestRunner.sqlite(db, context).start(NOTE)
    assert started.status == "review"

    restarted = IngestRunner.sqlite(db, context)  # a new process, same file
    assert restarted.status(started.thread_id).status == "review"
    done = restarted.resume(started.thread_id, "approve")
    assert done.status == "done" and done.written
