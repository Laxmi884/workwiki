# ruff: noqa: T201  (a CLI script prints its tables)
"""Summarize 0006 round-2 results: one table per question the experiment asks.

uv run python -m experiments.summarize_r2 <out-dir>
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

from experiments.experiment import load_corpus

PREFS = ["chores", "one_on_one", "customer", "interview", "state"]


def pct(x: float | None) -> str:
    """Format a share as a whole percent."""
    return "  -  " if x is None else f"{100 * x:4.0f}%"


def main() -> None:
    """Print the tables for every <variant>.json in the folder."""
    out = Path(sys.argv[1])
    labels, _ = load_corpus()
    style = {d: v["style"] for d, v in labels["days"].items()}
    runs = {
        p.stem: [r for r in json.loads(p.read_text()) if "first" in r or "error" in r]
        for p in sorted(out.glob("*.json"))
        if not p.stem.startswith("qa-") and "aborted" not in p.stem
    }
    print("\n## Overall (first pass → after one revise)\n")
    print(
        "| variant | days | recall | right page | misplaced/day | extra/day | "
        "requests/day | input tok/day | errors |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for name, rows in runs.items():
        ok = [r for r in rows if "first" in r]
        if not ok:
            continue
        f = [r["first"] for r in ok]
        g = [r["final"] for r in ok]
        print(
            f"| {name} | {len(ok)} | {pct(mean(x['recall'] for x in f))} → "
            f"{pct(mean(x['recall'] for x in g))} | {pct(mean(x['right_page'] for x in f))}"
            f" → {pct(mean(x['right_page'] for x in g))} | "
            f"{mean(len(x['misplaced']) for x in g):.1f} | "
            f"{mean(len(x['extra_lines']) for x in g):.1f} | "
            f"{mean(r['requests'] for r in ok):.0f} | "
            f"{mean(r['tokens']['input_tokens'] for r in ok) / 1000:.0f}k | "
            f"{sum(1 for r in rows if 'first' not in r)} |"
        )
    print("\n## First-pass right page by note style (= by week: see the caveat)\n")
    styles = ["template", "terse", "freeform", "mixed"]
    print("| variant | " + " | ".join(styles) + " |")
    print("|---" * (len(styles) + 1) + "|")
    for name, rows in runs.items():
        by = defaultdict(list)
        for r in rows:
            if "first" in r:
                by[style[r["day"]]].append(r["first"]["right_page"])
        print(
            f"| {name} | "
            + " | ".join(pct(mean(by[s]) if by[s] else None) for s in styles)
            + " |"
        )
    print("\n## First-pass right page by week (does it hold up as the wiki grows?)\n")
    days = list(labels["days"])
    print("| variant | wk1 | wk2 | wk3 | wk4 |")
    print("|---|---|---|---|---|")
    for name, rows in runs.items():
        weeks = defaultdict(list)
        for r in rows:
            if "first" in r:
                weeks[days.index(r["day"]) // 5].append(r["first"]["right_page"])
        print(
            f"| {name} | "
            + " | ".join(pct(mean(weeks[w]) if weeks[w] else None) for w in range(4))
            + " |"
        )
    print(
        "\n## Preferences followed on the first pass (before → after it was taught)\n"
    )
    print("| variant | " + " | ".join(PREFS) + " | candidate pages |")
    print("|---" * (len(PREFS) + 2) + "|")
    for name, rows in runs.items():
        cells = []
        for p in PREFS:
            acc = {True: [0, 0], False: [0, 0]}
            for r in rows:
                if "first" not in r:
                    continue
                stat = (r["vault"] if p == "state" else r["first"]["prefs"]).get(p)
                if stat:
                    acc[stat["active"]][0] += stat["ok"]
                    acc[stat["active"]][1] += stat["of"]
            before = acc[False][0] / acc[False][1] if acc[False][1] else None
            after = acc[True][0] / acc[True][1] if acc[True][1] else None
            cells.append(f"{pct(before)} → {pct(after)}")
        cand = sorted(
            {c for r in rows if "error" not in r for c in r["vault"]["candidate_pages"]}
        )
        print(f"| {name} | " + " | ".join(cells) + f" | {', '.join(cand) or 'none'} |")


if __name__ == "__main__":
    main()
