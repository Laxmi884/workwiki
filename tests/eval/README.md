# Eval (phase 6)

Twenty invented daily notes go through the real ingest graph, one day at a time, on a fresh vault. Then the real
assistant answers 30 questions about the finished vault. Code scores everything; no model grades. Not part of
`pytest`, because it needs a model and costs money (DeepSeek V4.1 Flash: about $0.35 for 20 days in 0006 round 2).
`tests/unit_tests/test_eval.py` checks the scoring and runs two days end to end with a scripted model.

```bash
uv run python -m tests.eval.run run --max-spend 3.00            # 20 days, then the questions
uv run python -m tests.eval.run run --days 2 --skip-qa          # a quick look
uv run python -m tests.eval.run run --model openrouter:<id>     # another model
uv run python -m tests.eval.run qa ~/.llm-wiki/evals/<run>/vault    # questions only
uv run python -m tests.eval.run compare ~/.llm-wiki/evals/<A> ~/.llm-wiki/evals/<B>
```

`.env` is loaded first (`WIKI_MODEL`, `WIKI_REASONING_EFFORT`, provider keys). Off by default, on with a flag:
- `--trace` sends traces to LangSmith project `llm-wiki-eval`. They hold the corpus text unredacted, but the corpus
  is invented.
- `--fallbacks` lets `WIKI_FALLBACK_MODELS` step in. Off by default so one run measures one model.

`--max-spend` stops before the next day once the OpenRouter key's total usage reaches that many dollars.

## What a day goes through

```
copy the note into the vault → ingest until the review → score the first pass
  → reviewer feedback → one revise → score again
  → on a preference's day: the rule as the review's "remember" answer (R3)
  → approve → the graph's apply writes and git-commits → check the vault
```

- **Reviewer:** `--reviewer oracle` (the default) plays a careful user, from the labels. Its feedback lists missed
  lines and wrong pages, each with the page it belongs on (by its slug in the vault, so the naming check accepts it),
  plus rules not followed once taught. It also answers the agent's questions: where a labelled line goes, "don't
  file" for a chore, "leave it out" for anything else. `none` approves as is.
- **Preferences:** P1–P5 (`preferences.yaml`) are taught on their day. That day's revise feedback carries the rule,
  and the review's "remember" answer saves it to `90_meta/rules.md`. Later days are scored on following it.

## Files

| File | What |
|---|---|
| `tests/fixtures/corpus/notes/` | 20 notes (2026-11-02 … 11-27), four styles: template, terse, freeform, mixed |
| `tests/fixtures/corpus/labels.yaml` | 82 labelled lines with acceptable pages, plus 11 chore lines and 2 interview candidates |
| `tests/fixtures/corpus/preferences.yaml` | 5 rules and the day each is taught |
| `tests/fixtures/corpus/qa.yaml`, `qa_keys.yaml` | 30 questions (2 unanswerable), key facts as accepted wordings |
| `corpus.py` | Loads the corpus; `make_vault` (fixture pages + seed, git init) |
| `score.py` | All scoring (see its docstring) |
| `ingest_eval.py` | One day through `IngestRunner`; totals |
| `qa_eval.py` | Questions to `build_assistant`, clock pinned to 2026-11-28 |
| `run.py` | CLI; writes `<out>/report.json` after every day and question |

## Reading a report

The headline (printed at the end; `compare` prints it for two runs):
- `first`/`final recall` and `right page`: the share of the 82 labelled lines filed, and filed on an acceptable
  page. Before and after the one revise.
- `asked`: labelled lines the agent didn't file but asked about. The guardrails tell it to ask when a line names no
  page, so these aren't silent misses; the oracle answers them in the revise.
- `extra lines`, `misplaced`, `chores filed`: noise. Extra lines aren't wrong in themselves, since the labels list
  what matters most, not everything.
- `pref <name>`: how often a taught preference was followed (only days after it was taught). `state` is P5,
  checked in each project's "## Current state" after apply.
- `numbers_to_check` (in the summary): numbers in a filed fact that its cited line doesn't contain. Read them:
  "1h 30m" → "90 min" is fine, an invented metric is not.
- `bookkeeping problems`, `uncommitted days`, `failed days`: should be 0. Anything else is a bug, not a model score.
- `qa key facts`: the mean share of key facts per answer. `unanswerable ok`: the assistant said "the notes don't
  say" when they don't.

One run is one sample. Decision 0006 found that differences under about 10 points between single runs are noise,
so only trust a difference that repeats.
