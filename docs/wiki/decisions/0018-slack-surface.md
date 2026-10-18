---
id: 0018
date: 2026-10-18
status: accepted
---

# 0018: The Slack surface

## Context

The user wanted to ingest and ask from the phone without opening the review page (daily-use plan, item 3;
Slack chosen over Telegram, personal workspace). Spec `docs/superpowers/specs/2026-10-18-slack-adapter-design.md`;
step 1 (streaming, `f6b5b13`) and step 2 (the bot, `5ee0831`) built, then threads as conversations (`fbddb80`).
Phone check on `~/WorkDev` passed 2026-10-18.

## Options

- **Slack bot in Socket Mode over `Backend`** (built): no inbound port, same rules as the web chat; Slack keeps
  the DM history (0016 addendum).
- **Telegram:** similar, but the user works in Slack.
- **Review page only on the phone (Tailscale):** works already, but no push, no streaming steps in a chat.

## Decision

- A sync Bolt bot (`python -m llm_wiki.slack`, or compose `--profile slack`) reaches the agents only through
  `Backend` and `chat.session` (0010, 0014). One allowed user (`WIKI_SLACK_USER_ID`), DMs only; the bot's own
  messages, edits and joins are ignored.
- Each run is one streaming reply in a thread under the user's message: steps as `task_update` chunks, then
  text; `chat.update` if Slack ends the stream.
- Commands start with `!` in Slack (`!ingest`): Slack's client never sends a message starting with `/`.
- A review ends with **Approve all / Reject / Open review page**. Buttons carry `{thread, seen}`; a review that
  changed since is refused (`check_answer`). A press swaps the buttons for the outcome; a server error puts
  them back.
- **Notices (step 3, 2026-10-18):** the bot checks `pending()` every 60 s and DMs a review opened elsewhere once;
  a review answered elsewhere gets its message replaced by the outcome (no buttons); one edited on the page is
  updated in place. What Slack shows is kept in `WIKI_STATE_DIR/slack-announced.json`. A review is cleared only
  when `review()` says it finished.
- A Slack thread is a conversation: a reply in a thread carries its earlier turns, read back from Slack (last
  20); a top-level message starts fresh. No thread mapping is stored.
- The Agent Server API requires `WIKI_SERVER_TOKEN` (bearer, `server_auth.py`; unset refuses all). The review
  page routes stay open: tailnet-only and CSRF-guarded, because a phone's browser can't send the token.

## Why

It puts ingest, review and questions in the app the user already has open, without a new port or a second
set of rules: every rule still lives in the backend and the review's checks.
