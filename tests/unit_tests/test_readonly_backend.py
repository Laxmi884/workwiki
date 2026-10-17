"""Agents can't change the vault or their skills: every route is a read-only backend.

Permission globs skip dot paths (`/**` doesn't match `/vault/.trash/x.md`), so the
guarantee is the backend itself: every write method refuses every path.
"""

import asyncio

import pytest
from deepagents.backends import CompositeBackend

from llm_wiki.assistant import agent as assistant_agent
from llm_wiki.ingest import agent as ingest_agent
from llm_wiki.readonly_backend import ReadOnlyFilesystemBackend

from .scripted_model import ToolScript

AWKWARD = [
    "/new.md",
    "/10_projects/project-atlas.md",  # exists
    "/.gitignore",
    "/.trash/x.md",
    "/.a/.b/c.md",
    "/10_projects/.draft.md",
    "/../outside.md",
]


def _snapshot(root):
    return {p: p.read_bytes() for p in sorted(root.parent.rglob("*")) if p.is_file()}


def _writes(backend, path):
    """Every write a backend offers, sync and async; each returns its error or None."""
    yield backend.write(path, "x").error
    yield backend.edit(path, "Atlas", "pwned").error
    yield backend.delete(path).error
    yield backend.upload_files([(path, b"x")])[0].error
    yield asyncio.run(backend.awrite(path, "x")).error
    yield asyncio.run(backend.aedit(path, "Atlas", "pwned")).error
    yield asyncio.run(backend.adelete(path)).error
    yield asyncio.run(backend.aupload_files([(path, b"x")]))[0].error


@pytest.mark.parametrize("path", AWKWARD)
def test_every_write_is_refused_on_every_path(vault_copy, path):
    backend = ReadOnlyFilesystemBackend(root_dir=vault_copy, virtual_mode=True)
    before = _snapshot(vault_copy)
    errors = list(_writes(backend, path))
    assert all(errors), errors
    assert _snapshot(vault_copy) == before


def test_reads_still_work(vault_copy):
    backend = ReadOnlyFilesystemBackend(root_dir=vault_copy, virtual_mode=True)
    assert "Atlas" in str(backend.read("/10_projects/project-atlas.md"))


def _routes(monkeypatch, module, build):
    """Build the agent with `create_deep_agent` captured; return its backend's routes."""
    captured = {}
    monkeypatch.setattr(
        module, "create_deep_agent", lambda **kwargs: captured.update(kwargs)
    )
    build()
    backend = captured["backend"]
    assert isinstance(backend, CompositeBackend)
    return backend.routes


@pytest.mark.parametrize("which", ["ingest", "assistant"])
def test_both_agents_see_only_read_only_routes(monkeypatch, vault_copy, which):
    model = ToolScript(messages=iter([]))
    if which == "ingest":
        routes = _routes(
            monkeypatch,
            ingest_agent,
            lambda: ingest_agent.build_ingest_agent(vault_copy, model),
        )
    else:
        routes = _routes(
            monkeypatch,
            assistant_agent,
            lambda: assistant_agent.build_assistant(vault_copy, model),
        )
    assert "/vault/" in routes
    for prefix, backend in routes.items():
        assert isinstance(backend, ReadOnlyFilesystemBackend), prefix
        assert backend.write("/.trash/x.md", "x").error, prefix
