import importlib

import pydantic
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from llm_wiki.config import Settings
from llm_wiki.models import (
    ModelSpec,
    choose_model_spec,
    make_chat_model,
    timeout_kwargs,
    without_sdk_retries,
)

# The module, not the compiled `graph` that llm_wiki/__init__.py re-exports.
graph_module = importlib.import_module("llm_wiki.graph")

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


# --- graph: context overrides WIKI_MODEL ----------------------------------------


@pytest.fixture
def fake_graph(monkeypatch, tmp_path):
    """Graph with WIKI_MODEL=ollama:default and a fake model that records the spec."""
    built = []

    def fake_make(spec):
        built.append(spec.spec)
        return GenericFakeChatModel(messages=iter([AIMessage("hello")]))

    settings = Settings(_env_file=None, vault_path=tmp_path, model="ollama:default")
    monkeypatch.setattr(graph_module, "get_settings", lambda: settings)
    monkeypatch.setattr(graph_module, "make_chat_model", fake_make)
    return graph_module.graph, built


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (None, "ollama:default"),  # local default from WIKI_MODEL
        ({"model": "claude:claude-haiku-5-5"}, "anthropic:claude-haiku-5-5"),
        ({"model": "openrouter:openai/gpt-5-mini"}, "openrouter:openai/gpt-5-mini"),
    ],
)
async def test_graph_uses_context_model(fake_graph, context, expected):
    graph, built = fake_graph
    out = await graph.ainvoke({"user_query": "hi"}, context=context)
    assert (out["answer"], out["model_used"], built) == ("hello", expected, [expected])


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
