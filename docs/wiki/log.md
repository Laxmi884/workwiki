# Log

Newest first. One entry per session, 2–5 bullets: what was done, what was learned, what's next.

## 2026-10-11

- Created the vault at `~/Work` with the planned folder layout; moved the first daily note into `00_raw/daily/`; `git init` and initial commit (`c977ec0`).
- Set up this project wiki (`docs/wiki/`) and moved status tracking out of `CLAUDE.md` into [[progress]].
- Proposed Obsidian block IDs (`^t-xxxx`) for task IDs; not decided yet.
- Configured Obsidian (daily notes, templates, attachment folders); committed vault settings (`5a87345`).
- Decided task IDs ([[decisions/0001-task-ids|0001]]): block IDs, never typed by the user, ID-only matching, rollover of open tasks into each day's note (phase 7). Updated CLAUDE.md `00_raw` rule.
- Decided daily note format and task → project mapping ([[decisions/0002-daily-note-format|0002]]); committed `50_templates/daily.md` to the vault (`be725f2`).
- Phase 0 started: scaffolded via a temp folder (`langgraph new` refuses non-empty dirs). Package rename half done: `langgraph.json` still pointed at `src/agent/`, so the graph failed to load (`GraphLoadError`) and Studio showed no output. Fix verified on a scratch copy.
- Learned: [[learnings/scaffolding-with-langgraph-new|scaffolding]]; State vs Context; partial state updates.
- Studio fixes: start with `uv run` (Anaconda CLI had langgraph-api 0.9.0); `LANGSMITH_TRACING_V2=false` in `.env` was blocking traces.
- Learned: [[learnings/context-and-assistants|Context and assistants]]; `Context` now has `vault_path` and `model`, and an assistant was saved in Studio.
- **Phase 0 done.** First code commit "Initial commit with scaffolding" (no secrets tracked, unit test passes).
- Next: phase 1, domain models and vault I/O.
