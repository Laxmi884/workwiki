# ruff: noqa: T201  (a CLI prints its progress)
"""Run the eval, or compare two runs. Needs a model and costs money; see README.md.

    uv run python -m tests.eval.run run                  # 20 days + Q&A
    uv run python -m tests.eval.run run --days 2 --skip-qa
    uv run python -m tests.eval.run qa <run dir>/vault   # Q&A only
    uv run python -m tests.eval.run compare <run A> <run B>
    uv run python -m tests.eval.run dataset --dry-run    # LangSmith Q&A dataset (0019)
    uv run python -m tests.eval.run qa <vault> --langsmith --out <dir>   # as an experiment

Each run writes `<out>/report.json` (saved after every day and question) and keeps the
finished vault in `<out>/vault/` (a git repo: one commit per approved day). `<out>`
defaults to `$WIKI_STATE_DIR/evals/<UTC time>-<model>`. `.env` is loaded first.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import langsmith as ls

from llm_wiki.config import get_settings, load_env_file
from llm_wiki.ingest import nodes
from llm_wiki.model_setup import configured_fallbacks, configured_model
from tests.eval import ingest_eval, langsmith_qa, qa_eval
from tests.eval.corpus import ROOT, load_labels, make_vault

TRACE_PROJECT = "llm-wiki-eval"


def openrouter_usage() -> float | None:
    """Return the OpenRouter key's total usage in dollars (None: no key or no answer)."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed URL
            return float(json.load(resp)["data"]["usage"])
    except OSError:
        return None


def code_version() -> dict[str, Any]:
    """Return the repo's commit and whether the working tree has changes."""

    def git(*args: str) -> str:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        )
        return out.stdout.strip()

    return {
        "commit": git("rev-parse", "--short", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
    }


@contextlib.contextmanager
def tracing(enabled: bool) -> Iterator[None]:
    """Trace to LangSmith project `llm-wiki-eval` only when asked (off by default).

    Off overrides `LANGSMITH_TRACING` from `.env`, so a plain eval run sends nothing.
    """
    with ls.tracing_context(enabled=enabled, project_name=TRACE_PROJECT):
        yield


@contextlib.contextmanager
def no_fallbacks(active: bool) -> Iterator[None]:
    """Measure one model: ingest gets no backup models while `active`."""
    original = nodes.fallback_models
    if active:
        nodes.fallback_models = lambda runtime: []  # type: ignore[assignment]
    try:
        yield
    finally:
        nodes.fallback_models = original  # type: ignore[assignment]


def save(out: Path, report: dict[str, Any]) -> None:
    """Write `<out>/report.json`."""
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))


def _out_dir(arg: Path | None, model: str) -> Path:
    if arg is not None:
        return arg
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M")
    name = model.rsplit("/", 1)[-1].replace(":", "-")
    return get_settings().state_dir / "evals" / f"{stamp}-{name}"


def _ask_all(
    out: Path, report: dict[str, Any], vault: Path, args: argparse.Namespace
) -> None:
    fallbacks = configured_fallbacks() if args.fallbacks else ()
    rows: list[dict[str, Any]] = []

    def on_question(row: dict[str, Any]) -> None:
        rows.append(row)
        report["qa"] = {"summary": qa_eval.summarize(rows), "rows": rows}
        save(out, report)
        print(f"  {row['score']:.2f}  {row['seconds']:>5}s  {row['q']}", flush=True)

    qa_eval.run_qa(
        vault,
        configured_model(args.model),
        fallbacks=fallbacks,
        on_question=on_question,
    )


def _ask_langsmith(
    out: Path, report: dict[str, Any], vault: Path, args: argparse.Namespace
) -> None:
    """Ask the questions as a LangSmith experiment (0019); the rows go in the report too."""
    fallbacks = configured_fallbacks() if args.fallbacks else ()
    run = langsmith_qa.target(vault, configured_model(args.model), fallbacks=fallbacks)
    results = langsmith_qa.experiment(
        run,
        repetitions=args.repetitions,
        metadata={
            "model": report["meta"]["qa_model"],
            "reasoning_effort": report["meta"]["qa_reasoning_effort"],
            "code": code_version(),
            "vault": str(vault),
        },
    )
    rows = langsmith_qa.rows(results)
    try:
        url = results.url
    except ValueError:  # results not uploaded (tests): no experiment page
        url = None
    report["qa"] = {
        "summary": qa_eval.summarize(rows),
        "rows": rows,
        "experiment": {
            "name": results.experiment_name,
            "url": url,
            "repetitions": args.repetitions,
        },
    }
    save(out, report)
    for row in rows:
        print(f"  {row['score']:.2f}  {row['seconds']:>5}s  {row['q']}", flush=True)
    print("experiment:", results.experiment_name, url or "", flush=True)


