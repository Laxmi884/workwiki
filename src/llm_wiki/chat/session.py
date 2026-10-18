"""One chat for both agents, over any `Backend` (decision 0010, step S3).

    message ──(code)──┬─ "/…" → the ingest review: start, show or answer it
                      └─ else → backend.ask: the assistant answers

Routing is code: a leading "/" is a command, never a model's guess. The surface keeps
the conversation and passes it in; nothing here is stored. A command acts on the
vault's open review; with more than one open (e.g. one started from Studio) the chat
lists them instead of guessing which one you meant.

`stream_reply` is the same chat, yielding the run's progress first (Slack draws it);
`reply` keeps only its last text.
"""

from __future__ import annotations

import datetime
from collections import deque
from collections.abc import Generator, Iterator, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from llm_wiki.backend import (
    Backend,
    ChatTurn,
    Finished,
    NoOpenReview,
    NoteNotFound,
    ReviewBusy,
    RunResult,
    StreamEvent,
    UnknownThread,
)
from llm_wiki.chat.commands import HELP, ChatCommand, is_command, parse_command
from llm_wiki.chat.render import render_result
from llm_wiki.domain.review import ReviewRequest
from llm_wiki.progress import Progress


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


class ChatReply(BaseModel):
    """The chat's last event: the reply text, and what a surface may add to it.

    Attributes:
        text: The reply, as `reply` returns it.
        result: The review it shows or answered, if any (a surface adds buttons).
        progress: The run's settled steps, if a run happened.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    result: RunResult | None = None
    progress: Progress | None = None


def _follow(events: Iterator[StreamEvent]) -> Generator[Progress, None, Finished]:
    """Pass a backend stream's progress through; return its `Finished` event."""
    for event in events:
        if isinstance(event, Finished):
            return event
        yield event
    raise RuntimeError("the backend stream ended without a Finished event")


def reply(
    backend: Backend,
    message: str,
    history: Sequence[ChatTurn] = (),
    *,
    link_base: str = "",
    today: datetime.date | None = None,
) -> str:
    """Return the chat's answer to `message`, given the conversation so far."""
    (last,) = deque(
        stream_reply(backend, message, history, link_base=link_base, today=today),
        maxlen=1,
    )
    assert isinstance(last, ChatReply)
    return last.text


def stream_reply(
    backend: Backend,
    message: str,
    history: Sequence[ChatTurn] = (),
    *,
    link_base: str = "",
    today: datetime.date | None = None,
) -> Iterator[Progress | ChatReply]:
    """Like `reply`, yielding the run's progress first; ends with one `ChatReply`."""
    if not is_command(message):
        finished = yield from _follow(
            backend.stream_ask(message, questions_only(history))
        )
        answer = finished.answer or ""
        if open_reviews := backend.pending():
            note = _note(open_reviews[0])
            answer += f"\n\n_(A review is still open for {note}: `/review` shows it.)_"
        yield ChatReply(text=answer, progress=finished.progress)
        return
    yield from _command(backend, message, link_base, today or datetime.date.today())


def _command(
    backend: Backend, message: str, link_base: str, today: datetime.date
) -> Iterator[Progress | ChatReply]:
    parsed = parse_command(message, today)
    if parsed.kind in ("help", "error"):
        yield ChatReply(text=parsed.message or HELP)
        return
    open_reviews = backend.pending()

    if parsed.kind == "ingest":
        if open_reviews:
            yield ChatReply(
                text=f"A review is open for {_note(open_reviews[0])}: "
                "`/approve` or `/reject` it first (`/review` shows it)."
            )
            return
        assert parsed.note_path is not None
        try:
            finished = yield from _follow(backend.stream_ingest(parsed.note_path))
        except NoteNotFound:
            yield ChatReply(text=f"No note at {parsed.note_path} in {backend.vault}.")
            return
        yield _shown(finished.result, link_base, finished.progress)
        return

    if not open_reviews:
        yield ChatReply(
            text="No review is open. Start one with `/ingest [YYYY-MM-DD]`."
        )
        return
    if len(open_reviews) > 1:
        rows = "\n".join(
            f"- [{_note(r)}]({review_link(link_base, r.thread_id)})"
            for r in open_reviews
        )
        yield ChatReply(
            text=f"{len(open_reviews)} reviews are open; answer them on the review page:\n{rows}"
        )
        return
    (open_review,) = open_reviews
    if parsed.kind == "review":
        yield _shown(open_review, link_base)
        return
    answer = parsed.answer
    if parsed.kind == "remember":  # adds to the lists the review already has
        assert open_review.review is not None
        answer = _remember_answer(open_review.review, parsed)
    assert answer is not None
    try:
        finished = yield from _follow(
            backend.stream_answer(open_review.thread_id, answer)
        )
    except (UnknownThread, NoOpenReview, ReviewBusy) as exc:  # answered elsewhere
        yield ChatReply(text=f"Can't answer that review here: {exc}")
        return
    yield _shown(finished.result, link_base, finished.progress)


def _shown(
    result: RunResult | None, link_base: str, progress: Progress | None = None
) -> ChatReply:
    """Return the reply that shows `result` with its review link."""
    assert result is not None
    return ChatReply(
        text=render_result(result, review_link(link_base, result.thread_id)),
        result=result,
        progress=progress,
    )


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
