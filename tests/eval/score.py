"""Scoring by code: one day's filing against its labels, the vault after apply, an answer.

No model grades anything here. Per day, first pass and after one revise:
- recall: labelled facts with a filing citing their line
- right_page: of all labelled facts, those filed on an acceptable page
- extra: cited lines with no label (chores counted apart)
- misplaced: filings of a labelled line on a page that doesn't fit (e.g. an update
  copied into the decision log), even if another filing of it is right
- prefs: per preference, labelled facts that follow it / facts it applies to
- numbers: numbers in a filed fact that its cited line doesn't contain (a hint for
  invented details, read by a person: "1h 30m" → "90 min" shows up too)

After apply (`vault_checks`): "## Current state" mentions what P5 expects, interview
candidates have no person page, every task line in the note has its ID and is in
`tasks.json`, and the ledger has the note.

Answers (`key_score`): the share of key-fact groups the answer contains; for an
unanswerable question, whether it says the notes don't say.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.ledger import LEDGER_PATH, load_ledger
from llm_wiki.task_tracking import TASK_INDEX_PATH, load_task_index

# A filing satisfies a preference when it lands on this folder and section.
PREF_PLACES = {
    "one_on_one": ("20_people/", "## 1:1 notes"),
    "customer": ("10_projects/", "## Customer asks"),
    "interview": ("10_projects/project-hiring-q4", "## Interviews"),
}
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _on(path: str, pages: list[str]) -> bool:
    return "*" in pages or any(p in path for p in pages)


def _follows(pref: str, hits: list[dict[str, Any]]) -> bool:
    folder, section = PREF_PLACES[pref]
    return any(h["path"].startswith(folder) and h["section"] == section for h in hits)


def unsupported_numbers(
    filed: list[dict[str, Any]], note_lines: list[str]
) -> list[dict[str, Any]]:
    """Return filings whose text has numbers the cited note line doesn't have."""
    out = []
    for f in filed:
        if not 1 <= f["line"] <= len(note_lines):
            continue
        source = note_lines[f["line"] - 1]
        missing = [n for n in NUMBER.findall(f["text"]) if n not in source]
        if missing:
            out.append({"line": f["line"], "path": f["path"], "numbers": missing})
    return out


def score(
    day: str,
    filed: list[dict[str, Any]],
    labels: dict[str, Any],
    note_lines: list[str],
) -> dict[str, Any]:
    """Score one day's filings (dicts: line, path, section, text) against its labels.

    Keys starting with "_" hold the label dicts for `oracle_feedback`; reports drop them.
    """
    d = labels["days"][day]
    facts = d["facts"]
    missed, wrong = [], []
    found = right = 0
    prefs: dict[str, list[int]] = {}
    for f in facts:
        hits = [x for x in filed if x["line"] == f["line"]]
        if not hits:
            missed.append(f)
            continue
        found += 1
        if any(_on(h["path"], f["pages"]) for h in hits):
            right += 1
        else:
            wrong.append((f, sorted({h["path"] for h in hits})))
        pref = f.get("pref")
        if pref in PREF_PLACES:  # "state" is checked on the vault after apply
            stat = prefs.setdefault(pref, [0, 0])
            stat[0] += int(_follows(pref, hits))
            stat[1] += 1
    chores = sorted({x["line"] for x in filed if x["line"] in d["chore_lines"]})
    if d["chore_lines"]:
        prefs["chores"] = [len(d["chore_lines"]) - len(chores), len(d["chore_lines"])]
    by_line = {f["line"]: f for f in facts}
    misplaced = sorted(
        {
            (x["line"], x["path"])
            for x in filed
            if x["line"] in by_line and not _on(x["path"], by_line[x["line"]]["pages"])
        }
    )
    cited = {x["line"] for x in filed}
    n = len(facts)
    return {
        "recall": round(found / n, 3),
        "right_page": round(right / n, 3),
        "found": found,
        "right": right,
        "expected": n,
        "missed": [f["anchor"] for f in missed],
        "wrong_page": [(f["anchor"], p) for f, p in wrong],
        "chores_filed": chores,
        "extra_lines": sorted(cited - set(by_line) - set(d["chore_lines"])),
        "misplaced": misplaced,
        "prefs": {k: {"ok": v[0], "of": v[1]} for k, v in prefs.items()},
        "numbers": unsupported_numbers(filed, note_lines),
        "_missed": missed,
        "_wrong": wrong,
    }


