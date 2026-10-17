"""`compose.yml`, how the server runs for real: reachable from this machine only.

`langgraph up` publishes the API and Postgres on every interface; our stack must not.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))


def test_only_the_api_is_published_and_only_on_localhost():
    services = COMPOSE["services"]
    published = {name: s.get("ports", []) for name, s in services.items()}
    assert published.pop("api") == ["127.0.0.1:${WIKI_PORT:-8123}:8000"]
    assert all(ports == [] for ports in published.values()), published


def test_the_vault_is_mounted_at_its_own_path_and_state_restores_strictly():
    api = COMPOSE["services"]["api"]
    assert "${WIKI_VAULT_PATH}:${WIKI_VAULT_PATH}" in api["volumes"]
    env = api["environment"]
    assert env["LANGGRAPH_STRICT_MSGPACK"] == "true"
    assert env["WIKI_BACKEND"] == "server"
    assert env["WIKI_SERVER_URL"] == "http://127.0.0.1:8000"
