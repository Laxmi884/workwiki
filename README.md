# llm-wiki

A personal work wiki that an LLM keeps for you. You write plain daily notes in Obsidian. An agent reads
each note, files what matters into a Markdown wiki (projects, people, decisions, impact), and **shows you
every change for approval before it writes anything**. Then you can ask questions about your work, from
the browser, the terminal or Slack.

- **Your notes are never rewritten by a model.** `00_raw/` is read-only to every agent. The only edits
  are code-made: a task ID suffix (` ^t-xxxxxx`) and the morning rollover note. Model-written text goes to
  other folders.
- **You approve exact diffs.** An ingest pauses at a review (`interrupt()`); approving covers precisely the
  diff you saw, applies it once, reads it back, then makes one git commit in the vault.
- **Code does the deterministic work** (task IDs, matching a tick to a task, dates, paths); the model only
  extracts and writes. Missing values stay empty. Nothing is invented.
- **Safe to re-run.** A ledger of processed notes makes ingest idempotent; a note that has not changed makes
  no model call.

It is also a learning project for LangGraph and Deep Agents: an ingest `StateGraph` with human approval,
and an assistant built with `create_deep_agent`. The design history is in [`docs/wiki/`](docs/wiki/index.md).

## What you get

| Part | What it does |
|---|---|
| **Ingest** | Reads a daily note, proposes file changes, waits for your approval, applies, commits |
| **Review page** (`/review`) | Approve, reject, edit, answer the agent's questions, or ask for a revision |
| **Assistant** (`/chat`, `python -m llm_wiki.ask`) | Answers from the wiki; can write a brag sheet for a review period |
| **Rollover** | Each morning creates the day's note with your open tasks carried over (no model) |
| **Slack bot** (optional) | Ingest, ask and approve from a DM; a DM when a review opens elsewhere |
| **Tests and evals** | Scripted models and a fixture vault; no API calls or real data in tests |

## How it runs

```
Obsidian vault (Markdown, git) ◀── agent reads/writes ──▶ Agent Server (Docker: API + Postgres + Redis)
   notes you write                                              ▲            ▲            ▲
                                                       review page      Slack bot     rollover cron
                                                       + /chat          (optional)    (07:00, optional)
```

Everything goes through one backend, so the web page, the CLI and Slack follow the same rules.

## Quick start (development, no server)

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp .env.example .env            # then edit it: see "Configuration"
uv run pytest tests/unit_tests  # no API key needed
uv run python -m llm_wiki.ask --vault tests/fixtures/vault "What is open on Atlas?"   # needs a model key
```

Use a **copy** of your vault while trying things out (`WIKI_VAULT_PATH`). The repo ships a fixture vault of
invented data at `tests/fixtures/vault/` for all development and tests.

## Run it for real (Docker)

This is the supported way to run it day to day. The server publishes only on `127.0.0.1`.

```sh
uv run langgraph build -t llm-wiki:local
WIKI_VAULT_PATH=$HOME/Work docker compose up -d --wait      # absolute path; see notes below
open http://127.0.0.1:8123/review                           # the review page; /chat for questions
```

Then, optionally:

```sh
# Create each morning's note at 07:00 (your time zone, from WIKI_TIMEZONE), tasks carried over
uv run python -m llm_wiki.schedule server install --at 07:00 --url http://127.0.0.1:8123

# Slack bot (see docs/setup/slack.md)
WIKI_VAULT_PATH=$HOME/Work docker compose --profile slack up -d --wait
```

Notes:
- The vault path must be absolute and outside `~/Documents`, `~/Desktop` and `~/Downloads` (Docker on macOS
  cannot mount those without a privacy grant).
- Rollover is **opt-in**: nothing is scheduled until you run `schedule … install`. You can also run it by
  hand: `uv run python -m llm_wiki.rollover`. Details: [`docs/scheduling.md`](docs/scheduling.md).
- Set Docker Desktop to start at login so the stack (`restart: unless-stopped`) returns after a reboot.
- After a code change, rebuild the image and run `docker compose up -d` again.

## Configuration (`.env`)

Copy `.env.example`. The ones you need:

| Variable | Meaning |
|---|---|
| `WIKI_VAULT_PATH` | The vault the agent reads and writes. Keep a dev copy here; override per command for real use |
| `WIKI_MODEL` | `provider:model`, e.g. `openrouter:deepseek/deepseek-v4.1-flash`, `anthropic:…`, `ollama:…` |
| `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` | Key for the provider you chose |
| `WIKI_TIMEZONE` | IANA name, so "today" is your day even in a UTC container |
| `WIKI_SERVER_TOKEN` | The Agent Server answers only clients that send this bearer token |
| `LANGSMITH_API_KEY` | The licensed server image needs it to start; tracing is a separate switch |
| `WIKI_REVIEW_URL` | Your phone-reachable review page (`https://<mac>.<tailnet>.ts.net:8443`); without it Slack has no "Open review page" button |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `WIKI_SLACK_USER_ID` | Only for the Slack bot |

The model is swappable (`init_chat_model`); a local model through Ollama works too.

## Privacy

- Your notes stay in your vault and git history on your machine.
- A cloud model sees the note text it is asked about. With tracing on, LangSmith also records run inputs
  and outputs. Both are your choice; see [decision 0016](docs/wiki/decisions/0016-real-notes-to-a-cloud-model.md).
  Turn tracing off with `LANGSMITH_TRACING=false` and `LANGSMITH_TRACING_V2=false`.
- Secrets live only in `.env` (git-ignored). Nothing prints them.
- Slack keeps the messages the bot sends (summaries of your notes) in your workspace.

## Setup guides

Start with [`docs/setup/README.md`](docs/setup/README.md): the order to do things in, a picture of how the
devices connect, and what has to be running for what.

- [`docs/setup/obsidian.md`](docs/setup/obsidian.md): create the vault, the folder layout, the daily
  template and Obsidian's settings.
- [`docs/setup/livesync.md`](docs/setup/livesync.md): sync the vault to your phone (LiveSync, CouchDB,
  Tailscale), and open the review page there.
- [`docs/setup/slack.md`](docs/setup/slack.md): the Slack bot.
- [`docs/scheduling.md`](docs/scheduling.md): the morning rollover (Docker cron or launchd).

## Development

```sh
uv run pytest tests/unit_tests                                  # fast, no network
WIKI_SERVER_TESTS=1 uv run pytest tests/unit_tests/test_backend_contract.py        # real `langgraph dev`
WIKI_SERVER_TESTS=docker uv run pytest tests/unit_tests/test_backend_contract.py   # real Docker image
uv run ruff check src tests && uv run ruff format --check src tests
```

Tests use a scripted model and the fixture vault, never a real vault. The evals in `tests/eval/` call a
real model and cost a little; read them before running.

Where things are: `src/llm_wiki/ingest/` (the graph), `assistant/` (the deep agent), `backend/` (local and
server), `review_web/` (the page), `slack/`, `domain/` (frozen Pydantic models). Project status, decisions
and what was learned: [`docs/wiki/`](docs/wiki/index.md). Coding agents: see [`AGENTS.md`](AGENTS.md).
