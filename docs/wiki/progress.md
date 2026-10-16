# Progress

Single source of truth for project status. Updated at the end of every session.

## Current focus

Working mode (CLAUDE.md, since 2026-10-15): Claude builds in reviewed steps, each with a git-ignored notebook in `notebooks/` (00–11 exist; `notebooks/_src/nbNN.py` builds them, `_src/run.sh` executes; answer keys live outside the repo, e.g. the session scratchpad). Commit only after the user approves.

**Where we are (2026-10-16, end of a long session):** phases 0–3 done; 0006 cut-over done (agent graph is the only ingest graph); 0007 task → project proposals done; phase 4 assistant (4a–4c) and the `wiki` chat front door (4d) committed; review page R1 committed (`9c0fee9`). All committed; 450 tests pass.

**Next: build decision [[decisions/0010-backends-local-and-server|0010]] in reviewed steps** (start in a fresh session):
1. ~~S1 `Backend` interface + `LocalBackend` + contract tests~~ done 2026-10-16 (`564c4ba`).
2. ~~S2 `ServerBackend` on `langgraph-sdk` + opt-in contract run against `langgraph dev`~~ done 2026-10-16 (`6e0e2f7`): `backend/server.py`, `get_backend()` (`WIKI_BACKEND`, `WIKI_SERVER_URL`), `tests/server/graphs.py` (real graphs, scripted models); `WIKI_SERVER_TESTS=1` runs all 10 contract tests on both backends (20 pass). Also fixed: the real `assistant` factory blocked the server's event loop (blockbuster regression test).
   - ~~Package cleanup before S3~~ done 2026-10-16 (user agreed): `ingest_agent/{graph,agent,staging}.py` + `skills/` moved into `ingest/` (one package: graph, agent, staging, nodes, state); the 0006 spike harness (`experiment.py`, `qa.py`) and `scripts/*` moved to a top-level `experiments/` package (`uv run python -m experiments.run_experiment …`); pytest `pythonpath = ["."]`; compiled graph name `ingest`. No behaviour change: 461 + 20 server contract tests pass, real `langgraph.json` loads all graphs.
3. S3 review page + a chat box on `Backend` ("/…" → ingest, else → assistant); delete `chat/graph.py` (`wiki` graph) and direct runner imports in `review_web/`.
4. S4 pass the node `config` into inner agents (`ingest/graph.py:148`, assistant calls) for nested streaming/cancel. Also here (found by notebook 13, 2026-10-16): the inner agent's state is checkpointed into the runner's store and holds `staging.Filed`, which the checkpoint allow-list blocks on load (`LocalBackend.pending` → `checkpointer.list(None)` logs "Blocked deserialization"; results unaffected, only `ts` is read); and `pending` loads every full checkpoint just to sort threads.
5. S5 target `langgraph.json` from 0010 (uv lockfile, Python 3.12, no `wiki`), upgrade `langgraph-api` (0.13.8 is EOL), `langgraph up` smoke test (vault volume, serde of our types; check that the image installs subpackages and `ingest/skills/*.md`: `pyproject` lists only `packages = ["llm_wiki"]` and no Markdown package data).
6. Then review page R2 (edit, task choices, wins, revise) and R3 (remember) on top of `Backend`.

**Notebooks:** 13 done 2026-10-16 (toy review graph on an in-process adapter and on `langgraph dev`, 3 exercises on the server adapter, then the real `LocalBackend` and `ServerBackend` side by side; answer key verified, kept outside the repo). "Where this lives now" notes added to 00, 02, 03, 05 and paid-media pointers to 10, 11 (2026-10-16; audit: every notebook has explanations, these six pointed at deleted code or had no paid-media link). To do: 12 (assistant + review-writer subagent). The user still has to work through 08–11 and 13.

Model: `openrouter:deepseek/deepseek-v4.1-flash` (~$0.001 per ingest, ~$0.002 per question); OpenRouter spend ≈ $2.47 of the $2.50 cap. Real-run check: scratch copy of `~/WorkDev`, `uv run --env-file .env`, `nodes.fallback_models = lambda r: []`.

