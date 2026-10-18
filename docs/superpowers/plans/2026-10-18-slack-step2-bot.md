# Slack Step 2: the Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In a DM with the `wiki` bot, `/ingest` shows the run's steps ticking, then the review summary with **Approve all / Reject / Open review page**. A question streams its answer. Buttons approve or reject exactly the review shown.

**Architecture:** `llm_wiki/slack/` is a thin surface:
- `render.py` (pure): `Progress` → Slack task chunks and text deltas; `RunResult` → markdown + buttons.
- `stream.py`: one streaming reply over `WebClient.chat_stream`, with the `chat.update` fallback.
- `handlers.py`: DM messages and button presses. They reach the agents only through `Backend` and `chat.session`.
- `app.py` (Bolt wiring, sync Socket Mode) and `__main__.py`.

The Agent Server gets bearer auth (`WIKI_SERVER_TOKEN`), because a second client (the bot) now calls its API. The bot runs as an opt-in compose service (`profiles: ["slack"]`).

**Tech Stack:**
- Python 3.12, pytest, uv
- slack-sdk 3.45.0 (`WebClient.chat_stream`, `task_update` chunks)
- slack-bolt 1.30.0 (`App`, `slack_bolt.adapter.socket_mode.SocketModeHandler`)
- langgraph-sdk 0.4.7 (`langgraph_sdk.Auth`)
- langgraph-api 0.16.1 (`langgraph.json` `"auth": {"path": ...}`)

**Spec:** `docs/superpowers/specs/2026-10-18-slack-adapter-design.md` (build order step 2). Step 1 (`f6b5b13`) and the review fixes (`7d988e5`) are in.

**Checked against current docs (2026-10-18):**
- `WebClient.chat_stream(channel, thread_ts, recipient_team_id=None, recipient_user_id=None, task_display_mode=None, buffer_size=256)` returns a `ChatStream` with `append(markdown_text=..., chunks=[...])` and `stop(markdown_text=..., blocks=..., chunks=...)`.
- A task chunk is `{"type": "task_update", "id", "title", "status", "details"?, "output"?}`, with `status` one of `pending | in_progress | complete | error` (chat.appendStream reference). The `task_update` and `plan_update` chunk text is limited to 256 characters.
- `chat.startStream` needs only `chat:write`, which the app already has (checked 2026-10-18).
- `langgraph.json` `auth.path` points to a `langgraph_sdk.Auth` with an `@auth.authenticate` handler. It covers the API routes. Our mounted `http.app` routes are not covered unless `http.enable_custom_route_auth` is set (`langgraph_api/config/schemas.py`).

**Where this differs from the spec, on purpose:**
- **Bearer auth on the Agent Server is added here.**
  - The whole-project review asked for it before Slack, because the bot is the first client that isn't the review page.
  - The review page routes stay unauthenticated, because a phone browser can't send a bearer token on page loads. They are tailnet-only and CSRF-guarded (`7d988e5`).
  - The page itself talks to the API with the token.
- **No `WIKI_PUBLIC_URL`.** The review link reuses `Settings.review_url` (`WIKI_REVIEW_URL`). "Open review page" is shown only when that URL isn't a loopback address, since a `127.0.0.1` link is useless on the phone.
- **Text before a tool call stays visible.** The assistant can stream text and then call a tool, and Slack can't take back streamed text. So when `Progress.text` restarts, the stream adds a blank line and continues, as paid-media `delivery.py` does.
- **Skipped steps show as complete with the detail "skipped".** Slack has no "skipped" status, and `pending` would look like it never finished.
- **No chat history from Slack.** Each DM message is asked on its own; the spec puts thread conversations out of scope.

## Global Constraints

- Slack reaches graphs only through `Backend`, `chat.session` and `progress` (spec: "`slack/` depends only on `Backend`, `session`, `progress`, `slack_bolt`, `slack_sdk`. Never touches the vault or graphs directly.").
- One allowed user (`WIKI_SLACK_USER_ID`), DMs only. Anything else is ignored, with one log line carrying only the user id.
- Logs carry thread ids and step names, never tokens or note text.
- Missing `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` or `WIKI_SLACK_USER_ID`: `python -m llm_wiki.slack` exits with a message naming the variable. Nothing else is affected.
- Buttons send `{"action": "approve" | "reject", "seen": <review token>}`. A stale token is refused by `check_answer` (I3).
- Tests: no Slack network, scripted models, fixture vault (`vault_copy`). The fake Slack client is a `WebClient` subclass whose `api_call` records calls, so the real `ChatStream` logic runs.
- Secrets: the user puts token values in `.env`. Claude never prints, logs or writes a token value.
- Commit only when the user says yes (memory: commit-after-walkthrough). Each task's "Commit" step means: stage, show the diff summary, wait.

## Review Focus

