---
phase: 8
date: 2026-10-18
tags: [slack, langgraph, interrupt, auth]
---

# The Slack surface

## What it is

A bot in a Slack DM that drives the same `Backend` as the web chat. Socket Mode: the bot connects out to
Slack, nothing listens on the Mac. Slack sends message events and `block_actions` (button presses); the bot
answers with one streaming message per run (`chat_stream`: task chunks for steps, then text, then buttons).
A button press becomes the same `ReviewAnswer` the page sends, so it resumes the paused graph with
`Command(resume=…)`.

## How we used it

- `src/llm_wiki/slack/render.py`: `task_chunks` (only steps that changed), `text_delta` (Slack can't unsend),
  `reply_text` (cut at 12,000 chars), `review_buttons` (value `{thread, seen}`; "Open review page" only for
  a non-loopback `WIKI_REVIEW_URL`).
- `src/llm_wiki/slack/stream.py::Reply`: `show` / `finish` / `fail`; `chat.update` when Slack ends the stream
  (`message_not_in_streaming_state`).
- `src/llm_wiki/slack/handlers.py::thread_history`: a reply inside a thread carries the thread's earlier turns,
  read back with `conversations.replies` (user's and bot's messages, last 20; `questions_only` drops commands).
  No state of ours: Slack is the memory, as the page is for the web chat.
- `src/llm_wiki/slack/handlers.py::WikiSlack`: `on_message` (subtype/bot/DM/user checks, `!` → `/`),
  `on_action` (buttons replaced first, `stream_answer` with `seen`, buttons back on a server error).
- `src/llm_wiki/slack/app.py` (sync Bolt, `ack()` first), `__main__.py` (exit 2 naming missing settings),
  `manifest.yml`, compose `slack` service (`--profile slack`), `docs/setup/slack.md`.
- `src/llm_wiki/server_auth.py`: `langgraph.json` `auth`; clients send `WIKI_SERVER_TOKEN`
  (`backend/server.py::auth_headers`).
- Tests: `test_slack_render.py`, `test_slack_stream.py`, `test_slack_handlers.py`, `test_slack_app.py`,
  `test_server_auth.py`, `slack_fake.py` (`FakeSlack`: a real `WebClient`, `api_call` recorded). Notebook 8.4.

## Paid-media reference

`src/paid_media_agent/surfaces/slack/`: `socket_mode.py` (async `AsyncApp` + `AsyncSocketModeHandler`, a
rate-limit retry handler), `delivery.py` (events → `chat_stream`), `blocks.py` (approve/reject buttons). We use
sync Bolt (0014), and our buttons carry the review token so a stale message can't approve a changed review.

## Gotchas

- **Slack swallows `/…`.** The Slack client takes a leading `/` as one of its own slash commands and never sends
  the message. DM commands start with `!` (a leading space also works).
- **Bolt makes a new `WebClient` per request** (`context.client`). A client passed to `App(client=…)` isn't
  what handlers get; tests patch `WebClient.api_call`. Bolt calls `auth.test` once on the first request to learn
  its own ids.
- **`ChatStream` buffers text** until 256 characters; chunks flush at once. Send steps and text in one `append`.
- **Chunk objects reach `api_call` unserialised** (`MarkdownTextChunk`); the fake converts them with `to_dict()`.
- **A session fixture named in a test's signature starts before function-scoped autouse fixtures**: the
  401 contract test's server came up without `WIKI_VAULT_PATH`. Fetch it with `request.getfixturevalue`.
- **A test that reads `.env` sees the developer's real values**: `test_server_backend_sends_the_token` broke once
  `WIKI_SERVER_TOKEN` was in `.env` (and its failure would print the token). It now `chdir`s to a temp folder.
- **`.env` already holds the real tokens**: running `python -m llm_wiki.slack` in the repo starts the real bot.
  Check start-up from a folder without `.env`.

## In my words

