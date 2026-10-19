# Setup guide: from nothing to every device connected

Do these in order. Each step links to the detailed guide. Steps 4 to 7 are optional; the wiki works from
step 3 on your computer alone.

## The picture

```
 iPhone ──── Obsidian + LiveSync ───────────┐
 (Tailscale on)  Slack app ──┐              │ private HTTPS (Tailscale)
                browser ─────┼──────────┐   │
 Other laptop ── Obsidian + LiveSync ───┼───┤
                                        ▼   ▼
 THE MAC (the one machine that runs everything)
   Obsidian ── vault folder on disk (~/Work, a git repo) ◀── agent reads/writes
   Docker:  Agent Server + Postgres + Redis   → review page /review, /chat   (127.0.0.1:8123)
   Docker:  CouchDB (sync hub for LiveSync)                                  (127.0.0.1:5984)
   Docker:  Slack bot (optional) ──outbound── Slack
   Tailscale ── gives CouchDB (:443) and the review page (:8443) a private HTTPS address
   Cron: rollover creates each morning's note
```

Key idea: **one machine (the Mac) owns the vault and runs the agent.** Other devices do not run the agent.
They edit notes (Obsidian + LiveSync keeps their copy in step with the Mac's folder) and reach the
review page or Slack over private channels. Nothing is exposed to the public internet.

What must be on for what:

| You want to… | Mac awake | Obsidian open on Mac | Docker stack up | Tailscale on |
|---|---|---|---|---|
| Ingest, ask, approve on the Mac | yes | no | yes | no |
| Sync notes phone ⇄ Mac | yes | **yes** | no | phone: yes |
| Review/chat on the phone | yes | no | yes | phone: yes |
| Slack | yes | no | yes (bot service) | no |
| Morning rollover | yes (or runs when it wakes) | no | yes | no |

## 1. Obsidian and the vault

Create the vault folder, the layout and the daily template, and set Obsidian's daily-note settings.
→ [`obsidian.md`](obsidian.md)

## 2. Install and configure the agent

Python 3.11+, `uv`, Docker. Copy `.env.example` to `.env` and fill it in (vault path, model and key,
time zone, server token, LangSmith key). Run the tests to confirm the install.
→ the main [`README.md`](../../README.md) ("Quick start" and "Configuration"). A coding agent can do this
for you: point it at [`AGENTS.md`](../../AGENTS.md).

## 3. Start the server

```sh
uv run langgraph build -t llm-wiki:local
WIKI_VAULT_PATH=$HOME/Work docker compose up -d --wait
```

Open `http://127.0.0.1:8123/review`. Write a note, ingest it, approve. Try it on a **copy** of your vault
first.
→ [`README.md`](../../README.md) ("Run it for real")

## 4. Morning rollover (optional, recommended)

Creates each day's note at a set time with your open tasks carried over. No model call.
→ [`../scheduling.md`](../scheduling.md)

## 5. Sync notes to your phone and other computers (optional)

Self-hosted LiveSync + CouchDB + Tailscale: free, end-to-end encrypted, private. The Mac is the hub. Order:
install Tailscale on the Mac and each device (same account) → CouchDB on the Mac → join the Mac's
Obsidian → join each other device.
→ [`livesync.md`](livesync.md)

Note: that guide is written around a dev vault called `~/WorkDev` and a CouchDB database named
`workdev`. For your real vault, use your real folder and a **different database name** wherever the guide
says `workdev`, so dev and real notes never mix.

**Adding another device later** (a second laptop, a new phone): install Obsidian and Tailscale, then follow
"7. Phone" in the LiveSync guide (it works for any device: make a new setup link on the Mac, join, overwrite
local with remote). It does not touch the agent.

## 6. Review page and chat on the phone (optional)

Give the server a private HTTPS address with `tailscale serve --bg --https=8443 8123`, then open
`https://<mac-name>.<tailnet>.ts.net:8443/review` on the phone (Tailscale on). Only your own devices can
reach it; never use `tailscale funnel` for it.
→ [`livesync.md`](livesync.md), section 9

## 7. Slack (optional)

Ingest, ask and approve from a DM, and get a DM when a review opens elsewhere. You create a Slack app from
the manifest in the repo; tokens go into `.env`.
→ [`slack.md`](slack.md)

## When something does not connect

| Symptom | Check |
|---|---|
| Phone edits do not reach the Mac | Obsidian open on the Mac? Sync preset set to LiveSync on both? Tailscale on? |
| Review page times out on the phone | Docker stack up? `tailscale serve status` shows port 8443? Phone on the tailnet? |
| 401 from the server | The client has no `WIKI_SERVER_TOKEN`, or the server started without one |
| Server will not start | `LANGSMITH_API_KEY` missing in `.env`, or the vault path is relative / not mountable |
| "Today" is wrong in notes | Set `WIKI_TIMEZONE` |
| Reboot stopped everything | Docker Desktop and Tailscale set to start at login; Obsidian reopened |
