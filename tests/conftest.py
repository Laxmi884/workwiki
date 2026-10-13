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
