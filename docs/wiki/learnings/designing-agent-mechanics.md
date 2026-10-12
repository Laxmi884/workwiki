---
phase: 1
date: 2026-10-12
tags: [method, design, state, tools, skills]
---

# Finding an agent's state, tools, skills and helpers

## What it is

You don't design state fields, tools, skills and helpers up front. Each one has a source and a trigger
that shows when it's needed. Start from hand-written examples of input and output, build the
deterministic core without an LLM, then add each piece when an example or a failing test calls for it.

| Piece | Where it comes from | When to add it |
|---|---|---|
| Domain models | Nouns in your hand-written examples (task, meeting, person, project) | First, before any graph |
| Pure functions | Verbs in the examples (parse, match, assign ID, resolve slug) | With the models, each with a unit test |
| Code vs model | Per verb: "would two careful people always get the same answer?" Yes → code. No → model | When you list the verbs |
| Graph nodes | Steps in the pipeline drawing; a node is a step that runs model or I/O work | When the core works without an LLM |
| State fields | What travels between nodes: anything a later node reads that an earlier node produced | Only when a later node needs it |
| Reducers | State fields where several nodes or parallel branches add items (lists of diffs) | When two writers touch one field |
| Context | Fixed for the whole run (vault path, model, dry run, today) | When a node needs a setting it must not change |
| Tools | Pure functions the *model* must be able to call (agent side only) | When the agent needs data or an action it can't get from its prompt |
| Skills | Instructions the model needs only for some tasks (brief format, review rubric) | After a trace shows the model getting a rule wrong |
| Helpers | Code repeated in two or three places | When it is duplicated, not before |
| Middleware | A concern that applies to every model or tool call (date, redaction, write guard) | When you notice the same check in many tools |

## How we used it

For llm-wiki the examples are a few daily notes plus the exact wiki diffs you'd expect from each (phase 6
turns these into evals). The nouns gave phase 1's models: `Task`, `DailyNote`, `Entity`. The deterministic
verbs (parse task line, new ID, resolve alias) are phase 1 code. The judgement verbs (extract events, map
prose to a project) are phase 2 model calls.

## Paid-media reference

`src/paid_media_agent/domain/` holds only models and pure logic (e.g. the proposal state machine in
`proposals.py`); it imports nothing from LangGraph. Tools, graph wiring and skills came later in
`tools/`, `assembly.py` and `workspace/skills/`.

## Gotchas

- Adding state fields "just in case" makes every node harder to reason about and every checkpoint bigger.
- A rule in a skill is a request; a rule in code is a guarantee. Put a rule in a skill only if code can't
  enforce it.

## In my words

