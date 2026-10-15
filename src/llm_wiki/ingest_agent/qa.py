"""Round 2 second-brain test (0006): ask a finished vault 30 known-answer questions.

One fixed Q&A deep agent (read-only `/vault/`) answers. Code scores each answer by the
share of key facts it contains (`qa_keys.yaml`); for an unanswerable question only "the
notes don't say" scores. An LLM judge (correct / partial / wrong) is optional: in the
first run a small judge marked right answers wrong for adding a year, so code scoring
is the default. Runs as a LangSmith
`evaluate` experiment over the dataset `llm-wiki-r2-qa`.

What the agent may read (`scope`):
- "wiki": the agent-maintained pages only (no 00_raw), to measure the wiki itself;
- "raw": the daily notes only, the no-wiki baseline;
- "all": both.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

import yaml
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.middleware import FilesystemMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import Client
from pydantic import BaseModel, Field

from llm_wiki.ingest_agent.agent import READ_TOOLS
from llm_wiki.ingest_agent.experiment import CORPUS
from llm_wiki.models import choose_model_spec, make_chat_model

DATASET = "llm-wiki-r2-qa"
Scope = Literal["wiki", "raw", "all"]

QA_PROMPT = """\
You answer questions about the user's work using their notes vault at /vault/. The vault \
has project pages (/vault/10_projects/), people (/vault/20_people/), concepts \
(/vault/15_concepts/), decisions (/vault/30_decisions/), wins and help given \
(/vault/40_impact/) and/or raw daily notes (/vault/00_raw/daily/). Use ls, grep and \
read_file to find the answer. Answer in 1-4 sentences with the specific dates, numbers \
and names. Use only what the vault says; if it doesn't say, answer "The notes don't say." \
Do not use write_todos or task."""


def scoped_vault(vault: Path, scope: Scope) -> Path:
    """Return a temp copy of `vault` limited to the scope's folders."""
    dest = Path(tempfile.mkdtemp(prefix=f"qa-{scope}-"))
    for item in vault.iterdir():
        is_raw = item.name == "00_raw"
        if item.name in ("90_meta", "50_templates") or not item.is_dir():
            continue
        if (scope == "wiki" and is_raw) or (scope == "raw" and not is_raw):
            continue
        shutil.copytree(item, dest / item.name)
    return dest


def build_qa_agent(vault: Path, model: Any) -> Any:
    """Return a read-only Q&A deep agent over `vault` (mounted at /vault/)."""
    backend = CompositeBackend(
        default=StateBackend(),
        routes={"/vault/": FilesystemBackend(root_dir=vault, virtual_mode=True)},
    )
    return create_deep_agent(
        model=model,
        system_prompt=QA_PROMPT,
        backend=backend,
        middleware=[FilesystemMiddleware(backend=backend, tools=READ_TOOLS)],
        name="qa-agent",
    )


class Grade(BaseModel):
    """The judge's grade for one answer."""

    reason: str = Field(description="One sentence: what matches or is missing/wrong.")
    grade: Literal["correct", "partial", "wrong"]


JUDGE_PROMPT = """\
You grade an answer against a reference answer about someone's work notes. The \
reference lists the key facts; it is not complete, so extra details in the answer \
(dates, full names, more context) are fine unless they contradict the reference.
- correct: has all the key facts of the reference (wording may differ), nothing \
contradicting it.
- partial: some key facts right, some missing, nothing contradicting.
- wrong: misses the key facts or states something that contradicts the reference.
If the reference says the question is unanswerable: correct if the answer says the \
notes don't say (mentioning related things it did find is fine); wrong if it gives a \
specific answer to the question."""


NOT_SAID = (
    "don't say", "do not say", "doesn't say", "does not say", "no information",
    "not recorded", "doesn't mention", "does not mention", "not mentioned", "no record",
    "not in the notes", "isn't in", "is not in", "don't mention", "do not mention",
    "don't record", "do not record", "doesn't record", "does not record",
)  # fmt: skip