def public(scored: dict[str, Any]) -> dict[str, Any]:
    """Return a score without its "_" keys (for the report)."""
    return {k: v for k, v in scored.items() if not k.startswith("_")}


def oracle_feedback(
    day: str,
    scored: dict[str, Any],
    labels: dict[str, Any],
    active: set[str],
    note_lines: list[str],
) -> list[str]:
    """Return the revise feedback a careful user would give, from the labels.

    Missed facts, wrong pages, and (for preferences already taught) chores filed and
    rules not followed.
    """
    out = []
    for f in scored["_missed"]:
        quote = note_lines[f["line"] - 1].strip()
        out.append(f'Line {f["line"]} ("{quote}") is worth keeping but wasn\'t filed.')
    for f, paths in scored["_wrong"]:
        want = ", ".join(p for p in f["pages"] if p != "*")
        out.append(
            f"Line {f['line']} went to {', '.join(paths)}; it belongs on {want}."
        )
    if "chores" in active:
        out += [
            f"Line {line} is a routine chore; don't file it."
            for line in scored["chores_filed"]
        ]
    missed = {f["line"] for f in scored["_missed"]}
    for f in labels["days"][day]["facts"]:
        pref = f.get("pref")
        stat = scored["prefs"].get(pref or "")
        if (
            pref in active
            and f["line"] not in missed
            and stat
            and stat["ok"] < stat["of"]
        ):
            out.append(
                f"Line {f['line']}: remember my rule about "
                f"{pref.replace('_', ' ')} (see the rules)."
            )
    return list(dict.fromkeys(out))


def section_lines(text: str, heading: str) -> list[str]:
    """Return the non-empty lines under `heading` ("## Current state")."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip() == heading
        elif inside and line.strip():
            out.append(line.strip())
    return out


def vault_checks(
    vault: Path, day: str, note_path: str, labels: dict[str, Any]
) -> dict[str, Any]:
    """Check the vault after apply (see the module docstring)."""
    out: dict[str, Any] = {}
    states = [f for f in labels["days"][day]["facts"] if f.get("pref") == "state"]
    if states:
        ok = 0
        for f in states:
            body = " ".join(
                line
                for p in (vault / "10_projects").glob(f"*{f['project']}*.md")
                for line in section_lines(p.read_text(), "## Current state")
            )
            ok += int(f["expect"].casefold() in body.casefold())
        out["state"] = {"ok": ok, "of": len(states)}
    candidates = [c.casefold() for c in labels["candidates"]]
    out["candidate_pages"] = sorted(
        p.name
        for p in (vault / "20_people").glob("*.md")
        if any(c in p.name.casefold() for c in candidates)
    )
    out["bookkeeping"] = bookkeeping(vault, note_path)
    return out


def bookkeeping(vault: Path, note_path: str) -> dict[str, Any]:
    """Return task lines without an ID or missing from `tasks.json`, and the ledger."""
    note = parse_daily_note(note_path, (vault / note_path).read_text())
    index_file = vault / TASK_INDEX_PATH
    index, _ = load_task_index(index_file.read_text() if index_file.exists() else None)
    known = set(index.tasks) if index else set()
    ledger_file = vault / LEDGER_PATH
    ledger, _ = load_ledger(ledger_file.read_text() if ledger_file.exists() else None)
    return {
        "tasks": len(note.tasks),
        "without_id": [t.line_no for t in note.tasks if not t.id],
        "not_indexed": [t.line_no for t in note.tasks if t.id and t.id not in known],
        "in_ledger": bool(ledger and note_path in ledger.notes),
    }


NOT_SAID = (
    "don't say", "do not say", "doesn't say", "does not say", "no information",
    "not recorded", "doesn't mention", "does not mention", "not mentioned", "no record",
    "not in the notes", "isn't in", "is not in", "don't mention", "do not mention",
    "don't record", "do not record", "doesn't record", "does not record",
)  # fmt: skip


def key_score(answer: str, keys: list[list[str]] | str) -> float:
    """Return the share of key-fact groups `answer` contains (case-insensitive).

    A group is a list of accepted wordings. For `keys == "unanswerable"` the answer
    must say the notes don't say.
    """
    text = answer.casefold().replace("’", "'")
    if keys == "unanswerable":
        return float(any(p in text for p in NOT_SAID))
    hits = sum(any(w.casefold() in text for w in group) for group in keys)
    return round(hits / len(keys), 3)
