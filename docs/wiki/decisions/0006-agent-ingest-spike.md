---
id: 0006
date: 2026-10-14
status: accepted
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
  `names.py`; their rules move to skills in the repo (user, 2026-10-14).
- Review feedback and approved "remember" rules go to the agent as skills/memory files.
- Paid-media reference: `runtime/local.py`, `workspace/skills/`, `tools/writes.py`.
  Also worth reading: LangChain's own `deepagents/examples/llm-wiki` (ingest/query/lint).

### Spike build (agreed 2026-10-14)

APIs checked with context7 on 2026-10-14 against `deepagents` 0.7.25 (needs
`langchain>=1.4.5`, which we have):

- `FilesystemMiddleware(backend=…, tools=["read_file","ls","glob","grep"])` hides write
  tools (0.7+); `permissions=[FilesystemPermission(operations=["write"], paths=["/**"],
  mode="deny")]` as a second layer (first match wins, default allow).
- Custom state subclasses `DeepAgentState` (0.6.6+); tools read it via `ToolRuntime`
  (`runtime.state`); per-run inputs via `context_schema`.
- `memory=[…]` always loaded; `skills=[dir]` is a folder of skill folders with `SKILL.md`.
- Not confirmed by the docs, so checked first in S1: a tool returning `Command(update=…)`
  into a custom state field; `virtual_mode=True` path handling; skills served from the
  repo while the backend is rooted at the vault (`CompositeBackend` route).

```
load_note → assign_ids → track_tasks            (code, as today)
  → stage   (wrapper node: invokes the ingest deep agent)
  → render → review ──approve/reject──→ END      (code, as today)
               └─ revise → stage (feedback + last staged edits)
```

- A separate graph `ingest_agent` (`ingest/agent_graph.py`), registered next to `ingest`;
  the 0004 graph is untouched so both run on the same notes.
- `stage` is a wrapper node, not a subgraph: it maps `IngestState` in and staged edits out.
  The agent has no checkpointer and no `interrupt_on`; HITL stays in the outer `review`.
- The note goes in the first message as numbered lines built from state (task IDs are not
  on disk yet), old lines marked `~` and not citable. The wiki is readable through
  `FilesystemBackend(root_dir=vault, virtual_mode=True)` with read tools only, so the agent
  can grep a page before adding a fact already there.
- Staging tools write `staged` / `questions` / `rejections` into the agent state:
  `propose_append(slug, heading, lines, cites)`, `propose_page(slug, title, body, cites)`,
  `ask(kind, name, cites)`. Code checks each call (slug → `path_for` allowed folder, never
  `00_raw/` or `90_meta/*.json`; cited lines exist, are new and prose; page exists or was
  proposed this run; heading format; size cap) and returns an error the agent can fix.
  Rejections are the "wrong or invented fact" count. Chosen over `response_format`, which
  only checks one answer at the end.
- Skills: anywhere for the spike; in the repo if B is adopted. `90_meta/rules.md` as memory.
- Review in the spike: approve, reject, revise, edit. Make page and people answers are
  given as revise feedback.
- Risks: Nemotron (free) tool calling unverified, so try it in S1 and fall back to Claude for
  the spike; `ScriptedModel` needs tool calls for end-to-end tests (S2 tests the checks);
  `write_todos` and `task` are always present (core middleware), so the prompt says not to use them.

