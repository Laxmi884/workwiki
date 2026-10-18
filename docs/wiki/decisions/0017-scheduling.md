---
id: 0017
date: 2026-10-18
status: accepted
---

# 0017: Scheduling: Agent Server cron and Mac launchd, one time-zone setting

## Context

Rollover (0001) has to run every morning on its own. The user will run the Docker Agent Server
(`compose.yml`), and wants the project, once open source, to schedule on a plain Mac too
(user, 2026-10-18: "happy to use docker as well … people should be able to schedule it on mac as
well … documentation … and have the code to support it").

Probed 2026-10-18 on our image (`langgraph-api` 0.16.1, self-hosted, licensed): crons work and a run
missed while the API was down ran on restart. But the image had **no time-zone data**: the server
refused every cron `timezone` (checked outside Python, so the `tzdata` Python package doesn't help),
and the container's "today" is UTC, a day ahead of Toronto every evening.

## Options

- Server cron only: one place, persists in Postgres, catches up after downtime; needs Docker running
  and an image rebuild per code change.
- launchd only: nothing running all day, Mac clock, runs on wake; Mac only, no server story.
- Both, behind one CLI and one time-zone setting. More code, but each fits a different user.

## Decision

Both, user's choice:
- `WIKI_TIMEZONE` (IANA name) decides "today" via `clock.today()`, for rollover and the assistant's
  date middleware; unset means the machine's local time. `tzdata` is a dependency (Python), and the
  image installs the system files (`RUN apk add --no-cache tzdata` in `langgraph.json`).
- `rollover_graph.py`: a one-node `rollover` graph in `langgraph.json` (JSON state, 0011).
- `python -m llm_wiki.schedule server install|show|remove`: one cron, tagged
  `{"job": "rollover"}`, at `--at` in `--timezone` / `WIKI_TIMEZONE` / this machine's zone; the run
  gets the zone as input. Crons are found by tag: search takes only an assistant UUID.
- `python -m llm_wiki.schedule launchd install|show|remove`: a LaunchAgent running this checkout's
  `python -m llm_wiki.rollover`, daily, at load and at login (`RunAtLoad`; rollover is idempotent).
- User guide: `docs/scheduling.md`.

## Why

Rollover is idempotent and code-only, so where it runs doesn't matter; what matters is that it runs
on the user's day. One setting gives the same "today" in a UTC container and on a laptop.
