---
phase: 3
date: 2026-10-14
tags: [langgraph, interrupt, cycles, hitl]
---

# The review loop: cycles back from an interrupt

## What it is

A graph may point backwards. After the `review` node pauses and gets an answer, a conditional
edge reads the answer and sends the run back to an earlier node; the run then reaches `review`
again and pauses once more. Each resume is one round.

## How we used it

- `src/llm_wiki/ingest/nodes.py` `review_changes`: returns only a state update for the chosen
  action (feedback + round count, decision pages, or the user's page text).
- `nodes.after_review`: approve/reject → `END`; make_page → `route`; edit → `render`; revise →
  `fan_out(state)`, which returns `Send`s straight to `extract`, so no extra node is needed.
- `src/llm_wiki/ingest/graph.py`: `add_conditional_edges("review", after_review, [...])`.
- `src/llm_wiki/review.py`: checks each answer (unknown IDs, code-owned files, revise limit).
- The user's text is a `UserText` edit (`domain/changes.py`), applied last in `render`, so
  the approved diff and digest include it.

## Paid-media reference

`docs/architecture/writes-and-approvals.md`: a change after review means a new proposal and a
new approval, never editing an approved diff.

## Gotchas

- `recursion_limit` (25) counts steps per invoke, not per thread: each resume starts fresh
  (`test_many_rounds_stay_under_the_recursion_limit`, 12 rounds).
- `warnings` uses `operator.add`, so rounds repeat old warnings; the review lists each once.
  Stale ones are not cleared yet.
- Parallel `extract` calls arrive in any order, so tests must not rely on "the last model
  call" (a flaky test taught us this).
- File IDs came from edit order and moved between rounds; listing by path keeps them stable.

## In my words

