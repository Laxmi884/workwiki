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

Planned for llm-wiki: a **dev** assistant (`vault_path` = `tests/fixtures/vault`, cheap model, fixed
`today`, `dry_run`) and a **real** one (`~/Work`, main model). Switching assistants moves from safe
testing to live use without code changes; the phase-7 morning schedule targets **real**.

From code: `graph.invoke({"user_query": "hi"}, context={"vault_path": "...", "model": "..."})`.

## Paid-media reference

Read alongside phase 1: paid-media's `config.py` (how it loads settings) and `assembly.py` (how settings
reach the graph). Compare with passing them as LangGraph context.

## Gotchas

- Put in context what a node must never change mid-run (vault path, model, dry-run). Putting
  `vault_path` in State would checkpoint it every step and let any node overwrite it.
- Studio builds the assistant form from the `Context` schema, so renaming a key changes the form;
  existing saved assistants keep the old keys.

## In my words

- Context is for settings specific to a run (user preferences etc.), separate from the graph's state.
