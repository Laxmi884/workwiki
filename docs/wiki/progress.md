# Progress

Single source of truth for project status. Updated at the end of every session.

## Current focus

Working mode (CLAUDE.md, since 2026-10-15): Claude builds in reviewed steps, each with a git-ignored notebook in `notebooks/` (00–15 exist; `notebooks/_src/nbNN.py` builds them, `_src/run.sh` executes; answer keys live outside the repo, e.g. the session scratchpad). Commit only after the user approves.

**Where we are (2026-10-16):** decision 0010 S1–S4 committed; **S5 and its four fixes committed (`4f46948`)**: new `langgraph.json`, `langgraph-api` 0.16.1, packaging, `.dockerignore`; then [[decisions/0011-guarantees-not-conventions|0011]]: JSON-only state with strict serde, read-only backends, our own `compose.yml`, explicit git identity. Notebook audit: 02 and 03 reworked off the old pipeline, stale claims fixed in 04–06, 09, 12–15, every notebook runs. 520 tests pass; `WIKI_SERVER_TESTS=1` adds 10 on `langgraph dev`, `WIKI_SERVER_TESTS=docker` runs the 11 server contract tests on the production image (strict). Next: R2 (review page edit, task choices, wins, revise); the user works through notebooks 02–03 (reworked) and 08–15.

**Decision [[decisions/0010-backends-local-and-server|0010]], step by step:**
1. ~~S1 `Backend` interface + `LocalBackend` + contract tests~~ done 2026-10-16 (`564c4ba`).
2. ~~S2 `ServerBackend` on `langgraph-sdk` + opt-in contract run against `langgraph dev`~~ done 2026-10-16 (`6e0e2f7`): `backend/server.py`, `get_backend()` (`WIKI_BACKEND`, `WIKI_SERVER_URL`), `tests/server/graphs.py` (real graphs, scripted models); `WIKI_SERVER_TESTS=1` runs all 10 contract tests on both backends (20 pass). Also fixed: the real `assistant` factory blocked the server's event loop (blockbuster regression test).
   - ~~Package cleanup before S3~~ done 2026-10-16 (user agreed): `ingest_agent/{graph,agent,staging}.py` + `skills/` moved into `ingest/` (one package: graph, agent, staging, nodes, state); the 0006 spike harness (`experiment.py`, `qa.py`) and `scripts/*` moved to a top-level `experiments/` package (`uv run python -m experiments.run_experiment …`); pytest `pythonpath = ["."]`; compiled graph name `ingest`. No behaviour change: 461 + 20 server contract tests pass, real `langgraph.json` loads all graphs.
