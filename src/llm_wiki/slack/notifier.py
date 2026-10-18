"""Tell the user about reviews in Slack, whatever started them (spec: Notifier).

Each pass reconciles what is open (`backend.pending()`) with what Slack shows
(`Announced`): a new review gets one DM with its buttons; a review answered elsewhere
gets its message replaced by the outcome (no buttons); a review edited on the page
gets its message updated in place. Code decides all of it; no model is called.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Literal

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from llm_wiki.backend import Backend, BackendError, RunResult, UnknownThread
from llm_wiki.slack.announced import Announced, Shown
from llm_wiki.slack.render import (
    NOTICE,
    answered_text,
    message_blocks,
    reply_text,
    review_buttons,
)

log = logging.getLogger(__name__)
INTERVAL = 60  # seconds between passes
GONE = {"message_not_found", "channel_not_found"}  # the message can't come back


class Notifier:
    """One user's review notices over one backend."""

    def __init__(
        self,
        backend: Backend,
        announced: Announced,
        *,
        user_id: str,
        review_url: str | None,
    ) -> None:
        """DM `user_id` about `backend`'s reviews; `announced` is shared with the handlers."""
        self._backend = backend
        self._announced = announced
        self._user_id = user_id
        self._review_url = review_url
        self._dm: str | None = None

    def run_forever(
        self, client: WebClient, stop: threading.Event, interval: float = INTERVAL
    ) -> None:
        """Run a pass every `interval` seconds until `stop` is set; a failed pass is logged."""
        while not stop.is_set():
            try:
                self.run_once(client)
            except Exception:  # noqa: BLE001  (the loop outlives any one pass)
                log.exception("slack: notifier pass failed")
            stop.wait(interval)

    def run_once(self, client: WebClient) -> None:
        """Announce new reviews, clear answered ones, refresh edited ones.

        No lock is held across the backend and Slack calls (they can be slow); a handler
        run in flight skips the pass, or the announcement. One failing review is logged
        and retried next pass; it doesn't stop the others.
        """
        if not self._announced.idle():
            return
        try:
            open_reviews = {r.thread_id: r for r in self._backend.pending()}
        except BackendError as exc:
            log.warning("slack: notifier can't reach the wiki server: %s", exc)
            return
        for thread, shown in self._announced.items():
            current = open_reviews.get(thread)
            if current is None:
                self._each(thread, self._answered, client, thread, shown)
            elif current.review and current.review.token != shown.seen:
                self._each(thread, self._refresh, client, current, shown)
        for result in reversed(list(open_reviews.values())):  # oldest first
            if self._announced.idle() and self._announced.get(result.thread_id) is None:
                self._each(result.thread_id, self._announce, client, result)

    @staticmethod
    def _each(thread: str, step: Callable[..., None], *args: Any) -> None:
        try:
            step(*args)
        except (BackendError, SlackApiError, OSError) as exc:
            log.warning("slack: notifier skipped %s this pass (%s)", thread, exc)

    def _announce(self, client: WebClient, result: RunResult) -> None:
        assert result.review is not None  # status "review" always carries one
        text = NOTICE + reply_text(result, self._review_url)
        response = client.chat_postMessage(
            channel=self._dm_channel(client),
            text=text,
            blocks=message_blocks(text, review_buttons(result, self._review_url)),
        )
        self._announced.record(
            result.thread_id,
            Shown(
                channel=response["channel"], ts=response["ts"], seen=result.review.token
            ),
        )
        log.info("slack: announced review %s", result.thread_id)

    def _refresh(self, client: WebClient, result: RunResult, shown: Shown) -> None:
        assert result.review is not None
        text = NOTICE + reply_text(result, self._review_url)
        match self._update(
            client, shown, text, review_buttons(result, self._review_url)
        ):
            case "ok":
                self._announced.record(
                    result.thread_id,
                    shown.model_copy(update={"seen": result.review.token}),
                )
            case "gone":
                self._announced.forget(result.thread_id)
            case _:  # a transient error: keep the record, try again next pass
                pass

    def _answered(self, client: WebClient, thread: str, shown: Shown) -> None:
        """Clear the buttons of a review that is no longer listed as open.

        `pending()` is capped, and a review being answered right now is running, so it
        isn't listed either. Only a finished review is cleared; one that is still open
        or whose run is unfinished keeps its record (BackendError: same, retried).
        """
        try:
            result = self._backend.review(thread)
        except UnknownThread:
            text = "This review isn't open any more."
        else:
            if result.status not in ("done", "skipped"):
                return
            text = answered_text(result, self._review_url)
        outcome = self._update(client, shown, text, None)
        if outcome != "error":
            self._announced.forget(thread)
            log.info("slack: cleared review %s (answered elsewhere)", thread)

    def _update(
        self,
        client: WebClient,
        shown: Shown,
        text: str,
        blocks: list[dict[str, Any]] | None,
    ) -> Literal["ok", "gone", "error"]:
        """Replace the message: "gone" if Slack no longer has it, "error" to retry."""
        try:
            client.chat_update(
                channel=shown.channel,
                ts=shown.ts,
                text=text,
                blocks=message_blocks(text, blocks),
            )
        except SlackApiError as exc:
            error = exc.response.get("error")
            log.warning("slack: could not update %s (%s)", shown.ts, error)
            return "gone" if error in GONE else "error"
        return "ok"

    def _dm_channel(self, client: WebClient) -> str:
        if self._dm is None:
            self._dm = client.conversations_open(users=self._user_id)["channel"]["id"]
        return self._dm
