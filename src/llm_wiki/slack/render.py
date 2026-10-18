"""What Slack shows, from what the backend returns (pure; no Slack calls).

- `Progress` → task_update chunks (only steps that changed) and answer text deltas.
- `RunResult` → the chat's markdown (`chat.render`) and Approve / Reject / Open buttons.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from llm_wiki.backend import RunResult
from llm_wiki.chat.render import render_result
from llm_wiki.chat.session import review_link
from llm_wiki.progress import Progress, Step

APPROVE = "wiki_approve"
REJECT = "wiki_reject"
OPEN_PAGE = "wiki_open_page"
MAX_MARKDOWN = 12_000  # markdown_text in chat.appendStream / chat.stopStream
MAX_TASK_TEXT = 256  # task_update chunk text
CUT = "\n\n… (cut: open the review page for the rest)"

# Slack has no "skipped": a step the run never needed shows as complete, "skipped".
_STATUS = {
    "pending": "pending",
    "running": "in_progress",
    "done": "complete",
    "skipped": "complete",
}
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _short(text: str, limit: int = MAX_TASK_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _chunk(step: Step) -> dict[str, str]:
    chunk = {
        "type": "task_update",
        "id": step.key,
        "title": _short(step.label),
        "status": _STATUS[step.state],
    }
    details = step.detail
    if details is None and step.state == "skipped":
        details = "skipped"
    elif details is None and step.calls:
        details = f"{step.calls} tool calls"
    if details:
        chunk["details"] = _short(details)
    return chunk


def task_chunks(before: Progress | None, after: Progress) -> list[dict[str, str]]:
    """Return a task chunk per step that is new or changed since `before`."""
    old = {s.key: s for s in before.steps} if before else {}
    return [_chunk(s) for s in after.steps if old.get(s.key) != s]


def text_delta(sent: str, text: str) -> str:
    """Return what to append so the reader sees `text`, given `sent` already shown.

    Streamed text can't be taken back: when the answer restarts (the assistant
    searched after a preamble), the new text goes after a blank line.
    """
    if text.startswith(sent):
        return text[len(sent) :]
    return "\n\n" + text if text else ""


def public_review_link(review_url: str, thread_id: str) -> str | None:
    """Return the review page link, or None when it would only work on this machine."""
    host = urlsplit(review_url).hostname or ""
    return None if host in _LOOPBACK else review_link(review_url, thread_id)


def reply_text(result: RunResult, review_url: str | None) -> str:
    """Return the result as markdown (the chat's own rendering), cut to Slack's limit."""
    link = public_review_link(review_url, result.thread_id) if review_url else None
    text = render_result(result, link)
    return text if len(text) <= MAX_MARKDOWN else text[: MAX_MARKDOWN - len(CUT)] + CUT


def review_buttons(result: RunResult, review_url: str | None) -> list[dict[str, Any]]:
    """Return Approve all / Reject (+ Open review page) for an open review, else []."""
    if result.status != "review" or result.review is None:
        return []
    value = json.dumps({"thread": result.thread_id, "seen": result.review.token})
    elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "action_id": APPROVE,
            "style": "primary",
            "text": {"type": "plain_text", "text": "Approve all"},
            "value": value,
        },
        {
            "type": "button",
            "action_id": REJECT,
            "style": "danger",
            "text": {"type": "plain_text", "text": "Reject"},
            "value": value,
        },
    ]
    link = public_review_link(review_url, result.thread_id) if review_url else None
    if link:
        elements.append(
            {
                "type": "button",
                "action_id": OPEN_PAGE,
                "url": link,
                "text": {"type": "plain_text", "text": "Open review page"},
            }
        )
    return [{"type": "actions", "block_id": "wiki_review", "elements": elements}]
