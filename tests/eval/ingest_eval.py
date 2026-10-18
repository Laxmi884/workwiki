"""Ingest the corpus day by day through the real ingest graph, like real use.

One fresh vault (`corpus.make_vault`), the days in order, so later days see the wiki
and the rules earlier days built. Every day:

    copy the note in → ingest until the review → score the first pass
      → reviewer feedback ("oracle": from the labels; "none": nothing) → one revise
      → score again
      → on a preference's day: the rule in the review's "remember" answer (R3)
      → approve → the graph's own `apply` writes and commits → check the vault

Everything goes through `IngestRunner` with an in-memory checkpointer; nothing here
writes to the vault itself except copying the day's note in.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler, get_usage_metadata_callback
from langchain_core.tracers.context import register_configure_hook

from llm_wiki.backend.base import RunResult
from llm_wiki.retries import is_transient
from llm_wiki.runner import IngestRunner
from tests.eval.corpus import load_labels, load_preferences, note_text
from tests.eval.score import (
    oracle_answers,
    oracle_feedback,
    public,
    score,
    vault_checks,
)

Reviewer = str  # "oracle" or "none"


class _CallCounter(BaseCallbackHandler):
    """Counts finished model calls (each is one request to the provider)."""

    def __init__(self) -> None:
        self.calls = 0

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        self.calls += 1


_counter: ContextVar[_CallCounter | None] = ContextVar("eval_calls", default=None)
register_configure_hook(_counter, inheritable=True)


@contextlib.contextmanager
def count_calls() -> Iterator[_CallCounter]:
    """Count the model calls made inside the block (subagents included)."""
    counter = _CallCounter()
    token = _counter.set(counter)
    try:
        yield counter
    finally:
        _counter.reset(token)


class DayFailed(RuntimeError):
    """The run didn't reach the expected state (no review, a refused answer, …)."""


def _expect_review(res: RunResult, step: str) -> RunResult:
    if res.status != "review" or res.review is None:
        raise DayFailed(f"{step}: expected a review, got {res.status} {res.warnings}")
    if res.review.error:
        raise DayFailed(f"{step}: answer refused: {res.review.error}")
    return res


def run_day(
    runner: IngestRunner,
    vault: Path,
    day: str,
    labels: dict[str, Any],
    prefs: list[dict[str, Any]],
    reviewer: Reviewer,
    thread_id: str,
) -> dict[str, Any]:
    """Ingest, review, approve and apply one day; return its scores and checks."""
    active = {p["check"] for p in prefs if p["after"] < day}
    taught = [p["rule"] for p in prefs if p["after"] == day]
    text = note_text(day)
    lines = text.splitlines()
    note = f"00_raw/daily/{day}.md"
    (vault / note).write_text(text)

    def scored(res: RunResult) -> dict[str, Any]:
        values = runner.graph.get_state(runner._config(thread_id)).values
        asked = [q.model_dump() for q in res.review.questions] if res.review else []
        return score(day, values.get("agent_filed", []), labels, lines, asked)

    res = _expect_review(runner.start(note, thread_id=thread_id), "start")
    first = scored(res)
    first_questions = [q.model_dump() for q in res.review.questions]  # type: ignore[union-attr]
    feedback, answers = [], {}
    if reviewer == "oracle":
        feedback = oracle_feedback(day, first, labels, active, lines, vault)
        answers = oracle_answers(day, first_questions, labels, vault)
    feedback += [f"Remember from now on: {rule}" for rule in taught]
    final, revised = first, False
    if (feedback or answers) and res.review.revisions_left > 0:  # type: ignore[union-attr]
        answer: dict[str, Any] = {"action": "revise"}
        if feedback:
            answer["feedback"] = "\n".join(feedback)
        if answers:
            answer["answers"] = answers
        res = _expect_review(runner.resume(thread_id, answer), "revise")
        final, revised = scored(res), True
    values = runner.graph.get_state(runner._config(thread_id)).values
    stats = values.get("agent_stats") or {}
    if taught:  # the user's "remember this", proposed in the same review (R3)
        res = _expect_review(
            runner.resume(thread_id, {"action": "answer", "rules": taught}), "remember"
        )
    done = runner.resume(thread_id, {"action": "approve"})
    if done.status != "done" or done.action != "approve":
        raise DayFailed(f"approve: ended {done.status} {done.warnings}")
    return {
        "active": sorted(active),
        "taught": taught,
        "first": public(first),
        "final": public(final),
        "revised": revised,
        "feedback": feedback,
        "answers": answers,
        "filed": values.get("agent_filed", []),
        "questions": values.get("questions", []),
        "refusals": stats.get("refusals", 0),
        "refused": stats.get("refused", []),
        "calls_by_tool": stats.get("calls_by_tool", {}),
        "written": list(done.written),
        "commit": done.commit,
        "warnings": list(done.warnings),
        "vault": vault_checks(vault, day, note, labels),
    }


