"""The eval corpus (`tests/fixtures/corpus/`): labels, preferences, questions, a fresh vault.

Invented data, written for decision 0006 round 2: 20 daily notes in four styles, 93
labelled lines, 5 preferences the simulated user teaches on the way, 30 questions with
key facts code can check.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/vault"
CORPUS = ROOT / "tests/fixtures/corpus"


def load_labels() -> dict[str, Any]:
    """Return `labels.yaml` with each anchor resolved to its note line number.

    Adds `line` to every fact and `chore_lines` to every day.

    Raises:
        AssertionError: An anchor matches no line or more than one.
    """
    labels = yaml.safe_load((CORPUS / "labels.yaml").read_text())
    labels["days"] = {str(k): v for k, v in labels["days"].items()}
    for day, d in labels["days"].items():
        lines = (CORPUS / "notes" / f"{day}.md").read_text().splitlines()

        def find(anchor: str, lines: list[str] = lines, day: str = day) -> int:
            hits = [n for n, text in enumerate(lines, 1) if anchor in text]
            assert len(hits) == 1, f"{day}: anchor {anchor!r} matches {hits}"
            return hits[0]

        for fact in d["facts"]:
            fact["line"] = find(fact["anchor"])
        d["chore_lines"] = [find(a) for a in d.get("chores", [])]
    return labels


def load_preferences() -> list[dict[str, Any]]:
    """Return `preferences.yaml`: id, the day it is taught (`after`), check, rule."""
    prefs = yaml.safe_load((CORPUS / "preferences.yaml").read_text())
    for p in prefs:
        p["after"] = str(p["after"])
    return prefs


def load_questions() -> list[dict[str, Any]]:
    """Return the questions with their key facts: q, answer, keys.

    `keys` is a list of groups of accepted wordings, or "unanswerable".
    """
    items = yaml.safe_load((CORPUS / "qa.yaml").read_text())
    keys = yaml.safe_load((CORPUS / "qa_keys.yaml").read_text())
    return [
        {
            "q": i["q"],
            "answer": i["answer"],
            "keys": "unanswerable" if i.get("unanswerable") else k,
        }
        for i, k in zip(items, keys, strict=True)
    ]


def note_text(day: str) -> str:
    """Return the corpus note for `day` ("2026-11-02")."""
    return (CORPUS / "notes" / f"{day}.md").read_text()


def make_vault(dest: Path) -> Path:
    """Build a fresh eval vault at `dest`: fixture pages + corpus seed, git-initialised.

    No daily notes (the eval adds one per day) and no stray page. The vault is a git
    repo with one commit, so `apply` commits each approved day like on the real vault.
    """
    shutil.copytree(
        FIXTURE, dest, ignore=shutil.ignore_patterns("__init__.py", "__pycache__")
    )
    for note in (dest / "00_raw/daily").glob("*.md"):
        note.unlink()
    (dest / "20_people/Meeting with Alex.md").unlink(missing_ok=True)
    shutil.copytree(CORPUS / "seed", dest, dirs_exist_ok=True)
    who = ["-c", "user.name=llm-wiki eval", "-c", "user.email=eval@localhost"]
    for args in (["init", "-q"], ["add", "-A"], [*who, "commit", "-q", "-m", "seed"]):
        subprocess.run(["git", *args], cwd=dest, check=True, capture_output=True)
    return dest
