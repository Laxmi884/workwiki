"""The review step's logic: build the request, check an answer, list what was approved.

Pure, so the `review` node only pauses and loops; see `ingest/nodes.py`.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

import pydantic

from llm_wiki.domain.changes import AppendToSection, Edit, FileDiff, is_wiki_page
from llm_wiki.domain.facts import SectionFacts
from llm_wiki.domain.review import (
    ReviewAnswer,
    ReviewDecision,
    ReviewFile,
    ReviewInboxItem,
    ReviewRequest,
)
from llm_wiki.routing import INBOX_PATH

MAX_REVISIONS = 3


def build_request(
    note: str,
    diffs: Sequence[FileDiff],
    facts: Mapping[str, SectionFacts],
    fact_edits: Iterable[Edit],
    warnings: Sequence[str],
    *,
    decision_pages: Collection[int] = (),
    revisions: int = 0,
    can_revise: bool = False,
    edited: Collection[str] = (),
) -> ReviewRequest:
    """Turn the rendered diffs and facts into what the user sees.

    Files are listed by path, so their IDs stay the same across review rounds while the
    same files are proposed. Warnings are listed once each (a revise round can repeat
    earlier ones).
    """
    diffs = sorted(diffs, key=lambda d: d.path)
    files = tuple(
        ReviewFile(
            id=f"f{i}",
            path=d.path,
            change="new" if d.before_sha256 is None else "changed",
            digest=d.digest,
            diff=d.unified_diff,
            editable=is_wiki_page(d.path),
            after_text=d.after_text if is_wiki_page(d.path) else "",
        )
        for i, d in enumerate(diffs, start=1)
    )
    found = [d for key in sorted(facts, key=int) for d in facts[key].decisions]
    decisions = tuple(
        ReviewDecision(
            id=f"d{i}", title=d.title, line=d.line, page=d.line in decision_pages
        )
        for i, d in enumerate(found, start=1)
    )
    inbox_lines = [
        line
        for edit in fact_edits
        if isinstance(edit, AppendToSection) and edit.path == INBOX_PATH
        for line in edit.lines
    ]
    inbox = tuple(
        ReviewInboxItem(id=f"i{i}", text=line.removeprefix("- "))
        for i, line in enumerate(inbox_lines, start=1)
    )
    return ReviewRequest(
        note=note,
        files=files,
        decisions=decisions,
        inbox=inbox,
        warnings=tuple(dict.fromkeys(warnings)),
        revisions_left=max(MAX_REVISIONS - revisions, 0) if can_revise else 0,
        can_revise=can_revise,
        edited=tuple(p for p in edited if p in {d.path for d in diffs}),
    )


def _unknown(kind: str, given: Sequence[str], valid: Sequence[str]) -> str | None:
    unknown = [i for i in given if i not in valid]
    if not unknown:
        return None
    return f"unknown {kind} ids {unknown}; valid ids: {', '.join(valid) or 'none'}"


def check_answer(
    request: ReviewRequest, raw: Any
) -> tuple[ReviewAnswer | None, str | None]:
    """Validate the user's answer. Returns (answer, None) or (None, why not).

    "approve" and "reject" may be sent as plain strings.
    """
    if isinstance(raw, str):
        raw = {"action": raw.strip().lower()}
    try:
        answer = ReviewAnswer.model_validate(raw)
    except pydantic.ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'answer'}: {e['msg']}"
            for e in exc.errors()
        )
        return None, f"invalid answer ({problems})"

    file_ids = [f.id for f in request.files]
    if answer.action == "approve":
        error = _unknown("file", answer.reject, file_ids)
    elif answer.action == "revise":
        if not request.can_revise:
            error = "nothing to revise: the note has no prose for the model to read"
        elif request.revisions_left == 0:
            error = (
                f"revise limit reached ({MAX_REVISIONS}); approve, edit, make_page "
                "or reject"
            )
        else:
            error = None
    elif answer.action == "make_page":
        error = _unknown(
            "decision", answer.decisions, [d.id for d in request.decisions]
        )
    elif answer.action == "edit":
        error = _unknown("file", [answer.file or ""], file_ids)
        if error is None:
            (target,) = [f for f in request.files if f.id == answer.file]
            if not target.editable:
                error = (
                    f"{target.id} is {target.path}, which only code writes; "
                    "edit a wiki page instead"
                )
    else:
        error = None
    return (None, error) if error else (answer, None)


def approved_digests(request: ReviewRequest, answer: ReviewAnswer) -> list[str]:
    """Return the digests of the files the answer approves (none for "reject")."""
    if answer.action != "approve":
        return []
    return [f.digest for f in request.files if f.id not in answer.reject]
