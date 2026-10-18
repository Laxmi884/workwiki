# The wiki in a Slack DM

Optional. The review page (`/review`) stays the default surface; the bot adds a DM where you can type
`!ingest`, ask a question, and press **Approve all / Reject** on a review. Spec:
`docs/superpowers/specs/2026-10-18-slack-adapter-design.md`.

```
phone ─ Slack DM ─▶ Slack ◀── Socket Mode (outbound only) ── bot (python -m llm_wiki.slack)
                                                               │ Authorization: Bearer WIKI_SERVER_TOKEN
                                                               ▼
                                                          Agent Server ── vault
```

The bot answers one user (you), in DMs only. It reaches the agents only through the Agent Server
(`WIKI_BACKEND=server`) or the local backend, never the vault directly.

## What Slack keeps

Your messages, the bot's answers, review summaries and step names stay in the DM history on Slack's
servers (a free workspace shows 90 days). See the 0016 addendum.

## 1. The Slack app

Create it from the manifest: api.slack.com/apps → **Create New App** → **From a manifest** → your
workspace → paste `src/llm_wiki/slack/manifest.yml`. It asks for `chat:write`, `im:history`, `im:read`,
`im:write`, subscribes to `message.im`, and turns on Socket Mode and interactivity.

Check **App Home → Messages Tab** is on and "Allow users to send Slash commands and messages from the
messages tab" is ticked. Otherwise Slack says sending messages to this app is turned off.

## 2. The values (into `.env`, never into a chat)

Edit `.env` yourself; these are secrets.

| Variable | Where it comes from |
|---|---|
| `SLACK_BOT_TOKEN` | **OAuth & Permissions** → Install to workspace → Bot User OAuth Token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | **Basic Information → App-Level Tokens** → generate with scope `connections:write` (`xapp-…`) |
| `WIKI_SLACK_USER_ID` | Your Slack profile → ⋯ → **Copy member ID** (`U…`). The only user the bot answers. |
| `WIKI_SERVER_TOKEN` | Any long random string; the Agent Server refuses API calls without it (see below). |
| `WIKI_REVIEW_URL` | Your tailnet review URL (e.g. `https://<mac>.<tailnet>.ts.net:8443`), for the **Open review page** button. A `127.0.0.1`/`localhost` URL hides the button, since it wouldn't open on the phone. |

Make the server token without showing it:

```
python -c "import secrets; print('WIKI_SERVER_TOKEN=' + secrets.token_urlsafe(32))" >> .env
```

The server, the review page inside it, `schedule` and the bot all read it from `.env`. Restart the
server after adding it.

## 3. Run it

Locally, next to `langgraph dev` (or with `WIKI_BACKEND=local`, no server):

```
uv run python -m llm_wiki.slack
```

With Docker, as an opt-in service next to the api (`docker compose up` alone never starts it):

```
uv run langgraph build -t llm-wiki:local
WIKI_VAULT_PATH=$HOME/WorkDev docker compose --profile slack up -d --wait
```

A missing `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` or `WIKI_SLACK_USER_ID` stops it at start with a line
naming the variable (exit 2); nothing else is affected.

## 4. Use it

In the DM with **wiki**, commands start with `!`: Slack keeps a leading `/` for its own slash
commands and never sends the message (a leading space, ` /ingest`, works too).
- `!ingest` (today) or `!ingest 2026-10-12`: the steps tick, then the review with **Approve all /
  Reject / Open review page**. Approve writes, commits and replaces the buttons.
- `!review`: the open reviews. `!help` lists the rest (`!revise …`, `!approve except f2`, …), shown
  with `/` as on the web chat.
- Anything else is a question; the answer streams.
- A Slack thread is a conversation: a reply inside a thread is answered with that thread's earlier
  questions and answers (the last 20 turns). A new top-level message starts a new conversation.

A button on a review that changed since (e.g. revised on the page) is refused: open it again.

## Review notices

The bot checks every minute for open reviews and DMs you once for each one opened outside
Slack (the review page, the CLI, a cron job), with the same **Approve all / Reject / Open review
page** buttons.

- A review answered somewhere else (the page, the CLI): its message loses the buttons and says
  "Answered elsewhere" with the outcome.
- A review edited on the page: its message is updated in place; you get no second DM.
- The bot remembers what it showed in `WIKI_STATE_DIR/slack-announced.json` (in compose, the
  `slack-state` volume). Deleting it means open reviews are announced once more; nothing else changes.
- The first start after this was added announces reviews that are already open, once.
- If the wiki server is down, the bot clears nothing and tries again a minute later.

## Troubleshooting

- **"Sending messages to this app has been turned off"**: the Messages Tab setting in step 1.
- **No answer at all**: `WIKI_SLACK_USER_ID` is wrong. The bot's log says
  `ignored a message from user U…`; compare that id.
- **"Can't reach the wiki server"**: the api is down, or the bot and server have different
  `WIKI_SERVER_TOKEN`s (the server log shows `401`).
- **The reply stops mid-run and then shows the result**: Slack ended the stream; the bot finishes the
  message with `chat.update`. Nothing is lost.