def run_ingest(
    vault: Path,
    model: str | None,
    reviewer: Reviewer = "oracle",
    days: list[str] | None = None,
    *,
    on_day: Callable[[dict[str, Any]], None] | None = None,
    stop: Callable[[], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Run the days in order on `vault` (made with `make_vault`); return one row each.

    `model` is "provider:model" (None: `WIKI_MODEL`). `on_day` gets each finished row
    (progress, saving); `stop` is asked before each day and ends the run when it
    returns a reason (e.g. a spend limit). A day that fails is recorded with its
    error and the run goes on; a transient provider failure is tried once more.
    """
    labels, prefs = load_labels(), load_preferences()
    context: dict[str, Any] = {"vault_path": str(vault)}
    if model:
        context["model"] = model
    runner = IngestRunner.in_memory(context=context)  # type: ignore[arg-type]
    rows: list[dict[str, Any]] = []
    for day in days or list(labels["days"]):
        if stop is not None and (reason := stop()):
            rows.append({"day": day, "error": f"stopped: {reason}"})
            break
        row: dict[str, Any] = {"day": day, "style": labels["days"][day]["style"]}
        started = time.monotonic()
        with get_usage_metadata_callback() as usage, count_calls() as calls:
            for attempt in range(2):
                try:
                    row |= run_day(
                        runner, vault, day, labels, prefs, reviewer, f"{day}-{attempt}"
                    )
                    row.pop("error", None)
                    break
                except Exception as exc:  # noqa: BLE001 - recorded, the run goes on
                    row["error"] = f"{type(exc).__name__}: {exc}"[:500]
                    if attempt or not is_transient(exc):
                        break
        row["seconds"] = round(time.monotonic() - started, 1)
        row["model_calls"] = calls.calls
        row["tokens"] = {
            k: sum(u.get(k, 0) for u in usage.usage_metadata.values())
            for k in ("input_tokens", "output_tokens")
        }
        rows.append(row)
        if on_day is not None:
            on_day(row)
    return rows


def _ratio(ok: int, of: int) -> dict[str, Any]:
    return {"ok": ok, "of": of, "rate": round(ok / of, 3) if of else None}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the run's totals: pooled scores, preferences once taught, cost, failures."""
    done = [r for r in rows if "error" not in r]
    out: dict[str, Any] = {
        "days": len(rows),
        "failed": [r["day"] for r in rows if "error" in r],
    }
    for when in ("first", "final"):
        s = [r[when] for r in done]
        expected = sum(x["expected"] for x in s)
        out[when] = {
            "recall": _ratio(sum(x["found"] for x in s), expected),
            "right_page": _ratio(sum(x["right"] for x in s), expected),
            "asked": sum(len(x.get("asked", [])) for x in s),
            "extra_lines": sum(len(x["extra_lines"]) for x in s),
            "misplaced": sum(len(x["misplaced"]) for x in s),
            "chores_filed": sum(len(x["chores_filed"]) for x in s),
            "numbers_to_check": sum(len(x["numbers"]) for x in s),
        }
        prefs: dict[str, list[int]] = {}
        for r in done:
            for name, stat in r[when]["prefs"].items():
                if name in r["active"]:  # only once the preference was taught
                    p = prefs.setdefault(name, [0, 0])
                    p[0], p[1] = p[0] + stat["ok"], p[1] + stat["of"]
            state = r["vault"].get("state")
            if state and when == "final" and "state" in r["active"]:
                p = prefs.setdefault("state", [0, 0])
                p[0], p[1] = p[0] + state["ok"], p[1] + state["of"]
        out[when]["prefs_taught"] = {k: _ratio(*v) for k, v in sorted(prefs.items())}
    out["candidate_pages"] = done[-1]["vault"]["candidate_pages"] if done else []
    out["bookkeeping_problems"] = [
        r["day"]
        for r in done
        if (b := r["vault"]["bookkeeping"])["without_id"]
        or b["not_indexed"]
        or not b["in_ledger"]
    ]
    out["uncommitted_days"] = [r["day"] for r in done if not r["commit"]]
    out["refusals"] = sum(r["refusals"] for r in done)
    out["questions"] = sum(len(r["questions"]) for r in done)
    out["seconds"] = round(sum(r.get("seconds", 0) for r in rows), 1)
    out["model_calls"] = sum(r.get("model_calls", 0) for r in rows)
    out["tokens"] = {
        k: sum(r.get("tokens", {}).get(k, 0) for r in rows)
        for k in ("input_tokens", "output_tokens")
    }
    return out
