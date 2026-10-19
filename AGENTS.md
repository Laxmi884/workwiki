# AGENTS.md: setting up and working on llm-wiki

For coding agents (Claude Code, Codex, Cursor, …) helping someone install, run or change this repo.
Humans: start with [`README.md`](README.md). This file is the runbook plus the rules that must not be
broken. If `CLAUDE.md` exists in a checkout it adds the maintainer's personal workflow; the rules below
apply to everyone.

## What this is

A local agent that turns daily work notes in an Obsidian vault into a Markdown wiki, with a human
approving every write. Ingest is a hand-built LangGraph `StateGraph` (`src/llm_wiki/ingest/`); the
assistant is a `create_deep_agent` (`src/llm_wiki/assistant/`). Surfaces (review page, CLI, Slack) reach
them only through `Backend` (`src/llm_wiki/backend/`).

## Rules you must not break

1. **Never touch a real vault without the user's explicit say-so.** Develop and test on
   `tests/fixtures/vault/` (invented data) or a copy. Never point `WIKI_VAULT_PATH` at the user's real
   vault on your own.
2. **`00_raw/` is read-only to every agent.** Exactly two code-only exceptions exist: appending a task
   ID suffix (` ^t-xxxxxx`) after an approved review, and the rollover job creating today's note. Never
   write model-produced text there; it goes to `05_generated/`.
3. **No write without an approved review.** Approval covers the exact diff (digest). Apply once, read back.
4. **Secrets stay in `.env`.** Never print, log, echo, commit or paste tokens or API keys (including in
   command output). Do not read `.env` aloud; check that a variable is set, not its value.
5. **Code decides, the model writes.** Task IDs, completion matching, dates, paths, frontmatter parsing
   and write guards are deterministic code. Do not move them into prompts.
6. **Never invent data.** A missing value stays `None`/empty. Domain models are frozen Pydantic models.
7. **Tests never use a real vault or call a model** (scripted model + fixture vault). Evals in
   `tests/eval/` call a real model and spend money: ask first.
8. **Ask before** pushing, deleting data, installing a schedule, restarting the user's running stack, or
   anything that costs money.

## Set it up for a user

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), Docker (for the server), an
[Obsidian](https://obsidian.md) vault that is a git repository.

1. **Install and verify** (no keys needed):
   ```sh
   uv sync
   uv run pytest tests/unit_tests          # expect all pass
   ```
2. **Configure** `cp .env.example .env`, then help the user fill in (tell them to paste secrets into the
   file themselves; do not ask them to paste secrets into the chat):
   - `WIKI_VAULT_PATH`: absolute path to their vault (a dev copy first).
   - `WIKI_MODEL` and the matching key (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, or a local
     `ollama:` model). `WIKI_TIMEZONE`: their IANA zone.
   - `WIKI_SERVER_TOKEN`: generate with
     `python -c "import secrets; print('WIKI_SERVER_TOKEN=' + secrets.token_urlsafe(32))" >> .env`
     (make sure `.env` ends with a newline first).
   - `LANGSMITH_API_KEY`: the licensed server image will not start without it. Tracing is separate:
     `LANGSMITH_TRACING_V2` / `LANGSMITH_TRACING` (set both to `false` to stop sending traces).
3. **Vault shape.** Walk the user through `docs/setup/obsidian.md` (folders, template, Obsidian
   settings); `docs/setup/README.md` is the overall order and shows how the devices connect. The vault needs `00_raw/daily/`, `50_templates/daily.md` (`template: daily-v1`) and
   the folders listed in `docs/wiki/decisions/0002-daily-note-format.md`. Check, do not create files in a
   real vault without asking.
4. **Quick try without a server:**
   `uv run python -m llm_wiki.ask --vault <dev copy> "What is open on Atlas?"` (needs a model key).
5. **Run the server (Docker):**
   ```sh
   uv run langgraph build -t llm-wiki:local
   WIKI_VAULT_PATH=<absolute vault path> docker compose up -d --wait
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8123/review     # expect 200
   ```
   The path must be absolute and outside `~/Documents`, `~/Desktop`, `~/Downloads` on macOS.
   `docker compose down` stops it.
6. **Optional: rollover cron** (creates each day's note; no model; ask the user for the time):
   `uv run python -m llm_wiki.schedule server install --at 07:00 --url http://127.0.0.1:8123`
   then `... server show`. Mac without Docker: `schedule launchd install`. See `docs/scheduling.md`.
7. **Optional: Slack bot**: follow `docs/setup/slack.md` (the user creates the Slack app from
   `src/llm_wiki/slack/manifest.yml`). Start with
   `WIKI_VAULT_PATH=<path> docker compose --profile slack up -d --wait`.
8. **Optional: phone and other devices**: `docs/setup/livesync.md` (LiveSync + CouchDB + Tailscale; the
   Mac is the hub; use a database name different from the dev vault's) and its section 9 for the review
   page on the phone.

## Working on the code

- Python 3.11+, `uv`. Match the surrounding style; Google-style docstrings; type hints.
- Before saying a change is done: `uv run pytest tests/unit_tests` and
  `uv run ruff check src tests && uv run ruff format --check src tests`.
- Server behaviour changes also need `WIKI_SERVER_TESTS=1 uv run pytest
  tests/unit_tests/test_backend_contract.py` (and `=docker` for anything that depends on the image).
  The Docker run needs `.env` (the licensed image needs the LangSmith key).
- TDD: write the failing test, watch it fail, then fix. A new rule gets a test that fails when the rule is
  removed.
- Check LangGraph / Deep Agents / LangChain APIs against current docs before relying on memory; they move.
- Keep `docs/wiki/` current if the repo uses it: `progress.md` (status), `log.md` (what happened),
  `decisions/NNNN-*.md` (settled choices).

## Where things are

| Path | What |
|---|---|
| `src/llm_wiki/ingest/` | the ingest graph, agent, staging, render |
| `src/llm_wiki/assistant/` | the Q&A / brag-sheet deep agent, skills |
| `src/llm_wiki/backend/` | `Backend` contract; `local.py` (in-process), `server.py` (Agent Server) |
| `src/llm_wiki/review_web/` | the review and chat pages |
| `src/llm_wiki/slack/` | the Slack bot: handlers, notifier, rendering |
| `src/llm_wiki/rollover.py`, `schedule.py` | the morning note and its scheduler |
| `compose.yml`, `langgraph.json` | how the server runs |
| `tests/fixtures/vault/` | invented fixture vault |
| `docs/setup/` (start at `README.md`), `docs/scheduling.md` | user setup guides |
| `docs/wiki/` | status, decisions, learnings |

## Common problems

- **Server container unhealthy / will not start:** missing `LANGSMITH_API_KEY` in `.env`, or the vault
  path is relative or in a folder macOS will not let Docker mount.
- **401 from the API:** the client lacks `WIKI_SERVER_TOKEN`, or the server was started without it
  (unset means everything is refused).
- **"today" is the wrong date in the container:** set `WIKI_TIMEZONE`.
- **Tests exhaust the scripted model in Docker:** tracing is on; both `LANGSMITH_TRACING` and
  `LANGSMITH_TRACING_V2` must be `false` in the test environment.
- **A Slack message starting with `/` never arrives:** Slack keeps those for itself; use `!ingest`.
