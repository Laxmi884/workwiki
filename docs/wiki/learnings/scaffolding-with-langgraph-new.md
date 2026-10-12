---
phase: 0
date: 2026-10-11
tags: [langgraph-cli, uv, scaffolding]
---

# Scaffolding with `langgraph new`

## What it is

`langgraph new` downloads a starter project (a template repo from GitHub, as a zip) and unpacks it. The
`new-langgraph-project-python` template gives a one-node `StateGraph`, a `langgraph.json` that tells the
CLI where the graph is, a `pyproject.toml`, tests and CI. It saves writing boilerplate; the graph logic is
ours to replace.

## How we used it

```bash
cd ~/Documents/Projects/llm-wiki
uvx --from "langgraph-cli[inmem]" langgraph new ./_scaffold --template new-langgraph-project-python
rsync -a ./_scaffold/ ./ && rm -rf ./_scaffold
```

- `uvx --from "langgraph-cli[inmem]" langgraph …`: run the `langgraph` command from a throwaway,
  cached environment without installing it. `--from` names the package that provides the command;
  `[inmem]` is the extra needed by `langgraph dev`. Avoids the global Anaconda `langgraph`.
- `langgraph new ./_scaffold --template new-langgraph-project-python`: create the project in a new
  folder, skipping the interactive template menu.
- **Why a temporary folder:** `langgraph new` refuses any non-empty directory ("The specified directory
  already exists and is not empty. Aborting"). The repo root already had `CLAUDE.md`, `docs/` and the PDF.
- `rsync -a ./_scaffold/ ./`: copy the folder's *contents* into the root. `-a` keeps permissions and
  includes hidden files (`.gitignore`, `.env.example`), which `mv _scaffold/* .` would skip. The trailing
  `/` on the source means "contents of", not the folder itself.
- `rm -rf ./_scaffold`: delete the now-empty temp folder.

Then `uv sync` (creates `.venv/`, installs deps including the dev group with `langgraph-cli[inmem]`),
`cp .env.example .env`, and `uv run langgraph dev` (server on `127.0.0.1:2024`, opens Studio).

Generated layout: `langgraph.json`, `pyproject.toml`, `uv.lock`, `src/<pkg>/graph.py`,
`tests/unit_tests/`, `tests/integration_tests/`, `.env.example`, `.gitignore` (already ignores `.env`,
`.venv`, `.langgraph_api/`).

## Paid-media reference

`src/paid_media_agent/` uses the same `src/` layout. Distribution name `paid-media-agent` (hyphens),
import package `paid_media_agent` (underscores). It builds with `uv_build`, which finds
`src/paid_media_agent` by convention, so it needs no package mapping. It has no `langgraph.json`; its
graph is assembled in `assembly.py` and deployed through Deep Agents instead of the CLI.

## Gotchas

- `langgraph new` only works in an empty or new directory.
- **Package rename:** the project name may contain hyphens (`llm-wiki`), the import folder may not
  (`src/llm_wiki`). Renaming means changing four places: the folder, `[tool.setuptools]` in
  `pyproject.toml`, imports in `__init__.py` and tests, and the path in `langgraph.json`. Then `uv sync`.
- If `langgraph.json` points to a missing file, `langgraph dev` logs `GraphLoadError … No such file`
  and Studio shows no output. Restart `langgraph dev` after editing `langgraph.json`.
- Always start the server with `uv run langgraph dev`. Plain `langgraph dev` runs the global Anaconda
  CLI (langgraph-api 0.9.0), not the project's `.venv` (0.13.8); Studio's version banner shows which one.
- Context in Studio: Graph mode → **Manage Assistants** (bottom-left of the Input panel) → each `Context`
  key is a field (e.g. "My Configurable Param"). Edits apply to runs; **Create New Assistant** saves them.
- Studio needs Chrome (Safari blocks localhost), or `langgraph dev --tunnel`.
- "Upgrade your agent server for Studio tracing" means `langgraph-api` is too old (0.10.0 shipped with
  the template lock). Fix: `uv lock --upgrade-package langgraph-api --upgrade-package langgraph-cli
  --upgrade-package langgraph-runtime-inmem && uv sync`. Traces also need `LANGSMITH_API_KEY` in `.env`.
- "LANGSMITH_API_KEY is missing" even with the key set: an explicit `LANGSMITH_TRACING_V2=false` (or
  `LANGSMITH_TRACING=false`) in `.env` wins over the key and turns tracing off. Use `LANGSMITH_TRACING=true`
  and remove the `_V2` line; restart the server.

## In my words

- `langgraph.json` → `graphs` points to the compiled graph object the server loads.
- State: data every node reads. Context: per-run settings, not stored in state.
- Fields a node doesn't return stay unchanged in the thread's state.
