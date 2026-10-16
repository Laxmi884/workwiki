# ruff: noqa: T201  (a CLI script prints its results)
"""Run the 0006 round-2 second-brain Q&A over finished vaults.

    uv run python -m experiments.run_qa --vaults <out>/vaults/ultra-oracle --scope wiki
    uv run python -m experiments.run_qa --vaults <out>/vaults/ultra-oracle --scope raw

Prints mean correctness per vault; details are in the LangSmith experiment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.qa import run_qa

ANSWER = "openrouter:deepseek/deepseek-v4.1-flash"
JUDGE = "openrouter:google/gemini-2.5-flash-lite"


def main() -> None:
    """Run the Q&A experiment for each vault."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--vaults", required=True, help="comma-separated vault folders")
    parser.add_argument("--scope", default="wiki", choices=["wiki", "raw", "all"])
    parser.add_argument("--answer-model", default=ANSWER)
    parser.add_argument("--judge-model", default=JUDGE)
    args = parser.parse_args()
    for vault in args.vaults.split(","):
        path = Path(vault)
        results = run_qa(
            path, path.name, args.scope, args.answer_model, args.judge_model
        )
        rows = [
            {
                "question": r["example"].inputs["question"],
                "answer": (r["run"].outputs or {}).get("answer"),
                "score": r["evaluation_results"]["results"][0].score,
                "comment": r["evaluation_results"]["results"][0].comment,
            }
            for r in results
            if r["evaluation_results"]["results"]
        ]
        judged = [r["score"] for r in rows if r["score"] is not None]
        mean = sum(judged) / len(judged) if judged else 0
        out = path.parent.parent / f"qa-{path.name}-{args.scope}.json"
        out.write_text(json.dumps(rows, indent=1))
        print(
            f"{path.name} {args.scope}: correctness {mean:.3f} over {len(judged)} "
            f"judged ({len(rows) - len(judged)} judge failures) -> {out}"
        )


if __name__ == "__main__":
    main()
