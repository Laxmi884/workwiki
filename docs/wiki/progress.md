# Progress

Single source of truth for project status. Updated at the end of every session.

## Current focus

Phase 2: ingest graph v1 (structured extraction, routing, `FileDiff` reducer).

## Open decisions

- Cloud model for development. Needed now (phase 2).
- How `Context.vault_path` and `Settings.vault_path` combine (proposal: Context optional, falls back to Settings).
- Where task → project mapping (0002 rules 1–5) and the task index live; needed by phase 2.

## Setup

- [x] Idea review and plan (2026-10-11)
- [x] Repo renamed to `llm-wiki`, Obsidian installed (2026-10-11)
- [x] Vault created at `~/Work`, folder layout, `git init`, initial commit (2026-10-11)
- [x] Project wiki skeleton in `docs/wiki/` (2026-10-11)
- [x] Obsidian settings: Daily notes → `00_raw/daily`, Templates → `50_templates`, attachments → `00_raw/attachments` (2026-10-11)
- [x] Decide task ID format: [[decisions/0001-task-ids|0001]] (2026-10-11)
- [x] Daily note format: [[decisions/0002-daily-note-format|0002]]; `50_templates/daily.md` committed to vault (2026-10-11)
- [x] Obsidian: Settings → Daily notes → Template file location → `50_templates/daily` (2026-10-11)

## Phases

- [x] **0. Scaffold** (2026-10-11): `langgraph new`, `langgraph dev`, open Studio. Read: `AGENTS.md`, `assembly.py`.
  - [x] `git init` the code repo, first commit (2026-10-11)
  - [x] Scaffold with `new-langgraph-project-python`, `uv sync` (2026-10-11)
  - [x] Fix package rename (`src/llm_wiki`, pyproject, imports, `langgraph.json`), upgrade `langgraph-api` (2026-10-11)
  - [x] `uv run langgraph dev`, run from Studio with context; tracing on; saved an assistant (2026-10-11)
  - [x] Can explain: `langgraph.json` → `graphs`, `State` vs `Context`, partial state updates (2026-10-11)
- [x] **1. Domain models and vault I/O** (no LLM) (2026-10-12). Read: `domain/`, `config.py`.
  - [x] 1a. `Task` model + `tasks_syntax` (parse task line, new ID, append ID), unit tests (2026-10-12)
  - [x] 1b. Frontmatter + daily-note parser per 0002 (sections, tags, tasks with section/parent context) (2026-10-12)
  - [x] Dev vault `~/WorkDev` (invented data, own git repo) for manual runs (2026-10-12)
  - [x] 1c. Entity model + index with aliases, slug → path resolution with allowed-folder check (2026-10-12)
  - [x] 1d. Fixture vault in `tests/fixtures/vault/` (invented data); parser tests run against it (2026-10-12)
  - [x] 1e. Settings (`WIKI_VAULT_PATH`, `task_ids`) per paid-media `config.py` (2026-10-12)
- [ ] **2. Ingest graph v1**: extraction, routing, `FileDiff` reducer. Read: `tools/compute.py`, `tools/artifacts.py`.
- [ ] **3. HITL and persistence**: `interrupt()`, `Command(resume=…)`, SQLite checkpointer, digests, git commit. Read: `docs/architecture/writes-and-approvals.md`, `tools/writes.py`.
- [ ] **4. Assistant deep agent**: filesystem backend, permissions, skills, review subagent. Read: `runtime/local.py`, `workspace/skills/`.
- [ ] **5. Middleware**: current date, write guard, redaction, offload. Read: `middleware/`.
- [ ] **6. Tests and evals**: scripted model, sample notes with expected diffs. Read: `testing/scripted_model.py`, `tests/eval/`.
- [ ] **7. Morning brief, schedule, CLI**. Read: `surfaces/runner.py`, `schedules/`.
  - [ ] Daily-note rollover job (create-if-absent, carried tasks from index, 14-day stale cutoff), per 0001
- [ ] **8. Optional**: `langgraph up`, `langgraph deploy`.
