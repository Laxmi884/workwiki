"""Bolt wiring and start-up: no Slack network."""

from importlib.resources import files

import yaml
from slack_bolt.request import BoltRequest
from slack_sdk import WebClient

from llm_wiki.config import Settings
from llm_wiki.slack.__main__ import missing_settings
from llm_wiki.slack.app import build_app

from .slack_fake import FakeSlack


class Recorder:
    def __init__(self):
        self.messages, self.actions = [], []

    def on_message(self, event, client):
        self.messages.append(event)

    def on_action(self, body, client):
        self.actions.append(body)


def test_events_and_buttons_reach_the_handlers_and_are_acked(monkeypatch):
    # Bolt makes its own WebClient per request: route every client's calls to the fake
    slack = FakeSlack()
    monkeypatch.setattr(
        WebClient, "api_call", lambda self, method, **kw: slack.api_call(method, **kw)
    )
    wiki = Recorder()
    app = build_app(wiki, bot_token="xoxb-test")
    event = {
        "type": "event_callback",
        "team_id": "T1",
        "event": {
            "type": "message",
            "channel_type": "im",
            "user": "U1",
            "text": "hi",
            "ts": "1.0",
            "channel": "D1",
        },
    }
    assert app.dispatch(BoltRequest(body=event, mode="socket_mode")).status == 200
    assert wiki.messages[0]["text"] == "hi"
    action = {
        "type": "block_actions",
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "channel": {"id": "D1"},
        "message": {"ts": "100.1"},
        "actions": [{"action_id": "wiki_approve", "value": "{}", "type": "button"}],
    }
    assert app.dispatch(BoltRequest(body=action, mode="socket_mode")).status == 200
    assert len(wiki.actions) == 1


def test_start_up_names_each_missing_setting(tmp_path):
    bare = Settings(_env_file=None, vault_path=tmp_path)
    assert missing_settings(bare) == [
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "WIKI_SLACK_USER_ID",
    ]
    ready = Settings(
        _env_file=None,
        vault_path=tmp_path,
        SLACK_BOT_TOKEN="xoxb-1",
        SLACK_APP_TOKEN="xapp-1",
        slack_user_id="U1",
    )
    assert missing_settings(ready) == []


def test_the_manifest_asks_only_for_what_the_bot_uses():
    manifest = yaml.safe_load(
        files("llm_wiki.slack").joinpath("manifest.yml").read_text()
    )
    assert sorted(manifest["oauth_config"]["scopes"]["bot"]) == [
        "chat:write",
        "im:history",
        "im:read",
        "im:write",
    ]
    settings = manifest["settings"]
    assert settings["socket_mode_enabled"] is True
    assert settings["interactivity"]["is_enabled"] is True
    assert settings["event_subscriptions"]["bot_events"] == ["message.im"]
