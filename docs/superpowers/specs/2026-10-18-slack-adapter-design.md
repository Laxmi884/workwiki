# Slack adapter: design

Date: 2026-10-18. Status: approved (spec reviewed by the user 2026-10-18).
Plan item 3 of the daily-use plan (`docs/wiki/progress.md`): ingest and questions from Slack.

## Goal

Use the wiki from the user's personal Slack workspace, on any device:

- run ingest and ask questions in a direct message with a bot;
- see the agent's progress while it works (steps, then the answer text as it streams);
- approve or reject a review from Slack, with a link to the review page for anything finer;
- get a DM when a review is waiting, whatever started it;
- optionally, ingest today's note automatically at a set time (off by default).

Success: on the phone, `/ingest` in the DM shows steps ticking, ends with a summary and
**Approve all / Reject / Open review page**; Approve writes and commits; a question streams its
answer; a quiet day (rollover-only or tick-only note) makes no model call.

## Principles this follows

LangGraph / LangChain:
- Streaming as documented: `stream_mode` `updates`, `messages`, `custom`, with `subgraphs=True`
  and `version="v2"` locally; `client.runs.stream(..., stream_mode=[...], stream_subgraphs=True)`
  on the Agent Server (`messages-tuple` there). Custom progress through `get_stream_writer()`.
- Human-in-the-loop unchanged: `interrupt()` in `review`, `Command(resume=…)` for answers. Slack
  buttons send the same `ReviewAnswer` the web page sends, on the same thread and checkpointer.
- One graph, thin surfaces (0005, 0010): Slack reaches graphs only through `Backend`.
- The stream is the contract (LangGraph frontend docs, `useStream` / graph-execution cards): the
  graph emits named steps; each surface draws them its own way.

AI system design:
- Code decides, the model writes: routing, "anything new?", which review to announce, button
  actions are code. The model only extracts and answers.
- Approval gates unchanged (0011): Slack writes to the vault only by approving a shown review.
- Least privilege: one allowed Slack user, DMs only, Socket Mode (no inbound port), tokens in
  `.env`.
- Idempotency: ledger skips unchanged notes; the notifier records what it announced.
- Cost control: no agent call on quiet notes; auto-ingest opt-in.
- Failure isolation: the bot is its own process; Slack or server outages don't take the other down.
- Sync core, async at the edges (0014): Bolt's sync Socket Mode handler, sync streaming APIs.

## Out of scope

Multi-user, Slack threads as separate conversations, editing pages in Slack (the review page does
that), the morning brief, Telegram.

## Components

```
src/llm_wiki/
  progress.py        NEW  pure: stream parts → Progress (steps + streamed text)
  backend/base.py    + stream_ingest(), stream_answer(), stream_ask()
  backend/local.py   + same, via graph.stream(..., subgraphs=True, version="v2")
  backend/server.py  + same, via client.runs.stream(..., stream_subgraphs=True)
  chat/session.py    + stream_reply(): same routing and rules as reply(), yields progress
  ingest/graph.py    stage: no agent call when nothing is new for it;
                     get_stream_writer() events from stage (tool-call count) and apply
  slack/             NEW
    app.py           Bolt app (sync), DM message handler, button action handlers, user check
    render.py        Progress → chat_stream task chunks; RunResult → Block Kit summary + buttons
    notifier.py      every 60 s: backend.pending() → DM each review not yet announced
    __main__.py      `uv run python -m llm_wiki.slack`
    manifest.yml     Slack app manifest (scopes, Socket Mode, events, interactivity)
  schedule.py        + daily ingest job when WIKI_AUTO_INGEST_AT is set; removed when unset
compose.yml          + `slack` service: same image, `python -m llm_wiki.slack`, server backend
```

Units and their contracts:

- **`progress.py`**: `Progress` (frozen model: ordered `steps` of `{key, label, state: pending |
  running | done | failed, detail}` and `text`), `step_plan(graph)` (the known step list for
  `ingest` / `apply` / `ask`), `advance(progress, part) -> Progress` for one stream part. No I/O.
  Both backends normalise their stream parts to the same shape (`type`, `ns`, `data`) before
  calling it.
