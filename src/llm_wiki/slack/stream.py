"""One reply in a DM thread: a Slack streaming message that `Progress` drives.

- `Reply.show(progress)`: chat.startStream (first time), then task chunks and text.
- `Reply.finish(text, blocks)`: chat.stopStream with the rest of the text and buttons
  (chat.postMessage when nothing was streamed).

Slack ends long streams by itself (`message_not_in_streaming_state`, slack-sdk issue
#1859); the message is then finished with `chat.update` on the stream's ts.
"""

from __future__ import annotations

import logging
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.web.chat_stream import ChatStream

from llm_wiki.progress import Progress
from llm_wiki.slack.render import MAX_MARKDOWN, task_chunks, text_delta

log = logging.getLogger(__name__)
EXPIRED = "message_not_in_streaming_state"


def _expired(exc: SlackApiError) -> bool:
    return exc.response.get("error") == EXPIRED


class Reply:
    """A streaming reply under the user's message (`thread_ts`)."""

    def __init__(
        self,
        client: WebClient,
        *,
        channel: str,
        thread_ts: str,
        team: str | None,
        user: str | None,
    ) -> None:
        """Reply in `channel` under `thread_ts`; `team` and `user` are the recipient's."""
        self._client = client
        self._channel = channel
        self._thread_ts = thread_ts
        self._team = team
        self._user = user
        self._stream: ChatStream | None = None
        self._shown: Progress | None = None
        self._sent = ""
        self._expired = False

    @property
    def ts(self) -> str | None:
        """The streaming message's ts, once Slack started it."""
        return self._stream.ts if self._stream else None

    def show(self, progress: Progress) -> None:
        """Send what changed since the last call: step chunks, then new answer text."""
        chunks = task_chunks(self._shown, progress)
        delta = text_delta(self._sent, progress.text)
        self._shown = progress
        if self._expired or not (chunks or delta):
            self._sent += delta
            return
        stream = self._open()
        try:
            # text alone is buffered by ChatStream (256 chars); chunks flush it
            stream.append(markdown_text=delta or None, chunks=chunks or None)
        except SlackApiError as exc:
            if not _expired(exc):
                raise
            self._expired = True  # finish() falls back to chat.update
        self._sent += delta

    def finish(
        self,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        progress: Progress | None = None,
    ) -> str | None:
        """End the reply with `text` (what isn't streamed yet) and `blocks`; return its ts."""
        if progress is not None:
            self.show(progress)
        if self._stream is None:
            response = self._client.chat_postMessage(
                channel=self._channel,
                thread_ts=self._thread_ts,
                text=text[:MAX_MARKDOWN],
                blocks=self._with_text(text, blocks),
            )
            return response.get("ts")
        rest = text_delta(self._sent, text)
        if not self._expired:
            try:
                self._stream.stop(markdown_text=rest or None, blocks=blocks)
                return self.ts
            except SlackApiError as exc:
                if not _expired(exc):
                    raise
        log.info("slack: stream expired; finishing with chat.update (ts %s)", self.ts)
        self._client.chat_update(
            channel=self._channel,
            ts=self.ts or "",
            text=text[:MAX_MARKDOWN],
            blocks=self._with_text(text, blocks),
        )
        return self.ts

    def fail(self, text: str) -> None:
        """End the reply with an error line; steps still running are marked failed."""
        running = (
            [
                c | {"status": "error"}
                for c in task_chunks(None, self._shown)
                if c["status"] == "in_progress"
            ]
            if self._shown
            else []
        )
        if self._stream is not None and not self._expired:
            self._stream.stop(markdown_text="\n\n" + text, chunks=running or None)
        else:
            self._client.chat_postMessage(
                channel=self._channel, thread_ts=self._thread_ts, text=text
            )

    def _open(self) -> ChatStream:
        if self._stream is None:
            self._stream = self._client.chat_stream(
                channel=self._channel,
                thread_ts=self._thread_ts,
                recipient_team_id=self._team,
                recipient_user_id=self._user,
                task_display_mode="plan",
            )
        return self._stream

    @staticmethod
    def _with_text(
        text: str, blocks: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        return [{"type": "markdown", "text": text[:MAX_MARKDOWN]}, *(blocks or [])]
