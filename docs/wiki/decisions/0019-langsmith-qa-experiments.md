---
id: 0019
date: 2026-10-23
status: accepted
---

# 0019: Q&A eval mirrored to LangSmith experiments; ingest eval stays local

## Context

The phase-6 eval (`tests/eval/`) is a home-made harness. The corpus is YAML in the repo, code scores everything
(`score.py`, no LLM judge), and each run writes `report.json`; `run.py compare` diffs two runs. LangSmith appears
only as opt-in tracing (`--trace`, project `llm-wiki-eval`). The user asked whether evals should go to LangSmith as
well. Two reasons to do it: this is a learning project and LangSmith datasets and experiments haven't been used yet;
and 0006 found that differences under about 10 points between single runs are noise, which repeated runs compared
side by side would show better than reading two `report.json` files.

The two halves of the eval differ in shape:
- **Q&A:** 30 independent questions (`qa.yaml`, `qa_keys.yaml`) against one finished vault, each scored by
  `score.key_score`. This matches LangSmith's model: a dataset of examples, a target function, evaluators.
- **Ingest:** 20 days that depend on each other. Each day changes the vault the next day reads, a day includes a
  review, one revise and an approve, and preferences taught on one day are scored on later days.

## Options

- **Leave it local only:** nothing new to keep working. No side-by-side experiment view, and the LangSmith evaluation
  workflow stays unlearned.
- **Move both halves to LangSmith:** one place for results. Ingest days aren't independent examples, so it would mean
  fighting the tool or rewriting the harness, and evals would need the network.
- **Mirror the Q&A half only, behind a flag:** a LangSmith dataset built from the repo YAML, the existing scorers as
  code evaluators, and `report.json` still written. Ingest stays local (at most `--trace`).

## Decision

Mirror the Q&A half only:
- The repo YAML stays the source of truth. A script uploads `qa.yaml` + `qa_keys.yaml` as the LangSmith dataset
  `llm-wiki-qa` (inputs: the question; reference outputs: the reference answer and the key facts, or `unanswerable`).
  If the YAML changes, the dataset is re-synced; nobody edits it in the LangSmith UI.
- `run.py qa --langsmith` (off by default) runs the questions through LangSmith's `evaluate` against a given finished
  vault: the target builds the assistant exactly as `qa_eval.run_qa` does, with the clock pinned to 2026-11-28, and the
  evaluators wrap `score.key_score` (key facts, unanswerable handled). Still no LLM judge. `report.json` is written as
  before, so a run without the network or the flag measures the same thing.
- Repeats use `evaluate`'s `num_repetitions` (API checked against docs.langchain.com/langsmith on 2026-10-23), each
  paid run quoted first under the model budget.
- The ingest eval stays in the local harness unchanged.
- Only the invented corpus goes to LangSmith; real notes never become dataset examples.

## Why

The Q&A questions already fit LangSmith's dataset-and-evaluators model, so mirroring them buys the experiment view
and repeated runs (and the learning) without changing what is measured. The ingest days don't fit, and the local
`report.json` keeps every eval working without the network.
