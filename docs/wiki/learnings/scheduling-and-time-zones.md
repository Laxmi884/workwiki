---
phase: 7
date: 2026-10-18
tags: [langgraph, agent-server, crons, launchd, time-zones]
---

# Scheduling and time zones

## What it is

An Agent Server runs **graphs** on a cron (`client.crons.create(graph, schedule=…, timezone=…,
input=…)`); each firing is a fresh run on a new thread. On a Mac, launchd does the same for any
command. Either way, a job that names things by date needs to know **whose** date: a server
container runs in UTC.

## How we used it

- `src/llm_wiki/clock.py`: `WIKI_TIMEZONE` → `clock.today()`, used by rollover and the assistant's
  date middleware.
- `src/llm_wiki/rollover_graph.py`: rollover as a one-node graph; the cron passes its zone as input.
- `src/llm_wiki/schedule.py`: `server install|show|remove` (one cron tagged `{"job": "rollover"}`) and
  `launchd install|show|remove` (`~/Library/LaunchAgents/com.llm-wiki.rollover.plist`).
- `langgraph.json`: `rollover` graph; `RUN apk add --no-cache tzdata`.
- Guide: `docs/scheduling.md`. Decision 0017. Notebook 8.2.

## Paid-media reference

`schedules/weekly_report.py` (`define_schedule(cron=…, timezone="UTC", prompt=…)`) on Managed Deep
Agents: their job is an agent prompt, scheduled in UTC because a 14-day report doesn't care whose
midnight it is. Ours is a code-only graph, scheduled in the user's zone because the note is named by
the user's day.

## Gotchas

- Our image (Chainguard) had no time-zone files. The server validates cron zones outside Python, so
  the `tzdata` wheel wasn't enough; `apk add tzdata` was.
- `crons.create` takes a graph name; `crons.search` only an assistant UUID. Tag your crons.
- A cron missed while the server was down ran on restart (seen once). A timezone-less cron's next
  run then came out in container-local time; always pass `timezone`.
- launchd `RunAtLoad` reruns the job at every login: fine only because rollover is idempotent.
- Fakes hid both server bugs; the real server found them in one run.

## In my words

