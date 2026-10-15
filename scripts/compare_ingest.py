# ruff: noqa: T201  (a CLI script prints its results)
"""Decision 0006 spike: run the 0004 pipeline and the ingest agent on the same notes.

Each run gets a fresh scratch copy of the fixture vault plus the spike notes, runs until
the review pauses (nothing is ever written), and is scored against hand-labelled facts:

- found: expected facts with at least one fact citing their line(s)
- right page: expected facts filed on an acceptable page
- extra: facts citing lines with nothing worth keeping
- refused/dropped: agent calls code refused; pipeline facts code dropped
- questions, seconds, tokens; stability = overlap of (line, page) between two runs

    uv run python scripts/compare_ingest.py --runs 2 --out /path/results.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from langchain_core.callbacks import get_usage_metadata_callback

from llm_wiki.diffs import vault_reader
from llm_wiki.domain.changes import AppendToSection
from llm_wiki.domain.facts import SectionFacts
from llm_wiki.entity_index import load_index
from llm_wiki.ingest.graph import builder as pipeline_builder
from llm_wiki.ingest_agent.graph import builder as agent_builder
from llm_wiki.routing import route_facts
from llm_wiki.runner import IngestRunner

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/vault"
SPIKE = ROOT / "tests/fixtures/spike"

# Expected facts: (lines that may be cited, acceptable page substrings, label).
# "*" accepts any page. Inbox counts as "parked", not right.
EXPECTED: dict[str, list[tuple[set[int], list[str], str]]] = {
    "00_raw/daily/2026-10-12.md": [
        ({22}, ["project-atlas"], "beta date moved"),
        ({23}, ["decisions-2026-q4", "30_decisions/decision-"], "keep old BI tool"),
        ({24, 25}, ["project-atlas", "person-priya-nair"], "flaky CI"),
        ({29}, ["person-sam-ortiz"], "Sam wants search"),
        ({34}, ["impact-2026"], "win: root cause"),
        ({43}, ["impact-2026", "person-priya-nair"], "helped Priya"),
        ({44}, ["impact-2026", "person-sam-ortiz"], "helped Sam"),
        ({47}, ["*"], "learned cookie domain"),
        ({48}, ["concept-feature-flags"], "flags expiry"),
    ],
    "00_raw/daily/2026-10-15.md": [
        ({11, 12}, ["project-atlas", "impact-2026"], "export bug root cause"),
        ({13, 14}, ["*"], "UBM blocked on ELTV"),
        ({20}, ["person-marta"], "Marta wants platform"),
        ({21}, ["person-marta", "project-atlas"], "Marta on-call worry"),
        ({22}, ["decisions-2026-q4", "30_decisions/decision-"], "Marta leads rewrite"),
        ({25}, ["project-atlas"], "GA Nov 18"),
        ({26}, ["project-atlas", "person-alex-chen"], "contractors"),
        ({27}, ["decisions-2026-q4", "project-atlas"], "drop PDF export"),
        ({30}, ["impact-2026"], "win: export bug"),
        ({31}, ["project-atlas", "person-viktor"], "scheduled exports ask"),
        ({34}, ["impact-2026", "person-viktor"], "helped Viktor"),
        ({37}, ["*"], "learned timestamptz"),
    ],
    "00_raw/daily/2026-10-16.md": [
        ({4, 5}, ["project-atlas"], "load test failed / GA"),
        ({6}, ["decisions-2026-q4", "30_decisions/decision-"], "no cache before GA"),
        ({8}, ["impact-2026", "person-sam-ortiz"], "helped Sam"),
        ({14}, ["person-hana"], "Hana wants walkthrough"),
        ({15}, ["impact-2026", "project-atlas"], "win: export fix shipped"),
        ({16}, ["*"], "learned EXPLAIN BUFFERS"),
    ],
}
INBOX = "05_generated/inbox.md"


def scratch_vault() -> Path:
    """Copy the fixture vault plus the spike notes to a new temp folder."""
    vault = Path(tempfile.mkdtemp(prefix="spike-vault-"))
    shutil.copytree(FIXTURE, vault, dirs_exist_ok=True)
    shutil.copytree(SPIKE, vault, dirs_exist_ok=True)
    return vault


def pipeline_filed(values: dict[str, Any], vault: Path) -> list[dict[str, Any]]:
    """Route each extracted fact alone to find which page(s) it lands on."""
    read = vault_reader(vault)
    entities = load_index(vault)
    filed = []
    for facts in values.get("facts", {}).values():
        for field in SectionFacts.model_fields:
            for f in getattr(facts, field):
                one = SectionFacts(**{field: [f]})
                edits, _ = route_facts(
                    values["note"], {"0": one}, entities, lambda p: read(p) is not None
                )
                paths = sorted(
                    {e.path for e in edits if isinstance(e, AppendToSection)}
                )
                for path in paths:
                    filed.append(
                        {
                            "line": f.line,
                            "path": path,
                            "kind": field,
                            "text": f.model_dump_json(exclude={"line"}),
                        }
                    )
    return filed


def score(note: str, filed: list[dict[str, Any]]) -> dict[str, Any]:
    """Score one run's filed facts against the expected facts for `note`."""
    expected = EXPECTED[note]
    wanted = set().union(*(lines for lines, _, _ in expected))
    found, right, parked, missed = 0, 0, 0, []
    for lines, pages, label in expected:
        hits = [f for f in filed if f["line"] in lines]
        if not hits:
            missed.append(label)
            continue
        found += 1
        if any("*" in pages or any(p in f["path"] for p in pages) for f in hits):
            right += 1
        elif any(f["path"] == INBOX for f in hits):
            parked += 1
    extra = sorted({f["line"] for f in filed if f["line"] not in wanted})
    return {
        "expected": len(expected),
        "found": found,
        "right_page": right,
        "parked_in_inbox": parked,
        "missed": missed,
        "extra_lines": extra,
    }


