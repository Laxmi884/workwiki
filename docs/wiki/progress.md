# Progress

Single source of truth for project status. Updated at the end of every session.

## Current focus

Option B of [[decisions/0006-agent-ingest-spike|0006]] adopted (2026-10-15): ingest by the `ingest_agent` deep agent with code-checked staging. Spike code (written by Claude, not committed) is being taught to the user lesson by lesson; then the 0006 to-dos, then phase 3 (3d apply, 3e SQLite + git) on top of the agent graph.

## Open decisions

- Which cloud model to use alongside the local default (mechanism settled in [[decisions/0003-model-selection|0003]]). `WIKI_MODEL` is now `openrouter:nvidia/nemotron-3-ultra-550b-a55b:free`, which extracts the fixture note correctly; `gemma4:12b` misses person notes.

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
- [x] **2. Ingest graph v1** (2026-10-13): extraction, routing, `FileDiff` reducer. Read: `tools/compute.py`, `tools/artifacts.py`. Design: [[decisions/0004-ingest-graph-design|0004]].
  - [x] 2a. Domain models: `SectionFacts` + fact types, `Edit` (`AppendId`, `AppendToSection`, `CreateFile`), `FileDiff` with digest (2026-10-12)
  - [x] 2b. `render_diffs`: apply edits per file, sha256, unified diff (2026-10-12)
  - [x] 2c. Code nodes: `load_note`, `assign_ids`, `track_tasks` (`90_meta/tasks.json`, mapping rules 1–3) (2026-10-12)
  - [x] 2d. `extract` with structured output; scripted fake model in tests; prompt tuned on fixture notes (2026-10-13)
  - [x] 2e. `route`, graph wiring (`Send` fan-out, merge-by-key reducer), Studio run on `~/WorkDev` (2026-10-13)
- [ ] **3. HITL and persistence**: `interrupt()`, `Command(resume=…)`, SQLite checkpointer, digests, git commit. Read: `docs/architecture/writes-and-approvals.md`, `tools/writes.py`.
  - [x] 3a. Ingest ledger `90_meta/ingest-ledger.json`: skip unchanged notes; edited notes give facts only from new lines (2026-10-13)
  - [x] 3b. `review` node with `interrupt()`, `ReviewRequest`/`ReviewAnswer`, `runner.py` (`start`/`resume`/`status`) without Studio ([[decisions/0005-surfaces-and-deployment|0005]]) (2026-10-14)
  - [x] 3c-1. Review loop: revise (feedback → re-extract, max 3), edit (user's full text for a
    wiki page), make page (decision gets its own page); each ends at a fresh review (2026-10-14)
  - [x] 3c-2 pair 1: wins + people questions (2026-10-14). Pair 2 dropped: 0006 adopted (2026-10-15).
  - [x] 0006 spike + round 2 built and run by Claude; option B adopted (2026-10-15)
  - [ ] 0006 teaching: user walks through the agent code (staging, agent, graph, skills). Lessons 1–2 given (2026-10-15)
  - [x] Learning notebooks 00–07 for all stages so far, git-ignored in `notebooks/` (2026-10-15)
  - [ ] Bugs from the real run: stale warnings (old rounds' refusals repeated in a revise review); an empty page left behind when all its facts are withdrawn (impact page after revise 2)
  - [ ] Sync trial: LiveSync + CouchDB + Tailscale on `~/WorkDev` (`docs/setup/livesync.md`)
  - [ ] 0006 to-dos: page list up front, no "first time" in text, new people as pages or questions, withdraw a proposed page; from the first real run: propose/ask instead of forcing a project, ad-hoc tasks get no page, task → project proposals, impact page = confirmed wins only, help given only on person/project pages (details in 0006)
  - [ ] 0006 cut-over: agent graph handles answer / make_page / edit, runner defaults to `ingest_agent`, then delete the 0004 model steps (extract, find_names, route, `ingest/graph.py`, `names.py`, round-1 script) and their tests From the first real note (2026-10-14): unknown names → people questions,
    new-project proposals for unlinked tasks (mapping rule 4), done tasks offered as win
    candidates, task sub-bullets read by the model, "remember this" → approved edit to
    aliases / `90_meta/rules.md`
  - [ ] 3d. `apply`: write approved files only if `before_sha256` still matches, read back. Built by Claude 2026-10-15 (`apply.py`, `apply` node in both graphs, notebook 08); awaiting the user's review
  - [ ] 3e. SQLite checkpointer; git commit of the vault
- [ ] **4. Assistant deep agent**: filesystem backend, permissions, skills, review subagent. Read: `runtime/local.py`, `workspace/skills/`.
- [ ] **5. Middleware**: current date, write guard, redaction, offload. Read: `middleware/`.
- [ ] **6. Tests and evals**: scripted model, sample notes with expected diffs. Read: `testing/scripted_model.py`, `tests/eval/`.
- [ ] **7. Morning brief, schedule, CLI**. Read: `surfaces/runner.py`, `schedules/`.
  - [ ] Daily-note rollover job (create-if-absent, carried tasks from index, 14-day stale cutoff), per 0001
- [ ] **8. Optional**: `langgraph up`, `langgraph deploy`.
