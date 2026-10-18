# Slack Step 3: the Notifier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The bot DMs the user once when a review opens, whatever started it (review page, CLI, cron), and
clears the buttons of a review answered somewhere else.

**Architecture:** The notifier is a reconcile loop in the bot's own process (a daemon thread). Every 60 s it
compares what is open (`backend.pending()`) with what Slack shows (`Announced`, a small JSON file of
`thread → {channel, ts, seen}`), and fixes the difference:
- open, not shown → post a DM with the summary and buttons, record it;
- shown, no longer open → replace the message with the outcome (no buttons), forget it;
- shown, open, token changed (edited on the page) → update the message's text and buttons in place.

The DM handlers write to the same `Announced`: a review they show is recorded, a review they answer is
forgotten. So an `!ingest` from Slack is never announced twice. While a handler run is in flight, notifier
passes are skipped (the run's review isn't recorded yet).

**Tech Stack:** Python 3.12, pytest, uv; slack-sdk 3.45.0 (`WebClient.conversations_open`, `chat_postMessage`,
`chat_update`); slack-bolt 1.30.0 (wiring unchanged); `threading`.

**Spec:** `docs/superpowers/specs/2026-10-18-slack-adapter-design.md` (build order step 3; "Notifier" in Data
flow; "Notifier state" in Components). Steps 1–2 are in (`f6b5b13`, `5ee0831`, `fbddb80`); decision 0018.

**Where this differs from the spec, on purpose:**
- **Answered elsewhere → the old message is updated, not only dropped from the file.** The spec only drops the
  record. The phone check (2026-10-18) found stale buttons that said "already answered" when pressed.
  Clearing them is part of step 3 (progress.md).
- **Edited on the page → the message is updated in place**, not announced again. An edit changes the token;
  the user gets one DM per review, not one per edit.
- **Handlers share the record**, so Slack-started reviews are not announced twice. The spec doesn't say this,
  but it's needed.
- **No new setting.** 60 s is a constant (`INTERVAL`); the state is at `WIKI_STATE_DIR/slack-announced.json`.

## Global Constraints

- `slack/` depends only on `Backend`, `session`, `progress`, `slack_bolt`, `slack_sdk` (spec, Components).
  The notifier never touches the vault or graphs.
- One allowed user (`WIKI_SLACK_USER_ID`), DMs only.
- Logs carry thread ids, never tokens or note text.
- Failure isolation: a Slack or server outage must not stop the bot or the loop.
- Tests: no Slack network (`FakeSlack`), scripted model, fixture vault (CLAUDE.md).
- Mutation check: removing the announced-file record fails a test (spec, Testing).
- Commit only after the user approves (memory: commit after walkthrough). "Stage" steps below mean "stage
  and ask".

**Changed in the build (2026-10-18, after the branch review):** `quiet()` (a lock held for the whole pass) became
`idle()` (no lock across slow calls; a button press never waits behind a pass). A review missing from
`pending()` is cleared only if `review()` says it is done or skipped. A Slack error other than
`message_not_found`/`channel_not_found` keeps the record and retries. Each review in a pass is guarded on its
own. Tests: `test_slack_notifier.py` (the five added at the end).

## Review Focus

1. **The bot restarts with reviews already announced** → no second DM. The state is a file, and compose must
   keep it when `up -d` recreates the container. Tests: Task 1 (reload), Task 5 (compose volume).
2. **The state file is missing, empty or corrupt** (hand-edited, half-written) → treated as empty and logged;
   the bot keeps running. Writes are atomic (temp file + `os.replace`). Test: Task 1.
3. **The state dir can't be written** (the container user lacks permission) → log it, keep the record in
   memory, and keep the loop going. Test: Task 1.
4. **The announced message was deleted in Slack** (`message_not_found` on `chat.update`) → forget it; don't
   crash and don't post an answered review again. Test: Task 2.
5. **The server is down for a pass** (`BackendError` from `pending()` or `review()`) → nothing is cleared,
   because an outage is not "answered"; the next pass works. Test: Task 2.

---

## File Structure

