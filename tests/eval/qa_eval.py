"""Ask a finished eval vault the 30 corpus questions with the real assistant (0008).

The assistant is built exactly as `llm_wiki.ask` builds it (skills, `find_tasks`,
date, redaction, tool list), one fresh agent per question, nothing saved. Code
scores each answer by its key facts (`score.key_score`); there is no LLM judge (in
0006 round 2 a small judge marked right answers wrong for adding a year).

The corpus is dated November 2026, so the assistant's clock is pinned to the day after
the last note: "now" questions mean the end of the corpus, not the day the eval runs.
"""

from __future__ import annotations

import datetime
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.language_models import BaseChatModel

from llm_wiki.assistant.agent import build_assistant
from llm_wiki.retries import is_transient
from tests.eval.corpus import load_questions
from tests.eval.ingest_eval import count_calls
from tests.eval.score import key_score

CORPUS_TODAY = datetime.date(2026, 11, 28)
RECURSION_LIMIT = 60


def answer(agent: Any, question: str) -> str:
    """Return the agent's final answer to one question."""
    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"recursion_limit": RECURSION_LIMIT},
    )
    return str(result["messages"][-1].text)


def ask(
    vault: Path,
    model: BaseChatModel,
    question: str,
    *,
    fallbacks: Sequence[BaseChatModel] = (),
) -> str:
    """Answer one question with a fresh assistant; retry once on a transient error."""
    for attempt in range(2):
        try:
            agent = build_assistant(
                vault, model, fallbacks=fallbacks, today=lambda: CORPUS_TODAY
            )
            return answer(agent, question)
        except Exception as exc:
            if attempt or not is_transient(exc):
                raise
    raise AssertionError("unreachable")


def run_qa(
    vault: Path,
    model: BaseChatModel,
    *,
    fallbacks: Sequence[BaseChatModel] = (),
    questions: list[dict[str, Any]] | None = None,
    on_question: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Ask each question (default: all 30) and score the answer; return one row each."""
    rows = []
    for item in questions or load_questions():
        row: dict[str, Any] = {"q": item["q"], "reference": item["answer"]}
        started = time.monotonic()
        with get_usage_metadata_callback() as usage, count_calls() as calls:
            try:
                row["answer"] = ask(vault, model, item["q"], fallbacks=fallbacks)
            except Exception as exc:  # noqa: BLE001 - recorded, the run goes on
                row["error"] = f"{type(exc).__name__}: {exc}"[:500]
        row["score"] = key_score(row.get("answer", ""), item["keys"])
        row["unanswerable"] = item["keys"] == "unanswerable"
        row["seconds"] = round(time.monotonic() - started, 1)
        row["model_calls"] = calls.calls
        row["tokens"] = {
            k: sum(u.get(k, 0) for u in usage.usage_metadata.values())
            for k in ("input_tokens", "output_tokens")
        }
        rows.append(row)
        if on_question is not None:
            on_question(row)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the mean key-fact score, full marks, unanswerable handling and cost."""
    scores = [r["score"] for r in rows]
    unanswerable = [r["score"] for r in rows if r["unanswerable"]]
    return {
        "questions": len(rows),
        "key_facts": round(sum(scores) / len(scores), 3) if scores else None,
        "full_marks": sum(s == 1.0 for s in scores),
        "zero": [r["q"] for r in rows if r["score"] == 0],
        "unanswerable_ok": f"{int(sum(unanswerable))}/{len(unanswerable)}",
        "failed": [r["q"] for r in rows if "error" in r],
        "seconds": round(sum(r["seconds"] for r in rows), 1),
        "model_calls": sum(r["model_calls"] for r in rows),
        "tokens": {
            k: sum(r["tokens"][k] for r in rows)
            for k in ("input_tokens", "output_tokens")
        },
    }
