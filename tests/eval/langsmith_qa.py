"""Mirror the Q&A questions to the LangSmith dataset `llm-wiki-qa` (0019).

The repo YAML (`qa.yaml`, `qa_keys.yaml`) stays the source: `sync` makes the dataset
match it, keyed by the question text, and changes nothing when they already match.
Only the invented corpus goes up; real notes never become examples.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tests.eval.corpus import load_questions

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
