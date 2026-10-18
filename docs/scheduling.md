# Scheduling the morning rollover

Every morning, **rollover** creates the day's daily note from your template (`50_templates/daily.md`)
with your open tasks carried under `## Carried over`, task IDs intact. It never overwrites a note
that exists, runs no model, and commits only the new note. You can always run it by hand:

```sh
uv run python -m llm_wiki.rollover                  # today
uv run python -m llm_wiki.rollover --date 2026-10-19 --no-commit
```

To run it on its own, pick **one** of the two schedulers below. Both are set up and removed with
`python -m llm_wiki.schedule`. (Running both does no harm, since rollover never overwrites, but it
wastes a run.)

| | Agent Server cron (Docker) | launchd (macOS) |
|---|---|---|
| Runs when | The server is up | The Mac is on |
| Missed run (server down, Mac asleep) | Runs when the server starts again | Runs when the Mac wakes; also at every login |
| Time zone | Explicit (`--timezone`) | The Mac's clock |
| After a code change | Rebuild the image | Nothing to do |
| Good for | A server that runs all day; the same server serves the review page | A laptop, no Docker |

## Your time zone

Daily notes are named by your day. A server container runs in UTC, and at 21:00 in Toronto, UTC is
already tomorrow. So set your time zone once in `.env`, as an
[IANA name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones):

```sh
WIKI_TIMEZONE=America/Toronto
```

It decides "today" for rollover and for the assistant ("what did I do this week?"). Unset, the
machine's local time is used, which is right on a laptop and wrong in a UTC container.

## Option A: a cron on the Agent Server (Docker)

Needs the server from `compose.yml` running (see the comments at the top of that file):

```sh
uv run langgraph build -t llm-wiki:local
WIKI_VAULT_PATH=$HOME/Work docker compose up -d --wait
```

Then register the schedule (once; it lives in the server's database and survives restarts):

```sh
uv run python -m llm_wiki.schedule server install --at 07:00 --url http://127.0.0.1:8123
uv run python -m llm_wiki.schedule server show   --url http://127.0.0.1:8123
uv run python -m llm_wiki.schedule server remove --url http://127.0.0.1:8123
```

- `--at` is a 24-hour time in `--timezone`, else `WIKI_TIMEZONE`, else this machine's zone. The
  run is told the same zone, so the note gets your date, not the container's.
- `--url` defaults to `WIKI_SERVER_URL`, else `http://127.0.0.1:2024` (`langgraph dev`). The
  compose stack publishes `8123` (`WIKI_PORT`).
- `install` replaces an earlier rollover cron; crons of other jobs are left alone.
- If the server is down when the cron is due, the run happens when it starts again (seen once, for
  one missed run).
- Set Docker Desktop to start at login, so the stack (`restart: unless-stopped`) comes back after a
  reboot.

The image must have the system time-zone files: `langgraph.json` installs them
(`RUN apk add --no-cache tzdata`). Without them the server refuses every `--timezone`.

## Option B: launchd on a Mac

```sh
uv run python -m llm_wiki.schedule launchd install --at 07:00
uv run python -m llm_wiki.schedule launchd show
uv run python -m llm_wiki.schedule launchd remove
```

- Run it from the repo folder: the job runs this checkout's Python (`.venv`) from this folder, so it
  reads the same `.env`. Moving or deleting the checkout breaks the job; run `install` again.
- The vault is `--vault`, else `WIKI_VAULT_PATH`.
- It writes `~/Library/LaunchAgents/com.llm-wiki.rollover.plist` and loads it. The job runs at the
  time on the Mac's clock, once when it's loaded (so `install` creates today's note right away),
  and at every login. If the Mac is asleep at the time, launchd runs the job when it wakes (launchd's
  documented behaviour; a Mac that is shut down runs it at the next login instead).
- Output goes to `~/Library/Logs/llm-wiki/rollover.log`.

Linux without Docker: put `cd /path/to/llm-wiki && .venv/bin/python -m llm_wiki.rollover` in your
crontab or a systemd timer; it behaves the same.

## Privacy

Rollover sends nothing anywhere. On the server, though, runs are traced to LangSmith when tracing
is on in `.env` (`LANGSMITH_TRACING` / `LANGSMITH_TRACING_V2`), and the traced result includes
your task text. Turn tracing off for real use if you don't want that.
