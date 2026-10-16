"""Ingest reviews and results as chat messages (Markdown; pure)."""

from __future__ import annotations

from llm_wiki.domain.review import ReviewRequest
from llm_wiki.runner import RunResult

MAX_DIFF_LINES = 80


def _is_code_owned(path: str) -> bool:
    return path.startswith("90_meta/") and path.endswith(".json")


def _diff_block(diff: str) -> str:
    lines = diff.rstrip("\n").splitlines()
    if len(lines) > MAX_DIFF_LINES:
        more = len(lines) - MAX_DIFF_LINES
        lines = [*lines[:MAX_DIFF_LINES], f"... {more} more lines"]
    return "```diff\n" + "\n".join(lines) + "\n```"


def render_review(review: ReviewRequest) -> str:
    """Return the review as a message: files with diffs, tasks, wins, what to answer."""
    out = [f"**Review: {review.note}**"]
    if review.error:
        out.append(f"⚠️ Not accepted: {review.error}")
    out.append("**Files** (approval covers exactly these changes):")
    for f in review.files:
        mine = " (your edit)" if f.path in review.edited else ""
        out.append(f"- `{f.id}` {f.path}: {f.change}{mine}")
    for f in review.files:
        if _is_code_owned(f.path):
            continue  # task index and ledger: summarised by the lists below
        out.append(f"`{f.id}` {f.path}\n{_diff_block(f.diff)}")
    if review.tasks:
        out.append(
            "**Tasks → project** (in tasks.json as shown; change with `/tasks`):"
        )
        for t in review.tasks:
            who = {"model": f"proposed: {t.why}", "user": "your answer"}.get(
                t.by or "", "no project"
            )
            out.append(f"- `{t.id}` L{t.line} {t.text} → {t.project or '—'} ({who})")
    if review.wins:
        out.append("**Done today** (pick wins with `/wins`):")
        out += [
            f"- `{w.id}` L{w.line} {w.text}{' ✅ win' if w.chosen else ''}"
            for w in review.wins
        ]
    if review.warnings:
        out.append("**Notes from the run:**")
        out += [f"- {w}" for w in review.warnings]
    revise = (
        f" · `/revise <what to change>` ({review.revisions_left} left)"
        if review.can_revise and review.revisions_left
        else ""
    )
    out.append(
        f"Answer: `/approve` · `/approve except f2` · `/reject`{revise} · `/edit f3`"
    )
    return "\n\n".join(out)


def render_result(result: RunResult) -> str:
    """Return a finished or skipped run as a message (a review uses `render_review`)."""
    if result.status == "review" and result.review is not None:
        return render_review(result.review)
    if result.status == "skipped":
        why = "; ".join(result.warnings) or "nothing to do"
        return f"Nothing to ingest: {why}"
    if result.action == "reject" or not result.written:
        notes = "".join(f"\n- {w}" for w in result.warnings[-3:])
        return f"Done. Nothing was written.{notes}"
    files = "\n".join(f"- {p}" for p in result.written)
    commit = f"\n\nVault commit `{result.commit[:12]}`." if result.commit else ""
    return f"Applied {len(result.written)} files:\n{files}{commit}"
