---
phase: 4
date: 2026-10-16
tags: [deepagents, permissions, subagents, skills]
---

# Deep agents: read-only filesystems, permissions and subagents

## What it is

`create_deep_agent` gives an agent filesystem tools over a backend, skills (SKILL.md folders read on
demand), memory files, and subagents it can delegate to with the `task` tool.

## How we used it

- `assistant/agent.py`: the vault is a `CompositeBackend` **route** (`/vault/`), not the default
  backend, because deepagents writes oversized tool results to the default backend without checking
  permissions (learned in 0006). Tools limited to `read_file`, `ls`, `glob`, `grep` via
  `FilesystemMiddleware(tools=...)`, and every write denied by `FilesystemPermission`.
- Permissions are **first-match-wins and allow by default**: put specific denies first
  (`/vault/.git/**`), then the broad rule (`write /** deny`).
- `review-writer` subagent: custom subagents inherit **neither skills nor middleware**, so it gets its
  own skill folder (`subagent_skills/`), its own read-only `FilesystemMiddleware`, and explicit
  `permissions` (a subagent's permissions *replace* the parent's). `response_format=BragDraft` makes
  the parent receive JSON (the subagent's `structured_response`) instead of prose.
- Code tools for deterministic data: `find_tasks` reads `tasks.json`; the model never parses it.

## Paid-media reference

`runtime/local.py`'s `filesystem_permissions()`: deny secrets, deny writes to skills, allow writes to
one workspace folder, deny everything else: the same ordered rules.

## Gotchas

- Forgetting the subagent's own `FilesystemMiddleware` would give it the default write tools.
- With a scripted model, the structured answer is a tool call named after the schema (`BragDraft`).
- Rule globs use wcmatch `BRACE | GLOBSTAR` without `DOTGLOB`: `*`/`**` never match a dot segment, so
  `write /** deny` doesn't cover `/vault/.trash/x.md` or `/vault/.gitignore`; only patterns that spell the dot
  (`/vault/.git/**`) do (found 2026-10-16, notebook 4.1). Fixed in the backend instead: every agent route is a
  `ReadOnlyFilesystemBackend` whose writes always fail (0011).
- Checked in notebook 4.1 (deepagents 0.7.25): a declarative subagent **inherits the parent's tools** unless its
  spec sets `tools`; its base stack has a `FilesystemMiddleware` with all file tools, replaced by a spec
  middleware of the same name; a schema error in the structured answer goes back to the subagent as the tool's
  reply, so it can retry.

## In my words

(for the user)
