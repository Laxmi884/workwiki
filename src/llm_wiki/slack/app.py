"""Bolt wiring: DM messages and the review buttons go to `WikiSlack` (sync, 0014)."""

from __future__ import annotations

import re
from typing import Any

from slack_bolt import Ack, App
from slack_sdk import WebClient

from llm_wiki.slack.handlers import WikiSlack


def build_app(
    wiki: WikiSlack, *, bot_token: str, client: WebClient | None = None
) -> App:
    """Return the Bolt app; `client` replaces Slack's WebClient in tests."""
    app = App(
        token=bot_token,
        client=client,
        token_verification_enabled=False,  # no auth.test call at start (tests, offline)
    )

    @app.event("message")
    def message(event: dict[str, Any], client: WebClient) -> None:
        wiki.on_message(event, client)

    @app.action(re.compile(r"^wiki_"))
    def button(ack: Ack, body: dict[str, Any], client: WebClient) -> None:
        ack()  # within Slack's 3 s; the run itself may take minutes
        wiki.on_action(body, client)

    return app