def run_once(kind: str, note: str, model: str | None) -> dict[str, Any]:
    """Run one graph on one note in a fresh vault until the review pauses."""
    vault = scratch_vault()
    context: dict[str, Any] = {"vault_path": str(vault)}
    if model:
        context["model"] = model
    graph_builder = agent_builder if kind == "agent" else pipeline_builder
    runner = IngestRunner.in_memory(context=context, graph_builder=graph_builder)
    started = time.monotonic()
    with get_usage_metadata_callback() as usage:
        result = runner.start(note)
    seconds = round(time.monotonic() - started, 1)
    values = runner.graph.get_state(runner._config(result.thread_id)).values
    if kind == "agent":
        filed = [dict(f, kind=f["section"]) for f in values.get("agent_filed", [])]
        refused = values.get("agent_stats", {}).get("refusals", 0)
        questions = list(values.get("questions", []))
    else:
        filed = pipeline_filed(values, vault)
        refused = sum("fact cites line" in w for w in result.warnings)
        questions = [f"{p.name} (lines {list(p.lines)})" for p in result.review.people]
    tokens = {
        k: sum(u.get(k, 0) for u in usage.usage_metadata.values())
        for k in ("input_tokens", "output_tokens")
    }
    pages = sorted(f.path for f in result.review.files)
    shutil.rmtree(vault)
    return {
        "kind": kind,
        "note": note,
        "seconds": seconds,
        "tokens": tokens,
        "score": score(note, filed),
        "refused_or_dropped": refused,
        "questions": questions,
        "files": pages,
        "filed": filed,
        "warnings": list(result.warnings),
        "agent_stats": values.get("agent_stats"),
    }


def stability(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> float:
    """Return the overlap (Jaccard) of (line, page) between two runs."""
    x = {(f["line"], f["path"]) for f in a}
    y = {(f["line"], f["path"]) for f in b}
    return round(len(x & y) / len(x | y), 2) if x | y else 1.0


def main() -> None:
    """Run every note × graph × run and print the scores."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--kinds", default="pipeline,agent")
    parser.add_argument("--notes", default=",".join(EXPECTED))
    args = parser.parse_args()
    results: list[dict[str, Any]] = []
    for note in args.notes.split(","):
        for kind in args.kinds.split(","):
            for i in range(args.runs):
                try:
                    r = run_once(kind, note, args.model)
                except Exception as exc:  # noqa: BLE001 - record and go on
                    r = {"kind": kind, "note": note, "error": repr(exc)}
                r["run"] = i + 1
                results.append(r)
                print(
                    json.dumps(
                        {
                            k: r.get(k)
                            for k in (
                                "kind",
                                "note",
                                "run",
                                "seconds",
                                "score",
                                "error",
                            )
                        }
                    ),
                    flush=True,
                )
                args.out.write_text(json.dumps(results, indent=2, default=str))
    for note in args.notes.split(","):
        for kind in args.kinds.split(","):
            rs = [
                r
                for r in results
                if r["note"] == note and r["kind"] == kind and "error" not in r
            ]
            if len(rs) >= 2:
                print(
                    note, kind, "stability", stability(rs[0]["filed"], rs[1]["filed"])
                )


if __name__ == "__main__":
    main()