1. **Slack message events that aren't the user's text**: edits (`message_changed`), the bot's own messages (`bot_id`), joins and file shares (`subtype`). They must be ignored silently; replying to its own message would loop. Task 4 pins this: `test_events_that_are_not_the_users_text_are_ignored`.
2. **A button on an old summary after the review changed** (e.g. a revise from the review page): refused with "changed since you opened it", and nothing is written. Task 5: `test_a_button_on_a_changed_review_is_refused`.
3. **The Slack stream dies mid-run** (`message_not_in_streaming_state`, slack-sdk issue #1859): the reply still ends with the result, via `chat.update`. Task 3: `test_an_expired_stream_finishes_with_chat_update`.
4. **The Agent Server is down when a button is pressed**: say so and put the buttons back, so the user can retry. Task 5: `test_a_backend_failure_restores_the_buttons`.
5. **A long review** (many facts, files, warnings): Slack text limits (12,000 characters for markdown) are cut in code, so the API never refuses them. Task 2: `test_long_reviews_are_cut_to_slack_limits`.

---

## File Structure

```
src/llm_wiki/
  server_auth.py        NEW  bearer-token Auth for the Agent Server (langgraph.json "auth")
  config.py             + server_token, slack_bot_token, slack_app_token, slack_user_id
  backend/server.py     ServerBackend sends Authorization: Bearer <WIKI_SERVER_TOKEN>
  schedule.py           _client sends it too
  slack/
    __init__.py         NEW  package docstring
    render.py           NEW  pure: Progress → chunks/text delta; RunResult → text + buttons
    stream.py           NEW  Reply: one streaming reply (chat_stream; chat.update fallback)
    handlers.py         NEW  WikiSlack: on_message, on_action (user check, routing)
    app.py              NEW  build_app(wiki, bot_token, client=None) -> slack_bolt.App
    __main__.py         NEW  env check, SocketModeHandler(...).start()
    manifest.yml        NEW  the Slack app manifest (scopes, Socket Mode, events)
langgraph.json          + "auth": {"path": "./src/llm_wiki/server_auth.py:auth"}
compose.yml             + slack service, profiles ["slack"]
pyproject.toml          + slack-sdk>=3.45.0, slack-bolt>=1.30.0; package-data manifest.yml
.env.example            + WIKI_SERVER_TOKEN, SLACK_*, WIKI_SLACK_USER_ID
docs/setup/slack.md     NEW  setup guide
docs/wiki/decisions/0016-real-notes-to-a-cloud-model.md   + addendum: Slack
tests/unit_tests/
  test_server_auth.py   NEW
  slack_fake.py         NEW  FakeSlack(WebClient): records api_call, scripted failures
  test_slack_render.py  NEW
  test_slack_stream.py  NEW
  test_slack_handlers.py NEW
  test_slack_app.py     NEW
  test_compose.py       + slack service test
  test_backend_contract.py  dev/docker servers run with auth and a test token
notebooks/_src/nb8.4.py, notebooks/8.4_chat_surface.ipynb   (git-ignored)
```

---

### Task 1: Bearer auth on the Agent Server

**Files:**
- Create: `src/llm_wiki/server_auth.py`, `tests/unit_tests/test_server_auth.py`
- Modify: `src/llm_wiki/config.py` (Settings), `src/llm_wiki/backend/server.py` (`ServerBackend.__init__`), `src/llm_wiki/schedule.py` (`_client`), `langgraph.json`, `tests/conftest.py`, `tests/unit_tests/test_backend_contract.py` (dev config, `SERVER_ENV`, docker override), `.env.example`

**Interfaces:**
- Produces:
  - `Settings.server_token: SecretStr | None` (`WIKI_SERVER_TOKEN`)
  - `server_auth.auth: langgraph_sdk.Auth`
  - `server_auth.check_bearer(authorization: str | None, expected: str | None) -> None`, which raises `Auth.exceptions.HTTPException(status_code=401)`
  - `backend.server.auth_headers() -> dict[str, str]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit_tests/test_server_auth.py
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


def test_server_backend_sends_the_token(monkeypatch):
    from llm_wiki.backend.server import auth_headers
    from llm_wiki.config import get_settings

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
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/unit_tests/test_server_auth.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.server_auth'`

- [ ] **Step 3: Implement**

```python
# src/llm_wiki/server_auth.py
"""Who may call the Agent Server: clients that send `WIKI_SERVER_TOKEN` (Slack step 2).

`langgraph.json` `"auth": {"path": "./src/llm_wiki/server_auth.py:auth"}` runs this on
every API request. Clients (`ServerBackend`, the review page inside the server, the
Slack bot, `schedule`) send `Authorization: Bearer <token>`. An unset token refuses
everything, so a missing setting never means "open". The review page's own routes
(`http.app`) are not covered: a phone's browser can't send the header; they stay
tailnet-only and refuse cross-site posts (review_web `_json_body`).
"""

from __future__ import annotations

import hmac
import os

from langgraph_sdk import Auth

auth = Auth()


def check_bearer(authorization: str | None, expected: str | None) -> None:
    """Raise 401 unless `authorization` is "Bearer <expected>" (constant-time compare)."""
    if not expected:
        raise Auth.exceptions.HTTPException(
            status_code=401, detail="the server has no WIKI_SERVER_TOKEN set"
        )
    scheme, _, token = (authorization or "").partition(" ")
    if scheme != "Bearer" or not hmac.compare_digest(token.encode(), expected.encode()):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Unauthorized")


@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    """Let the request in as the one user, or refuse it with 401."""
    check_bearer(authorization, os.environ.get("WIKI_SERVER_TOKEN"))
    return {"identity": "wiki-user"}
```

`config.py`, in `Settings` (plus a line in its docstring):

```python
    server_token: SecretStr | None = None  # WIKI_SERVER_TOKEN
```

`backend/server.py`:

```python
def auth_headers() -> dict[str, str]:
    """Return the Authorization header for the Agent Server (none if no token is set)."""
    token = get_settings().server_token
    return {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}

# in ServerBackend.__init__:
        self._client = client or get_sync_client(url=self.url, headers=auth_headers())
```

`schedule.py` `_client(url)`: `return get_sync_client(url=url, headers=auth_headers())`, importing `auth_headers` from `llm_wiki.backend.server`.

`langgraph.json`: add `"auth": {"path": "./src/llm_wiki/server_auth.py:auth"}` at the top level.

Tests:
- `tests/conftest.py` `_vaults_tests_may_use`: add `monkeypatch.setenv("WIKI_SERVER_TOKEN", "test-token")`.
- `test_backend_contract.py`:
  - `_dev_server` config gets `"auth": {"path": f"{ROOT}/src/llm_wiki/server_auth.py:auth"}`.
  - `SERVER_ENV` gets `"WIKI_SERVER_TOKEN": "test-token"`.
  - The docker override `environment` gets `"WIKI_SERVER_TOKEN": "test-token"`.
  - New contract test:

```python
def test_the_server_refuses_a_client_without_the_token(server_url):
    assert httpx.post(f"{server_url}/threads/search", json={}).status_code == 401
    ok = httpx.post(
        f"{server_url}/threads/search",
        json={},
        headers={"Authorization": "Bearer test-token"},
    )
    assert ok.status_code == 200
```

`.env.example`:

```
# The Agent Server answers only clients that send this token (Slack step 2). Make one:
#   python -c "import secrets; print('WIKI_SERVER_TOKEN=' + secrets.token_urlsafe(32))" >> .env
WIKI_SERVER_TOKEN=
```

- [ ] **Step 4: Run, expect PASS**

Run: `uv run pytest tests/unit_tests -q && WIKI_SERVER_TESTS=1 uv run pytest tests/unit_tests/test_backend_contract.py -q`
Expected: all pass; contract 36/36.

- [ ] **Step 5: Real check (free).** Run `uv run langgraph dev --port 2099 --no-browser --no-reload`.
  - A `curl` to `/threads/search` without the token gives 401.
  - With the `.env` token it gives 200. Load the token with `set -a; . ./.env` in the same shell, and never echo it.
  - `/review` still loads, and its answer still reaches the API (the page's backend sends the token).
  - The user's own `.env` needs `WIKI_SERVER_TOKEN` before their server runs again. Tell them, with the command above.

- [ ] **Step 6: Commit (after the user's yes)**: "Agent Server: bearer auth (WIKI_SERVER_TOKEN)".

---

### Task 2: `slack/render.py` (pure)

**Files:**
- Create: `src/llm_wiki/slack/__init__.py`, `src/llm_wiki/slack/render.py`, `tests/unit_tests/test_slack_render.py`
- Modify: `pyproject.toml` (`uv add "slack-sdk>=3.45.0" "slack-bolt>=1.30.0"`)

**Interfaces:**
- Consumes:
  - `progress.Progress`, `progress.Step` (`key`, `label`, `state`, `detail`, `calls`)
  - `backend.RunResult`
  - `chat.render.render_result(result, link)`
  - `chat.session.review_link(base, thread_id)`
  - `ReviewRequest.token`
- Produces:
  - `task_chunks(before: Progress | None, after: Progress) -> list[dict[str, str]]`
  - `text_delta(sent: str, text: str) -> str`
  - `public_review_link(review_url: str, thread_id: str) -> str | None`
  - `reply_text(result: RunResult, review_url: str | None) -> str`
  - `review_buttons(result: RunResult, review_url: str | None) -> list[dict[str, Any]]`
  - Constants: `APPROVE = "wiki_approve"`, `REJECT = "wiki_reject"`, `OPEN_PAGE = "wiki_open_page"`, `MAX_MARKDOWN = 12_000`, `MAX_TASK_TEXT = 256`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit_tests/test_slack_render.py
"""Progress and results as Slack sees them: task chunks, text deltas, buttons."""

import json

from llm_wiki.backend import RunResult
from llm_wiki.domain.review import ReviewRequest
from llm_wiki.progress import Progress, Step, step_plan
from llm_wiki.slack.render import (
    APPROVE,
    MAX_MARKDOWN,
    REJECT,
    public_review_link,
    reply_text,
    review_buttons,
    task_chunks,
    text_delta,
)

REVIEW = ReviewRequest(note="00_raw/daily/2026-10-12.md", files=())
OPEN = RunResult(thread_id="ingest-abc", status="review", review=REVIEW)


def test_the_first_chunks_are_every_step_then_only_changes():
    plan = step_plan("ingest")
    first = task_chunks(None, plan)
    assert [c["id"] for c in first] == [s.key for s in plan.steps]
    assert first[0] == {
        "type": "task_update", "id": "load_note", "title": "Read note", "status": "in_progress",
    }
    assert {c["status"] for c in first[1:]} == {"pending"}
    done = plan.model_copy(
        update={"steps": (plan.steps[0].model_copy(update={"state": "done"}), *plan.steps[1:])}
    )
    assert task_chunks(plan, done) == [
        {"type": "task_update", "id": "load_note", "title": "Read note", "status": "complete"}
    ]


def test_details_tool_calls_and_skips_show_on_the_step():
    step = Step(key="stage", label="Agent filing facts", state="running", calls=3)
    running = Progress(steps=(step,))
    assert task_chunks(None, running)[0]["details"] == "3 tool calls"
    skipped = Progress(steps=(step.model_copy(update={"state": "skipped", "calls": 0}),))
    assert task_chunks(running, skipped)[0] == {
        "type": "task_update", "id": "stage", "title": "Agent filing facts",
        "status": "complete", "details": "skipped",
    }
    quiet = step.model_copy(update={"state": "done", "detail": "nothing new to file"})
    assert task_chunks(running, Progress(steps=(quiet,)))[0]["details"] == "nothing new to file"


def test_text_delta_appends_and_starts_over_after_a_reset():
    assert text_delta("", "Atlas") == "Atlas"
    assert text_delta("Atlas", "Atlas beta") == " beta"
    assert text_delta("Let me look.", "The beta") == "\n\nThe beta"  # Slack can't unsend
    assert text_delta("Atlas", "Atlas") == ""


def test_an_open_review_gets_buttons_carrying_its_token():
    (actions,) = review_buttons(OPEN, "https://mac.tail.ts.net:8443")
    assert [e.get("action_id") for e in actions["elements"]][:2] == [APPROVE, REJECT]
    value = json.loads(actions["elements"][0]["value"])
    assert value == {"thread": "ingest-abc", "seen": REVIEW.token}
    assert actions["elements"][2]["url"] == "https://mac.tail.ts.net:8443/review/ingest-abc"


def test_no_link_button_for_a_loopback_review_url():
    for local in ("http://127.0.0.1:2024", "http://localhost:8123", "http://[::1]:2024"):
        assert public_review_link(local, "ingest-abc") is None
    (actions,) = review_buttons(OPEN, "http://127.0.0.1:2024")
    assert len(actions["elements"]) == 2


def test_finished_runs_get_no_buttons():
    done = RunResult(
        thread_id="t", status="done", action="approve", written=("a.md",), commit="e9c695b1"
    )
    assert review_buttons(done, None) == []
    assert reply_text(done, None).startswith("Applied 1 files")


def test_long_reviews_are_cut_to_slack_limits():
    many = ReviewRequest(note="n", files=(), warnings=tuple("w" * 200 for _ in range(200)))
    text = reply_text(RunResult(thread_id="t", status="review", review=many), None)
    assert len(text) <= MAX_MARKDOWN
    assert text.endswith("(cut: open the review page for the rest)")
    long_step = Step(key="stage", label="x" * 400, state="running", detail="d" * 400)
    chunk = task_chunks(None, Progress(steps=(long_step,)))[0]
    assert len(chunk["title"]) <= 256 and len(chunk["details"]) <= 256
```

- [ ] **Step 2: Run, expect FAIL** (`uv add` first, so the Slack imports in later tasks resolve)

Run: `uv add "slack-sdk>=3.45.0" "slack-bolt>=1.30.0" && uv run pytest tests/unit_tests/test_slack_render.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.slack'`

- [ ] **Step 3: Implement**

```python
# src/llm_wiki/slack/__init__.py
"""The Slack surface (spec 2026-10-18, step 2): DMs with the wiki bot, over `Backend`."""
```

```python
# src/llm_wiki/slack/render.py
"""What Slack shows, from what the backend returns (pure; no Slack calls).

    Progress  → task_update chunks (only steps that changed) + answer text deltas
    RunResult → the chat's markdown (`chat.render`) + Approve / Reject / Open buttons
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from llm_wiki.backend import RunResult
from llm_wiki.chat.render import render_result
from llm_wiki.chat.session import review_link
from llm_wiki.progress import Progress, Step

APPROVE = "wiki_approve"
REJECT = "wiki_reject"
OPEN_PAGE = "wiki_open_page"
MAX_MARKDOWN = 12_000  # markdown_text in chat.appendStream / chat.stopStream
MAX_TASK_TEXT = 256  # task_update chunk text
CUT = "\n\n… (cut: open the review page for the rest)"

# Slack has no "skipped": a step the run never needed shows as complete, "skipped".
_STATUS = {
    "pending": "pending",
    "running": "in_progress",
    "done": "complete",
    "skipped": "complete",
}
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _short(text: str, limit: int = MAX_TASK_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _chunk(step: Step) -> dict[str, str]:
    chunk = {
        "type": "task_update",
        "id": step.key,
        "title": _short(step.label),
        "status": _STATUS[step.state],
    }
    details = step.detail
    if details is None and step.state == "skipped":
        details = "skipped"
    elif details is None and step.calls:
        details = f"{step.calls} tool calls"
    if details:
        chunk["details"] = _short(details)
    return chunk


def task_chunks(before: Progress | None, after: Progress) -> list[dict[str, str]]:
    """Return a task chunk per step that is new or changed since `before`."""
    old = {s.key: s for s in before.steps} if before else {}
    return [_chunk(s) for s in after.steps if old.get(s.key) != s]


def text_delta(sent: str, text: str) -> str:
    """Return what to append so the reader sees `text`, given `sent` already shown.

    Streamed text can't be taken back: when the answer restarts (the assistant
    searched after a preamble), the new text goes after a blank line.
    """
    if text.startswith(sent):
        return text[len(sent) :]
    return "\n\n" + text if text else ""


def public_review_link(review_url: str, thread_id: str) -> str | None:
    """Return the review page link, or None when it would only work on this machine."""
    host = urlsplit(review_url).hostname or ""
    return None if host in _LOOPBACK else review_link(review_url, thread_id)


def reply_text(result: RunResult, review_url: str | None) -> str:
    """Return the result as markdown (the chat's own rendering), cut to Slack's limit."""
    link = public_review_link(review_url, result.thread_id) if review_url else None
    text = render_result(result, link)
    return text if len(text) <= MAX_MARKDOWN else text[: MAX_MARKDOWN - len(CUT)] + CUT


def review_buttons(result: RunResult, review_url: str | None) -> list[dict[str, Any]]:
    """Return Approve all / Reject (+ Open review page) for an open review, else []."""
    if result.status != "review" or result.review is None:
        return []
    value = json.dumps({"thread": result.thread_id, "seen": result.review.token})
    elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "action_id": APPROVE,
            "style": "primary",
            "text": {"type": "plain_text", "text": "Approve all"},
            "value": value,
        },
        {
            "type": "button",
            "action_id": REJECT,
            "style": "danger",
            "text": {"type": "plain_text", "text": "Reject"},
            "value": value,
        },
    ]
    link = public_review_link(review_url, result.thread_id) if review_url else None
    if link:
        elements.append(
            {
                "type": "button",
                "action_id": OPEN_PAGE,
                "url": link,
                "text": {"type": "plain_text", "text": "Open review page"},
            }
        )
    return [{"type": "actions", "block_id": "wiki_review", "elements": elements}]
```

- [ ] **Step 4: Run, expect PASS**

Run: `uv run pytest tests/unit_tests/test_slack_render.py -q && uv run ruff check src tests && uv run ruff format src tests`
Expected: 7 passed. Mutation check: drop the `old.get(s.key) != s` filter, and the "only changes" test fails.

- [ ] **Step 5: Commit (after the user's yes)**: "Slack: render progress and results (pure)".

---

### Task 3: `slack/stream.py`: one streaming reply

**Files:**
- Create: `src/llm_wiki/slack/stream.py`, `tests/unit_tests/slack_fake.py`, `tests/unit_tests/test_slack_stream.py`

**Interfaces:**
- Consumes: `render.task_chunks`, `render.text_delta`, `render.MAX_MARKDOWN`.
- Produces:
  - `Reply(client: WebClient, *, channel: str, thread_ts: str, team: str | None, user: str | None)`
  - `Reply.show(progress: Progress) -> None`
  - `Reply.finish(text: str, blocks: list[dict] | None = None, progress: Progress | None = None) -> str | None` (returns the reply's ts)
  - `Reply.fail(text: str) -> None`
  - `slack_fake.FakeSlack(WebClient)` with `.calls: list[tuple[str, dict]]`, `.fail_next: dict[str, str]`, `.methods()`, `.chunks()`, `.text()`

- [ ] **Step 1: Read `slack_sdk/web/chat_stream.py` (3.45.0) once.** Note the following, and use exactly those names below; if a name differs, record a ledger ruling:
  - how `ChatStream` sends `chat.startStream` (on its first flush);
  - which attribute holds the stream's ts after that (e.g. `_stream_ts`);
  - which arguments `api_call` receives (`json=` vs `params=`).

- [ ] **Step 2: Write the fake and the failing tests**

```python
# tests/unit_tests/slack_fake.py
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
        self._ts = 0

    def _response(self, method: str, data: dict[str, Any]) -> SlackResponse:
        return SlackResponse(
            client=self, http_verb="POST", api_url=method, req_args={},
            data=data, headers={}, status_code=200,
        )

    def api_call(self, api_method: str, **kwargs: Any) -> SlackResponse:  # type: ignore[override]
        args: dict[str, Any] = {}
        for key in ("json", "params", "data"):
            args |= kwargs.get(key) or {}
        self.calls.append((api_method, args))
        if error := self.fail_next.pop(api_method, None):
            raise SlackApiError(error, self._response(api_method, {"ok": False, "error": error}))
        data: dict[str, Any] = {"ok": True}
        if api_method in ("chat.startStream", "chat.postMessage"):
            self._ts += 1
            data |= {"ts": f"100.{self._ts}", "channel": args.get("channel")}
        return self._response(api_method, data)

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]

    def chunks(self) -> list[dict[str, Any]]:
        return [
            c for _, a in self.calls for c in a.get("chunks") or []
            if c.get("type") == "task_update"
        ]

    def text(self) -> str:
        """All text the user would read: streamed, posted, stopped or updated."""
        out = []
        for _, a in self.calls:
            out += [c.get("text", "") for c in a.get("chunks") or [] if c.get("type") == "markdown_text"]
            out += [a.get("markdown_text") or "", a.get("text") or ""]
            out += [b.get("text", "") for b in a.get("blocks") or [] if isinstance(b.get("text"), str)]
        return "".join(out)
```

```python
# tests/unit_tests/test_slack_stream.py
"""One streaming reply: steps tick, text streams, the result ends it."""

from llm_wiki.progress import step_plan
from llm_wiki.slack.stream import Reply

from .slack_fake import FakeSlack


def reply(slack):
    return Reply(slack, channel="D1", thread_ts="1.0", team="T1", user="U1")


def with_state(progress, **states):
    return progress.model_copy(update={"steps": tuple(
        s.model_copy(update={"state": states.get(s.key, s.state)}) for s in progress.steps
    )})


def test_steps_stream_then_the_result_stops_it():
    slack = FakeSlack()
    r = reply(slack)
    plan = step_plan("ingest")
    r.show(plan)
    r.show(with_state(plan, load_note="done", assign_ids="running"))
    ts = r.finish("**Review: n**", blocks=[{"type": "actions", "elements": []}])
    assert slack.methods()[0] == "chat.startStream"
    assert slack.methods()[-1] == "chat.stopStream"
    start = slack.calls[0][1]
    assert (start["channel"], start["thread_ts"]) == ("D1", "1.0")
    assert start["task_display_mode"] == "plan"
    assert {"load_note", "assign_ids"} <= {c["id"] for c in slack.chunks()}
    assert slack.calls[-1][1]["blocks"] and "**Review: n**" in slack.text()
    assert ts == "100.1"


def test_answer_text_streams_as_it_grows_and_is_not_repeated():
    slack = FakeSlack()
    r = reply(slack)
    ask = step_plan("ask")
    for text in ("Atlas", "Atlas beta", "Atlas beta moved."):
        r.show(ask.model_copy(update={"text": text}))
    r.finish("Atlas beta moved.")
    assert slack.text().count("Atlas beta moved.") == 1


def test_an_expired_stream_finishes_with_chat_update():
    slack = FakeSlack()
    r = reply(slack)
    r.show(step_plan("ingest"))
    slack.fail_next["chat.stopStream"] = "message_not_in_streaming_state"
    r.finish("**Review: n**", blocks=[{"type": "actions", "elements": []}])
    method, args = slack.calls[-1]
    assert method == "chat.update" and args["ts"] == "100.1"
    assert args["blocks"][-1]["type"] == "actions"


def test_a_finish_without_progress_posts_one_message():
    slack = FakeSlack()
    reply(slack).finish("No review is open.")
    assert slack.methods() == ["chat.postMessage"]
    assert "No review is open." in slack.text()


def test_fail_marks_running_steps_as_errors():
    slack = FakeSlack()
    r = reply(slack)
    r.show(step_plan("ingest"))
    r.fail("Can't reach the wiki server: down")
    stop = slack.calls[-1][1]
    assert any(c["status"] == "error" for c in stop["chunks"])
    assert "Can't reach the wiki server" in slack.text()
```

- [ ] **Step 3: Run, expect FAIL**

Run: `uv run pytest tests/unit_tests/test_slack_stream.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.slack.stream'`

- [ ] **Step 4: Implement**

```python
# src/llm_wiki/slack/stream.py
"""One reply in a DM thread: a Slack streaming message that `Progress` drives.

    Reply.show(progress)        → chat.startStream (first time), then task chunks + text
    Reply.finish(text, blocks)  → chat.stopStream with the rest of the text + buttons
                                  (chat.postMessage when nothing was streamed)

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
        self, client: WebClient, *, channel: str, thread_ts: str,
        team: str | None, user: str | None,
    ) -> None:
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
        return getattr(self._stream, "_stream_ts", None)  # name per Step 1

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
            if chunks:
                stream.append(chunks=chunks)
            if delta:
                stream.append(markdown_text=delta)
        except SlackApiError as exc:
            if not _expired(exc):
                raise
            self._expired = True  # finish() falls back to chat.update
        self._sent += delta

    def finish(
        self, text: str, blocks: list[dict[str, Any]] | None = None,
        progress: Progress | None = None,
    ) -> str | None:
        """End the reply with `text` (what isn't streamed yet) and `blocks`; return its ts."""
        if progress is not None:
            self.show(progress)
        if self._stream is None:
            response = self._client.chat_postMessage(
                channel=self._channel, thread_ts=self._thread_ts,
                text=text[:MAX_MARKDOWN], blocks=self._with_text(text, blocks),
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
            channel=self._channel, ts=self.ts or "",
            text=text[:MAX_MARKDOWN], blocks=self._with_text(text, blocks),
        )
        return self.ts

    def fail(self, text: str) -> None:
        """End the reply with an error line; steps still running are marked failed."""
        running = [
            c | {"status": "error"} for c in task_chunks(None, self._shown)
            if c["status"] == "in_progress"
        ] if self._shown else []
        if self._stream is not None and not self._expired:
            self._stream.stop(markdown_text="\n\n" + text, chunks=running or None)
        else:
            self._client.chat_postMessage(
                channel=self._channel, thread_ts=self._thread_ts, text=text
            )

    def _open(self) -> ChatStream:
        if self._stream is None:
            self._stream = self._client.chat_stream(
                channel=self._channel, thread_ts=self._thread_ts,
                recipient_team_id=self._team, recipient_user_id=self._user,
                task_display_mode="plan",
            )
        return self._stream

    @staticmethod
    def _with_text(text: str, blocks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        return [{"type": "markdown", "text": text[:MAX_MARKDOWN]}, *(blocks or [])]
```

- [ ] **Step 5: Run, expect PASS**

Run: `uv run pytest tests/unit_tests/test_slack_stream.py -q`
Expected: 5 passed. Mutation check: remove the `chat_update` fallback, and `test_an_expired_stream_finishes_with_chat_update` fails.

- [ ] **Step 6: Commit (after the user's yes)**: "Slack: streaming reply with chat.update fallback".

---

### Task 4: `slack/handlers.py`: DM messages

**Files:**
- Create: `src/llm_wiki/slack/handlers.py`, `tests/unit_tests/test_slack_handlers.py`

**Interfaces:**
- Consumes:
  - `chat.session.stream_reply(backend, message, history=(), *, link_base=...)`, which yields `Progress`, then one `ChatReply(text, result, progress)`
  - `render.reply_text`, `render.review_buttons`
  - `stream.Reply`
- Produces:
  - `WikiSlack(backend: Backend, *, user_id: str, review_url: str | None)`
  - `WikiSlack.on_message(event: dict, client: WebClient) -> None`
  - `CRASHED = "Something failed (details in the log)."`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit_tests/test_slack_handlers.py
"""DM messages and buttons over a local backend: scripted models, fake Slack."""

import json

import pytest
from langchain_core.messages import AIMessage

from llm_wiki.backend import BackendError
from llm_wiki.backend import local as local_backend
from llm_wiki.backend.local import LocalBackend
from llm_wiki.ingest import nodes
from llm_wiki.slack.handlers import WikiSlack
from llm_wiki.slack.render import APPROVE, REJECT

from .scripted_model import AgentRounds, ToolScript, bi_tool_decision
from .slack_fake import FakeSlack

USER = "U0USER"
NOTE = "00_raw/daily/2026-10-12.md"


@pytest.fixture
def bot(vault_copy, monkeypatch):
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([bi_tool_decision()]))
    monkeypatch.setattr(
        local_backend, "configured_model",
        lambda model: ToolScript(messages=iter([AIMessage("Atlas beta moved to Oct 20.")])),
    )
    monkeypatch.setattr(local_backend, "configured_fallbacks", lambda: [])
    backend = LocalBackend.in_memory(vault_copy)
    wiki = WikiSlack(backend, user_id=USER, review_url="https://mac.tail.ts.net:8443")
    return wiki, backend


def dm(text, user=USER, **extra):
    return {
        "type": "message", "channel_type": "im", "channel": "D1", "user": user,
        "text": text, "ts": "1.0", "team": "T1", **extra,
    }


def test_only_the_allowed_user_in_a_dm_is_answered(bot):
    wiki, backend = bot
    for event in (dm("/ingest 2026-10-12", user="U0OTHER"),
                  dm("/ingest 2026-10-12", channel_type="channel")):
        slack = FakeSlack()
        wiki.on_message(event, slack)
        assert slack.calls == []
    assert backend.pending() == []


@pytest.mark.parametrize("extra", [
    {"subtype": "message_changed"}, {"bot_id": "B1"}, {"subtype": "channel_join"},
    {"subtype": "file_share"}, {"text": "   "},
])
def test_events_that_are_not_the_users_text_are_ignored(bot, extra):
    wiki, backend = bot
    slack = FakeSlack()
    wiki.on_message(dm("/ingest 2026-10-12") | extra, slack)
    assert slack.calls == [] and backend.pending() == []


def test_ingest_streams_steps_then_the_review_with_buttons(bot):
    wiki, backend = bot
    slack = FakeSlack()
    wiki.on_message(dm("/ingest 2026-10-12"), slack)
    assert slack.methods()[0] == "chat.startStream"
    assert {"load_note", "stage", "review"} <= {c["id"] for c in slack.chunks()}
    (actions,) = slack.calls[-1][1]["blocks"]
    assert [e["action_id"] for e in actions["elements"]][:2] == [APPROVE, REJECT]
    (review,) = backend.pending()
    assert json.loads(actions["elements"][0]["value"])["thread"] == review.thread_id
    assert f"**Review: {NOTE}**" in slack.text()


def test_a_question_streams_its_answer(bot):
    wiki, _ = bot
    slack = FakeSlack()
    wiki.on_message(dm("When is the Atlas beta?"), slack)
    assert "Atlas beta moved to Oct 20." in slack.text()
    assert slack.calls[0][1]["thread_ts"] == "1.0"  # a reply under the user's message


def test_a_backend_failure_is_said_and_the_bot_stays_up(bot, monkeypatch):
    wiki, backend = bot

    def down(*a, **k):
        raise BackendError("the Agent Server at http://api:8000 failed: ConnectError")

    monkeypatch.setattr(backend, "pending", down)
    slack = FakeSlack()
    wiki.on_message(dm("/review"), slack)
    assert "Can't reach the wiki server: the Agent Server" in slack.text()


def test_an_unexpected_error_is_reported_without_details(bot, monkeypatch):
    wiki, backend = bot

    def boom(*a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(backend, "pending", boom)
    slack = FakeSlack()
    wiki.on_message(dm("/review"), slack)
    assert "Something failed (details in the log)" in slack.text()
    assert "secret detail" not in slack.text()
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/unit_tests/test_slack_handlers.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.slack.handlers'`

- [ ] **Step 3: Implement**

```python
# src/llm_wiki/slack/handlers.py
"""What the bot does with a DM or a button press (no Bolt here: `app.py` wires it).

    DM text ──user check──▶ chat.session.stream_reply ──▶ Reply (steps, text, result)
    button  ──user check──▶ backend.stream_answer(thread, {action, seen}) ──▶ Reply

Only `user_id`, only DMs. The same routing as the web chat: "/…" is a command,
anything else a question.
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

    def __init__(self, backend: Backend, *, user_id: str, review_url: str | None) -> None:
        self._backend = backend
        self._user_id = user_id
        self._review_url = review_url

    def on_message(self, event: dict[str, Any], client: WebClient) -> None:
        """Answer one DM from the allowed user; ignore every other event."""
        if event.get("subtype") or event.get("bot_id") or event.get("channel_type") != "im":
            return
        if event.get("user") != self._user_id:
            log.info("slack: ignored a message from user %s", event.get("user"))
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        reply = Reply(
            client, channel=event["channel"],
            thread_ts=event.get("thread_ts") or event["ts"],
            team=event.get("team"), user=self._user_id,
        )
        self._run(reply, lambda: stream_reply(
            self._backend, text, link_base=self._review_url or ""
        ))

    def _run(self, reply: Reply, events: Callable[[], Iterator[Progress | ChatReply]]) -> None:
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
```

- [ ] **Step 4: Run, expect PASS**

Run: `uv run pytest tests/unit_tests/test_slack_handlers.py -q`
Expected: 10 passed. Mutation check: remove the `event.get("user") != self._user_id` check, and the first test fails.

- [ ] **Step 5: Commit (after the user's yes)**: "Slack: DM handler (user check; ingest and questions streamed)".

---

### Task 5: Buttons: Approve all / Reject

**Files:**
- Modify: `src/llm_wiki/slack/handlers.py`, `tests/unit_tests/test_slack_handlers.py`

**Interfaces:**
- Consumes:
  - `Backend.review(thread) -> RunResult`
  - `Backend.stream_answer(thread, answer)`, which raises `UnknownThread`, `NoOpenReview` or `ReviewBusy` while iterating
  - Slack's `block_actions` payload: `user.id`, `channel.id`, `team.id`, `message.ts`, `message.thread_ts`, `message.blocks`, `actions[0].action_id`, `actions[0].value`
- Produces: `WikiSlack.on_action(body: dict, client: WebClient) -> None`

- [ ] **Step 1: Write the failing tests** (append to `test_slack_handlers.py`)

```python
def press(slack, wiki, action_id, value, user=USER, blocks=None):
    body = {
        "user": {"id": user}, "channel": {"id": "D1"}, "team": {"id": "T1"},
        "message": {"ts": "100.1", "thread_ts": "1.0",
                    "blocks": blocks or [{"type": "actions", "elements": []}]},
        "actions": [{"action_id": action_id, "value": value}],
    }
    wiki.on_action(body, slack)


def opened(wiki):
    slack = FakeSlack()
    wiki.on_message(dm("/ingest 2026-10-12"), slack)
    (actions,) = slack.calls[-1][1]["blocks"]
    return actions["elements"][0]["value"]


def test_approve_writes_and_the_buttons_are_replaced(bot, vault_copy):
    wiki, backend = bot
    value = opened(wiki)
    slack = FakeSlack()
    press(slack, wiki, APPROVE, value)
    method, args = slack.calls[0]
    assert method == "chat.update" and args["text"] == "Approving…"  # no double press
    assert all(b["type"] != "actions" for b in args["blocks"])
    assert {"apply", "commit"} <= {c["id"] for c in slack.chunks()}
    assert "Applied" in slack.text()
    assert backend.pending() == [] and "^t-" in (vault_copy / NOTE).read_text()


def test_a_second_press_says_already_answered(bot):
    wiki, _ = bot
    value = opened(wiki)
    press(FakeSlack(), wiki, REJECT, value)
    slack = FakeSlack()
    press(slack, wiki, APPROVE, value)
    assert "already answered" in slack.text()
    assert "Done. Nothing was written." in slack.text()


def test_a_button_on_a_changed_review_is_refused(bot, vault_copy):
    wiki, backend = bot
    value = opened(wiki)
    stale = json.dumps(json.loads(value) | {"seen": "0" * 16})  # e.g. revised on the page
    slack = FakeSlack()
    press(slack, wiki, APPROVE, stale)
    assert "changed since you opened it" in slack.text()
    assert len(backend.pending()) == 1 and "^t-" not in (vault_copy / NOTE).read_text()


def test_a_backend_failure_restores_the_buttons(bot, monkeypatch):
    wiki, backend = bot
    value = opened(wiki)

    def down(*a, **k):
        raise BackendError("the Agent Server at http://api:8000 failed: ConnectError")

    monkeypatch.setattr(backend, "stream_answer", down)
    slack = FakeSlack()
    original = [{"type": "actions", "elements": [{"action_id": APPROVE}]}]
    press(slack, wiki, APPROVE, value, blocks=original)
    updates = [a for m, a in slack.calls if m == "chat.update"]
    assert updates[-1]["blocks"] == original
    assert "Can't reach the wiki server" in slack.text()


def test_presses_from_another_user_do_nothing(bot):
    wiki, backend = bot
    value = opened(wiki)
    slack = FakeSlack()
    press(slack, wiki, APPROVE, value, user="U0OTHER")
    assert slack.calls == [] and len(backend.pending()) == 1


def test_a_busy_review_is_said(bot):
    wiki, backend = bot
    value = opened(wiki)
    running = backend.stream_answer(json.loads(value)["thread"], "reject")
    next(running)
    slack = FakeSlack()
    press(slack, wiki, APPROVE, value)
    assert "already being answered" in slack.text()
    list(running)
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/unit_tests/test_slack_handlers.py -q`
Expected: the 6 new tests FAIL with `AttributeError: 'WikiSlack' object has no attribute 'on_action'`

- [ ] **Step 3: Implement** (add to `WikiSlack`)

```python
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
        working = "Approving…" if verb == "approve" else "Rejecting…"
        client.chat_update(  # buttons gone first: no double press
            channel=channel, ts=message["ts"], text=working,
            blocks=[{"type": "markdown", "text": working}],
        )
        reply = Reply(
            client, channel=channel, thread_ts=message.get("thread_ts") or message["ts"],
            team=body.get("team", {}).get("id"), user=self._user_id,
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
            if result.status == "review":  # not accepted (e.g. stale): buttons back
                client.chat_update(channel=channel, ts=message["ts"], text="Review", blocks=original)
            reply.finish(reply_text(result, self._review_url),
                         review_buttons(result, self._review_url) or None, finished.progress)
        except (UnknownThread, NoOpenReview) as exc:
            current = self._backend.review(target["thread"])
            reply.finish(f"This review was already answered ({exc}).\n\n"
                         + reply_text(current, self._review_url))
        except ReviewBusy as exc:
            reply.finish(f"{exc}; wait for it to finish.")
        except BackendError as exc:
            client.chat_update(channel=channel, ts=message["ts"], text="Review", blocks=original)
            reply.fail(f"Can't reach the wiki server: {exc}")
        except Exception:  # noqa: BLE001
            log.exception("slack: button failed on thread %s", target.get("thread"))
            client.chat_update(channel=channel, ts=message["ts"], text="Review", blocks=original)
            reply.fail(CRASHED)
```

A stale `seen` comes back as a review with `review.error` set ("this review changed since you opened it…"). The original buttons go back, and `reply_text` renders "⚠️ Not accepted: …" from `render_review`.

- [ ] **Step 4: Run, expect PASS**

Run: `uv run pytest tests/unit_tests/test_slack_handlers.py -q`
Expected: 16 passed. Mutation checks:
- Drop `answer["seen"]`, and the changed-review test fails.
- Drop the first `chat_update`, and the replaced-buttons test fails.

- [ ] **Step 5: Commit (after the user's yes)**: "Slack: Approve all / Reject buttons".

---

### Task 6: Bolt wiring, `__main__`, manifest, settings

**Files:**
- Create: `src/llm_wiki/slack/app.py`, `src/llm_wiki/slack/__main__.py`, `src/llm_wiki/slack/manifest.yml`, `tests/unit_tests/test_slack_app.py`
- Modify: `src/llm_wiki/config.py`, `pyproject.toml` (package-data `"llm_wiki.slack" = ["manifest.yml"]`), `.env.example`

**Interfaces:**
- Consumes: `WikiSlack` (anything with `on_message(event, client)` and `on_action(body, client)`).
- Produces:
  - `Settings.slack_bot_token: SecretStr | None` (env `SLACK_BOT_TOKEN`), `Settings.slack_app_token: SecretStr | None` (`SLACK_APP_TOKEN`), `Settings.slack_user_id: str | None` (`WIKI_SLACK_USER_ID`)
  - `app.build_app(wiki, *, bot_token: str, client: WebClient | None = None) -> slack_bolt.App`
  - `__main__.missing_settings(settings: Settings) -> list[str]`
  - `__main__.main() -> int`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit_tests/test_slack_app.py
"""Bolt wiring and start-up: no Slack network."""

from importlib.resources import files

import yaml
from slack_bolt.request import BoltRequest

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


def test_events_and_buttons_reach_the_handlers_and_are_acked():
    wiki = Recorder()
    app = build_app(wiki, bot_token="xoxb-test", client=FakeSlack())
    event = {"type": "event_callback", "team_id": "T1", "event": {
        "type": "message", "channel_type": "im", "user": "U1", "text": "hi",
        "ts": "1.0", "channel": "D1",
    }}
    assert app.dispatch(BoltRequest(body=event, mode="socket_mode")).status == 200
    assert wiki.messages[0]["text"] == "hi"
    action = {"type": "block_actions", "team": {"id": "T1"}, "user": {"id": "U1"},
              "channel": {"id": "D1"}, "message": {"ts": "100.1"},
              "actions": [{"action_id": "wiki_approve", "value": "{}", "type": "button"}]}
    assert app.dispatch(BoltRequest(body=action, mode="socket_mode")).status == 200
    assert len(wiki.actions) == 1


def test_start_up_names_each_missing_setting(tmp_path):
    bare = Settings(_env_file=None, vault_path=tmp_path)
    assert missing_settings(bare) == ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "WIKI_SLACK_USER_ID"]
    ready = Settings(
        _env_file=None, vault_path=tmp_path,
        SLACK_BOT_TOKEN="xoxb-1", SLACK_APP_TOKEN="xapp-1", slack_user_id="U1",
    )
    assert missing_settings(ready) == []


def test_the_manifest_asks_only_for_what_the_bot_uses():
    manifest = yaml.safe_load(files("llm_wiki.slack").joinpath("manifest.yml").read_text())
    assert sorted(manifest["oauth_config"]["scopes"]["bot"]) == [
        "chat:write", "im:history", "im:read", "im:write",
    ]
    settings = manifest["settings"]
    assert settings["socket_mode_enabled"] is True
    assert settings["interactivity"]["is_enabled"] is True
    assert settings["event_subscriptions"]["bot_events"] == ["message.im"]
```

The `test_start_up_names_each_missing_setting` test needs the conftest to unset `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` and `WIKI_SLACK_USER_ID`. Add `monkeypatch.delenv(name, raising=False)` for each in `_vaults_tests_may_use`: the developer's real `.env` values must never reach a test.

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/unit_tests/test_slack_app.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.slack.app'`

- [ ] **Step 3: Implement**

`config.py`, in `Settings` (plus docstring lines; `AliasChoices` and `SecretStr` from pydantic):

```python
    slack_bot_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("SLACK_BOT_TOKEN", "slack_bot_token")
    )
    slack_app_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("SLACK_APP_TOKEN", "slack_app_token")
    )
    slack_user_id: str | None = None  # WIKI_SLACK_USER_ID
```

```python
# src/llm_wiki/slack/app.py
"""Bolt wiring: DM messages and the review buttons go to `WikiSlack` (sync, 0014)."""

from __future__ import annotations

import re
from typing import Any

from slack_bolt import Ack, App
from slack_sdk import WebClient

from llm_wiki.slack.handlers import WikiSlack


def build_app(wiki: WikiSlack, *, bot_token: str, client: WebClient | None = None) -> App:
    """Return the Bolt app; `client` replaces Slack's WebClient in tests."""
    app = App(
        token=bot_token, client=client,
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
```

```python
# src/llm_wiki/slack/__main__.py
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
        print(f"llm_wiki.slack: set {', '.join(missing)} (docs/setup/slack.md)", file=sys.stderr)
        return 2
    from slack_bolt.adapter.socket_mode import SocketModeHandler  # noqa: PLC0415

    from llm_wiki.backend import get_backend  # noqa: PLC0415
    from llm_wiki.slack.app import build_app  # noqa: PLC0415
    from llm_wiki.slack.handlers import WikiSlack  # noqa: PLC0415

    assert settings.slack_bot_token and settings.slack_app_token and settings.slack_user_id
    wiki = WikiSlack(get_backend(), user_id=settings.slack_user_id, review_url=settings.review_url)
    app = build_app(wiki, bot_token=settings.slack_bot_token.get_secret_value())
    SocketModeHandler(app, settings.slack_app_token.get_secret_value()).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```yaml
# src/llm_wiki/slack/manifest.yml — create the Slack app from this (docs/setup/slack.md)
display_information:
  name: wiki
  description: Your work wiki in a DM (llm-wiki)
features:
  app_home:
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: wiki
    always_online: false
oauth_config:
  scopes:
    bot: [chat:write, im:history, im:read, im:write]
settings:
  event_subscriptions:
    bot_events: [message.im]
  interactivity:
    is_enabled: true
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

`.env.example`:

```
# Slack bot (optional; docs/setup/slack.md). Values go in .env only, never in a chat.
# SLACK_BOT_TOKEN=     # xoxb-…  OAuth & Permissions
# SLACK_APP_TOKEN=     # xapp-…  Basic Information → App-Level Tokens (connections:write)
# WIKI_SLACK_USER_ID=  # your member id (profile → ⋯ → Copy member ID); the only user answered
```

- [ ] **Step 4: Run, expect PASS**

Run: `uv run pytest tests/unit_tests/test_slack_app.py -q && uv run pytest tests/unit_tests -q`
Expected: 3 passed; the whole suite passes.

- [ ] **Step 5: Commit (after the user's yes)**: "Slack: Bolt app, start-up check, manifest".

---

### Task 7: Compose service, setup guide, 0016 addendum

**Files:**
- Modify: `compose.yml`, `tests/unit_tests/test_compose.py`, `docs/wiki/decisions/0016-real-notes-to-a-cloud-model.md`
- Create: `docs/setup/slack.md`

- [ ] **Step 1: Write the failing test** (append to `test_compose.py`)

```python
def test_the_slack_bot_is_opt_in_and_reaches_the_api_inside_compose():
    slack = COMPOSE["services"]["slack"]
    assert slack["profiles"] == ["slack"]  # `docker compose up` alone never starts it
    assert slack["entrypoint"] == ["python", "-m", "llm_wiki.slack"]
    env = slack["environment"]
    assert env["WIKI_BACKEND"] == "server"
    assert env["WIKI_SERVER_URL"] == "http://api:8000"
    assert "ports" not in slack  # Socket Mode: nothing listens
    assert "${WIKI_VAULT_PATH}:${WIKI_VAULT_PATH}:ro" in slack["volumes"]
    assert slack["depends_on"]["api"]["condition"] == "service_healthy"
```

- [ ] **Step 2: Run, expect FAIL**: `uv run pytest tests/unit_tests/test_compose.py -q` gives `KeyError: 'slack'`.

- [ ] **Step 3: Implement**

`compose.yml`, under `services:`:

```yaml
  # The Slack bot, opt-in: docker compose --profile slack up -d --wait
  # Socket Mode connects out to Slack; nothing listens here. The vault is mounted
  # read-only only because settings check that WIKI_VAULT_PATH exists.
  slack:
    image: ${WIKI_IMAGE:-llm-wiki:local}
    profiles: ["slack"]
    entrypoint: ["python", "-m", "llm_wiki.slack"]
    env_file:
      - path: .env
        required: false
    environment:
      WIKI_VAULT_PATH: ${WIKI_VAULT_PATH:?set WIKI_VAULT_PATH to the vault's absolute path}
      WIKI_BACKEND: server
      WIKI_SERVER_URL: http://api:8000
    volumes:
      - ${WIKI_VAULT_PATH}:${WIKI_VAULT_PATH}:ro
    depends_on:
      api:
        condition: service_healthy
    healthcheck:
      disable: true
    restart: unless-stopped
```

`docs/setup/slack.md` covers these steps:
1. **What it is.** Opt-in; the web page stays the default.
2. **The Slack app**, created from `src/llm_wiki/slack/manifest.yml` (done 2026-10-18; re-check that the Messages tab is on).
3. **Where each value comes from.** The user pastes values into `.env`, never into a chat.
4. **`WIKI_SERVER_TOKEN`**, with the one-line command.
5. **`WIKI_REVIEW_URL`**, set to the tailnet URL for the "Open review page" button.
6. **Running it:** `uv run python -m llm_wiki.slack`, or `docker compose --profile slack up -d --wait` after `uv run langgraph build -t llm-wiki:local`.
7. **Troubleshooting:**
   - Slack says sending messages is turned off (`messages_tab_disabled`).
   - No answer: `WIKI_SLACK_USER_ID` is wrong; the log names the ignored user id.
   - "Can't reach the wiki server": the api is down, or the tokens don't match (401).

`0016` addendum (2026-10-18, Slack):
- With the Slack bot on, the user's messages, the bot's answers, review summaries and step names are kept in the DM history on Slack's servers. A free workspace shows 90 days.
- Diffs and vault files are not sent, except as summarised in the review text.
- Turning the bot off stops new messages. Deleting the history is the user's call.

- [ ] **Step 4: Run, expect PASS**:
  - `uv run pytest tests/unit_tests -q`
  - `docker compose config --services` lists `api postgres redis`.
  - `docker compose --profile slack config --services` also lists `slack`.

- [ ] **Step 5: Commit (after the user's yes)**: "Slack: opt-in compose service, setup guide, 0016 addendum".

---

### Task 8: Notebook 8.4, wiki, real check

**Files:**
- Create: `notebooks/_src/nb8.4.py`, which builds `notebooks/8.4_chat_surface.ipynb` (git-ignored); `docs/wiki/learnings/slack-surface.md` (from `_template.md`)
- Modify: `notebooks/README.md` (8.4 row), `docs/wiki/index.md`, `docs/wiki/log.md`, `docs/wiki/progress.md`

- [ ] **Step 1: Notebook 8.4** (spec: "fake Slack client, buttons → `Command(resume=…)`, allow-listing, sync vs async Socket Mode"). It runs without keys, using `FakeSlack` and the scripted models from `tests/unit_tests`. Sections:
  1. A Slack message event and a `block_actions` payload, by hand.
  2. A toy graph with `interrupt()`; a button press becomes `Command(resume=...)`.
  3. Allow-listing as one `if`, and why the bot's own messages loop without the `bot_id` check.
  4. `chat_stream` task chunks from a toy `Progress` (on `FakeSlack`, printing each call).
  5. The real `WikiSlack` on a vault copy: `/ingest`, then Approve.
  6. Sync vs async Socket Mode (0014), and paid-media `socket_mode.py`.
  7. Exercises: `chunks_for(before, after)`; a `stale_button` check; `coalesce(calls, per_second)`; 3 questions.

  Build with `_nb.build`, then run `uv run jupyter nbconvert --to notebook --execute --inplace notebooks/8.4_chat_surface.ipynb`. Expected: runs clean.
- [ ] **Step 2: Wiki.** Add the learning note `slack-surface` (repo paths; paid-media `surfaces/slack/`), link it from `index.md`, add the `log.md` entry, and update `progress.md` (step 2 built, awaiting commit).
- [ ] **Step 3: Whole-branch review.** Use a fresh reviewer agent only if the user asks; otherwise do a self-review against this plan and the spec, recorded in the ledger.
- [ ] **Step 4: Real check** (≈ $0.02; only after the user's OK in chat):
  - On `~/WorkDev`, run the bot locally against the dev or Docker server.
  - From the phone:
    - `/ingest`: steps tick, then the summary and buttons.
    - Approve: files written and committed, buttons replaced.
    - One question streams its answer.
    - A quiet note shows "nothing new to file" with no model call.
  - Record it in the log.
- [ ] **Step 5: Decision 0018 (Slack surface)** is written once the user approves step 2 (spec).

---

## Self-review notes

- **Spec coverage:**
  - DM ingest with streamed steps and summary buttons: Tasks 4 and 5.
  - Questions stream: Task 4.
  - Approve/Reject via the same `ReviewAnswer`: Task 5.
  - Buttons replaced and second press refused: Task 5.
  - User check: Tasks 4 and 5.
  - Stream-expired fallback: Task 3.
  - Errors table: Tasks 4 and 5.
  - Missing-token exit: Task 6.
  - Manifest, setup doc, compose service: Tasks 6 and 7.
  - 0016 addendum: Task 7.
  - Notebook 8.4: Task 8.
  - "Progress updates buffered: at most ~1 Slack call per second": `ChatStream` buffers text (256 characters), and step chunks are sent only on change. An ingest has about 10 step changes, plus one per inner tool round. If the real run hits `ratelimited`, add a 1-second coalescing window in `Reply.show`: a ledger ruling, tested with `FakeSlack.fail_next`.
  - Notifier (step 3) and auto-ingest (step 4) are not in this plan.
- **Placeholders:** none. Task 3 Step 1 makes reading `chat_stream.py` explicit, so the ts attribute isn't guessed.
- **Types:**
  - `Reply.finish(text, blocks, progress)` is used the same way in Tasks 4 and 5.
  - `review_buttons` returns `[]` for a closed review.
  - `ReviewRequest.token` and `ReviewAnswer.seen` come from `7d988e5`.
