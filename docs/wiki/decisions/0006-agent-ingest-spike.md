---
id: 0006
date: 2026-10-14
status: proposed
---

# 0006: Ingest by a deep agent with code guardrails (spike)

## Context

Note-taking is personal; every user writes differently. The ingest graph (0004) handles
meaning with code: fixed `##` sections (0002), per-section focus prompts, routing rules,
a name scan, and now project grouping and sub-bullets. Each new note shape adds special
code. Meanwhile the model already does the judgment well (the 2026-10-14 name scan found
Eugene and Joanna and skipped ELTV/UBM/Gitlab in one call), and a deep agent can search
and read the wiki instead of getting it all in one prompt. The user proposed (2026-10-14)
letting deep agents with skills do extraction, wiki writing and remembering from reviews,
keeping the review-first HITL, task IDs and dedup.

## Options

- A: keep the hand-built pipeline (0004) and finish 3c-2 pair 2 (projects, sub-bullets).
  Predictable, cheap, testable; grows special code for every note style.
- B: replace the meaning part with an ingest deep agent; keep guarantees in code
  ("the agent proposes, code checks"). Handles any note style; rules become editable
  skills; costs more calls, less repeatable, needs a capable model.
- C: one agent for ingest and Q&A. Rejected for now: Q&A must be read-only, ingest must
  go through approval; they share skills and the wiki, not one running agent.

## Proposed design (spike for B)

```
load_note → assign_ids → track_tasks            (code, unchanged)
  → ingest agent (create_deep_agent)
      reads: the note, wiki pages via read-only filesystem tools, search
      skills: how to file decisions / wins / people notes / updates, the user's rules
      "write" tools only STAGE edits; code checks each one:
        allowed folder (path_for / WritablePath), cited note line exists and is new,
        page exists or is proposed in this review, never 00_raw/ or 90_meta/*.json
      may also stage questions (people, projects) for the review
  → render → review (same ReviewRequest, revise/answer/edit/make_page) → apply → git
```

- Stays in code: task IDs and `tasks.json`, raw-note protection, path guards, line
  citations checked against the note, digests, apply-once + read-back, ledger, git.
- Replaced if the spike wins: `extraction.py` focus/sections, `routing.py` placement,
  `names.py`; their rules move to skills in the vault (editable in Obsidian).
- Review feedback and approved "remember" rules go to the agent as skills/memory files.
- Before building: check current Deep Agents APIs with context7 (`create_deep_agent`,
  skills, memory, `FilesystemBackend`/`CompositeBackend`, permissions, tool interrupts).
  Paid-media reference: `runtime/local.py`, `workspace/skills/`, `tools/writes.py`.
  Also worth reading: LangChain's own `deepagents/examples/llm-wiki` (ingest/query/lint).

## How we decide

Run both pipelines on the same notes: fixture `2026-10-12`, the user's `2026-10-14`, and
one note written in a different style (no fixed sections). Compare: facts found, wrong or
invented facts (citation check failures), right pages chosen, questions asked, time,
tokens, and stability across two runs. Record the result here and set the status.

## Decision

Pending the spike.

## Why

To be filled in from the comparison.