```
src/llm_wiki/slack/
  announced.py   NEW  Announced: thread → Shown(channel, ts, seen), JSON file, lock, busy()/quiet()
  notifier.py    NEW  Notifier.run_once(client) / run_forever(client, stop)
  render.py      + message_blocks(text, blocks), answered_text(result, review_url), NOTICE
  stream.py      Reply._with_text → render.message_blocks
  handlers.py    WikiSlack(…, announced=): record what it shows, forget what it answers, busy()
  __main__.py    one Announced; notifier thread next to SocketModeHandler
compose.yml      slack: WIKI_STATE_DIR + named volume
tests/unit_tests/
  slack_fake.py               + conversations.open returns {"channel": {"id": "D1"}}
  test_slack_announced.py     NEW
  test_slack_notifier.py      NEW
  test_slack_handlers.py      + records/forgets
  test_slack_app.py           + state path, notifier thread
  test_compose.py             + state volume
docs/setup/slack.md           + "Review notices"
```

---

### Task 1: `Announced`, the record of what Slack shows

**Files:**
- Create: `src/llm_wiki/slack/announced.py`
- Test: `tests/unit_tests/test_slack_announced.py`

**Interfaces:**
- Produces:
  - `class Shown(BaseModel)`, frozen: `channel: str`, `ts: str`, `seen: str` (the review token shown).
  - `class Announced`:
    - `__init__(path: Path | None)` (None = memory only)
    - `get(thread) -> Shown | None`
    - `items() -> list[tuple[str, Shown]]`
    - `record(thread, shown)`, `forget(thread)`
    - `busy() -> ContextManager[None]`: marks a handler run.
    - `quiet() -> ContextManager[bool]`: a notifier pass. It holds the lock and yields False while any run
      is busy.

- [ ] **Step 1: Write the failing tests**

```python
"""What Slack shows, kept across restarts: the notifier and the handlers share it."""

import json
import threading

from llm_wiki.slack.announced import Announced, Shown

SHOWN = Shown(channel="D1", ts="100.1", seen="abc")


def test_a_record_survives_a_restart(tmp_path):
    path = tmp_path / "state" / "slack-announced.json"
    Announced(path).record("ingest-1", SHOWN)
    again = Announced(path)
    assert again.get("ingest-1") == SHOWN
    again.forget("ingest-1")
    assert Announced(path).items() == []


def test_a_bad_file_counts_as_empty(tmp_path, caplog):
    path = tmp_path / "slack-announced.json"
    for text in ("", "{not json", '{"ingest-1": {"channel": 1}}', "[]"):
        path.write_text(text, encoding="utf-8")
        assert Announced(path).items() == []
    assert "slack-announced.json" in caplog.text


def test_a_write_that_fails_keeps_the_record_in_memory(tmp_path, caplog):
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")
    announced = Announced(blocked / "slack-announced.json")  # its parent is a file
    announced.record("ingest-1", SHOWN)
    assert announced.get("ingest-1") == SHOWN
    assert "could not save" in caplog.text


def test_writes_are_whole_files(tmp_path):
    path = tmp_path / "slack-announced.json"
    announced = Announced(path)
    announced.record("ingest-1", SHOWN)
    announced.record("ingest-2", SHOWN)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"ingest-1", "ingest-2"}
    assert [p.name for p in tmp_path.iterdir()] == ["slack-announced.json"]  # no temp left


def test_a_pass_is_not_quiet_while_a_run_is_busy():
    announced = Announced(None)
    with announced.quiet() as quiet:
        assert quiet
    with announced.busy():
        with announced.quiet() as quiet:
            assert not quiet
    with announced.quiet() as quiet:
        assert quiet


def test_a_run_waits_for_a_pass_in_progress():
    announced = Announced(None)
    order = []

    def run():
        with announced.busy():
            order.append("run")

    with announced.quiet():
        runner = threading.Thread(target=run)
        runner.start()
        runner.join(0.2)  # blocked on the pass
        order.append("pass done")
    runner.join(1)
    assert order == ["pass done", "run"]
```

- [ ] **Step 2: Run them, expect ImportError**

Run: `uv run pytest tests/unit_tests/test_slack_announced.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'llm_wiki.slack.announced'`

- [ ] **Step 3: Implement**

