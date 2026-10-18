"""What the bot does with a DM or a button press (no Bolt here: `app.py` wires it).

- DM text: user check, then `chat.session.stream_reply`, shown by `Reply`.
- Button: user check, then `backend.stream_answer(thread, {action, seen})`, shown by
  `Reply`.

Only `user_id`, only DMs. The same routing as the web chat: "/…" is a command,
anything else a question. Slack's client keeps a leading "/" for its own slash
commands and never sends the message, so "!ingest" stands for "/ingest" here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

from slack_sdk import WebClient

from llm_wiki.backend import (
    Backend,
    BackendError,
    NoOpenReview,
    ReviewBusy,
    UnknownThread,
)
from llm_wiki.chat.session import ChatReply, stream_reply
from llm_wiki.progress import Progress
from llm_wiki.slack.render import APPROVE, REJECT, reply_text, review_buttons
from llm_wiki.slack.stream import Reply

log = logging.getLogger(__name__)
CRASHED = "Something failed (details in the log)."


class WikiSlack:
    """The bot's behaviour over one backend; Slack calls go to the `client` passed in."""

    def __init__(
        self, backend: Backend, *, user_id: str, review_url: str | None
    ) -> None:
        """Answer only `user_id`; link reviews to `review_url` when it isn't local."""
        self._backend = backend
        self._user_id = user_id
        self._review_url = review_url

    def on_message(self, event: dict[str, Any], client: WebClient) -> None:
        """Answer one DM from the allowed user; ignore every other event."""
        if (
            event.get("subtype")
            or event.get("bot_id")
            or event.get("channel_type") != "im"
        ):
            return
        if event.get("user") != self._user_id:
            log.info("slack: ignored a message from user %s", event.get("user"))
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        if text.startswith("!"):
            text = "/" + text[1:]
        reply = Reply(
            client,
            channel=event["channel"],
            thread_ts=event.get("thread_ts") or event["ts"],
            team=event.get("team"),
            user=self._user_id,
        )
        self._run(
            reply,
            lambda: stream_reply(self._backend, text, link_base=self._review_url or ""),
        )

    def on_action(self, body: dict[str, Any], client: WebClient) -> None:
        """Answer the review a button names: approve or reject what was shown, once."""
        user = body.get("user", {}).get("id")
        if user != self._user_id:
            log.info("slack: ignored a button press from user %s", user)
            return
        (action,) = body["actions"]
        if action["action_id"] not in (APPROVE, REJECT):
            return  # "Open review page" is a link; Slack still sends the press
        target = json.loads(action["value"])
        verb = "approve" if action["action_id"] == APPROVE else "reject"
        channel, message = body["channel"]["id"], body["message"]
        original = message.get("blocks") or []
        kept = [b for b in original if b.get("type") != "actions"]  # the review text

        def mark(line: str) -> None:
            """Show `line` where the buttons were (no button: no double press)."""
            status = {"type": "context", "elements": [{"type": "mrkdwn", "text": line}]}
            client.chat_update(
                channel=channel, ts=message["ts"], text=line, blocks=[*kept, status]
            )

        def restore() -> None:
            client.chat_update(
                channel=channel, ts=message["ts"], text="Review", blocks=original
            )

        mark("Approving…" if verb == "approve" else "Rejecting…")
        reply = Reply(
            client,
            channel=channel,
            thread_ts=message.get("thread_ts") or message["ts"],
            team=body.get("team", {}).get("id"),
            user=self._user_id,
        )
        answer: dict[str, Any] = {"action": verb}
        if verb == "approve":
            answer["seen"] = target["seen"]
        try:
            finished = None
            for event in self._backend.stream_answer(target["thread"], answer):
                if isinstance(event, Progress):
                    reply.show(event)
                else:
                    finished = event
            assert finished is not None and finished.result is not None
            result = finished.result
            text = reply_text(result, self._review_url)
            # not accepted (e.g. changed since): the reply below has current buttons
            mark(
                "Not accepted: see the reply."
                if result.status == "review"
                else text.splitlines()[0]
            )
            reply.finish(
                text,
                review_buttons(result, self._review_url) or None,
                finished.progress,
            )
        except UnknownThread:
            mark("This review isn't open any more.")
            reply.finish(
                f"This review isn't open any more (no thread {target['thread']})."
            )
        except NoOpenReview as exc:
            mark("Already answered.")
            current = self._backend.review(target["thread"])
            reply.finish(
                f"This review was already answered ({exc}).\n\n"
                + reply_text(current, self._review_url)
            )
        except ReviewBusy as exc:
            restore()  # the other answer may still fail
            reply.finish(f"{exc}; wait for it to finish.")
        except BackendError as exc:
            restore()
            reply.fail(f"Can't reach the wiki server: {exc}")
        except Exception:  # noqa: BLE001
            log.exception("slack: button failed on thread %s", target.get("thread"))
            restore()
            reply.fail(CRASHED)

    def _run(
        self, reply: Reply, events: Callable[[], Iterator[Progress | ChatReply]]
    ) -> None:
        try:
            last: ChatReply | None = None
            for event in events():
                if isinstance(event, Progress):
                    reply.show(event)
                else:
                    last = event
            assert last is not None
            if last.result is not None:
                text = reply_text(last.result, self._review_url)
                blocks = review_buttons(last.result, self._review_url)
            else:
                text, blocks = last.text, []
            reply.finish(text, blocks or None, last.progress)
        except BackendError as exc:
            reply.fail(f"Can't reach the wiki server: {exc}")
        except Exception:  # noqa: BLE001  (the user gets a line; the log has the trace)
            log.exception("slack: message handler failed")
            reply.fail(CRASHED)