- **Backend streaming**: `stream_ingest(note) / stream_answer(thread, answer) / stream_ask(q,
  history)` are generators yielding `Progress` and returning the same value as today's blocking
  method (`RunResult` or answer text) via the generator's return (wrapped as a final event). The
  blocking methods stay and are unchanged; the CLI, web page and tests keep working.
- **`chat/session.stream_reply()`**: same command parsing and rules as `reply()` (one open
  review, `/ingest` refused while one is open, several open → list links). Yields `Progress`;
  ends with the reply text and, for a review, the `RunResult` so a surface can add buttons.
- **`slack/`** depends only on `Backend`, `session`, `progress`, `slack_bolt`, `slack_sdk`.
  Never touches the vault or graphs directly.
- **Notifier state**: `WIKI_STATE_DIR/slack-announced.json` (thread ids announced, with time).

Steps shown (labels in `progress.py`, keyed by node name):

| Graph | Steps |
|---|---|
| ingest | Read note (`load_note`) → Task IDs (`assign_ids`) → Tasks (`track_tasks`) → Agent filing facts (`stage`, detail "n tool calls" from `custom`) → Projects (`map_tasks`) → Checks (`render`) → Review ready (`review` interrupt) |
| answer | by action (`progress.answer_plan`): approve → Writing files (`apply`) → Git commit (`custom` from apply); revise → Agent revising → Projects → Checks → Review ready; answer/edit → (Picked wins) → Projects → Checks → Review ready; reject → Closing the review |
| ask | Searching the wiki (tool calls) → answer text from `messages` tokens of the assistant (subgraph namespaces flattened; only the top-level agent's final text is shown) |

`stage` skipped (nothing new) shows as "Agent: nothing new to file ✓".

## Data flow

Question: DM → user check → `session.stream_reply()` → `backend.stream_ask()` → `Progress` →
`chat_stream` reply in a thread under the user's message (step line, then streamed text) →
`stop()`.

Ingest: DM `/ingest [date]` → `stream_ingest(note)` → steps tick in one streaming message → run
pauses at `interrupt()` → `stop(blocks=…)` with the summary (note, file count, facts, attention
items) and buttons **Approve all**, **Reject**, **Open review page** (`WIKI_PUBLIC_URL` +
`/review/{thread}`).

Button: Bolt action → user check → `stream_answer(thread, "approve" | "reject")` → steps → final
text ("Written: 11 files, commit e9c695b") replaces the buttons (`chat.update`), so they can't be
pressed twice. Button value = thread id; the action re-reads the review first and refuses if it is
no longer open.

Notifier: loop every 60 s → `pending()` → for each thread not in the announced file: DM summary +
buttons, record it. A review answered elsewhere is dropped from the file on the next pass.

Auto-ingest (opt-in): `WIKI_AUTO_INGEST_AT=HH:MM` → `schedule install` adds a daily job (server
cron with explicit `timezone`, or launchd) that runs the `ingest` graph on today's note
(`WIKI_TIMEZONE`). Unchanged note → `load_note` skips (no model); nothing new → stage skip (no
model). A review that opens is announced by the notifier.

Long runs: if `chat_stream` fails with `message_not_in_streaming_state` (Slack ends streams after
a while; slack-sdk issue #1859), switch to `chat.update` on `stream.ts` for the rest.

## Agent skip (quiet notes)

`stage` returns no edits without building the agent when `citable_lines(...)` is empty and there
are no open tasks needing a project (`open_tasks` empty). Code-only nodes still run: IDs, tick
tracking, open-tasks page, ledger. Today a rollover-only or tick-only note costs one agent run
(≈ $0.005); after this, $0.

Changed in the build (step 1, 2026-10-18): the `open_tasks` condition is dropped. A rollover note
carries open tasks every day, so it would rarely skip; the review still asks the user about tasks
with no project (code, no model). The rule is `staging.needs_agent`: the agent runs when a citable
line is not a known task with the same words. Revise always runs the agent.

## Configuration

`.env` (and `.env.example`):

```
SLACK_BOT_TOKEN=xoxb-…
SLACK_APP_TOKEN=xapp-…
WIKI_SLACK_USER_ID=U…            # the only user answered
WIKI_PUBLIC_URL=https://<mac>.<tailnet>.ts.net:8443
WIKI_AUTO_INGEST_AT=             # optional HH:MM in WIKI_TIMEZONE; empty = off
```

Missing Slack tokens or user id: `python -m llm_wiki.slack` exits with a message naming the
variable; nothing else is affected. Missing `WIKI_PUBLIC_URL`: no "Open review page" button.

Slack app (user, once): create from `slack/manifest.yml`; bot scopes `chat:write`, `im:history`,
`im:read`, `im:write` (plus whatever `chat.startStream` requires on a free workspace: checked
during build and recorded in the manifest); Socket Mode on; events `message.im`; interactivity on.
Setup steps go in `docs/setup/slack.md`.

## Security

- Messages and button presses from any user other than `WIKI_SLACK_USER_ID`, or outside a DM:
  ignored, one log line (user id only).
- Logs carry thread ids and step names, never tokens or note text.
- Answers and summaries live in Slack's DM history: work notes reach Slack's servers. Recorded as
  an addendum to decision 0016 (real notes to cloud services).

## Errors

| Situation | Behaviour |
|---|---|
| Backend down (`BackendError`) | Reply "Can't reach the wiki server: …"; bot stays up |
| Review answered elsewhere | Button replies "already answered" + current result |
| Several reviews open | List links (same rule as chat) |
| Stream expired | `chat.update` fallback |
| Slack rate limit | Progress updates buffered: at most ~1 Slack call per second |
| Unexpected exception in a handler | Reply "Something failed (details in the log)"; logged with trace |

## Testing

No Slack network, scripted model, fixture vault (CLAUDE.md):
- `progress.py`: recorded stream-part sequences → expected `Progress`.
- Backend contract tests: `stream_*` on local and server backends yield the same step sequence and
  return the same value as the blocking methods.
- `stream_reply`: same answers as `reply()` for every command in `test_chat*`.
- `slack/`: fake client recording calls: wrong user ignored; `/ingest` → steps → summary +
  buttons; Approve → apply, buttons replaced; second press refused; stream-expired fallback;
  notifier announces once, survives restart.
- Agent skip: rollover-only and tick-only notes → zero model calls, ticks recorded.
- Schedule: `WIKI_AUTO_INGEST_AT` set/unset installs/removes the job (cron and launchd paths).
- Mutation checks: removing the user check, the skip, or the announced-file record each fails a
  test.
- Real check (user OK, ~$0.02): `/ingest` and one question from the phone on `~/WorkDev`.

## Learning

Notebooks (git-ignored, built and verified with each step):
- 8.3 Streaming: stream modes, `subgraphs`, `get_stream_writer`, events → steps (toy graph, fake
  model).
- 8.4 A chat surface: fake Slack client, buttons → `Command(resume=…)`, allow-listing, sync vs
  async Socket Mode.

Paid-media reference: `src/paid_media_agent/surfaces/slack/` (`service.py` event handling and
approver mapping, `delivery.py` `chat_stream`, `blocks.py` Block Kit, `socket_mode.py` async
handler) and `surfaces/runner.py`.

## Build order

Each step reviewed and committed before the next:
1. `progress.py`, backend `stream_*`, `session.stream_reply`, agent skip (notebook 8.3).
2. Slack bot: app, render, manifest, setup doc, compose service (notebook 8.4).
3. Notifier.
4. Auto-ingest option in `schedule.py`.

A decision note (0018, Slack surface) is written once step 2 is approved.