```python
"""What Slack shows of each open review: one message per thread, with the token shown.

The notifier (`notifier.py`) and the DM handlers (`handlers.py`) share one `Announced`.
A handler records the review it shows and forgets the one it answers, so the notifier
announces only reviews started elsewhere, and clears the buttons of reviews answered
elsewhere. The record is a JSON file in `WIKI_STATE_DIR`, so a restart doesn't
announce everything again; a bad or unwritable file is logged and the bot goes on.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)


class Shown(BaseModel):
    """The Slack message showing a review: where it is and which token it shows."""

    model_config = ConfigDict(frozen=True)

    channel: str
    ts: str
    seen: str


class Announced:
    """Thread id → `Shown`, saved to `path` after each change (None: memory only)."""

    def __init__(self, path: Path | None) -> None:
        """Load `path`; a missing, empty or bad file counts as nothing shown."""
        self._path = path
        # re-entrant: a notifier pass holds it (quiet) and records/forgets inside
        self._lock = threading.RLock()
        self._busy = 0
        self._shown: dict[str, Shown] = self._load()

    def get(self, thread: str) -> Shown | None:
        """Return the message showing `thread`'s review, if any."""
        with self._lock:
            return self._shown.get(thread)

    def items(self) -> list[tuple[str, Shown]]:
        """Return every (thread, message) shown."""
        with self._lock:
            return list(self._shown.items())

    def record(self, thread: str, shown: Shown) -> None:
        """Remember that `shown` shows `thread`'s review."""
        with self._lock:
            self._shown[thread] = shown
            self._save()

    def forget(self, thread: str) -> None:
        """Drop `thread` (answered, or its message is gone)."""
        with self._lock:
            if self._shown.pop(thread, None) is not None:
                self._save()

    @contextmanager
    def busy(self) -> Iterator[None]:
        """Mark a handler run; waits for a notifier pass in progress to finish."""
        with self._lock:
            self._busy += 1
        try:
            yield
        finally:
            with self._lock:
                self._busy -= 1

    @contextmanager
    def quiet(self) -> Iterator[bool]:
        """Hold off runs for one notifier pass; yield False if a run is in flight.

        A run's review isn't recorded until the run ends, so a pass during it could
        announce that review a second time.
        """
        with self._lock:
            yield self._busy == 0

    def _load(self) -> dict[str, Shown]:
        if self._path is None or not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
            return {thread: Shown.model_validate(v) for thread, v in raw.items()}
        except (OSError, ValueError, ValidationError) as exc:
            log.warning("slack: ignoring %s (%s)", self._path, type(exc).__name__)
            return {}

    def _save(self) -> None:
        """Write the whole file atomically (called with the lock held)."""
        if self._path is None:
            return
        data = {t: s.model_dump() for t, s in self._shown.items()}
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("slack: could not save %s (%s)", self._path, exc.strerror)
```

(`json.JSONDecodeError` and `ValidationError` are both `ValueError` subclasses; keeping `ValidationError` in
the tuple is only for the reader.)

- [ ] **Step 4: Run, expect PASS**

Run: `uv run pytest tests/unit_tests/test_slack_announced.py -q`
Expected: 6 passed

- [ ] **Step 5: Stage** `src/llm_wiki/slack/announced.py tests/unit_tests/test_slack_announced.py`

---

### Task 2: The notifier pass

**Files:**
- Create: `src/llm_wiki/slack/notifier.py`
- Modify: `src/llm_wiki/slack/render.py` (add `NOTICE`, `ANSWERED`, `message_blocks`, `answered_text`)
- Modify: `src/llm_wiki/slack/stream.py` (`Reply._with_text` → `message_blocks`)
- Modify: `tests/unit_tests/slack_fake.py` (`conversations.open`)
- Test: `tests/unit_tests/test_slack_notifier.py`

**Interfaces:**
- Consumes: `Announced`, `Shown` (Task 1); `Backend.pending()`, `Backend.review(thread)`;
  `render.reply_text`, `render.review_buttons`.
- Produces:
  - `render.message_blocks(text: str, blocks: list[dict] | None) -> list[dict]` (a markdown block, then
    `blocks`).
  - `render.answered_text(result: RunResult, review_url: str | None) -> str`.
  - `notifier.INTERVAL = 60`.
  - `class Notifier(backend, announced, *, user_id, review_url)`:
    - `run_once(client: WebClient) -> None`
    - `run_forever(client, stop: threading.Event, interval=INTERVAL) -> None`

- [ ] **Step 1: Make the fake Slack open a DM**

In `FakeSlack.api_call`, next to the `conversations.replies` branch:

```python
        if api_method == "conversations.open":
            data |= {"channel": {"id": "D1"}}
```

- [ ] **Step 2: Write the failing tests**

