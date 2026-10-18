---
phase: 6
date: 2026-10-17
tags: [evals, testing]
---

# Evals

## What it is

A fixed, labelled set of inputs that the *real* system (real model, real graph) runs over, scored by code, so two
runs can be compared after a change. Unit tests with a scripted model check the code; an eval measures the model
and the prompts. Four steps: label once as data, run, score in code, compare.

## How we used it

- `tests/eval/`:
  - `corpus.py`: labels with text anchors resolved to line numbers; `make_vault` = fixture pages + seed + `git init`.
  - `score.py`: recall, right page, extra, misplaced, preferences, numbers missing from the cited line, vault
    checks, key facts in answers.
  - `ingest_eval.py`: each day through `IngestRunner`: start → oracle revise → remember → approve, so the graph's
    own apply and git commit run.
  - `qa_eval.py`: the real assistant, clock pinned to 2026-11-28.
  - `run.py`: CLI (`run`, `qa`, `compare`); writes `report.json`.
- Corpus `tests/fixtures/corpus/`: 20 notes, 82 labelled lines + 11 chores, 5 preferences, 30 questions. It was
  written for the 0006 spike; the spike harness (`experiments/`) is now replaced and deleted.
- Two numbers per run:
  - **first**: the cold read. Watch it when changing skills or models.
  - **final**: after one revise from an oracle that reads the labels. It measures whether feedback is used, not
    first-pass quality.
- Preferences are taught through the review's remember answer (the user's own path) and scored only on days after
  they were taught.
- `tests/unit_tests/test_eval.py`: scoring, plus two corpus days end to end with a scripted model. Mutation-checked:
  skipping the remember answer or the oracle makes it fail. Notebook 7.1.

## Paid-media reference

`tests/eval/` has three parts:
- `questions.json`, with an `expect` line for each question;
- `run_questions.py`, which runs them against a live server;
- `grade.py`, which checks numeric answers against ground truth computed from the fixtures and prints a table for a
  person to judge the rest.

Same shape as ours (data → run → code grade → human look), but smaller: their agent only reads, so there are no
labelled filings and no reviewer.

## Gotchas

- Labels drift from the code. The spike's "93 labelled lines" counted the chores; the scoring test now pins 82 + 11.
- The guardrails (3.6) refuse a fact whose cited line names neither the target page nor sits under a heading or
  parent bullet that does. So a scripted "wrong page" filing has to be one code accepts. The first smoke script put
  a line about the events index on Sam's page and was refused.
- One run is one sample: 0006 saw differences under ~10 points between runs of the same setup.
- The first real runs mostly found bugs in our code, not the model: 10 of 27 calls refused on day 1 (a short-name check, a stale tool description, missing aliases), and a rule the naming check couldn't honour (0015). Fixing them cut refusals 39 → 9 and time in half.
- An oracle that only says "line 7 is worth keeping" scores the agent's correct questions as misses: it has to answer like a user (name the page, answer the questions) and count `asked` apart.
- A scorer can be wrong too: "decided **not** to add" failed a substring match until markdown was stripped. Read the low-scoring answers before believing the number.
- OpenRouter's `usage` lags by minutes, so a spend stop that reads it overshoots; a key credit limit is the real cap.
- Off by default:
  - LangSmith tracing (`--trace`); otherwise `.env` might turn it on;
  - fallback models (`--fallbacks`).

  So a plain run measures one model and sends no traces.

## In my words

