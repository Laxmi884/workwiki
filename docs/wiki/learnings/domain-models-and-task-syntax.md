---
phase: 1
date: 2026-10-12
tags: [pydantic, regex, testing, domain]
---

# Domain models and task syntax

## What it is

The **domain model** says what a task *is* (`Task`: id, text, status, where it came from). The **syntax
module** says how a task is *written in Markdown* and translates between the two. Keeping them apart means
every part of the app (parser, index, ingest, rollover) shares one tested translator, and a different
format (Tasks plugin, sidecar) only means swapping that module.

## How we used it

- `src/llm_wiki/domain/tasks.py`: `TaskStatus` (`StrEnum`) and frozen `Task` (Pydantic). Invariants live in
  the type: `id` matches `t-[0-9a-z]{6}` or is `None`; `line_no >= 1`. No Markdown here.
- `src/llm_wiki/tasks_syntax.py`:
  - `parse_task_line`: line shape regex, then ID-at-end regex on the rest, then plain Python
    (empty text means not a task). Returns `ParsedTaskLine` or `None`.
  - `status_from_char`: dict lookup; unknown chars are open.
  - `new_task_id`: random base36 ID, retried while in `existing`. `rand` is injectable for tests.
  - `append_id`: adds ` ^t-…` only. Drops trailing spaces, keeps the line ending, raises on non-task,
    existing ID or malformed ID.
- `tests/unit_tests/test_tasks_syntax.py`: parametrized tables, plus a round-trip test proving `append_id`
  changes nothing but the ID.

## Paid-media reference

`src/paid_media_agent/domain/common.py` (`StrEnum`s) and `proposals.py` (frozen models, logic as data
plus pure functions). `config.py:34` `ModelConfig.parse` shows when a classmethod fits: building the
object either works or is an error.

## Gotchas

- Pydantic: `str | None` allows `None` but is still **required**; add `= None` to make it optional.
- Use `None` only when a real task can lack the value; otherwise make the field required.
- Frozen `BaseModel` for anything that crosses a boundary (JSON, LLM, graph state); frozen dataclass
  for values private to one module (`ParsedTaskLine`).
- One regex for the whole line misreads `- [ ] ^t-k3x9q2` as text; split into shape regex + ID regex +
  an empty-text check.
- Return `None` for normal "no" answers (most lines aren't tasks); raise for caller bugs (`append_id` on
  a non-task).
- Random IDs can collide (birthday problem: ~2% across 10k unchecked IDs). `existing` must come from
  scanning all notes, and each new ID must be added to it before generating the next in one run.

## In my words