```python
"""The notifier: one DM per review opened elsewhere; buttons cleared when it's answered elsewhere."""

import threading

import pytest
from langchain_core.messages import AIMessage

from llm_wiki.backend import BackendError
from llm_wiki.backend import local as local_backend
from llm_wiki.backend.local import LocalBackend
from llm_wiki.ingest import nodes
from llm_wiki.slack.announced import Announced
from llm_wiki.slack.notifier import Notifier
from llm_wiki.slack.render import APPROVE

from .scripted_model import AgentRounds, ToolScript, bi_tool_decision
from .slack_fake import FakeSlack

USER = "U0USER"
NOTE = "00_raw/daily/2026-10-12.md"


@pytest.fixture
def setup(vault_copy, monkeypatch, tmp_path):
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([bi_tool_decision()]))
    monkeypatch.setattr(
        local_backend,
        "configured_model",
        lambda model: ToolScript(messages=iter([AIMessage("ok")])),
    )
    monkeypatch.setattr(local_backend, "configured_fallbacks", lambda: [])
    backend = LocalBackend.in_memory(vault_copy)
    announced = Announced(tmp_path / "slack-announced.json")
    notifier = Notifier(
        backend, announced, user_id=USER, review_url="https://mac.tail.ts.net:8443"
    )
    return backend, announced, notifier


def posts(slack):
    return [a for m, a in slack.calls if m == "chat.postMessage"]


def updates(slack):
    return [a for m, a in slack.calls if m == "chat.update"]


def buttons(args):
    return [
        e["action_id"]
        for b in args.get("blocks") or []
        if b["type"] == "actions"
        for e in b["elements"]
    ]


def test_a_review_opened_elsewhere_is_announced_once(setup, tmp_path):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    notifier.run_once(slack)
    (post,) = posts(slack)
    assert post["channel"] == "D1" and "thread_ts" not in post
    assert APPROVE in buttons(post)
    assert "waiting" in post["text"]
    assert announced.get(result.thread_id).seen == result.review.token
    # a restart reads the file: still one DM
    again = Notifier(
        backend, Announced(tmp_path / "slack-announced.json"), user_id=USER, review_url=None
    )
    again.run_once(slack)
    assert len(posts(slack)) == 1


def test_a_review_answered_elsewhere_loses_its_buttons(setup):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    backend.answer(result.thread_id, {"action": "reject"})
    notifier.run_once(slack)
    (update,) = updates(slack)
    assert update["channel"] == "D1" and update["ts"] == "100.1"  # the fake's first ts
    assert buttons(update) == []
    assert "Answered elsewhere" in update["text"]
    assert announced.get(result.thread_id) is None
    notifier.run_once(slack)
    assert len(updates(slack)) == 1


def test_an_edit_on_the_page_updates_the_message_in_place(setup):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    page = next(f for f in result.review.files if not f.path.startswith("90_meta"))
    edited = backend.answer(
        result.thread_id, {"action": "edit", "file": page.id, "text": "# Edited\n"}
    )
    assert edited.status == "review" and edited.review.token != result.review.token
    notifier.run_once(slack)
    assert len(posts(slack)) == 1
    (update,) = updates(slack)
    assert APPROVE in buttons(update)
    assert edited.review.token in str(update["blocks"])  # buttons carry the new token
    assert announced.get(result.thread_id).seen == edited.review.token


def test_a_deleted_message_is_forgotten(setup):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    backend.answer(result.thread_id, {"action": "reject"})
    slack.fail_next["chat.update"] = "message_not_found"
    notifier.run_once(slack)
    assert announced.get(result.thread_id) is None
    assert len(posts(slack)) == 1  # not posted again


def test_a_server_outage_clears_nothing(setup, monkeypatch, caplog):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)

    def down():
        raise BackendError("connection refused")

    monkeypatch.setattr(backend, "pending", down)
    notifier.run_once(slack)
    assert updates(slack) == []
    assert announced.get(result.thread_id) is not None
    assert "connection refused" in caplog.text


def test_no_pass_while_a_run_is_busy(setup):
    backend, announced, notifier = setup
    backend.start_ingest(NOTE)
    slack = FakeSlack()
    with announced.busy():
        notifier.run_once(slack)
    assert slack.calls == []


def test_the_loop_survives_a_failing_pass(setup, monkeypatch):
    backend, announced, notifier = setup
    stop = threading.Event()
    calls = []

    def boom(client):
        calls.append(1)
        if len(calls) == 2:
            stop.set()
        raise RuntimeError("bad pass")

    monkeypatch.setattr(notifier, "run_once", boom)
    notifier.run_forever(FakeSlack(), stop, interval=0)
    assert len(calls) == 2
```

