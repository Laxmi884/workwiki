# llm-wiki: personal work wiki agent

## What we're building

A local agent that turns everyday work notes (daily scratchpads, meeting notes, tasks, ad-hoc help given to
others, business knowledge) into an LLM-maintained Markdown wiki. Humans and agents can both read it.
It answers questions, builds a morning agenda, and compiles a brag sheet for performance reviews.

The original idea and a Gemini-generated design are in `Building a Local AI Wiki.pdf`. Treat that PDF as
input, not a spec. Known problems in it: too much to build at once, `00_raw/` described as immutable while
agents write into it, string-matched task completion, no idempotency, invented metrics marked `validated`,
outdated model names, an unverified "Google OKF" claim.

## This is a learning project

The user is learning LangGraph, Deep Agents and the LangGraph CLI by building this, alongside studying
`~/Projects/paid-media-agent` (LangChain's paid-media agent; study path:
https://claude.ai/artifact/U11JqpVsxN7kyem41JN6gc).

How to help:
- **The user writes the code.** Explain concepts, set the exercise for each phase, review their code, and
  point to the paid-media file that solves the same problem. Write code only when asked.
- Build from scratch, not by copying paid-media files. Use `langgraph new` for boilerplate.
- Check LangGraph and Deep Agents APIs against current docs (context7) before stating them as well as use appropriate langgraph skills.
- Add complexity only after the simpler version fails.

## Architecture

Two agents that share one set of components:

| Part | Built with | Job |
|---|---|---|
| Ingest compiler | A hand-built `StateGraph` | Reads a daily note, extracts events, proposes `FileDiff`s, pauses for human approval (`interrupt()`), applies the diffs once, then git-commits the vault |
| Assistant | `create_deep_agent` | Q&A, morning brief, review synthesis (subagent). Filesystem backend over the vault, `00_raw/` read-only through permissions, rules as skills |

Rules carried over from paid-media-agent:
- Frozen Pydantic domain models. Missing values stay `None`; never invent metrics.
- Code does deterministic work: task IDs, completion matching, frontmatter parsing, date-drift checks.
  The model extracts and writes.
- The model passes entity slugs (`person-alex-chen`); code resolves them to paths and blocks writes
  outside allowed folders.
- Proposed writes carry a digest. Approval covers the exact diff. Apply once, then read back to verify.
- An ingest ledger (hash of processed notes) makes re-runs idempotent.
- Tests use a scripted model and a fixture vault, never the real vault.
- Task IDs follow decision 0001 (`docs/wiki/decisions/0001-task-ids.md`): Obsidian block IDs
  (`^t-xxxxxx`), matched by ID only; the user never types IDs. `00_raw/` is read-only to every agent
  with exactly two code-only exceptions: (1) ingest appends ` ^t-xxxxxx` to a task line, via HITL
  approval, diff verified to add only that suffix; (2) the phase-7 rollover job creates today's daily
  note if absent, with open tasks carried from the index. Model-written text never goes into `00_raw/`;
  it goes to `05_generated/`.
- Daily notes follow decision 0002 (`docs/wiki/decisions/0002-daily-note-format.md`): fixed `##`
  sections routed by code, optional `#win`/`#decision`/`#blocked` tags, `template: daily-v1`
  frontmatter, forgiving parser. Task → project mapping order: explicit link, inherited context, alias,
  approved model proposal, none. The parser, extractors and assistant skills all follow this contract.
- `init_chat_model` keeps the model swappable. Develop with a cloud model; local (Ollama) comes later.

## Locations

- **Code:** this repo (`~/Documents/Projects/llm-wiki`). Python 3.11+, `uv`.
- **Real vault:** `/Users/laxmikantmukkawar/Work` (Obsidian, git-initialised, not in iCloud). The agent reads its path from
  `WIKI_VAULT_PATH`.
- **Fixture vault:** `tests/fixtures/vault/`. Invented data only. Use it for all development and tests.

Planned vault layout:

```
00_raw/daily/        human-written daily notes (agent: read-only, except the two code-only writes below)
00_raw/attachments/
05_generated/        agent-written agenda/briefs (keeps 00_raw clean)
10_projects/  15_concepts/  20_people/  30_decisions/  40_impact/
50_templates/  90_meta/  (entity index, ingest ledger)
index.md  log.md
```

## Phases

Each phase lists the paid-media files to read alongside it.

0. Scaffold with `langgraph new --template new-langgraph-project-python`, run `langgraph dev`, open
   Studio. Read: `AGENTS.md`, `assembly.py`.
1. Domain models and vault I/O, no LLM: Pydantic models, frontmatter parser, task IDs, entity index
   with aliases. Read: `domain/`, `config.py`.
2. Ingest graph v1: structured extraction, routing, `FileDiff` reducer. Read: `tools/compute.py`,
   `tools/artifacts.py`.
3. HITL and persistence: `interrupt()`, `Command(resume=…)`, SQLite checkpointer, digests, git commit.
   Read: `docs/architecture/writes-and-approvals.md`, `tools/writes.py`.
4. Assistant deep agent: filesystem backend, permissions, skills, review subagent.
   Read: `runtime/local.py`, `workspace/skills/`.
5. Middleware: current date, write guard, redaction, offload. Read: `middleware/`.
6. Tests and evals: scripted model, sample notes with expected diffs. Read: `testing/scripted_model.py`,
   `tests/eval/`.
7. Morning brief, schedule, CLI surface (Telegram later). Read: `surfaces/runner.py`, `schedules/`.
8. Optional: `langgraph up`, `langgraph deploy`.

## Project wiki (`docs/wiki/`)

Status, history, learnings and decisions live in `docs/wiki/`, not in this file. Claude maintains it;
the user does not edit it by hand.

- **Session start:** read `docs/wiki/progress.md` and the latest entries in `docs/wiki/log.md` before
  suggesting next steps.
- **Session end, or after any milestone** (task done, phase step done, decision made): update the wiki
  without being asked.
  - `progress.md`: tick finished items with the date, update "Current focus" and "Open decisions".
    It is the only place status lives.
  - `log.md`: add or extend today's entry at the top. 2–5 bullets: done, learned, next. Absolute dates.
  - `learnings/<concept-slug>.md`: one note per new concept the user worked through (from
    `learnings/_template.md`), with repo paths and the paid-media reference. Leave "In my words" for
    the user; offer to fill it from what they said.
  - `decisions/NNNN-<slug>.md`: when a decision is settled (from `decisions/_template.md`), then
    remove it from "Open decisions".
  - `index.md`: add a one-line link for each new learning or decision note.
- Keep notes short and factual. Record only what actually happened; no planned work marked done.
- Wiki updates are documentation, not code, so they are exempt from "the user writes the code".
