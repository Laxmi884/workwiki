---
phase: 7
date: 2026-10-16
tags: [deepagents, memory, review]
---

# Memory through the review

## What it is

What the agents remember (`90_meta/rules.md`, page `aliases:`) changes only as a proposed edit in a
review, approved and committed like any other file. Deep Agents' `memory=` files are always in the
system prompt and, by default, the agent may edit them itself; ours can't (read-only backend).

## How we used it

- `src/llm_wiki/remember.py`: `check_rules`, `check_aliases` (refuses a name another page has: shared
  names link neither), `remember_edits` (rules → `## Rules` line or new file, aliases → `AddAlias`).
- `src/llm_wiki/ingest/nodes.py`: `_entities(remembered=True)` overlays remembered aliases with
  `with_proposed`, so `map_tasks` links "stakeholders" tasks to Atlas in the same review; `pages` for
  the check is built without them. `render` adds remember edits after the agent's, before user text.
- Rules work from the next ingest: `ingest/agent.py` loads `rules.md` when the agent is built.
- Answers carry whole lists (`rules`, `aliases`); the page sends list ± one, the chat merges
  (`chat/session.py` `_remember_answer`).

## Paid-media reference

`docs/customization.md`, "Memory and scheduling": learned memory off by default; when on, the agent
saves it and every caller can influence it. We keep the user as the only writer.

## Gotchas

- One parsed box ("X = Y" is an alias) misreads rules that contain "=" (user chose two inputs).
- `AddAlias` used to re-dump the frontmatter with `yaml.safe_dump` (flow lists became block lists,
  comments would be lost). Now it edits only the aliases lines and parses the result back, refusing
  if anything but the alias changed (`diffs.py` `_add_alias`).
- `WritablePath` allows `90_meta/*.md`; slug-only staging and the review are what keep agents off
  `rules.md`.

## In my words

