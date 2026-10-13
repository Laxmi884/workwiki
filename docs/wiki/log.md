# Log

Newest first. One entry per session, 2–5 bullets: what was done, what was learned, what's next.

## 2026-10-12

- Phase 1 started; split into steps 1a–1e in [[progress]]. First exercise: `Task` model + `tasks_syntax`.
- Learned: [[learnings/designing-agent-mechanics|how to find state, tools, skills and helpers]] (from examples outwards, add on demand).
- **1a done:** `domain/tasks.py` (`TaskStatus`, frozen `Task`) and `tasks_syntax.py` (`parse_task_line`, `status_from_char`, `new_task_id`, `append_id`); 56 unit tests pass.
- Learned: [[learnings/domain-models-and-task-syntax|domain vs syntax]]; Pydantic optional vs default; function vs classmethod; seeded randomness for tests; ID collision guard.
- 1b-1/1b-2 done: `frontmatter.py` (`split_frontmatter`, keeps body start line, errors as data, `yaml.safe_load`) and `daily_syntax.py` (`split_sections`: `##` only, unknown → Log, code fences skipped); `domain/daily.py` (`SectionKind`, `Section`). Chose own split over `python-frontmatter` (it strips leading body lines, raises on unrendered `{{date}}`). 78 unit tests pass.
- 1b-3 done: `parse_daily_note(path, text) -> DailyNote` in `daily_syntax.py`; `Task` gains `section`, `subheading`, `parent` (indent stack); date = frontmatter, else filename, warning on drift; problems are warnings, never exceptions. `SectionKind` moved to `domain/common.py` to avoid a circular import. 91 unit tests pass.
- Committed 1b-3 (`7e2f60a`). Created a **dev vault** at `~/WorkDev` (own git repo, baseline `bf6607d`): same layout, template and Obsidian settings as `~/Work`, plus an invented sample note `00_raw/daily/2026-10-12.md`. It's a playground for Obsidian and `langgraph dev`; reset with `git reset --hard && git clean -fd`. The frozen pytest inputs stay in `tests/fixtures/vault/` (1d). The parser reads the sample cleanly: 12 tasks, the fenced checkbox skipped, no warnings.
- **1b done.** 1b-4: `inline_syntax.py` (`find_links`, `find_tags`). `Task` gains `links` and `tags` from its own line; inline code, embeds, URLs, `[[a#b]]` and `#42` are excluded. 119 unit tests pass. Learned: [[learnings/parsing-notes-with-line-numbers|parsing notes with line numbers]].
- Next: 1c, entity model and alias index (slug → path, allowed-folder check).

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
