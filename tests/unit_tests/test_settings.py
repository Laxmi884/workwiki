import pydantic
import pytest

from llm_wiki.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("WIKI_VAULT_PATH", "WIKI_TASK_IDS"):
        monkeypatch.delenv(name, raising=False)


def test_reads_vault_path_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_VAULT_PATH", str(tmp_path))
    settings = Settings(_env_file=None)
    assert (settings.vault_path, settings.task_ids) == (tmp_path.resolve(), "inline")


def test_reads_dotenv_file(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(f"WIKI_VAULT_PATH={vault}\nOTHER_KEY=ignored\n")
    assert Settings(_env_file=env_file).vault_path == vault.resolve()


def test_expands_home(monkeypatch, tmp_path):
    (tmp_path / "WorkDev").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = Settings(_env_file=None, vault_path="~/WorkDev")
    assert settings.vault_path == (tmp_path / "WorkDev").resolve()


def test_vault_path_is_required():
    with pytest.raises(pydantic.ValidationError, match="vault_path"):
        Settings(_env_file=None)


def test_vault_must_exist(tmp_path):
    with pytest.raises(pydantic.ValidationError, match="does not exist"):
        Settings(_env_file=None, vault_path=tmp_path / "missing")


def test_sidecar_task_ids_not_built_yet(tmp_path):
    with pytest.raises(pydantic.ValidationError, match="task_ids"):
        Settings(_env_file=None, vault_path=tmp_path, task_ids="sidecar")


def test_frozen(tmp_path):
    settings = Settings(_env_file=None, vault_path=tmp_path)
    with pytest.raises(pydantic.ValidationError):
        settings.task_ids = "inline"


def test_fallback_models_from_comma_list(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "WIKI_FALLBACK_MODELS", " openrouter:a/b:free, ,claude:claude-haiku-5-5 "
    )
    settings = Settings(_env_file=None, vault_path=tmp_path)
    assert settings.fallback_models == (
        "openrouter:a/b:free",
        "anthropic:claude-haiku-5-5",
    )


def test_fallback_models_default_empty_and_checked(monkeypatch, tmp_path):
    monkeypatch.delenv("WIKI_FALLBACK_MODELS", raising=False)
    assert Settings(_env_file=None, vault_path=tmp_path).fallback_models == ()
    monkeypatch.setenv("WIKI_FALLBACK_MODELS", "no-provider")
    with pytest.raises(pydantic.ValidationError, match="provider:model"):
        Settings(_env_file=None, vault_path=tmp_path)
