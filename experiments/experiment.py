"""Round 2 of the 0006 spike: ingest 20 corpus days in order, like real use.

Each variant (model + skills + reviewer) runs the ingest agent graph and gets its own scratch vault, seeded with
the fixture pages. Every day:

    note → ingest until the review → score the first pass
         → reviewer feedback (labels = "oracle", a teacher model, or none) → one revise
         → approve all → apply the diffs to the scratch vault → score the final pass
         → on a preference day, the rule is added to 90_meta/rules.md

so later days see the wiki and the rules the earlier days built. Days are traced to
LangSmith (project `llm-wiki-r2`) with the day's scores as feedback.

Scores per day (first pass and final):
- recall: labelled facts with a filing citing their line
- right_page: of those, filed on an acceptable page
- extra: cited lines with no labelled fact (chores counted apart)
- misplaced: filings of a labelled line on a page that doesn't fit (e.g. an update
  copied into the decision log), even if another filing of it is right
- prefs: per preference, labelled facts that follow it / facts it applies to; whether
  the preference was active (taught on an earlier day) is recorded too
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import json
import os
import shutil
import time
import urllib.request
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import langsmith as ls
import yaml
from langchain_core.callbacks import BaseCallbackHandler, get_usage_metadata_callback
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tracers.context import register_configure_hook
from pydantic import BaseModel, Field

from llm_wiki.domain.changes import FileDiff, sha256_text
from llm_wiki.ingest.agent import SKILLS_DIR, _section_body
from llm_wiki.ingest.graph import builder as agent_builder
from llm_wiki.models import choose_model_spec, make_chat_model
from llm_wiki.runner import IngestRunner

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/vault"
CORPUS = ROOT / "tests/fixtures/corpus"
PROJECT = "llm-wiki-r2"
RULES = "90_meta/rules.md"
RETRIES = 4  # per day, only for rate limits (429)


# --- corpus -----------------------------------------------------------------------


def load_corpus() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (labels with line numbers resolved, preferences)."""
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
    prefs = yaml.safe_load((CORPUS / "preferences.yaml").read_text())
    for p in prefs:
        p["after"] = str(p["after"])
    return labels, prefs


def make_vault(dest: Path) -> Path:
    """Copy the fixture pages (no daily notes, no stray page) plus the corpus seed."""
    shutil.copytree(FIXTURE, dest)
    for note in (dest / "00_raw/daily").glob("*.md"):
        note.unlink()
    (dest / "20_people/Meeting with Alex.md").unlink(missing_ok=True)
    shutil.copytree(CORPUS / "seed", dest, dirs_exist_ok=True)
    return dest


# --- counting model calls -----------------------------------------------------------


class CallCounter(BaseCallbackHandler):
    """Counts finished model calls (each is one request to the provider)."""

    def __init__(self) -> None:
        """Start at zero."""
        self.calls = 0

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """Count one finished call."""
        self.calls += 1


_counter_var: ContextVar[CallCounter | None] = ContextVar("call_counter", default=None)
register_configure_hook(_counter_var, inheritable=True)


@contextlib.contextmanager
def count_calls() -> Iterator[CallCounter]:
    """Count model calls made inside the block."""
    counter = CallCounter()
    token = _counter_var.set(counter)
    try:
        yield counter
    finally:
        _counter_var.reset(token)


# --- what was filed -----------------------------------------------------------------


