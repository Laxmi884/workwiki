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
- [[learnings/memory-through-review|Memory through the review]]: rules and aliases as proposed edits; same-review overlay vs next-run memory; two inputs, whole-list answers
- [[learnings/middleware-current-date|Middleware: the current date]]: wrap_model_call vs dynamic_prompt vs before_model; per-call clock, not in the thread; order and subagent inheritance
- [[learnings/tool-allow-list|Tool allow-list]]: hide and refuse in code, not prompts; why hiding alone isn't enough; ingest's `task` hole
- [[learnings/redaction|Redaction]]: filter the request, not the state; why `PIIMiddleware` leaks in a deep agent; replacing the general-purpose subagent
- [[learnings/offload|Offload]]: cap, page or offload; Deep Agents' built-in eviction for tool results and user messages; why no middleware of ours
- [[learnings/evals|Evals]]: label once, run the real graph, score in code, compare; first vs final pass; one run is one sample

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
- [[decisions/0012-open-tasks-page|0012]]: one code-generated `05_generated/open-tasks.md` (by project, by person), proposed with the task index; pages stay facts only
- [[decisions/0013-bookkeeping-all-or-nothing|0013]]: the note's task IDs, `tasks.json`, `open-tasks.md` and the ledger are written with every approval, never left out one by one
- [[decisions/0014-sync-core-async-edges|0014]]: our nodes, tools, runner and CLI stay sync; async only where the framework calls it; stream with sync APIs
- [[decisions/0015-rules-can-name-pages|0015]]: a page `90_meta/rules.md` links as `[[slug]]` passes the naming check for any line
- [[decisions/0016-real-notes-to-a-cloud-model|0016]]: real notes go to the cloud model (DeepSeek via OpenRouter); LangSmith tracing of real notes not decided