## Open decisions

- Which cloud model to use alongside the local default (mechanism settled in [[decisions/0003-model-selection|0003]]). `WIKI_MODEL` is now `openrouter:deepseek/deepseek-v4.1-flash` (since 2026-10-16; the free Nemotron hung and returned provider errors); `gemma4:12b` misses person notes.

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
  - [x] 0006 cut-over (2026-10-16; step 1 `4fd1836`, steps 2+3 awaiting commit): the agent graph is the only ingest graph (runner default, `langgraph.json` `ingest`); review lost `make_page`, people questions and the inbox; deleted `ingest/graph.py`, extract/find_names/fan_out/route, `extraction.py` (line helpers → `note_lines.py`), `names.py`, `answers.py`, `domain/facts.py`, fact routing (`routing.py` keeps origins, paths, `win_edits`), `scripts/compare_ingest.py`; experiment harness agent-only. 415 tests pass.
    new-project proposals for unlinked tasks (mapping rule 4), done tasks offered as win
    candidates, task sub-bullets read by the model, "remember this" → approved edit to
    aliases / `90_meta/rules.md`
  - [ ] 3d. `apply`: write approved files only if `before_sha256` still matches, read back. Built by Claude 2026-10-15 (`apply.py`, `apply` node in both graphs, notebook 08); awaiting the user's review
  - [ ] 3e. SQLite checkpointer; git commit of the vault. Built by Claude 2026-10-15 (`IngestRunner.sqlite`, `vault_git.py`, notebook 09); awaiting the user's review
- [ ] **4. Assistant deep agent**: filesystem backend, permissions, skills, review subagent. Read: `runtime/local.py`, `workspace/skills/`. Design: [[decisions/0008-assistant-agent|0008]].
  - [x] 4a. Read-only assistant over `/vault/`, `find_tasks`, skills (vault-map, answering, brag-sheet), `rules.md` memory (Claude, 2026-10-16; awaiting review)
  - [x] 4b. `review-writer` subagent → sourced `BragDraft` (Claude, 2026-10-16; awaiting review)
  - [x] 4c. `assistant` graph factory in `langgraph.json` (template graph removed), `python -m llm_wiki.ask` (Claude, 2026-10-16; awaiting review)
  - [x] 4d. `wiki` chat front door: slash commands drive ingest via the SQLite runner, other messages go to the assistant; questions work while a review is open (Claude, 2026-10-16; awaiting review)
  - [ ] Notebook 12; real brag-sheet run once a vault has applied ingests
- [ ] **5. Middleware**: current date, write guard, redaction, offload. Read: `middleware/`.
- [ ] **6. Tests and evals**: scripted model, sample notes with expected diffs. Read: `testing/scripted_model.py`, `tests/eval/`.
- [ ] **7. Morning brief, schedule, CLI**. Read: `surfaces/runner.py`, `schedules/`.
  - [ ] Daily-note rollover job (create-if-absent, carried tasks from index, 14-day stale cutoff), per 0001
  - [x] Review page R1 (Claude, 2026-10-16; awaiting review): fact-centric page on the 2026-10-15 mockup's design (each fact beside its note line, whole-file Include, exact diff on demand, bookkeeping folded, phone layout), served by `langgraph dev` via `http.app` or `python -m llm_wiki.review_web`; reviews tied to their vault ([[decisions/0009-review-page-and-remember|0009]])
  - [ ] Review page R2: edit, task → project choices, win picks, revise box; R3: remember
  - [ ] Telegram/Slack adapters: summary + Approve all / Reject + link to the review page
- [ ] **8. Optional**: `langgraph up`, `langgraph deploy`. Hosting only (0005 addendum): vault + `WIKI_STATE_DIR` volumes, auth on the review page's custom routes, vault git clone synced with the Mac. The UI does not wait for this.
