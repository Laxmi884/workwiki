"""A `WebClient` whose API calls are recorded, not sent: the real `ChatStream` runs on it."""

from __future__ import annotations

from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.web.slack_response import SlackResponse


class FakeSlack(WebClient):
    """Every API call is appended to `calls`; `fail_next[method] = error` fails it once."""

    def __init__(self) -> None:
        super().__init__(token="xoxb-test")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_next: dict[str, str] = {}
        self.replies: list[dict[str, Any]] = []  # what conversations.replies returns
        self._ts = 0

    def _response(self, method: str, data: dict[str, Any]) -> SlackResponse:
        return SlackResponse(
            client=self,
            http_verb="POST",
            api_url=method,
            req_args={},
            data=data,
            headers={},
            status_code=200,
        )

    def api_call(self, api_method: str, **kwargs: Any) -> SlackResponse:  # type: ignore[override]
        args: dict[str, Any] = {}
        for key in ("json", "params", "data"):
            args |= kwargs.get(key) or {}
        if args.get("chunks"):  # ChatStream adds its own text chunks as objects
            args["chunks"] = [
                c if isinstance(c, dict) else c.to_dict() for c in args["chunks"]
            ]
        self.calls.append((api_method, args))
        if error := self.fail_next.pop(api_method, None):
            raise SlackApiError(
                error, self._response(api_method, {"ok": False, "error": error})
            )
        data: dict[str, Any] = {"ok": True}
        if api_method == "conversations.replies":
            data |= {"messages": self.replies, "has_more": False}
        if api_method == "auth.test":  # Bolt learns its own ids, to ignore its messages
            data |= {"user_id": "U0BOT", "bot_id": "B0BOT", "team_id": "T1"}
        if api_method in ("chat.startStream", "chat.postMessage"):
            self._ts += 1
            data |= {"ts": f"100.{self._ts}", "channel": args.get("channel")}
        return self._response(api_method, data)

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]

    def chunks(self) -> list[dict[str, Any]]:
        return [
            c
            for _, a in self.calls
            for c in a.get("chunks") or []
            if c.get("type") == "task_update"
        ]

    def text(self) -> str:
        """All text the user would read: streamed, posted, stopped or updated."""
        out = []
        for _, a in self.calls:
            out += [
                c.get("text", "")
                for c in a.get("chunks") or []
                if c.get("type") == "markdown_text"
            ]
            out += [a.get("markdown_text") or "", a.get("text") or ""]
            out += [
                b.get("text", "")
                for b in a.get("blocks") or []
                if isinstance(b.get("text"), str)
            ]
        return "".join(out)
