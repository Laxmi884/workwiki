"""Mirror the Q&A questions to the LangSmith dataset `llm-wiki-qa` and run them as experiments (0019).

The repo YAML (`qa.yaml`, `qa_keys.yaml`) stays the source: `sync` makes the dataset
match it, keyed by the question text, and changes nothing when they already match.
Only the invented corpus goes up; real notes never become examples.

`experiment` runs the questions through `langsmith.evaluate`: the target is the same
`qa_eval.ask` the local run uses, the evaluator is `score.key_score` (no LLM judge),
and `rows` turns the results into `report.json` rows, so both give the same score.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import langsmith
from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.language_models import BaseChatModel

from tests.eval import qa_eval
from tests.eval.corpus import load_questions
from tests.eval.ingest_eval import count_calls
from tests.eval.score import key_score

DATASET = "llm-wiki-qa"
DESCRIPTION = (
    "llm-wiki Q&A eval: 30 questions about the invented eval corpus, with key facts "
    "scored by code. Synced from tests/fixtures/corpus/qa*.yaml; don't edit here."
)


def examples(questions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return one example per question: inputs `question`, outputs `answer` and `keys`."""
    return [
        {
            "inputs": {"question": item["q"]},
            "outputs": {"answer": item["answer"], "keys": item["keys"]},
        }
        for item in questions or load_questions()
    ]


@dataclass(frozen=True)
class Plan:
    """What `sync` would change: examples to create, update (with `id`), delete (ids)."""

    create: list[dict[str, Any]]
    update: list[dict[str, Any]]
    delete: list[str]

    @property
    def empty(self) -> bool:
        return not (self.create or self.update or self.delete)


def plan(wanted: list[dict[str, Any]], existing: list[dict[str, Any]]) -> Plan:
    """Return the changes that make `existing` (with ids) match `wanted`, by question."""
    by_question: dict[str, dict[str, Any]] = {}
    duplicates = []
    for e in existing:
        q = e["inputs"].get("question")
        if q in by_question:
            duplicates.append(e["id"])
        else:
            by_question[q] = e
    create, update = [], []
    for w in wanted:
        e = by_question.pop(w["inputs"]["question"], None)
        if e is None:
            create.append(w)
        elif e["outputs"] != w["outputs"]:
            update.append({"id": e["id"], **w})
    return Plan(create, update, duplicates + [e["id"] for e in by_question.values()])


def sync(client: Any, name: str = DATASET, *, dry_run: bool = False) -> Plan:
    """Make the LangSmith dataset `name` match the repo YAML; return what changed.

    `client` is a `langsmith.Client`. With `dry_run`, read only: nothing is created,
    updated or deleted, and the returned plan says what would be.
    """
    existing: list[dict[str, Any]] = []
    dataset = None
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
        existing = [
            {"id": str(e.id), "inputs": e.inputs, "outputs": e.outputs or {}}
            for e in client.list_examples(dataset_id=dataset.id)
        ]
    changes = plan(examples(), existing)
    if dry_run or changes.empty:
        return changes
    if dataset is None:
        dataset = client.create_dataset(name, description=DESCRIPTION)
    if changes.create:
        client.create_examples(dataset_id=dataset.id, examples=changes.create)
    if changes.update:
        client.update_examples(dataset_id=dataset.id, updates=changes.update)
    if changes.delete:
        client.delete_examples(changes.delete)
    return changes


def key_facts(
    outputs: dict[str, Any] | None, reference_outputs: dict[str, Any]
) -> dict[str, Any]:
    """LangSmith evaluator: `key_score` of the answer against the example's key facts."""
    answer = (outputs or {}).get("answer", "")
    return {"key": "key_facts", "score": key_score(answer, reference_outputs["keys"])}


def target(
    vault: Path, model: BaseChatModel, *, fallbacks: Sequence[BaseChatModel] = ()
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return the experiment target: one question in, the answer plus its cost out."""

    def answer_question(inputs: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        with get_usage_metadata_callback() as usage, count_calls() as calls:
            answer = qa_eval.ask(vault, model, inputs["question"], fallbacks=fallbacks)
        return {
            "answer": answer,
            "seconds": round(time.monotonic() - started, 1),
            "model_calls": calls.calls,
            "tokens": {
                k: sum(u.get(k, 0) for u in usage.usage_metadata.values())
                for k in ("input_tokens", "output_tokens")
            },
        }

    return answer_question


def experiment(
    run: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    data: Any = DATASET,
    repetitions: int = 1,
    metadata: dict[str, Any] | None = None,
    upload_results: bool = True,
) -> Any:
    """Run every example in `data` through `run`, `repetitions` times, scored by `key_facts`.

    One question at a time (`max_concurrency=0`), like the local run. Returns
    LangSmith's `ExperimentResults`; `upload_results=False` keeps it all local (tests).
    """
    return langsmith.evaluate(
        run,
        data=data,
        evaluators=[key_facts],
        experiment_prefix="qa",
        metadata=metadata,
        num_repetitions=repetitions,
        max_concurrency=0,
        upload_results=upload_results,
    )


def rows(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one `report.json` row per run, shaped like `qa_eval.run_qa`'s rows."""
    out = []
    for r in results:
        run, ref = r["run"], r["example"].outputs or {}
        outputs = run.outputs or {}
        scores = {e.key: e.score for e in r["evaluation_results"]["results"]}
        row: dict[str, Any] = {
            "q": r["example"].inputs["question"],
            "reference": ref.get("answer"),
            "answer": outputs.get("answer", ""),
            "score": scores.get("key_facts") or 0.0,
            "unanswerable": ref.get("keys") == "unanswerable",
            "seconds": outputs.get("seconds", 0.0),
            "model_calls": outputs.get("model_calls", 0),
            "tokens": outputs.get("tokens", {"input_tokens": 0, "output_tokens": 0}),
        }
        if run.error:
            row["error"] = str(run.error)[:500]
        out.append(row)
    return out