def cmd_run(args: argparse.Namespace) -> None:
    """Ingest the corpus into a fresh vault, then ask it the questions."""
    settings = get_settings()
    model = args.model or settings.model or "?"
    out = _out_dir(args.out, model)
    out.mkdir(parents=True, exist_ok=True)
    vault = make_vault(out / "vault")
    days = list(load_labels()["days"])
    days = args.only_days.split(",") if args.only_days else days[: args.days or None]
    report: dict[str, Any] = {
        "meta": {
            "started": datetime.datetime.now(datetime.UTC).isoformat(
                timespec="seconds"
            ),
            "code": code_version(),
            "model": model,
            "reasoning_effort": settings.reasoning_effort,
            "reviewer": args.reviewer,
            "fallbacks": args.fallbacks,
            "days": days,
            "openrouter_usage_before": openrouter_usage(),
        }
    }
    print("out:", out, flush=True)
    rows: list[dict[str, Any]] = []

    def on_day(row: dict[str, Any]) -> None:
        rows.append(row)
        report["ingest"] = {"summary": ingest_eval.summarize(rows), "days": rows}
        save(out, report)
        if "error" in row:
            print(f"{row['day']}  ERROR {row['error']}", flush=True)
            return
        first, final = row["first"], row["final"]
        print(
            f"{row['day']}  recall {first['recall']:.2f}→{final['recall']:.2f}  "
            f"right page {first['right_page']:.2f}→{final['right_page']:.2f}  "
            f"{row['seconds']}s  {row['model_calls']} calls  "
            f"{row['refusals']} refused",
            flush=True,
        )

    def stop() -> str | None:
        if args.max_spend is None or (spent := openrouter_usage()) is None:
            return None
        if spent >= args.max_spend:
            return f"OpenRouter usage ${spent:.3f} >= ${args.max_spend}"
        return None

    with tracing(args.trace), no_fallbacks(not args.fallbacks):
        ingest_eval.run_ingest(
            vault, args.model, args.reviewer, days, on_day=on_day, stop=stop
        )
        if not args.skip_qa and stop() is None:
            print("questions:", flush=True)
            _ask_all(out, report, vault, args)
    report["meta"]["openrouter_usage_after"] = openrouter_usage()
    save(out, report)
    print(json.dumps(headline(report), indent=1))


def cmd_qa(args: argparse.Namespace) -> None:
    """Ask an existing vault the questions; the report goes next to it."""
    vault = args.vault.resolve()
    out = args.out or vault.parent
    report_file = out / "report.json"
    report = (
        json.loads(report_file.read_text()) if report_file.exists() else {"meta": {}}
    )
    report["meta"]["qa_model"] = args.model or get_settings().model
    report["meta"]["qa_reasoning_effort"] = get_settings().reasoning_effort
    out.mkdir(parents=True, exist_ok=True)
    if args.langsmith:  # the experiment is traced to LangSmith by design
        _ask_langsmith(out, report, vault, args)
    else:
        with tracing(args.trace):
            _ask_all(out, report, vault, args)
    print(json.dumps(headline(report), indent=1))


def headline(report: dict[str, Any]) -> dict[str, Any]:
    """Return the numbers worth printing (and comparing) from a report."""
    head: dict[str, Any] = {}
    ingest = report.get("ingest", {}).get("summary")
    if ingest:
        for when in ("first", "final"):
            s = ingest[when]
            head[f"{when} recall"] = s["recall"]["rate"]
            head[f"{when} right page"] = s["right_page"]["rate"]
            head[f"{when} asked"] = s.get("asked")
            head[f"{when} extra lines"] = s["extra_lines"]
            head[f"{when} misplaced"] = s["misplaced"]
            head[f"{when} chores filed"] = s["chores_filed"]
            for name, p in s["prefs_taught"].items():
                head[f"{when} pref {name}"] = p["rate"]
        for key in ("refusals", "questions", "seconds", "model_calls"):
            head[f"ingest {key}"] = ingest[key]
        head["failed days"] = len(ingest["failed"])
        head["bookkeeping problems"] = len(ingest["bookkeeping_problems"])
        head["candidate pages"] = len(ingest["candidate_pages"])
    qa = report.get("qa", {}).get("summary")
    if qa:
        head["qa key facts"] = qa["key_facts"]
        head["qa full marks"] = qa["full_marks"]
        head["qa unanswerable ok"] = qa["unanswerable_ok"]
        head["qa seconds"] = qa["seconds"]
    meta = report.get("meta", {})
    before = meta.get("openrouter_usage_before")
    after = meta.get("openrouter_usage_after")
    if before is not None and after is not None:
        head["cost $"] = round(after - before, 4)
    return head