If `bi_tool_decision()` is used up by the first ingest in a test that needs only one, leave it as is. If the
fixture vault's `90_meta` prefix is wrong for bookkeeping files, pick the page with
`next(f for f in result.review.files if f.path.startswith(("10_", "15_", "20_")))`.

- [ ] **Step 3: Run, expect ImportError**

Run: `uv run pytest tests/unit_tests/test_slack_notifier.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'llm_wiki.slack.notifier'`

- [ ] **Step 4: Render helpers** (append to `render.py`)

```python
NOTICE = "**A review is waiting.**\n\n"
ANSWERED = "**Answered elsewhere.**\n\n"


def message_blocks(
    text: str, blocks: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Return a whole message: `text` as a markdown block, then `blocks`."""
    return [{"type": "markdown", "text": text[:MAX_MARKDOWN]}, *(blocks or [])]


def answered_text(result: RunResult, review_url: str | None) -> str:
    """Return what replaces a review's buttons once it was answered somewhere else."""
    return ANSWERED + reply_text(result, review_url)
```

In `stream.py`, delete `Reply._with_text`, and call `message_blocks(text, blocks)` at its two call sites
(import it from `render`).

- [ ] **Step 5: Implement `notifier.py`**

```python
"""Tell the user about reviews in Slack, whatever started them (spec: Notifier).

Each pass reconciles what is open (`backend.pending()`) with what Slack shows
(`Announced`): a new review gets one DM with its buttons; a review answered elsewhere
gets its message replaced by the outcome (no buttons); a review edited on the page
gets its message updated in place. Code decides all of it; no model is called.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

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
        """Announce new reviews, clear answered ones, refresh edited ones."""
        with self._announced.quiet() as quiet:
            if not quiet:
                return
            try:
                open_reviews = {r.thread_id: r for r in self._backend.pending()}
            except BackendError as exc:
                log.warning("slack: notifier can't reach the wiki server: %s", exc)
                return
            for thread, shown in self._announced.items():
                current = open_reviews.get(thread)
                if current is None:
                    self._answered(client, thread, shown)
                elif current.review and current.review.token != shown.seen:
                    self._refresh(client, current, shown)
            for result in reversed(list(open_reviews.values())):  # oldest first
                if self._announced.get(result.thread_id) is None:
                    self._announce(client, result)

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
        if self._update(client, shown, text, review_buttons(result, self._review_url)):
            self._announced.record(
                result.thread_id, shown.model_copy(update={"seen": result.review.token})
            )
        else:
            self._announced.forget(result.thread_id)

    def _answered(self, client: WebClient, thread: str, shown: Shown) -> None:
        # BackendError here ends the pass with the record kept: the next pass retries
        try:
            text = answered_text(self._backend.review(thread), self._review_url)
        except UnknownThread:
            text = "This review isn't open any more."
        self._update(client, shown, text, None)
        self._announced.forget(thread)
        log.info("slack: cleared review %s (answered elsewhere)", thread)

    def _update(
        self,
        client: WebClient,
        shown: Shown,
        text: str,
        blocks: list[dict[str, Any]] | None,
    ) -> bool:
        """Replace the message; False if Slack no longer has it."""
        try:
            client.chat_update(
                channel=shown.channel,
                ts=shown.ts,
                text=text,
                blocks=message_blocks(text, blocks),
            )
        except SlackApiError as exc:
            log.warning(
                "slack: could not update %s (%s)", shown.ts, exc.response.get("error")
            )
            return False
        return True

    def _dm_channel(self, client: WebClient) -> str:
        if self._dm is None:
            self._dm = client.conversations_open(users=self._user_id)["channel"]["id"]
        return self._dm
```

- [ ] **Step 6: Run, expect PASS; the Slack suite stays green**

Run: `uv run pytest tests/unit_tests/test_slack_notifier.py tests/unit_tests/test_slack_stream.py tests/unit_tests/test_slack_handlers.py -q`
Expected: all pass

