import os

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


def test_state_dir_default_and_outside_the_vault(monkeypatch, tmp_path):
    monkeypatch.delenv("WIKI_STATE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(_env_file=None, vault_path=vault)
    assert settings.state_dir == tmp_path / ".llm-wiki"
    with pytest.raises(pydantic.ValidationError, match="outside the vault"):
        Settings(_env_file=None, vault_path=vault, state_dir=vault / ".state")


# `.env` for the commands we start ourselves: `langgraph dev` loads it ("env" in
# langgraph.json); `python -m llm_wiki.review_web` and `llm_wiki.ask` didn't, so the
# provider SDKs found no API key (the first real run, 2026-10-16).


def test_load_env_file_sets_missing_keys_and_keeps_existing(monkeypatch, tmp_path):
    from llm_wiki.config import load_env_file

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("WIKI_MODEL", "from-the-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=sk-test\nWIKI_MODEL=from-the-file\n")
    assert load_env_file(env_file) is True
    assert os.environ["OPENROUTER_API_KEY"] == "sk-test"
    assert os.environ["WIKI_MODEL"] == "from-the-shell"  # the shell wins
    assert load_env_file(tmp_path / "missing.env") is False


def test_review_page_and_ask_commands_load_dotenv(monkeypatch, tmp_path):
    import uvicorn

    from llm_wiki import ask
    from llm_wiki.review_web import __main__ as review_main

    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-test\n")
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(
        uvicorn, "run", lambda *a, **k: seen.append(os.getenv("OPENROUTER_API_KEY"))
    )
    monkeypatch.setattr(
        ask, "ask", lambda *a, **k: seen.append(os.getenv("OPENROUTER_API_KEY")) or ""
    )
    monkeypatch.setattr("sys.argv", ["review_web"])
    for command in (review_main.main, lambda: ask.main(["What's open?"])):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        command()
    assert seen == ["sk-test", "sk-test"]


def test_reasoning_effort_default_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("WIKI_REASONING_EFFORT", raising=False)
    assert Settings(_env_file=None, vault_path=tmp_path).reasoning_effort is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("low", "low"), (" Minimal ", "minimal"), ("none", "none"), ("", None)],
)
def test_reasoning_effort_from_env(monkeypatch, tmp_path, raw, expected):
    monkeypatch.setenv("WIKI_REASONING_EFFORT", raw)
    assert Settings(_env_file=None, vault_path=tmp_path).reasoning_effort == expected


def test_reasoning_effort_checked_at_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_REASONING_EFFORT", "lowest")
    with pytest.raises(pydantic.ValidationError, match="reasoning_effort"):
        Settings(_env_file=None, vault_path=tmp_path)


def test_a_run_may_name_only_the_vault_or_one_under_a_vault_root(tmp_path):
    from llm_wiki.config import VaultNotAllowed, resolve_vault

    vault, roots, other = tmp_path / "vault", tmp_path / "tests", tmp_path / "other"
    for d in (vault / "10_projects", roots / "copy", other):
        d.mkdir(parents=True)
    (roots / "escape").symlink_to(other)
    settings = Settings(_env_file=None, vault_path=vault, vault_roots=str(roots))
    assert resolve_vault(None, settings) == vault.resolve()
    assert resolve_vault(str(vault), settings) == vault.resolve()
    assert resolve_vault(roots / "copy", settings) == (roots / "copy").resolve()
    for bad in (other, vault / "10_projects", roots / "escape", "/", roots / "missing"):
        with pytest.raises(VaultNotAllowed):
            resolve_vault(bad, settings)
