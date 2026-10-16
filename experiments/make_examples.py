# ruff: noqa: T201  (a CLI script prints where it wrote)
"""Build a student's skills folder with worked examples from a teacher's run (0006 r2).

Takes the teacher's approved filings on the first N corpus days that the labels score
as right, plus the chores it correctly left out and one Current state it wrote, and
writes them into a copy of the skills as wiki-filing/examples.md.

    uv run python -m experiments.make_examples <out>/ultra-oracle.json <out>/vaults/ultra-oracle \
        <out>/skills-examples --days 5
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from experiments.experiment import CORPUS, load_corpus
from llm_wiki.ingest.agent import SKILLS_DIR, _section_body


def target_of(path: str) -> str:
    """Turn a page path back into the tool's target name."""
    if path.startswith("40_impact/"):
        return "impact"
    if path.startswith("30_decisions/2"):
        return "decision-log"
    return path.rsplit("/", 1)[-1].removesuffix(".md")


def main() -> None:
    """Write the examples skills folder."""
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("vault", type=Path)
    parser.add_argument("dest", type=Path)
    parser.add_argument("--days", type=int, default=5)
    args = parser.parse_args()
    labels, _ = load_corpus()
    rows = json.loads(args.results.read_text())[: args.days]
    parts = [
        "# Worked examples",
        "",
        "Approved filings from this wiki's own first days: how notes like the user's are",
        "filed here. Follow the same choices of page, section and wording.",
    ]
    for r in rows:
        day = r["day"]
        lines = (CORPUS / "notes" / f"{day}.md").read_text().splitlines()
        facts = {f["line"]: f for f in labels["days"][day]["facts"]}
        parts += ["", f"## Note {day}", ""]
        for f in r["filed"]:
            fact = facts.get(f["line"])
            if fact is None or not any(
                p == "*" or p in f["path"] for p in fact["pages"]
            ):
                continue
            section = f["section"].removeprefix("## ")
            parts.append(f'- Line "{lines[f["line"] - 1].strip()}"')
            target = target_of(f["path"])
            title = (
                f', title="{f["text"].rstrip(".")}"' if target == "decision-log" else ""
            )
            parts.append(
                f'  → file_fact(target="{target}", section="{section}", '
                f'text="{f["text"]}"{title})'
            )
        for line in labels["days"][day]["chore_lines"]:
            parts.append(
                f'- Line "{lines[line - 1].strip()}" → not filed (routine chore)'
            )
    atlas = args.vault / "10_projects/project-atlas.md"
    state = (
        _section_body(atlas.read_text(), "## Current state") if atlas.exists() else []
    )
    if state:
        parts += ["", "## A Current state (update_summary on project-atlas)", ""]
        parts += [f"- {line}" for line in state]
    shutil.copytree(SKILLS_DIR, args.dest, dirs_exist_ok=True)
    (args.dest / "wiki-filing/examples.md").write_text("\n".join(parts) + "\n")
    skill = args.dest / "wiki-filing/SKILL.md"
    skill.write_text(
        skill.read_text()
        + "\n## Worked examples\n\nRead /skills/wiki-filing/examples.md once: approved "
        "filings from this wiki's first days.\n"
    )
    print("wrote", args.dest / "wiki-filing/examples.md")


if __name__ == "__main__":
    main()