- [ ] **Step 7: Mutation checks** (revert each one after)
  - Remove `self._announced.record(...)` in `_announce` → `test_a_review_opened_elsewhere_is_announced_once`
    fails.
  - Make `_answered` skip `_update` → `test_a_review_answered_elsewhere_loses_its_buttons` fails.
  - Delete `if not quiet: return` → `test_no_pass_while_a_run_is_busy` fails.

- [ ] **Step 8: Stage** the five changed files.

---

### Task 3: Handlers share the record

**Files:**
- Modify: `src/llm_wiki/slack/handlers.py` (`WikiSlack.__init__`, `on_message`, `_run`, `on_action`)
- Test: `tests/unit_tests/test_slack_handlers.py`

**Interfaces:**
- Consumes: `Announced`, `Shown` (Task 1); `Reply.finish(...) -> str | None` (returns the message ts);
  `Notifier` (Task 2, in tests only).
- Produces: `WikiSlack(backend, *, user_id, review_url, announced: Announced | None = None)`. None means
  `Announced(None)`, so existing callers and tests keep working. Also a `WikiSlack.announced` property.

- [ ] **Step 1: Write the failing tests** (append to `test_slack_handlers.py`)

```python
from llm_wiki.slack.notifier import Notifier


def notifier_for(wiki, backend):
    return Notifier(backend, wiki.announced, user_id=USER, review_url=None)


def test_a_review_started_in_slack_is_not_announced_again(bot):
    wiki, backend = bot
    slack = FakeSlack()
    wiki.on_message(dm("/ingest 2026-10-12"), slack)
    (result,) = backend.pending()
    assert wiki.announced.get(result.thread_id).seen == result.review.token
    before = len(slack.calls)
    notifier_for(wiki, backend).run_once(slack)
    assert slack.calls[before:] == []


def test_a_review_answered_by_button_is_not_cleared_again(bot):
    wiki, backend = bot
    slack = FakeSlack()
    wiki.on_message(dm("/ingest 2026-10-12"), slack)
    (result,) = backend.pending()
    wiki.on_action(press(REJECT, result.thread_id, result.review.token), slack)
    assert wiki.announced.get(result.thread_id) is None
    before = len(slack.calls)
    notifier_for(wiki, backend).run_once(slack)
    assert slack.calls[before:] == []


def test_a_refused_press_records_the_reply_with_current_buttons(bot):
    wiki, backend = bot
    slack = FakeSlack()
    wiki.on_message(dm("/ingest 2026-10-12"), slack)
    (result,) = backend.pending()
    first = wiki.announced.get(result.thread_id)
    wiki.on_action(press(APPROVE, result.thread_id, "stale-token"), slack)  # refused
    shown = wiki.announced.get(result.thread_id)
    assert shown is not None and shown.ts != first.ts  # the new reply, not the pressed one
```

`press(action_id, thread, seen)` builds a button body. If the file already has a helper for this, use it and
adapt the calls above to its signature. If not, copy the body the existing approve test sends into a helper;
don't invent a new shape.

- [ ] **Step 2: Run, expect FAIL** (`AttributeError: 'WikiSlack' object has no attribute 'announced'`)

Run: `uv run pytest tests/unit_tests/test_slack_handlers.py -q`

- [ ] **Step 3: Implement**

In `WikiSlack.__init__`, add `announced: Announced | None = None` and
`self._announced = announced or Announced(None)`. Add:

```python
    @property
    def announced(self) -> Announced:
        """What this bot shows of each review (shared with the notifier)."""
        return self._announced

    def _shown(self, channel: str, ts: str | None, result: RunResult) -> None:
        """Record or forget `result`'s review once a reply has shown it."""
        if result.status == "review" and result.review is not None and ts:
            self._announced.record(
                result.thread_id,
                Shown(channel=channel, ts=ts, seen=result.review.token),
            )
        else:
            self._announced.forget(result.thread_id)
```

- `on_message`: wrap the `self._run(...)` call in `with self._announced.busy():`, and pass
  `channel=event["channel"]` to `_run` (a new keyword). In `_run`, when `last.result is not None`:
  `ts = reply.finish(text, blocks or None, last.progress)`, then `self._shown(channel, ts, last.result)`.
- `on_action`: wrap everything from `mark("Approving…" …)` to the end of the `try`/`except` in
  `with self._announced.busy():`. On success, after `ts = reply.finish(...)`, call
  `self._shown(channel, ts, result)`. In the `UnknownThread` and `NoOpenReview` branches, call
  `self._announced.forget(target["thread"])`. For `ReviewBusy`, `BackendError` and other exceptions, keep the
  record: the pressed message got its buttons back.

