# Progress

Single source of truth for project status. Updated at the end of every session.

## Current focus

Working mode (CLAUDE.md, since 2026-10-15): Claude builds in reviewed steps, each with a git-ignored notebook in `notebooks/` (00–11 exist; `notebooks/_src/nbNN.py` builds them, `_src/run.sh` executes; an answer key lives outside the repo). Ingest = the `ingest_agent` graph ([[decisions/0006-agent-ingest-spike|0006]] option B); phase 3 is built (3d apply, 3e SQLite runner + vault git commit) and the 0006 agent-quality fixes are in; all three await the user's review (notebooks 08–10).

**0007 committed (`00551c0`), timeout + task-named person pages committed (`aee8200`), 2026-10-16.** Notebook 11 built (4 exercises, answer-key verified): the user works through it, then Claude reviews the answers. Model now `deepseek/deepseek-v4.1-flash` (~$0.001/run; spend $2.47 of $2.50). Remaining 0006 to-do: cut-over (agent graph default, delete the 0004 model steps).

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
  - [x] Bugs from the real run: stale warnings; empty page left after withdrawals (2026-10-15)
  - [ ] Sync trial: LiveSync + CouchDB + Tailscale on `~/WorkDev` (`docs/setup/livesync.md`)
  - [x] 0006 agent-quality fixes (Claude, 2026-10-15; awaiting review; notebook 10): name-or-ask for person/project pages, wins = `#win` + review picks, help given on person pages, empty pages dropped, same-turn pages, headings citable for names, per-round warnings, no "first time" (details in 0006)
  - [x] Task → project proposals designed: [[decisions/0007-task-project-proposals|0007]] (2026-10-16)
  - [x] 0007 task → project proposals built and committed (`00551c0`, 2026-10-16)
  - [x] Timeout + task-named person pages committed (`aee8200`, 2026-10-16)
  - [x] Notebook 11 for 0007 built, answer-key verified (2026-10-16); user exercises pending
  - [ ] 0006 cut-over: agent graph handles answer / make_page / edit, runner defaults to `ingest_agent`, then delete the 0004 model steps (extract, find_names, route, `ingest/graph.py`, `names.py`, round-1 script) and their tests From the first real note (2026-10-14): unknown names → people questions,
    new-project proposals for unlinked tasks (mapping rule 4), done tasks offered as win
    candidates, task sub-bullets read by the model, "remember this" → approved edit to
    aliases / `90_meta/rules.md`
  - [ ] 3d. `apply`: write approved files only if `before_sha256` still matches, read back. Built by Claude 2026-10-15 (`apply.py`, `apply` node in both graphs, notebook 08); awaiting the user's review
  - [ ] 3e. SQLite checkpointer; git commit of the vault. Built by Claude 2026-10-15 (`IngestRunner.sqlite`, `vault_git.py`, notebook 09); awaiting the user's review
- [ ] **4. Assistant deep agent**: filesystem backend, permissions, skills, review subagent. Read: `runtime/local.py`, `workspace/skills/`.
- [ ] **5. Middleware**: current date, write guard, redaction, offload. Read: `middleware/`.
- [ ] **6. Tests and evals**: scripted model, sample notes with expected diffs. Read: `testing/scripted_model.py`, `tests/eval/`.
- [ ] **7. Morning brief, schedule, CLI**. Read: `surfaces/runner.py`, `schedules/`.
  - [ ] Daily-note rollover job (create-if-absent, carried tasks from index, 14-day stale cutoff), per 0001
- [ ] **8. Optional**: `langgraph up`, `langgraph deploy`.