Steps (user writes the code): S1 minimal agent over the fixture vault (no writes, skills
load, `Command` state update, model tool calling); S2 staging checks + tools, tests first;
S3 `stage` node + `agent_graph`, Studio run; S4 skills from `routing.py`/`names.py`;
S5 revise loop; S6 `scripts/compare_ingest.py`, a free-style invented note outside the
fixture vault, both graphs twice on 3 notes (the user's note only on a scratch `~/WorkDev`).

## How we decide

Run both pipelines on the same notes: fixture `2026-10-12`, the user's `2026-10-14`, and
one note written in a different style (no fixed sections). Compare: facts found, wrong or
invented facts (citation check failures), right pages chosen, questions asked, time,
tokens (`usage_metadata`), and stability across two runs (overlap of path + cited
lines). Facts found are scored against a short expected list per note. Record the result here and set the status.

## Spike result (2026-10-15)

Built and run end to end by Claude at the user's request (2026-10-14: "run the whole
experiment end to end … if we decide to go with this route then you can teach me").
Code: `src/llm_wiki/ingest_agent/` (`staging.py` checks, `agent.py`, `graph.py`, two
skills), `scripts/compare_ingest.py`, notes in `tests/fixtures/spike/`, 18 unit tests
(402 total pass). Not committed.

S1 findings: everything worked as documented in `deepagents` 0.7.25 (read-tool allowlist,
deny-write permissions, `DeepAgentState`, `Command` from a tool, skills via a
`CompositeBackend` route, virtual paths refuse `..`). Two things the docs don't say:
- Oversized tool results are written straight to the backend, past permission checks,
  so the vault is a `/vault/` route over an in-memory default, not the default backend.
- The model makes parallel tool calls; each sees the staging from before that turn, so
  per-call checks miss repeats in one turn (it created the impact page twice).
  `staging.finalize` re-checks the whole staging at the end.

Comparison: model `nemotron-3-ultra` (free OpenRouter), 2 runs each, scratch copies of the
fixture vault, stopped at the review. The user's real 2026-10-14 note no longer exists
on disk, so an invented note in the same shape stood in (`2026-10-15`: task
sub-bullets, unknown names Marta/Viktor, ELTV/UBM/Gitlab acronyms).

| Note | Graph | Found | Right page | Extra | Refused | Stability | Seconds | Input tokens |
|---|---|---|---|---|---|---|---|---|
| fixture 2026-10-12 (template) | pipeline | 9/9, 9/9 | 9, 9 | 1 | 0 | 1.00 | 119, 193 | 9k |
| | agent | 8/9, 8/9 | 8, 8 | 1, 0 | 0 | 0.92 | 51, 31 | 32–39k |
| 2026-10-15 (like the user's note) | pipeline | 8/12, 8/12 | 7, 7 | 0 | 0 | 0.90 | 32, 53 | 9k |
| | agent | 11/12, 11/12 | 11, 11 | 0 | 1, 0 | 0.87 | 112, 165 | 122k |
| 2026-10-16 (free-style, no sections) | pipeline | 6/6, 6/6 | 5, 5 | 0 | 0 | 1.00 | 42, 29 | 3k |
| | agent | 5/6, 4/6 | 5, 4 | 1, 0 | 0 | 0.64 | 112, 97 | 141–162k |

What the numbers hide:
- Pipeline misses on 2026-10-15 are the known gaps: task sub-bullets (root cause, the
  UBM blocker) are never read, and lines about an unknown person (Marta) give no facts;
  it asks "Marta? Viktor?" in the review instead. The agent read sub-bullets, proposed
  pages for Marta and Viktor and filed their facts there.
- Agent misses are mostly learnings with no concept page (the skill says skip; the
  pipeline parks them in the inbox), and once Hana (asked a question instead).
- Wrong or invented: no citation failures in either. One refusal (agent cited a `###`
  heading for a new page). One agent misreading: "Updated the Atlas status page for the
  first time" (it turned the brief's "done for the first time" into a claim, and filed a
  chore as a win). The pipeline wrote `minutes: null` for "(15 min)".
- Cost: the agent uses 4–18× more input tokens (it re-reads pages, globs 6–8 times);
  free here, roughly $0.4–0.5 per note on a Sonnet-priced model. Time is similar on the
  free tier (both noisy).
- Revise (S5, one real run on 2026-10-16): three feedback points (make Hana a page,
  status page is a chore, file the EXPLAIN learning on a new concept page) all applied
  in one 60 s round; approve gave 9 digests; nothing written.

## Round 2 plan (agreed 2026-10-15)

The user asked for more testing before deciding: better skills ("a true agent", a second
brain), many models incl. ~30B ones, whether small models can learn from bigger ones, and
how quality holds as notes, reviews and preferences pile up. Budget: free OpenRouter models
plus $2.50 credit (1000 free requests/day); no frontier models this round.

1. Agent v2: fewer requests (page list + page outlines in the brief, batched tool calls),
   more skills (note reading, filing, people/projects, decisions/wins, page upkeep), a
   guarded `update_summary` for a page's "Current state", `90_meta/rules.md` as memory.
2. Corpus: 20 invented workdays (2026-11-02 → 11-27) with storylines (GA date slips twice,
   a decision reversed, new people, recurring 1:1s, hiring), changing note styles, labelled
   expected facts, 5 preferences introduced on set days, 30 Q&A pairs.
3. Sequential harness: each day ingest → score → scripted review (one revise round) →
   approve → apply to a scratch vault → preference rules added on their days. Tracks
   recall, right page, preference adherence, duplicates, requests/tokens as the vault grows.
4. Teacher→student: small model with base skills vs + worked examples from the teacher's
   early days vs teacher-as-reviewer (no labels). Teacher: free Nemotron 3 Ultra.
5. Second brain: one fixed Q&A agent answers 30 questions over each finished wiki, and over
   the raw notes only (does the wiki add value?); judged by a model of another family.
6. Harness in LangSmith (traces per run, feedback scores, Q&A as an `evaluate` experiment).

Models: Nemotron 3 Ultra / Super / Nano 30B, Gemma 4 31B / 26B-A4B (free); Muse Glimmer
30B, Qwen3.8 27B, DeepSeek V4.1 Flash, Hy3 (paid, ~one pass each, stop at $2.20);
local `gemma4:12b`. Baseline: the 0004 pipeline on Nemotron 3 Ultra.

## Round 2 result (2026-10-15)

Run by Claude end to end. Code: `ingest_agent/` (staging v2 with `update_summary`,
`withdraw_fact`, free section names; five skills; `experiment.py` sequential harness;
`qa.py`), `ReplaceSection` edit, `scripts/run_experiment.py`, `summarize_r2.py`,
`make_examples.py`, `run_qa.py`, corpus in `tests/fixtures/corpus/` (20 invented days,
93 labelled lines, 5 preferences, 30 Q&A with code-checked key facts). 412 tests pass.
Traces and Q&A experiments in LangSmith (`llm-wiki-r2`, dataset `llm-wiki-r2-qa`).
Spend: $2.47 of the $2.50 budget. Not committed.

One run per variant, all 20 days in order, oracle = scripted reviewer from the labels.

| Variant | Recall 1st → revised | Right page 1st → revised | Req/day | Cost (20 days) |
|---|---|---|---|---|
| DeepSeek V4.1 Flash agent | 97% → 99% | 97% → 99% | 9 | $0.35 |
| Nemotron 3 Ultra agent (free) | 93% → 100% | 93% → 100% | 10 | free |
| Muse Glimmer 30B agent | 90% → 99% | 89% → 99% | 20 | $1.55 |
| 0004 pipeline (Nemotron Ultra) | 93% → 99% | 74% → 89% | 4 | free |
| Nemotron 3 Nano 30B-A3B agent | 61% → 95% | 57% → 93% | 16 | $0.32 |
| Nano + teacher examples | 59% → 93% | 55% → 87% | 12 | $0.26 |
| Nano + teacher (Ultra) as reviewer | 76% → 93% | 67% → 87% | 19 | ~$0.28 |

Preferences (first pass, before → after taught): every agent followed "1:1 notes",
"interviews" and "chores" 80–100% after one teaching (Nano + teacher-reviewer only 40% on
1:1 notes); "Customer asks" 50–100%; "Current state" 50–100%. The pipeline: 0% (it has
no way to learn a rule). No candidate page was created after the rule; one was created
on the day it was taught (there is no tool to withdraw a proposed page).

Second brain (DeepSeek Flash answering, code-scored key facts): wikis 0.97–0.98 (Nano +
examples 0.91), raw notes only 0.93. At 20 notes a grep-capable agent finds most answers
in the raw notes; the wiki's edge was "what is true now" questions. A small LLM judge
(Gemini 2.5 Flash Lite) marked right answers wrong for adding a year, so it was dropped.

Invented details: no invented metric found (the code flags numbers missing from the cited
line; all hits were "1h 30m → 90 min", wrapped lines, or dates resolved correctly).

Bugs found and fixed on the way: revise discarded the earlier filing (now kept, with
`withdraw_fact`); withdrawn facts still blocked re-filing; provider overloads (Nemotron
free) needed retries; the first LLM judge counted its own errors as wrong.

Caveats: one run per variant (2-run stability in round 1 was 0.64–0.92, so differences
under ~10 points are noise); the corpus and labels were written by Claude; each week uses
one note style, so "more notes" and "note style" can't be separated; no frontier model
and no Qwen (budget); free Gemma 4 was rate-limited upstream and Nano is no longer free.

Findings:
1. The agent beats the pipeline where it matters: right page 93–97% vs 74%, and only
   the agent can learn preferences.
2. Cheap mid/large models are enough: DeepSeek V4.1 Flash was the best and cheapest paid
   model; free Nemotron Ultra matched it but is slow and often overloaded.
3. A ~30B MoE (3B active) is not good enough alone (first pass ~60%, worst on free-form
   and mixed notes) but reaches ~93% with one round of review feedback.
4. Teacher examples did not help the small model; the big model as reviewer (no labels)
   got the small model to the same final quality as label feedback (93% / 87%), but it
   enforced the user's rules less consistently.

## Decision

**B, accepted by the user on 2026-10-15.** Ingest is done by the `ingest_agent` deep agent
whose staged edits are checked by code; task IDs, the review (`interrupt()`), digests,
apply and git stay in code. The review loop stays. Skills live in the repo
(`src/llm_wiki/ingest_agent/skills/`). Model: DeepSeek V4.1 Flash recommended, free
Nemotron Ultra as fallback; the `WIKI_MODEL` choice stays open in progress.md. The 0004
pipeline stays in the repo until the agent covers everything it does; 3c-2 pair 2 is
dropped (the agent already handles sub-bullets and grouping).

Still to do: give the agent the page list up front (fewer globs), stop "first time"
leaking into text, decide whether new people become pages directly or questions, a way
to withdraw a proposed page, and check stability on free-style notes.

From the user's first real run (2026-10-15, own 2026-10-14 note on `~/WorkDev`): the agent
put all work on the only project page (invented "Atlas", probably via its "BI migration"
alias), and filed every first-time done task as a win (also offered again as review win
candidates). Agreed to-dos:
- No project page fits → the agent proposes a new project or asks; never forces an
  existing one. Ad-hoc tasks get no page (they stay in the note and `tasks.json`).
- Task → project mapping by approved model proposal (0002 rule 4), so done tasks can be
  grouped by project later.
- The impact page holds only confirmed wins: `#win` lines and wins the user ticks in the
  review. The agent stops filing wins itself; one win picker in the review, not two.
- Help given lives on the person page (and the project page if it was project work), not
  copied to the impact page. Help for someone without a page → propose a page or ask.
- Brag sheet / review summary is built on request (phase 4) from confirmed wins, done
  tasks, project pages and person pages, grouped however the user asks.

Done 2026-10-15 (code checks in `staging.py`, skills updated): a person/project page
(existing or proposed this run) takes a fact only if the line, a parent bullet or its
`###` heading names it, code mapped the task to it, or the user's revise feedback names
the page; `## Wins` only for `#win` lines, picked done tasks reach the impact page via a
code-only `pick_wins` node; help given only on person/project pages; empty new pages
dropped; facts may name a page proposed in the same turn (checked in `finalize`);
`###` headings citable for propose_page/ask_user; the review lists dropped facts and
questions, refusals only counted. Real note, free Nemotron: first pass three projects
named as written, Joanna's items, line 9 asked as ad hoc, lines 8/12/13 listed as not
filed; one revise with the user's answers filed them. Still open: task → project
mapping proposals (0002 rule 4). The round-2 corpus numbers predate these rules.

## Why

On the 20-day corpus the agent filed 89–97% of facts on the right page first time with
mid-size models (74% for the pipeline), learned the user's filing preferences from one
correction (the pipeline can't), and handles real-looking notes without new code per
note shape. Code still guarantees everything that must be exact.

## Cut-over (2026-10-16)

User chose: drop the review's `make_page` for now (decisions stay in the quarterly log; re-add as a code step if missed), and keep the agent-only parts of the experiment harness as a start for phase 6 evals. Three steps: (1) runner and `langgraph.json` default to the agent graph, tests ported; (2) review cleanup: drop `make_page`, people questions/answers and the inbox; (3) delete the 0004 model steps, `names.py`, `answers.py`, `domain/facts.py`, fact routing and their tests, keeping shared helpers.
