import os
import shutil
from pathlib import Path

import pytest

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"
TRACING_FLAGS = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2")


@pytest.fixture(autouse=True)
def _environment_restored_and_untraced():
    """Each test gets the environment the run started with, tracing off, and gives it back.

    Defined first, so it is set up before the other fixtures' `setenv`s and torn down
    after them. A CLI test's `load_env_file` copies the developer's `.env` into
    `os.environ`; without the restore it leaked into every later test. Tracing is off
    because tests send nothing to LangSmith, and because tracing serializes a scripted
    model's answer iterator, which uses it up (2026-10-25). Set to "false", not
    removed: `load_env_file` never overrides a variable that is already set.
    """
    saved = dict(os.environ)
    os.environ.update(dict.fromkeys(TRACING_FLAGS, "false"))
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def fixture_vault() -> Path:
    """Invented, frozen vault. Read only: tests that write must copy it to tmp_path."""
    return FIXTURE_VAULT


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="session", autouse=True)
def _fixture_vault_stays_frozen():
    """Fail the run if any test wrote to the fixture vault (apply writes since 3d)."""
    before = _snapshot(FIXTURE_VAULT)
    yield
    after = _snapshot(FIXTURE_VAULT)
    changed = sorted(
        k for k in before.keys() | after.keys() if before.get(k) != after.get(k)
    )
    assert not changed, f"tests changed the fixture vault: {changed}"


@pytest.fixture
def vault_copy(tmp_path) -> Path:
    """A writable copy of the fixture vault, for runs that approve (and so write)."""
    vault = tmp_path / "vault"
    shutil.copytree(FIXTURE_VAULT, vault)
    return vault


@pytest.fixture(autouse=True)
def _no_fallback_models(monkeypatch):
    """Tests never build the backup models from the developer's `.env`."""
    from llm_wiki.ingest import nodes

    monkeypatch.setattr(nodes, "fallback_models", lambda runtime: [])


@pytest.fixture(scope="session")
def _default_vault(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("default-vault")


@pytest.fixture(autouse=True)
def _vaults_tests_may_use(monkeypatch, tmp_path_factory, _default_vault):
    """Runs may name vault copies under pytest's temp folder or the fixture vault.

    `resolve_vault` refuses any other vault a run names. `WIKI_VAULT_PATH` is an empty
    temp folder, so no test falls back to the developer's `.env` vault.
    """
    from llm_wiki.config import get_settings

    monkeypatch.setenv("WIKI_VAULT_PATH", str(_default_vault))
    roots = f"{tmp_path_factory.getbasetemp()},{FIXTURE_VAULT}"
    monkeypatch.setenv("WIKI_VAULT_ROOTS", roots)
    monkeypatch.setenv(
        "WIKI_SERVER_TOKEN", "test-token"
    )  # what the contract servers want
    for name in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "WIKI_SLACK_USER_ID"):
        monkeypatch.delenv(
            name, raising=False
        )  # the developer's real values never reach a test
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
