# llm-wiki project wiki

Working notes for building llm-wiki: where we are, what we did, what we learned, what we decided.
Maintained by Claude (see `CLAUDE.md` → "Project wiki"). Open `docs/wiki/` as an Obsidian vault to browse.

## Start here

- [[progress]]: phase checklist, current focus, open decisions
- [[log]]: dated session log, newest first

## Learnings

One note per concept. Template: `learnings/_template.md`.

- [[learnings/scaffolding-with-langgraph-new|Scaffolding with langgraph new]]: temp-folder scaffold, uvx/rsync, package rename, dev-server gotchas
- [[learnings/context-and-assistants|Context and assistants]]: State vs Context, assistants as saved configs, dev vs real vault
- [[learnings/designing-agent-mechanics|Finding state, tools, skills and helpers]]: where each piece comes from and when to add it
- [[learnings/domain-models-and-task-syntax|Domain models and task syntax]]: frozen `Task`, `tasks_syntax` translator, None vs default, regex split, ID collisions

## Decisions

Numbered, one per decision. Template: `decisions/_template.md`.

- [[decisions/0001-task-ids|0001]]: task IDs are Obsidian block IDs, matched by ID only; two code-only writes to `00_raw/`; daily-note rollover
- [[decisions/0002-daily-note-format|0002]]: daily note sections, tags, `daily-v1` template; task → project mapping order
