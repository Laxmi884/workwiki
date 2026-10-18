# ruff: noqa: T201  (a CLI says what is missing)
"""`uv run python -m llm_wiki.slack`: the wiki bot in Socket Mode (no inbound port).

Needs SLACK_BOT_TOKEN, SLACK_APP_TOKEN and WIKI_SLACK_USER_ID (`docs/setup/slack.md`);
reaches the agents through `get_backend()` (`WIKI_BACKEND`, `WIKI_SERVER_URL`).
"""

from __future__ import annotations

import logging
import sys

from llm_wiki.config import Settings, get_settings, load_env_file


def missing_settings(settings: Settings) -> list[str]:
    """Return the variables the bot needs that are unset."""
    needed = {
        "SLACK_BOT_TOKEN": settings.slack_bot_token,
        "SLACK_APP_TOKEN": settings.slack_app_token,
        "WIKI_SLACK_USER_ID": settings.slack_user_id,
    }
    return [name for name, value in needed.items() if not value]


def main() -> int:
    """Start the bot, or exit 2 naming what is missing."""
    load_env_file()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = get_settings()
    if missing := missing_settings(settings):
        print(
            f"llm_wiki.slack: set {', '.join(missing)} (docs/setup/slack.md)",
            file=sys.stderr,
        )
        return 2
    from slack_bolt.adapter.socket_mode import SocketModeHandler  # noqa: PLC0415

    from llm_wiki.backend import get_backend  # noqa: PLC0415
    from llm_wiki.slack.app import build_app  # noqa: PLC0415
    from llm_wiki.slack.handlers import WikiSlack  # noqa: PLC0415

    assert (
        settings.slack_bot_token and settings.slack_app_token and settings.slack_user_id
    )
    wiki = WikiSlack(
        get_backend(), user_id=settings.slack_user_id, review_url=settings.review_url
    )
    app = build_app(wiki, bot_token=settings.slack_bot_token.get_secret_value())
    SocketModeHandler(app, settings.slack_app_token.get_secret_value()).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
