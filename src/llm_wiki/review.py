"""The review step's logic: build the request, check an answer, list what was approved.

Pure, so the `review` node only pauses and loops; see `ingest/nodes.py`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pydantic

from llm_wiki.domain.changes import AppendToSection, Edit, FileDiff
from llm_wiki.domain.facts import SectionFacts
from llm_wiki.domain.review import (
    ReviewAnswer,
    ReviewDecision,
    ReviewFile,
    ReviewInboxItem,
    ReviewRequest,
)
from llm_wiki.routing import INBOX_PATH


def build_request(
    note: str,
    diffs: Sequence[FileDiff],
    facts: Mapping[str, SectionFacts],
    fact_edits: Iterable[Edit],
    warnings: Sequence[str],
) -> ReviewRequest:
    """Turn the rendered diffs and facts into what the user sees."""
    files = tuple(
        ReviewFile(
            id=f"f{i}",
            path=d.path,
            change="new" if d.before_sha256 is None else "changed",
            digest=d.digest,
            diff=d.unified_diff,
        )
        for i, d in enumerate(diffs, start=1)
    )
    found = [d for key in sorted(facts, key=int) for d in facts[key].decisions]
    decisions = tuple(
        ReviewDecision(id=f"d{i}", title=d.title, line=d.line)
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
        warnings=tuple(warnings),
    )


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
    known = {f.id for f in request.files}
    unknown = [i for i in answer.reject if i not in known]
    if unknown:
        valid = ", ".join(f.id for f in request.files)
        return None, f"unknown file ids {unknown}; valid ids: {valid}"
    return answer, None


def approved_digests(request: ReviewRequest, answer: ReviewAnswer) -> list[str]:
    """Return the digests of the files the answer approves (none for "reject")."""
    if answer.action == "reject":
        return []
    return [f.digest for f in request.files if f.id not in answer.reject]