def key_score(answer: str, keys: list[list[str]] | str) -> float:
    """Score an answer by code: the share of key-fact groups it contains.

    A group is a list of accepted wordings (case-insensitive substrings). For an
    unanswerable question (`keys == "unanswerable"`) the answer must say the notes
    don't say.
    """
    text = answer.casefold().replace("’", "'")
    if keys == "unanswerable":
        return float(any(p in text for p in NOT_SAID))
    hits = sum(any(w.casefold() in text for w in group) for group in keys)
    return round(hits / len(keys), 3)


def load_keys() -> dict[str, list[list[str]] | str]:
    """Return question → key-fact groups (tests/fixtures/corpus/qa_keys.yaml)."""
    items = yaml.safe_load((CORPUS / "qa.yaml").read_text())
    keys = yaml.safe_load((CORPUS / "qa_keys.yaml").read_text())
    return {i["q"]: k for i, k in zip(items, keys, strict=True)}


def ensure_dataset(client: Client) -> str:
    """Create the Q&A dataset once (examples from tests/fixtures/corpus/qa.yaml)."""
    if not client.has_dataset(dataset_name=DATASET):
        items = yaml.safe_load((CORPUS / "qa.yaml").read_text())
        ds = client.create_dataset(DATASET, description="0006 round 2 second-brain Q&A")
        client.create_examples(
            dataset_id=ds.id,
            examples=[
                {
                    "inputs": {"question": i["q"]},
                    "outputs": {
                        "answer": i["answer"],
                        "unanswerable": bool(i.get("unanswerable")),
                    },
                }
                for i in items
            ],
        )
    return DATASET


def run_qa(
    vault: Path,
    label: str,
    scope: Scope,
    answer_model: str,
    judge_model: str,
    max_concurrency: int = 2,
    use_judge: bool = False,
) -> Any:
    """Run the Q&A experiment for one vault and scope; return LangSmith's results."""
    client = Client()
    dataset = ensure_dataset(client)
    view = scoped_vault(vault, scope)
    answerer = make_chat_model(choose_model_spec(answer_model, None), temperature=0)
    judge = make_chat_model(
        choose_model_spec(judge_model, None), temperature=0
    ).with_structured_output(Grade)

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(4):  # provider overloads come and go
            try:
                agent = build_qa_agent(view, answerer)
                result = agent.invoke(
                    {"messages": [HumanMessage(inputs["question"])]},
                    config={"recursion_limit": 40},
                )
                return {"answer": result["messages"][-1].text}
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(20 * (attempt + 1))
        raise AssertionError("unreachable")

    keys = load_keys()

    def key_facts(inputs: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": "key_facts",
            "score": key_score(outputs["answer"], keys[inputs["question"]]),
        }

    def correctness(
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        reference_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        ref = (
            "UNANSWERABLE: the notes don't say."
            if reference_outputs.get("unanswerable")
            else reference_outputs["answer"]
        )
        g = judge.with_retry(stop_after_attempt=4, wait_exponential_jitter=True).invoke(
            [
                SystemMessage(JUDGE_PROMPT),
                HumanMessage(
                    f"Question: {inputs['question']}\nReference: {ref}\n"
                    f"Answer: {outputs['answer']}"
                ),
            ]
        )
        score = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}[g.grade]
        return {"key": "correctness", "score": score, "comment": g.reason}

    try:
        return client.evaluate(
            target,
            data=dataset,
            evaluators=[key_facts, correctness] if use_judge else [key_facts],
            experiment_prefix=f"qa-{label}-{scope}",
            max_concurrency=max_concurrency,
            metadata={
                "vault": label,
                "scope": scope,
                "answer_model": answer_model,
                "judge_model": judge_model,
            },
        )
    finally:
        shutil.rmtree(view, ignore_errors=True)