def cmd_compare(args: argparse.Namespace) -> None:
    """Print two runs' headline numbers side by side, then per-day/question changes."""
    a, b = (json.loads((p / "report.json").read_text()) for p in (args.a, args.b))
    ha, hb = headline(a), headline(b)
    print(f"{'':28} {'A':>10} {'B':>10}")
    for key in dict.fromkeys([*ha, *hb]):
        va, vb = ha.get(key), hb.get(key)
        mark = "" if va == vb else "  *"
        print(f"{key:28} {va!s:>10} {vb!s:>10}{mark}")
    days_a = {r["day"]: r for r in a.get("ingest", {}).get("days", [])}
    for r in b.get("ingest", {}).get("days", []):
        old = days_a.get(r["day"])
        if old and "final" in old and "final" in r:
            lost = sorted(set(r["final"]["missed"]) - set(old["final"]["missed"]))
            won = sorted(set(old["final"]["missed"]) - set(r["final"]["missed"]))
            if lost or won:
                print(f"{r['day']}: B missed {lost}; B found {won}")
    qa_a = {r["q"]: r["score"] for r in a.get("qa", {}).get("rows", [])}
    for r in b.get("qa", {}).get("rows", []):
        if r["q"] in qa_a and qa_a[r["q"]] != r["score"]:
            print(f"{qa_a[r['q']]:.2f} → {r['score']:.2f}  {r['q']}")


def cmd_dataset(args: argparse.Namespace) -> None:
    """Make the LangSmith dataset match the repo's questions; print what changed."""
    changes = langsmith_qa.sync(ls.Client(), args.name, dry_run=args.dry_run)
    verb = "would" if args.dry_run else "did"
    if changes.empty:
        print(f"{args.name}: matches the repo YAML, nothing to do")
        return
    print(
        f"{args.name}: {verb} create {len(changes.create)}, "
        f"update {len(changes.update)}, delete {len(changes.delete)}"
    )
    for e in changes.create + changes.update:
        print(f"  {e['inputs']['question']}")


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run the subcommand."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--model", help='"provider:model" (default: WIKI_MODEL)')
        p.add_argument("--out", type=Path, help="run folder")
        p.add_argument("--trace", action="store_true", help="trace to LangSmith")
        p.add_argument(
            "--fallbacks", action="store_true", help="allow WIKI_FALLBACK_MODELS"
        )

    run = sub.add_parser("run", help="ingest the corpus, then ask the questions")
    common(run)
    run.add_argument("--reviewer", choices=["oracle", "none"], default="oracle")
    run.add_argument("--days", type=int, default=0, help="only the first N days")
    run.add_argument("--only-days", default="", help="comma-separated days")
    run.add_argument("--skip-qa", action="store_true")
    run.add_argument(
        "--max-spend",
        type=float,
        default=None,
        help="stop before a day once OpenRouter usage (all time, $) reaches this",
    )
    run.set_defaults(func=cmd_run)

    qa = sub.add_parser("qa", help="ask an existing vault the questions")
    common(qa)
    qa.add_argument("vault", type=Path)
    qa.add_argument(
        "--langsmith",
        action="store_true",
        help=f"run as an experiment on the LangSmith dataset {langsmith_qa.DATASET}",
    )
    qa.add_argument(
        "--repetitions", type=int, default=1, help="with --langsmith: runs per question"
    )
    qa.set_defaults(func=cmd_qa)

    compare = sub.add_parser("compare", help="compare two run folders")
    compare.add_argument("a", type=Path)
    compare.add_argument("b", type=Path)
    compare.set_defaults(func=cmd_compare)

    dataset = sub.add_parser("dataset", help="sync the LangSmith Q&A dataset (0019)")
    dataset.add_argument("--name", default=langsmith_qa.DATASET)
    dataset.add_argument(
        "--dry-run", action="store_true", help="read only: show what would change"
    )
    dataset.set_defaults(func=cmd_dataset)

    args = parser.parse_args(argv)
    load_env_file()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