- [ ] **Step 4: Run, expect PASS**, then the whole unit suite:

Run: `uv run pytest tests/unit_tests -q`
Expected: all pass (833 before this step, plus the new tests)

- [ ] **Step 5: Mutation check:** remove the `self._shown(...)` call in `_run` →
  `test_a_review_started_in_slack_is_not_announced_again` fails. Revert.

- [ ] **Step 6: Stage** `handlers.py`, `test_slack_handlers.py`.

---

### Task 4: Wire it into the bot

**Files:**
- Modify: `src/llm_wiki/slack/__main__.py`
- Test: `tests/unit_tests/test_slack_app.py`

**Interfaces:**
- Consumes: `Announced`, `Notifier`, `WikiSlack(..., announced=)`.
- Produces:
  - `announced_path(settings) -> Path`, which is `settings.state_dir.expanduser() / "slack-announced.json"`.
  - `start_notifier(notifier, client) -> tuple[threading.Thread, threading.Event]`, a daemon thread named
    `wiki-notifier`.

- [ ] **Step 1: Failing tests** (append to `test_slack_app.py`)

```python
import threading

from llm_wiki.slack.__main__ import announced_path, start_notifier


def test_state_lives_in_the_state_dir(tmp_path):
    from llm_wiki.config import Settings

    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(vault_path=vault, state_dir=tmp_path / "state")
    assert announced_path(settings) == tmp_path / "state" / "slack-announced.json"


def test_the_notifier_runs_in_a_daemon_thread_until_stopped():
    started = threading.Event()

    class Waits:
        def run_forever(self, client, stop, interval=60):
            started.set()
            stop.wait()

    thread, stop = start_notifier(Waits(), client=None)
    assert thread.daemon and thread.name == "wiki-notifier"
    assert started.wait(1)
    stop.set()
    thread.join(1)
    assert not thread.is_alive()
```

If `Settings(...)` needs other fields or env isolation in this repo, build it the way `test_settings.py` does.

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Implement** in `__main__.py`:

```python
def announced_path(settings: Settings) -> Path:
    """Return where the bot keeps what it showed in Slack (outside the vault)."""
    return settings.state_dir.expanduser() / "slack-announced.json"


def start_notifier(
    notifier: Notifier, client: WebClient
) -> tuple[threading.Thread, threading.Event]:
    """Run the notifier next to Socket Mode; the thread ends with the process."""
    stop = threading.Event()
    thread = threading.Thread(
        target=notifier.run_forever,
        args=(client, stop),
        name="wiki-notifier",
        daemon=True,
    )
    thread.start()
    return thread, stop
```

In `main()`, after the settings check:
- `backend = get_backend()` and `announced = Announced(announced_path(settings))`;
- `WikiSlack(backend, …, announced=announced)`;
- before `SocketModeHandler(...).start()`:

```python
    start_notifier(
        Notifier(
            backend,
            announced,
            user_id=settings.slack_user_id,
            review_url=settings.review_url,
        ),
        WebClient(token=settings.slack_bot_token.get_secret_value()),
    )
```

Keep `Announced`, `Notifier` and `WebClient` imported inside `main()` like the existing imports. Import the
two names the helpers' signatures use (`Notifier`, `WebClient`) under `TYPE_CHECKING` as well. Update the
module docstring: "…and DMs the user when a review opens elsewhere (`notifier.py`)".

- [ ] **Step 4: Run, expect PASS.** Then run `uv run pytest tests/unit_tests -q`, `uv run ruff check src tests`
  and `uv run ruff format --check src tests`; all must be clean. Use the lint commands the repo already uses.

- [ ] **Step 5: Stage.**

---

### Task 5: Keep the state in compose; document it

**Files:**
- Modify: `compose.yml` (slack service, plus a top-level `volumes:`)
- Modify: `docs/setup/slack.md`
- Test: `tests/unit_tests/test_compose.py`

- [ ] **Step 1: Find a path the image's user can write** (check, don't guess):

Run: `docker run --rm --entrypoint sh llm-wiki:local -c 'id; echo $HOME; ls -ld $HOME'`
Expected: the non-root user and a home it owns. A named volume mounted on a path that exists in the image
takes that path's owner. On a path that doesn't exist, the volume is root-owned and the bot can't write;
Review Focus 3 would then only log. Use `$HOME/.llm-wiki` if the user owns its home. Otherwise create the
directory with the right owner in `dockerfile_lines` (`langgraph.json`) and pin it with a test.