def filed_of(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the run's filings as dicts: line, path, section, text."""
    return list(values.get("agent_filed", []))


# --- scoring ------------------------------------------------------------------------


def _on(path: str, pages: list[str]) -> bool:
    return "*" in pages or any(p in path for p in pages)


def _follows(pref: str, hits: list[dict[str, Any]]) -> bool:
    want = {
        "one_on_one": ("20_people/", "## 1:1 notes"),
        "customer": ("10_projects/", "## Customer asks"),
        "interview": ("10_projects/project-hiring-q4", "## Interviews"),
    }[pref]
    return any(h["path"].startswith(want[0]) and h["section"] == want[1] for h in hits)


def score(
    day: str, filed: list[dict[str, Any]], labels: dict[str, Any], active: set[str]
) -> dict[str, Any]:
    """Score one day's filings against its labels (see the module docstring)."""
    d = labels["days"][day]
    facts = d["facts"]
    found = right = 0
    missed, wrong = [], []
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
        if pref and pref != "state":  # state is checked on the vault after apply
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
    extra = sorted(cited - {f["line"] for f in facts} - set(d["chore_lines"]))
    n = len(facts)
    return {
        "recall": round(found / n, 3),
        "right_page": round(right / n, 3),
        "found": found,
        "expected": n,
        "missed": [f["anchor"] for f in missed],
        "wrong_page": [(f["anchor"], p) for f, p in wrong],
        "chores_filed": chores,
        "extra_lines": extra,
        "misplaced": misplaced,
        "prefs": {
            k: {"ok": v[0], "of": v[1], "active": k in active} for k, v in prefs.items()
        },
        "_missed": missed,
        "_wrong": wrong,
    }


def vault_checks(
    vault: Path, day: str, labels: dict[str, Any], active: set[str]
) -> dict[str, Any]:
    """Check the vault after apply: Current state (P5) and candidate pages (P4)."""
    out: dict[str, Any] = {}
    states = [f for f in labels["days"][day]["facts"] if f.get("pref") == "state"]
    if states:
        ok = 0
        for f in states:
            pages = [
                p
                for p in (vault / "10_projects").glob("*.md")
                if f["project"] in p.name
            ]
            body = " ".join(
                line
                for p in pages
                for line in _section_body(p.read_text(), "## Current state")
            )
            ok += int(f["expect"].casefold() in body.casefold())
        out["state"] = {"ok": ok, "of": len(states), "active": "state" in active}
    candidates = [c.casefold() for c in labels["candidates"]]
    out["candidate_pages"] = [
        p.name
        for p in (vault / "20_people").glob("*.md")
        if any(c in p.name.casefold() for c in candidates)
    ]
    return out


# --- reviewers ----------------------------------------------------------------------


def oracle_feedback(
    day: str,
    scored: dict[str, Any],
    labels: dict[str, Any],
    active: set[str],
    note_lines: list[str],
) -> list[str]:
    """Feedback a careful user would give, from the labels and active preferences."""
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


class TeacherReview(BaseModel):
    """A reviewer model's verdict on one day's filing."""

    approve: bool = Field(description="True if the filing is good enough as is.")
    feedback: list[str] = Field(
        default_factory=list,
        description="Concrete fixes, one per item: missed facts (with line numbers), "
        "wrong pages, invented details, chores filed, rules not followed.",
    )


TEACHER_PROMPT = """\
You review how an assistant filed one daily note into a personal work wiki. Check: facts \
worth keeping that it missed (decisions, wins, project news, things about people, help \
given, customer requests, learnings), facts on the wrong page, details it invented, \
routine chores it filed, and the user's rules it did not follow. Be concise and \
concrete, cite note line numbers. Approve if there is nothing important to fix."""


def teacher_feedback(
    model: Any, note_text: str, filed: list[dict[str, Any]], rules: str
) -> list[str]:
    """Ask a (bigger) model to review the filing instead of the user."""
    numbered = "\n".join(
        f"{n:>4} | {t}" for n, t in enumerate(note_text.splitlines(), 1)
    )
    filing = "\n".join(
        f"- line {f['line']} → {f['path']} {f['section']}: {f['text']}" for f in filed
    )
    review = model.with_structured_output(TeacherReview).invoke(
        [
            SystemMessage(TEACHER_PROMPT),
            HumanMessage(
                f"User's rules:\n{rules}\n\nNote:\n{numbered}\n\n"
                f"Filing:\n{filing or '(nothing filed)'}"
            ),
        ]
    )
    return [] if review.approve else list(review.feedback)


# --- one variant --------------------------------------------------------------------


@dataclasses.dataclass
class Variant:
    """One experiment arm.

    Attributes:
        name: Short label, e.g. "glimmer-oracle".
        model: "provider:model" for the ingest model.
        reviewer: "oracle" (labels), "teacher" (a model) or "none" (approve as is).
        teacher: "provider:model" for the teacher reviewer.
        skills: Skills folder for the agent.
        days: Run only these days (default: all, in order).
    """

    name: str
    model: str
    reviewer: str = "oracle"
    teacher: str | None = None
    skills: Path = SKILLS_DIR
    days: list[str] | None = None


def apply_diffs(vault: Path, diffs: list[FileDiff]) -> None:
    """Write approved diffs to the scratch vault, refusing if a file changed meanwhile."""
    for d in diffs:
        path = vault / d.path
        before = path.read_text(encoding="utf-8") if path.exists() else None
        if (None if before is None else sha256_text(before)) != d.before_sha256:
            raise RuntimeError(f"{d.path} changed since it was rendered")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(d.after_text, encoding="utf-8")


def _state(runner: IngestRunner, thread_id: str) -> dict[str, Any]:
    return runner.graph.get_state(runner._config(thread_id)).values


def run_day(
    v: Variant,
    runner: IngestRunner,
    vault: Path,
    day: str,
    labels: dict[str, Any],
    prefs: list[dict[str, Any]],
    teacher: Any,
    attempt: int = 0,
) -> dict[str, Any]:
    """Ingest, review, revise once, approve and apply one day; return its scores.

    Nothing is written before the approval, so a failed attempt can simply be run
    again (on a new thread).
    """
    active = {p["check"] for p in prefs if p["after"] < day}
    taught = [p for p in prefs if p["after"] == day]
    src = CORPUS / "notes" / f"{day}.md"
    note = f"00_raw/daily/{day}.md"
    shutil.copy(src, vault / note)

    res = runner.start(note, thread_id=f"{v.name}-{day}-{attempt}")
    values = _state(runner, res.thread_id)
    filed = filed_of(values)
    first = score(day, filed, labels, active)
    if v.reviewer == "oracle":
        lines = src.read_text().splitlines()
        feedback = oracle_feedback(day, first, labels, active, lines)
    elif v.reviewer == "teacher":
        rules = (vault / RULES).read_text()
        feedback = teacher_feedback(teacher, src.read_text(), filed, rules)
    else:
        feedback = []
    feedback += [f"Remember from now on: {p['rule']}" for p in taught]

    final, revised = first, False
    if feedback and res.review is not None and res.review.revisions_left > 0:
        answer = {"action": "revise", "feedback": "\n".join(feedback)}
        res = runner.resume(res.thread_id, answer)
        values = _state(runner, res.thread_id)
        filed = filed_of(values)
        final, revised = score(day, filed, labels, active), True
    diffs = list(values.get("diffs", []))
    runner.resume(res.thread_id, {"action": "approve"})
    apply_diffs(vault, diffs)
    for p in taught:  # the approved "remember this"
        with (vault / RULES).open("a", encoding="utf-8") as fh:
            fh.write(f"\n- {p['rule']}\n")
    public = {"first": first, "final": final}
    return {
        **{
            k: {x: y for x, y in s.items() if not x.startswith("_")}
            for k, s in public.items()
        },
        "vault": vault_checks(vault, day, labels, active),
        "revised": revised,
        "feedback": feedback,
        "filed": filed,
        "refusals": (values.get("agent_stats") or {}).get("refusals"),
        "questions": values.get("questions", []),
        "files": [d.path for d in diffs],
    }


def openrouter_spent() -> float:
    """Return the account's total OpenRouter usage in dollars."""
    key = os.environ["OPENROUTER_API_KEY"]
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https URL
        return float(json.load(resp)["data"]["total_usage"])


def _transient(error: str) -> bool:
    """Return whether a provider error is worth retrying (rate limit, overload, 5xx)."""
    return any(
        s in error for s in ("429", "overloaded", "502", "503", "504", "timed out")
    )


def run_variant(
    v: Variant, out_dir: Path, *, reuse: bool = False
) -> list[dict[str, Any]]:
    """Run the days in order for one variant; keep per-day results and the final vault.

    With `reuse`, continue on the variant's existing vault and results (e.g. to re-run
    days that failed); re-run days replace their old rows.
    """
    labels, prefs = load_corpus()
    vault_dir = out_dir / "vaults" / v.name
    vault = vault_dir if reuse and vault_dir.exists() else make_vault(vault_dir)
    old_file = out_dir / f"{v.name}.json"
    previous = json.loads(old_file.read_text()) if reuse and old_file.exists() else []
    context = {"vault_path": str(vault), "model": v.model, "skills_dir": str(v.skills)}
    runner = IngestRunner.in_memory(context=context, graph_builder=agent_builder)
    teacher = None
    if v.reviewer == "teacher":
        teacher = make_chat_model(choose_model_spec(v.teacher, None), temperature=0)
    client = ls.Client()
    session = client.create_project(project_name=PROJECT, upsert=True).id
    results: list[dict[str, Any]] = []
    limit = float(os.environ.get("R2_MAX_TOTAL_USAGE", "0") or 0)
    paid = not v.model.endswith(":free")
    for day in v.days or list(labels["days"]):
        if paid and limit and (spent := openrouter_spent()) >= limit:
            msg = f"stop {v.name}: OpenRouter usage ${spent:.2f} >= ${limit}"
            print(msg, flush=True)  # noqa: T201 - progress line
            break
        run_id = uuid.uuid4()
        row: dict[str, Any] = {"variant": v.name, "day": day, "model": v.model}
        started = time.monotonic()
        meta = {"variant": v.name, "model": v.model, "day": day, "reviewer": v.reviewer}
        with (
            ls.tracing_context(project_name=PROJECT, enabled=True),
            ls.trace(
                f"{v.name} {day}", run_id=run_id, inputs={"day": day}, metadata=meta
            ) as rt,
            get_usage_metadata_callback() as usage,
            count_calls() as calls,
        ):
            for attempt in range(RETRIES + 1):
                try:
                    row |= run_day(
                        v, runner, vault, day, labels, prefs, teacher, attempt
                    )
                    row.pop("error", None)
                    break
                except Exception as exc:  # noqa: BLE001 - record it, go on
                    row["error"] = repr(exc)[:500]
                    if not _transient(row["error"]) or attempt == RETRIES:
                        break
                    time.sleep(60 * (attempt + 1))  # free models: shared rate limits
            rt.end(
                outputs={k: row.get(k) for k in ("first", "final", "vault", "error")}
            )
        row["seconds"] = round(time.monotonic() - started, 1)
        row["requests"] = calls.calls
        row["tokens"] = {
            k: sum(u.get(k, 0) for u in usage.usage_metadata.values())
            for k in ("input_tokens", "output_tokens")
        }
        if "error" not in row:
            for when in ("first", "final"):
                for key in ("recall", "right_page"):
                    client.create_feedback(
                        run_id,
                        key=f"{when}_{key}",
                        score=row[when][key],
                        session_id=session,
                    )
        previous = [r for r in previous if r["day"] != day]
        results.append(row)
        summary = {
            k: row.get(k) for k in ("variant", "day", "seconds", "requests", "error")
        }
        summary["recall"] = [row.get(w, {}).get("recall") for w in ("first", "final")]
        print(json.dumps(summary), flush=True)  # noqa: T201 - progress line
        merged = sorted([*previous, *results], key=lambda r: r["day"])
        old_file.write_text(json.dumps(merged, indent=1, default=str))
    return results


def timestamp() -> str:
    """Return a short UTC timestamp for output folder names."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M")
