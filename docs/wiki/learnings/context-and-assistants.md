---
phase: 0
date: 2026-10-11
tags: [langgraph, context, assistants, studio]
---

# Context and assistants

## What it is

**Context** is per-run configuration declared with `StateGraph(State, context_schema=Context)` and read
in nodes via `runtime.context`. The caller sets it before the run; nodes can't change it, and it is not
checkpointed with the thread. **State** is the data the run works on: nodes read it and return updates.

An **assistant** is the graph plus a saved, named set of context values. Same code, different settings.
Each graph in `langgraph.json` gets a default assistant; Studio's **Manage Assistants** → **Create New
Assistant** saves another one, selectable from Studio, the API (`assistant_id`) or a schedule.

## How we used it

`src/llm_wiki/graph.py`: `Context` has `vault_path` and `model`. In Studio, Graph mode →
**Manage Assistants** (bottom-left of the Input panel) shows one field per `Context` key.

Planned for llm-wiki: a **dev** assistant (`vault_path` = `~/WorkDev`, cheap model, fixed `today`,
`dry_run`) and a **real** one (`~/Work`, main model). Switching assistants moves from safe testing to
live use without code changes; the phase-7 morning schedule targets **real**. pytest never uses an
assistant: it passes `tests/fixtures/vault` directly.

From code: `graph.invoke({"user_query": "hi"}, context={"vault_path": "...", "model": "..."})`.

**Settings vs Context (1e, 2026-10-12).** `src/llm_wiki/config.py` `Settings` (pydantic-settings, prefix
`WIKI_`) holds process defaults from the environment / `.env`: `WIKI_VAULT_PATH` (required, `~`
expanded, must exist; `.env` points at `~/WorkDev`) and `WIKI_TASK_IDS` (only `inline`). Context is the
per-run layer on top. Open for phase 2: make `Context.vault_path` optional and fall back to `Settings`.

## Paid-media reference

`src/paid_media_agent/config.py:108` `Settings(BaseSettings)`: env + `.env`, `extra="ignore"`, typed
fields with bounds. `assembly.py` builds the agent from one `Settings` instance.

## Gotchas

- Put in context what a node must never change mid-run (vault path, model, dry-run). Putting
  `vault_path` in State would checkpoint it every step and let any node overwrite it.
- Studio builds the assistant form from the `Context` schema, so renaming a key changes the form;
  existing saved assistants keep the old keys.
- A required setting with no default is a safety feature: a missing `WIKI_VAULT_PATH` fails at startup
  instead of silently pointing at some default vault.
- pydantic-settings does not expand `~`; the validator does (`expanduser().resolve()`).
- In tests pass `_env_file=None` and clear `WIKI_*` env vars, or the developer's `.env` leaks in.

## In my words

- Context is for settings specific to a run (user preferences etc.), separate from the graph's state.
