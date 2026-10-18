"""The Agent Server answers only clients that send WIKI_SERVER_TOKEN (Slack step 2)."""

import json
from pathlib import Path

import pytest
from langgraph_sdk import Auth

from llm_wiki.server_auth import check_bearer


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer wrong-token", "Basic dGVzdA==", "bearer s3cret-token"],
)
def test_requests_without_the_token_are_refused(header):
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        check_bearer(header, "s3cret-token")
    assert exc.value.status_code == 401


def test_the_token_lets_a_request_in():
    check_bearer("Bearer s3cret-token", "s3cret-token")


def test_a_server_without_a_token_refuses_everything():
    # fail closed: an unset WIKI_SERVER_TOKEN must not mean "no auth"
    with pytest.raises(Auth.exceptions.HTTPException, match="WIKI_SERVER_TOKEN"):
        check_bearer("Bearer anything", None)


def test_server_backend_sends_the_token(monkeypatch, tmp_path):
    from llm_wiki.backend.server import auth_headers
    from llm_wiki.config import get_settings

    monkeypatch.chdir(tmp_path)  # no .env here: the developer's real token stays out

    monkeypatch.setenv("WIKI_SERVER_TOKEN", "s3cret-token")
    get_settings.cache_clear()
    assert auth_headers() == {"Authorization": "Bearer s3cret-token"}
    monkeypatch.delenv("WIKI_SERVER_TOKEN")
    get_settings.cache_clear()
    assert auth_headers() == {}


def test_langgraph_json_turns_auth_on_for_the_api_only():
    config = json.loads((Path(__file__).parents[2] / "langgraph.json").read_text())
    assert config["auth"] == {"path": "./src/llm_wiki/server_auth.py:auth"}
    assert not config["http"].get("enable_custom_route_auth")  # pages: see the plan
