# Free sync for the vault: Self-hosted LiveSync + CouchDB + Tailscale

Goal: write notes on any device (phone, laptop) and have them in the vault on the Mac, where the
agent runs; approved agent edits flow back. Free, end-to-end encrypted, nothing exposed to the
internet. Try it on `~/WorkDev` first; touch `~/Work` only once this works.

```
iPhone / other laptop ──(Obsidian + LiveSync plugin)──┐
                                                      │ https://<mac>.<tailnet>.ts.net  (Tailscale, private)
Mac: Obsidian + LiveSync ── CouchDB in Docker ◀───────┘
       │ files on disk
       └── llm-wiki agent reads/writes ~/WorkDev
```

Checked against the LiveSync server guide (`docs/setup_own_server.md`, updated 2026-09-29) on
2026-10-15. Mac status that day: Docker 28 and Obsidian installed; Tailscale and Deno not installed.

## What it costs and what to know first

- Free: CouchDB (open source), LiveSync (open source plugin), Tailscale Personal plan, Deno.
- Phones need **HTTPS with a valid certificate**. `tailscale serve` gives one, private to your
  devices. No port forwarding, no public URL.
- The Mac is the server: phones sync when they can reach it. While it's off, phone edits wait
  and sync later.
- LiveSync works through the Obsidian app. Agent edits written to disk sync out (and phone edits
  land on disk) only while **Obsidian is open on the Mac**. Keep it running.
- Hidden files (`.git/`) are not synced by default; leave that off. Git history stays on the Mac.
- Work notes: everything stays on your devices (CouchDB on your Mac, encrypted with your
  passphrase). Still check your company's rules for work notes on a personal phone.

## 1. Install the tools (Mac)

```sh
brew install deno                 # LiveSync's setup scripts need Deno 2
brew install --cask tailscale     # or the Mac App Store version
```

Open Tailscale, sign in (free Personal plan), and install Tailscale on the phone with the same
account. In the Tailscale admin console → **DNS**: turn on **MagicDNS** and **HTTPS Certificates**.

## 2. Run CouchDB in Docker

Keep credentials out of the repo:

```sh
mkdir -p ~/.livesync/couchdb-data ~/.livesync/couchdb-etc && cd ~/.livesync
cat > env <<'EOF'
export username=wiki-admin
export password=<a long random password>
EOF
chmod 600 env && . ./env

docker run --name couchdb-for-ols -d --restart always \
  -e COUCHDB_USER=$username -e COUCHDB_PASSWORD=$password \
  -v ~/.livesync/couchdb-data:/opt/couchdb/data \
  -v ~/.livesync/couchdb-etc:/opt/couchdb/etc/local.d \
  -p 127.0.0.1:5984:5984 couchdb
```

`127.0.0.1:` keeps CouchDB off your Wi-Fi network; only Tailscale (next step) reaches it.
If the container exits, check `docker logs couchdb-for-ols` (the guide notes the data folders
end up owned by uid 5984).

## 3. Configure CouchDB for LiveSync

```sh
cd ~/.livesync && . ./env
export hostname=http://localhost:5984 database=workdev
curl -s https://raw.githubusercontent.com/vrtmrz/obsidian-livesync/main/utils/couchdb/couchdb-init.sh | bash
```

Expect `CouchDB provisioning completed.` (it sets CORS, request sizes, and creates the `workdev`
database).

## 4. Give it a private HTTPS address

```sh
tailscale serve --bg 5984
tailscale serve status            # shows https://<mac-name>.<tailnet>.ts.net → localhost:5984
```

Check from the phone's browser (Tailscale on): `https://<mac-name>.<tailnet>.ts.net/` should show
CouchDB's JSON welcome.

## 5. Make a setup link

```sh
cd ~/.livesync && . ./env
export hostname=https://<mac-name>.<tailnet>.ts.net database=workdev
export passphrase=<vault encryption passphrase: long, keep it safe>
deno run --minimum-dependency-age=0 --allow-env \
  https://raw.githubusercontent.com/vrtmrz/obsidian-livesync/main/utils/setup/generate_setup_uri.ts
```

It prints an `obsidian://setuplivesync?...` link, a **URI passphrase** (shown once) and an ID
recovery code. Save the passphrase and recovery code in a password manager. The link expires after
the printed date (7-day window).

## 6. First device: the Mac's `~/WorkDev`

1. Obsidian → open `~/WorkDev` → Settings → Community plugins → Browse → **Self-hosted LiveSync** →
   install, enable.
2. Open the `obsidian://setuplivesync?...` link (paste it in Safari) → enter the URI passphrase →
   follow the prompts for a **new** remote: this Mac's files go up first.
3. Make a test note; check it shows no errors in the LiveSync log.

## 7. Phone

1. Install Obsidian and Tailscale (connected).
2. Create an **empty** vault named WorkDev, install and enable Self-hosted LiveSync.
3. On the Mac, LiveSync → **Copy setup URI** for another device (don't reuse the first link);
   send it to the phone, and the passphrase through a different channel.
4. Open the link on the phone, enter the passphrase, choose to fetch from the remote.

Test both ways: edit on the phone → see it on the Mac; edit on the Mac → see it on the phone.

## 8. Check it with the agent

1. Write a short daily note on the phone in `00_raw/daily/`.
2. On the Mac (Obsidian open), run ingest on it (`langgraph dev` → `ingest_agent`).
3. Once 3d (apply) exists: approve, then see the updated pages on the phone.

## Undo

```sh
tailscale serve reset
docker rm -f couchdb-for-ols && rm -rf ~/.livesync
```
Then disable the plugin in Obsidian. The vault files on disk are untouched.
