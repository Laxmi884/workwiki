import pydantic
import pytest

from llm_wiki import model_setup
from llm_wiki.config import Settings
from llm_wiki.models import (
    ModelSpec,
    choose_model_spec,
    make_chat_model,
    reasoning_kwargs,
    timeout_kwargs,
    without_sdk_retries,
)

# --- parsing -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "provider", "model"),
    [
        ("anthropic:claude-haiku-5-5", "anthropic", "claude-haiku-5-5"),
        ("claude:claude-haiku-5-5", "anthropic", "claude-haiku-5-5"),  # alias
        ("ollama:llama3.1:8b", "ollama", "llama3.1:8b"),  # ":" in the model
        (
            "openrouter:meta-llama/llama-3.3-70b-instruct:free",
            "openrouter",
            "meta-llama/llama-3.3-70b-instruct:free",
        ),
        (" Ollama : qwen3 ", "ollama", "qwen3"),  # spacing, case
        ("gemini:gemini-3-flash", "google_genai", "gemini-3-flash"),
    ],
)
def test_parse(spec, provider, model):
    parsed = ModelSpec.parse(spec)
    assert (parsed.provider, parsed.model) == (provider, model)
    assert parsed.spec == f"{provider}:{model}"


@pytest.mark.parametrize("spec", ["", "claude-haiku-5-5", ":llama3", "ollama:", "  "])
def test_parse_rejects(spec):
    with pytest.raises(ValueError, match="provider:model"):
        ModelSpec.parse(spec)


# --- choosing ------------------------------------------------------------------


def test_override_wins():
    assert choose_model_spec("ollama:qwen3", "anthropic:x").spec == "ollama:qwen3"


@pytest.mark.parametrize("override", [None, "", "   "])
def test_falls_back_to_default(override):
    assert choose_model_spec(override, "anthropic:x").spec == "anthropic:x"


def test_nothing_chosen():
    with pytest.raises(ValueError, match="no model chosen"):
        choose_model_spec(None, None)


# --- building (offline: constructing a client makes no request) -----------------


@pytest.mark.parametrize(
    ("spec", "class_name", "key_env"),
    [
        ("anthropic:claude-haiku-5-5", "ChatAnthropic", "ANTHROPIC_API_KEY"),
        ("ollama:llama3.1:8b", "ChatOllama", None),
        ("openrouter:openai/gpt-5-mini", "ChatOpenRouter", "OPENROUTER_API_KEY"),
    ],
)
def test_make_chat_model(monkeypatch, spec, class_name, key_env):
    if key_env:
        monkeypatch.setenv(key_env, "test-key-not-real")
    chat = make_chat_model(ModelSpec.parse(spec))
    assert type(chat).__name__ == class_name


# --- settings ------------------------------------------------------------------


def test_settings_model_is_normalised(tmp_path):
    settings = Settings(_env_file=None, vault_path=tmp_path, model="Claude:x")
    assert settings.model == "anthropic:x"


def test_settings_bad_model_fails_at_startup(tmp_path):
    with pytest.raises(pydantic.ValidationError, match="provider:model"):
        Settings(_env_file=None, vault_path=tmp_path, model="gpt-5")


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openrouter", {"timeout": 180000}),  # milliseconds
        ("anthropic", {"timeout": 180.0}),
        ("ollama", {}),  # local: never cut off
    ],
)
def test_timeout_kwargs(provider, expected):
    assert timeout_kwargs(provider, 180.0) == expected
    assert timeout_kwargs(provider, None) == {}


def test_openrouter_model_gets_the_timeout_and_no_sdk_retries(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    spec = ModelSpec.parse("openrouter:openai/gpt-5-mini")
    chat = make_chat_model(spec, **timeout_kwargs(spec.provider, 2.5))
    assert chat.request_timeout == 2500
    assert without_sdk_retries(chat) is chat
    assert chat.client.sdk_configuration.retry_config.strategy == "none"


# --- reasoning effort (WIKI_REASONING_EFFORT) ------------------------------------


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openrouter", {"reasoning": {"effort": "low"}}),
        ("anthropic", {}),  # its effort field also turns thinking on: not mapped
        ("ollama", {}),  # local models already run with reasoning off
    ],
)
def test_reasoning_kwargs(provider, expected):
    assert reasoning_kwargs(provider, "low") == expected
    assert reasoning_kwargs(provider, None) == {}


def test_openrouter_request_carries_the_effort(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    chat = model_setup.build_model(
        ModelSpec.parse("openrouter:deepseek/deepseek-v4.1-flash"),
        reasoning_effort="low",
    )
    assert chat.reasoning == {"effort": "low"}
    # The params ChatOpenRouter passes to `client.chat.send` (built offline).
    assert chat._default_params["reasoning"] == {"effort": "low"}


def test_no_effort_adds_nothing(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    chat = model_setup.build_model(ModelSpec.parse("openrouter:openai/gpt-5-mini"))
    assert chat.reasoning is None
    assert "reasoning" not in chat._default_params


def test_other_providers_ignore_the_effort(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    claude = model_setup.build_model(
        ModelSpec.parse("anthropic:claude-haiku-5-5"), reasoning_effort="low"
    )
    assert claude.reasoning_effort is None
    assert claude.thinking is None
    local = model_setup.build_model(
        ModelSpec.parse("ollama:llama3.1:8b"), reasoning_effort="high"
    )
    assert local.reasoning is False  # unchanged: no hidden thinking locally


def test_configured_models_use_the_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    (tmp_path / "vault").mkdir()
    settings = Settings(
        _env_file=None,
        vault_path=tmp_path / "vault",
        state_dir=tmp_path / "state",
        model="openrouter:deepseek/deepseek-v4.1-flash",
        fallback_models=("openrouter:openai/gpt-5-mini", "ollama:qwen3"),
        reasoning_effort="minimal",
    )
    monkeypatch.setattr(model_setup, "get_settings", lambda: settings)
    main = model_setup.configured_model()
    remote, local = model_setup.configured_fallbacks()
    assert main.reasoning == remote.reasoning == {"effort": "minimal"}
    assert local.reasoning is False
