---
id: 0011
date: 2026-10-16
status: accepted
---

# 0011: Guarantees in code, not conventions: JSON state, read-only backends, our compose, explicit git identity

## Context

The S5 smoke test and walkthrough (`langgraph up` on the real image) found four places where the project relied
on something outside its control:

1. **Serde.** Our state held Pydantic objects; checkpointers re-import them only from an allow-list. The server
   logged "will be blocked in a future version", strict mode (`LANGGRAPH_STRICT_MSGPACK`) turned them into dicts
   (reviews failed with `AttributeError`), and `langgraph-api` 0.16.1 ignores `checkpointer.serde.allowed_msgpack_modules`.
2. **Read-only vault.** The deny-all-writes rules (`/**`, `/vault/**`) are wcmatch globs without `DOTGLOB`: they
   don't match `/vault/.trash/x.md`. Only the read-only tool list kept agents from writing dot paths.
3. **Exposure.** `langgraph up` publishes the API and Postgres (`5433`, password `postgres`) on every interface,
   and two `up` stacks collide on 5433. An extra compose file can add to its config but not remove ports.
4. **Git identity.** Vault commits used the machine's git config. The image has none: approve wrote the files,
   the commit failed ("Author identity unknown"), the files stayed staged.

## Options

- Configure around each one (allow-lists in two places, more glob patterns, firewall rules, a `.gitconfig` in the
  image): each still depends on something we don't control or can't fully express.
- Make each property hold by construction, with a test that fails when it stops holding.

## Decision

By construction (user chose "fix all", 2026-10-16):

1. **State stores plain JSON; nodes see models.** `ingest/state.py` `STORED_AS_JSON` (field → `TypeAdapter`) and
   `models_in_node`, applied to every node and router in `ingest/graph.py`. Both serializers are strict: the
   runner's (`allowed_msgpack_modules=None`) and the server's (`LANGGRAPH_STRICT_MSGPACK=true` in `compose.yml`
   and the contract servers); the type allow-list is gone. `test_state_json.py` checks every checkpoint of a full
   review is plain JSON, every model-typed field has a codec, and old object-holding threads still load.
2. **Read-only is a property of the backend.** `readonly_backend.py` `ReadOnlyFilesystemBackend` refuses `write`,
   `edit`, `delete`, `upload_files` (sync and async) for every path; both agents' `/vault/` and skills routes use
   it. Tool lists and permission rules stay as further locks. `test_readonly_backend.py`.
3. **We run the server from our own `compose.yml`** on the `langgraph build` image: API on `127.0.0.1` only,
   Postgres and Redis unpublished, vault at the same absolute path, strict serde. `langgraph up` stays for quick
   experiments. The contract suite's Docker mode (`WIKI_SERVER_TESTS=docker`) runs this exact file.
   `test_compose.py`. Replaces `compose.vault.yml`.
4. **Commits carry their own identity**: `vault_git` passes `WIKI_GIT_AUTHOR` (default `llm-wiki
   <llm-wiki@localhost>`) as author and committer; a failed commit unstages its paths. `test_vault_git.py` runs
   git with no identity and `user.useConfigOnly=true`, as in a container.

## Why

Each fix removes a dependency instead of documenting it, and each has a test that fails the day the property
breaks, so the guarantees hold on any machine, container or server we run on later (phase 8).

## Addendum 2026-10-17: agents can't write `90_meta/`

5. **Where an edit may write is part of its type, for memory too.** Before, `WritablePath` allowed
   `90_meta/*.md` (for R3's rules file), so only slug-only staging and the review kept agents off
   `rules.md`, which both agents read as memory; the review could even edit it. Now `WritablePath` refuses
   `90_meta/`; `RememberRules` (path type: exactly `90_meta/rules.md`) is the only edit that writes it, built
   only by `remember.remember_edits`; a `FileDiff`'s edits must all target its own path. Option not taken:
   letting `AppendToSection` also accept `rules.md` (any bug producing one could write memory).
   Cost: a review saved before this change that holds a remembered rule no longer loads (reject, re-run).
   Tests in `test_changes.py`, `test_remember.py`, `test_review.py`, `test_review_web.py`,
   `test_agent_staging.py`.
