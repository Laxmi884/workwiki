# ruff: noqa: T201  (a CLI script prints its progress)
"""Run round-2 variants of the 0006 spike over the 20-day corpus (sequential days).

    uv run python -m experiments.run_experiment --variants ultra-oracle --days 2
    uv run python -m experiments.run_experiment --variants glimmer-oracle,qwen27-oracle

Results: <out>/<variant>.json and <out>/vaults/<variant>/ (the finished wiki).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from experiments.experiment import (
    Variant,
    load_corpus,
    run_variant,
    timestamp,
)

OR = "openrouter:"
MODELS = {
    "ultra": OR + "nvidia/nemotron-3-ultra-550b-a55b:free",
    "super": OR + "nvidia/nemotron-3-super-120b-a12b:free",
    "nano": OR + "nvidia/nemotron-3-nano-30b-a3b",
    "gemma31": OR + "google/gemma-4-31b-it:free",
    "gemma26": OR + "google/gemma-4-26b-a4b-it:free",
    "glimmer": OR + "meta/muse-glimmer-30b",
    "qwen27": OR + "qwen/qwen3.8-27b",
    "qwen35": OR + "qwen/qwen3.6-35b-a3b",
    "dsflash": OR + "deepseek/deepseek-v4.1-flash",
    "hy3": OR + "tencent/hy3",
}


def variant(name: str, examples: Path | None) -> Variant:
    """Parse "<model>-<reviewer>[-examples]" into a Variant."""
    parts = name.split("-")
    model, reviewer = MODELS[parts[0]], parts[1]
    v = Variant(name=name, model=model, reviewer=reviewer, teacher=MODELS["ultra"])
    if "examples" in parts:
        assert examples is not None, (
            "--examples-skills is needed for an -examples variant"
        )
        v.skills = examples
    return v


def main() -> None:
    """Run each named variant in turn."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", required=True)
    parser.add_argument("--days", type=int, default=0, help="only the first N days")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--examples-skills", type=Path, default=None)
    parser.add_argument(
        "--only-days", default="", help="comma-separated days to (re)run"
    )
    parser.add_argument("--reuse", action="store_true", help="continue existing vaults")
    args = parser.parse_args()
    out = args.out or Path("/tmp/llm-wiki-r2") / timestamp()
    out.mkdir(parents=True, exist_ok=True)
    labels, _ = load_corpus()
    days = list(labels["days"])[: args.days] if args.days else None
    if args.only_days:
        days = args.only_days.split(",")
    print("out:", out, flush=True)
    for name in args.variants.split(","):
        v = variant(name, args.examples_skills)
        v.days = days
        run_variant(v, out, reuse=args.reuse)


if __name__ == "__main__":
    main()
