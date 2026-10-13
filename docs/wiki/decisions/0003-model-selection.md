---
id: 0003
date: 2026-10-12
status: accepted
---

# 0003: Model selection: any provider, set locally or per Studio assistant

## Context

The user wants to switch between local (Ollama) and cloud models (Anthropic, OpenRouter, others) without
code changes, both from the local environment and when creating assistants in LangSmith Studio.

## Options

- Hard-code one provider class (`ChatAnthropic`): simplest, no switching.
- `init_chat_model` with `configurable_fields` (model chosen via `config["configurable"]`): built in, but
  the choice lives in run config rather than the graph's typed `Context`, so Studio's assistant form
  does not show it.
- One `"provider:model"` string, parsed by our code, passed to `init_chat_model(model, model_provider=…)`;
  default from `WIKI_MODEL`, overridden per run by `Context.model`: chosen.

## Decision

- Spec string `provider:model`, split on the **first** `:` only (Ollama `llama3.1:8b` and OpenRouter
  `meta-llama/…:free` names contain `:` and `/`). Aliases: `claude` → `anthropic`, `google`/`gemini` →
  `google_genai`. Any `init_chat_model` provider works once `langchain-<provider>` is installed.
- Order: run context `model` (Studio assistant, `context=` on invoke) → `WIKI_MODEL` → error.
- API keys and hosts stay in each provider's own env vars (`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`,
  `OLLAMA_HOST`); our code never handles them.
- Dev default: `WIKI_MODEL=ollama:qwen3:4b` (local, free, private).
- Code: `src/llm_wiki/models.py` (`ModelSpec`, `choose_model_spec`, `make_chat_model`),
  `config.py` (`Settings.model`, validated at startup), `graph.py` (`Context.model`).

## Why

One typed string works the same in `.env`, in code and in Studio's assistant form, and validating it at
startup turns a typo into an immediate error instead of a failed run later.
