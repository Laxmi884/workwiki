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
- [[learnings/parsing-notes-with-line-numbers|Parsing notes with line numbers]]: frontmatter split, sections, fences, indent stack, links/tags, errors as data
- [[learnings/entity-slugs-and-alias-index|Entity slugs and the alias index]]: model passes slugs, code picks the folder; aliases, ambiguity as data
- [[learnings/structured-extraction-with-checks|Structured extraction with code checks]]: schema output, code repairs vs retries, scripted model, local-model loops
- [[learnings/send-fan-out-and-reducers|Send fan-out and reducers]]: one `extract` per section in parallel, merge by key, retry one branch
- [[learnings/interrupt-and-the-runner|interrupt() and our own runner]]: pause/resume with any checkpointer, nodes re-run on resume, allow-listing our types
- [[learnings/review-loop|The review loop]]: conditional edges back from `review`, one round per resume, recursion limit per invoke
- [[learnings/hidden-retries-and-timeouts|Hidden retries and timeouts]]: SDK retries hid a 20-minute hang; one visible retry layer + request timeout
- [[learnings/deep-agent-permissions-and-subagents|Deep agent permissions and subagents]]: first-match-wins rules, vault as a route, subagents inherit no skills/middleware, response_format
- [[learnings/agent-server-and-deployment|The Agent Server: dev, up, build]]: who owns persistence, graph factories, http.app, langgraph.json keys, licences
- [[learnings/backend-interface-and-node-input|Backend interface and node input]]: ports and adapters with contract tests; a node only gets its annotated schema's keys

## Decisions

Numbered, one per decision. Template: `decisions/_template.md`.

- [[decisions/0001-task-ids|0001]]: task IDs are Obsidian block IDs, matched by ID only; two code-only writes to `00_raw/`; daily-note rollover
- [[decisions/0002-daily-note-format|0002]]: daily note sections, tags, `daily-v1` template; task → project mapping order
- [[decisions/0003-model-selection|0003]]: any provider via one `provider:model` string; `WIKI_MODEL` default, Studio assistant / run context overrides
- [[decisions/0004-ingest-graph-design|0004]]: ingest graph: code around a narrow extract step, edits → diffs with digests, review loop with feedback
- [[decisions/0005-surfaces-and-deployment|0005]]: own runner + SQLite by default; Telegram/Slack/CLI as adapters; self-hosted Agent Server (free lite mode) and LangSmith Deployment optional
- [[decisions/0006-agent-ingest-spike|0006]]: ingest by a deep agent that stages edits checked by code (accepted 2026-10-15; replaces the 0004 extraction)
- [[decisions/0007-task-project-proposals|0007]]: task → project by model proposal: `map_task` checked by code, in by default, correctable in review, sticky user "none"
- [[decisions/0008-assistant-agent|0008]]: assistant deep agent: read-only vault, `find_tasks`, skills, review-writer subagent with a sourced draft, Studio + `ask`
- [[decisions/0009-review-page-and-remember|0009]]: HTML review page over the same runner (one review, many windows); remember proposed in the review; reviews tied to their vault
- [[decisions/0010-backends-local-and-server|0010]]: one `Backend` interface, two backends (local runner, Agent Server); surfaces route `/…` themselves; amends 0005
- [[decisions/0011-guarantees-not-conventions|0011]]: guarantees in code: JSON-only state (strict serde), read-only backends, our own `compose.yml`, explicit git identity
