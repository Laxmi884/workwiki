import shutil
from pathlib import Path

import pytest

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"


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