3. ~~S3 review page + a chat box on `Backend`~~ done 2026-10-16: `chat/session.py` (`reply(backend, message, history)`: "/…" → ingest commands on the vault's open review, else `backend.ask`; with 2+ open reviews it lists them instead of guessing); `/chat` page on the review web app (browser keeps the history); review page and index on `get_backend()` (index: open reviews only); `wiki` graph, `chat/graph.py` and `runner.shared_runner` deleted; `ServerBackend` turns HTTP failures into `BackendError` (page shows 502 JSON). Checked in Chrome on a scripted run: ingest → question while open → approve; phone width OK.
4. ~~S4 inner agents~~ done 2026-10-16. Spike on toy graphs: config already reaches an inner graph invoked in a node (its steps stream under `stage`, even with our explicit `recursion_limit`), so nothing to pass; but by default (`checkpointer=None`) the inner agent wrote its own checkpoints (every message, `Filed` objects) into the thread store, and a `stage` retry restarts it anyway. Fix: `build_ingest_agent(..., checkpointer=False)`; test checks steps still stream and only the ingest graph's checkpoints are stored. The "Blocked deserialization of Filed" warning is gone. Not checked: cancelling a server run mid-agent.
5. ~~S5~~ done 2026-10-16 (`4f46948`): `langgraph.json` as 0010's target plus `dockerfile_lines: ["ENV UV_NO_DEV=1"]` (else `uv export` puts the dev group, jupyterlab/pytest/`langgraph-api`, in the image: 185 vs 72 packages) (the serde allow-list tried here didn't work and was replaced by JSON state, 0011); `langgraph-api` 0.13.8 → 0.16.1 (cli 0.4.36, inmem 0.36.1; base image runs 0.16.1 too). `pyproject`: `packages.find where=src` + `package-data` for skills and templates (a real wheel had only the top-level modules; the image's editable install hid it), `test_packaging.py`. Allow-list `.dockerignore` (the generated Dockerfile copied `.env`, `.langgraph_api/`, notebooks, tests). Contract on Docker (first `langgraph up`, now `WIKI_SERVER_TESTS=docker`: `langgraph build` + our `compose.yml`, scripted graphs via `LANGSERVE_GRAPHS`, a copy of `tests/` because macOS blocks Docker mounts from `~/Documents`, vaults at the same path, own compose project): 11/11 on Postgres, strict. `compose.yml` for real use (0011). Real-graph smoke on a copy of `~/WorkDev` (no model calls): both graphs load, review page 200, skills found. Walkthrough example (2026-10-16, scripted graphs on `up`): approve wrote the files into the mounted vault but **the commit failed** ("Author identity unknown": the container has no git identity; files left staged).
   - ~~S5 fixes~~ done 2026-10-16 (user: "fix all"; `4f46948`), decision [[decisions/0011-guarantees-not-conventions|0011]]: JSON-only state (`ingest/state.py` `STORED_AS_JSON` + `models_in_node`; runner and servers strict; allow-list deleted; `test_state_json.py`), `ReadOnlyFilesystemBackend` for every agent route (`test_readonly_backend.py`), `compose.yml` (API on 127.0.0.1, Postgres/Redis unpublished; `test_compose.py`; `compose.vault.yml` retired), `WIKI_GIT_AUTHOR` for vault commits (`test_vault_git.py` now runs without any git identity, like a container).
6. Then review page R2 (edit, task choices, wins, revise) and R3 (remember) on top of `Backend`.

**Notebooks** (rule, user 2026-10-16: every step ships with its notebook, built and verified before the step is reported done; S5 gets one too): 13 done 2026-10-16 (toy review graph on an in-process adapter and on `langgraph dev`, 3 exercises on the server adapter, then the real `LocalBackend` and `ServerBackend` side by side; answer key verified, kept outside the repo). "Where this lives now" notes added to 00, 02, 03, 05 and paid-media pointers to 10, 11 (2026-10-16; audit: every notebook has explanations, these six pointed at deleted code or had no paid-media link). 14 done 2026-10-16 (S4: an agent inside a node: config reaches it, `None`/`True`/`False` persistence, node retry restarts it, default `retry_on` skips `TimeoutError`; 3 exercises, answer key verified). 12 done 2026-10-16 (assistant: vault as a route, two locks, first-match-wins rules, the dotfile gap, code-owned `find_tasks`, review-writer subagent: inherited tools, base write tools without its own middleware, schema errors retried; 3 exercises). 15 done 2026-10-16 (S5: toy project through the CLI's Dockerfile generator, allow-list `.dockerignore`, `UV_NO_DEV`, wheel vs editable, serde default/listed/strict, same-path vault mount; 4 exercises). Answer keys for 12 and 15 verified (✅ for the key, ❌ for wrong answers), kept in the session scratchpad. The user still has to work through 08–15. Audit 2026-10-16 (user asked: "old code where we were using pipeline"): 02 reworked (typed tool arguments + `stage_fact` checks + `response_format`; the per-section structured answer is a history note) and 03 reworked (reducers for parallel tool calls in `IngestAgentState`; `Send` kept as a LangGraph tool, old code via `git show b30f52b^:…`); stale claims fixed in 04 (apply is built), 05/09/14 (strict serializer, no allow-list), 06 (six tools, read-only backend), 13 (Docker mode). New exercises for the fixes: 09 ex4 (commit identity), 12 section "lock 3" + ex4 (read-only backend), 15 sections 4–5 + ex3 (JSON codec) and ex5 (exposed ports). The shared `check` now shows ❌ when a wrong answer crashes. All 16 run; keys verified for 02, 03, 09 ex4, 12, 15 (✅ for the key, ❌ for wrong answers).

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
