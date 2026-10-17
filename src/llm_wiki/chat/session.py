"""One chat for both agents, over any `Backend` (decision 0010, step S3).

    message ──(code)──┬─ "/…" → the ingest review: start, show or answer it
                      └─ else → backend.ask: the assistant answers

Routing is code: a leading "/" is a command, never a model's guess. The surface keeps
the conversation and passes it in; nothing here is stored. A command acts on the
vault's open review; with more than one open (e.g. one started from Studio) the chat
lists them instead of guessing which one you meant.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import Any

from llm_wiki.backend import (
    Backend,
    ChatTurn,
    NoOpenReview,
    NoteNotFound,
    RunResult,
    UnknownThread,
)
from llm_wiki.chat.commands import HELP, ChatCommand, is_command, parse_command
from llm_wiki.chat.render import render_result
from llm_wiki.domain.review import ReviewRequest


def questions_only(history: Sequence[ChatTurn]) -> list[ChatTurn]:
    """Return the turns the assistant should see: commands and their replies left out."""
    kept: list[ChatTurn] = []
    skip_reply = False
    for role, text in history:
        if role == "user":
            skip_reply = is_command(text)
            if not skip_reply:
                kept.append((role, text))
        elif not skip_reply:
            kept.append((role, text))
    return kept


def review_link(base: str, thread_id: str) -> str:
    """Return the review page for `thread_id` (`base` "" gives a relative link)."""
    return f"{base.rstrip('/')}/review/{thread_id}"


def reply(
    backend: Backend,
    message: str,
    history: Sequence[ChatTurn] = (),
    *,
    link_base: str = "",
    today: datetime.date | None = None,
) -> str:
    """Return the chat's answer to `message`, given the conversation so far."""
    if not is_command(message):
        answer = backend.ask(message, questions_only(history))
        if open_reviews := backend.pending():
            note = _note(open_reviews[0])
            answer += f"\n\n_(A review is still open for {note}: `/review` shows it.)_"
        return answer
    return _command(backend, message, link_base, today or datetime.date.today())


def _command(
    backend: Backend, message: str, link_base: str, today: datetime.date
) -> str:
    parsed = parse_command(message, today)
    if parsed.kind in ("help", "error"):
        return parsed.message or HELP
    open_reviews = backend.pending()

    if parsed.kind == "ingest":
        if open_reviews:
            return (
                f"A review is open for {_note(open_reviews[0])}: "
                "`/approve` or `/reject` it first (`/review` shows it)."
            )
        assert parsed.note_path is not None
        try:
            result = backend.start_ingest(parsed.note_path)
        except NoteNotFound:
            return f"No note at {parsed.note_path} in {backend.vault}."
        return render_result(result, review_link(link_base, result.thread_id))

    if not open_reviews:
        return "No review is open. Start one with `/ingest [YYYY-MM-DD]`."
    if len(open_reviews) > 1:
        rows = "\n".join(
            f"- [{_note(r)}]({review_link(link_base, r.thread_id)})"
            for r in open_reviews
        )
        return f"{len(open_reviews)} reviews are open; answer them on the review page:\n{rows}"
    (open_review,) = open_reviews
    if parsed.kind == "review":
        result = open_review
    else:
        answer = parsed.answer
        if parsed.kind == "remember":  # adds to the lists the review already has
            assert open_review.review is not None
            answer = _remember_answer(open_review.review, parsed)
        assert answer is not None
        try:
            result = backend.answer(open_review.thread_id, answer)
        except (UnknownThread, NoOpenReview) as exc:  # answered elsewhere meanwhile
            return f"Can't answer that review here: {exc}"
    return render_result(result, review_link(link_base, result.thread_id))


def _remember_answer(review: ReviewRequest, parsed: ChatCommand) -> dict[str, Any]:
    """Return the "answer" that remembers the review's rules or aliases plus one more."""
    if parsed.rule is not None:
        return {"action": "answer", "rules": [*review.rules, parsed.rule]}
    assert parsed.alias is not None
    name, slug = parsed.alias
    aliases = {a.name: a.slug for a in review.aliases}
    return {"action": "answer", "aliases": {**aliases, name: slug}}


def _note(result: RunResult) -> str:
    return result.review.note if result.review else result.thread_id