- [ ] **Step 2: Failing test** (append to `test_compose.py`)

```python
def test_the_slack_bot_keeps_what_it_announced_across_restarts():
    slack = COMPOSE["services"]["slack"]
    state = slack["environment"]["WIKI_STATE_DIR"]
    assert f"slack-state:{state}" in slack["volumes"]
    assert "slack-state" in COMPOSE["volumes"]
```

- [ ] **Step 3: Implement** in the `compose.yml` slack service:

```yaml
    environment:
      …
      WIKI_STATE_DIR: <path from step 1>   # slack-announced.json: one DM per review, across restarts
    volumes:
      - ${WIKI_VAULT_PATH}:${WIKI_VAULT_PATH}:ro
      - slack-state:<path from step 1>
```

Also add `slack-state:` under the top-level `volumes:` (add the key if it's missing).

- [ ] **Step 4: Run, expect PASS.** Then a real check, with no model and no Slack: in the slack service
  container (`docker compose --profile slack run --rm --entrypoint python slack -c …`), record a `Shown` at
  `announced_path(get_settings())`, read it back in a new `Announced`, then forget it.
  Expected: the record reads back and the log has no "could not save" line.

- [ ] **Step 5: Docs.** Add a "Review notices" section to `docs/setup/slack.md`:
  - The bot checks every minute. It DMs you once for each review opened outside Slack (review page, CLI,
    cron).
  - When a review is answered elsewhere, its message loses the buttons and shows the outcome. When one is
    edited on the page, its message is updated in place.
  - The record is `WIKI_STATE_DIR/slack-announced.json` (in compose, the `slack-state` volume). If you delete
    it, open reviews are announced once more; nothing else changes.
  - On the first start after this step, reviews that are already open are announced once, because they
    weren't recorded.

- [ ] **Step 6: Stage.**

---

### Task 6: Notebook 8.5, real check, wiki

**Files:**
- Create: `notebooks/_src/nb8.5.py` → `notebooks/8.5_notifier.ipynb` (git-ignored); the answer key goes in the
  session scratchpad.
- Modify: `notebooks/README.md` (the Part 8 list).
- Modify: `docs/wiki/progress.md`, `docs/wiki/log.md`, `docs/wiki/index.md`,
  `docs/wiki/decisions/0018-slack-surface.md` (an addendum on notices).
- Create: `docs/wiki/learnings/reconcile-loop.md` (from `_template.md`).

- [ ] **Step 1: Notebook 8.5, "A notifier: reconcile, don't replay events"** (toy data, no keys, no Slack):
  1. Push vs poll: the bot polls `pending()` because the server has no webhook to us, and Socket Mode only
     connects outward.
  2. Desired vs shown state: a dict of open reviews against a dict of shown messages. The three differences
     (new, gone, changed token) as a pure function `plan(open, shown) -> list[action]`.
  3. Idempotency: run the pass twice, restart (reload the JSON), corrupt the file.
  4. Races: a handler run in flight; `busy()` / `quiet()` with two threads.
  5. Exercises: (a) write `plan()`; (b) make the save atomic; (c) say what goes wrong if an outage counts as
     "answered", and write the test that catches it.
  6. How the real code does it (`slack/announced.py`, `slack/notifier.py`). Paid-media has no notifier; the
     closest files are `surfaces/slack/service.py` (approvals) and `surfaces/runner.py`.
  Build it with `notebooks/_src/run.sh`; it must run clean.

- [ ] **Step 2: Real check on `~/WorkDev`.** Get the user's OK first: about $0.005–0.01 for one ingest, $0 if
  the note is quiet. Run the bot locally against the compose server, then:
  - start an ingest from the review page on the phone → a DM arrives within a minute;
  - reject it on the page → the DM loses its buttons within a minute;
  - `!ingest` in Slack → no extra DM.

- [ ] **Step 3: Wiki** (CLAUDE.md "Session end"): tick step 3 in progress with the date (next: step 4,
  auto-ingest), add a log entry, write the learning note `reconcile-loop`, add the index line and the 0018
  addendum.

- [ ] **Step 4: Ask the user to commit** (code first, then wiki), per memory.
