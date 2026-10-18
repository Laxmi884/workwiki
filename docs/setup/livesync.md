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
2026-10-15; steps 1–3 run for real on 2026-10-18 (CouchDB 3.5.3, LiveSync scripts at commit
`f0ea5cb`).

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
brew install deno                     # steps 3 and 5 both run Deno 2 scripts
brew install --cask tailscale-app     # pkg installer: asks for your Mac password; or the App Store version
```

Open Tailscale, sign in (free Personal plan), and install Tailscale on the phone with the same
account. In the Tailscale admin console → **DNS**: turn on **MagicDNS** and **HTTPS Certificates**.

## 2. Run CouchDB in Docker

Keep credentials out of the repo:

```sh
mkdir -p ~/.livesync/couchdb-data ~/.livesync/couchdb-etc && cd ~/.livesync
umask 077
printf 'export username=wiki-admin\nexport password=%s\n' "$(openssl rand -hex 24)" > env
. ./env

docker run --name couchdb-for-ols -d --restart always \
  -e COUCHDB_USER=$username -e COUCHDB_PASSWORD=$password \
  -v ~/.livesync/couchdb-data:/opt/couchdb/data \
  -v ~/.livesync/couchdb-etc:/opt/couchdb/etc/local.d \
  -p 127.0.0.1:5984:5984 couchdb:3
```

`127.0.0.1:` keeps CouchDB off your Wi-Fi network; only Tailscale (next step) reaches it.
If the container exits, check `docker logs couchdb-for-ols` (the guide notes the data folders
end up owned by uid 5984).

## 3. Configure CouchDB for LiveSync

```sh
cd ~/.livesync && . ./env
export hostname=http://localhost:5984 database=workdev
REF=f0ea5cb3a0a52f4cde7cb4d1ff24d7fa24d56068   # a commit whose scripts you have read; main moves
deno run --minimum-dependency-age=0 --allow-env --allow-net \
  https://raw.githubusercontent.com/vrtmrz/obsidian-livesync/$REF/utils/couchdb/provision.ts
```

Upstream's `couchdb-init.sh` now only wraps this Deno script. Run it from the URL: it imports a
sibling file, so a downloaded copy fails with "Module not found". Expect
`CouchDB provisioning completed.` It enables single-node mode, sets CORS to Obsidian's origins and
request sizes, and creates the `workdev` database. Settings land in
`~/.livesync/couchdb-etc/docker.ini` and survive a restart.

## 4. Give it a private HTTPS address

```sh
tailscale serve --bg 5984
tailscale serve status            # shows https://<mac-name>.<tailnet>.ts.net → localhost:5984
```

Check from the phone's browser (Tailscale on): `https://<mac-name>.<tailnet>.ts.net/` should
ask for a username and password (Safari) or show `{"error":"unauthorized",…}`: provisioning requires a login
even for `/`. That means it works; cancel, LiveSync gets the login from the setup link.
The first `tailscale serve` prints a login.tailscale.com link and waits until you enable Serve there.

## 5. Make a setup link

```sh
cd ~/.livesync && . ./env
export hostname=https://<mac-name>.<tailnet>.ts.net database=workdev
export passphrase=<vault encryption passphrase: long, keep it safe>
deno run --minimum-dependency-age=0 --allow-env \
  https://raw.githubusercontent.com/vrtmrz/obsidian-livesync/$REF/utils/setup/generate_setup_uri.ts
```

It prints an `obsidian://setuplivesync?...` link, a **URI passphrase** (shown once) and an ID
recovery code. Save the passphrase and recovery code in a password manager. The link expires after
the printed date (7-day window).

The link plus its URI passphrase unlock everything (CouchDB login and vault passphrase). Never
paste the output into a chat or ticket; if it leaks, change the CouchDB password and make a new link.

## 6. First device: the Mac's `~/WorkDev`

1. Obsidian → close every other vault window (the link goes to the window in front) → open
   `~/WorkDev` → Settings → Community plugins → turn off Restricted mode → Browse →
   **Self-hosted LiveSync** → install, **enable**. Installed but not enabled, the link gives
   "Unrecognized URI".
2. Open the `obsidian://setuplivesync?...` link (paste it in Safari) → enter the URI passphrase →
   **Initialise and overwrite remote** (this Mac's files go up first). Leave Customisation Sync and
   Hidden File Sync off.
3. The plugin's settings file holds the CouchDB password and passphrase; keep it out of git:
   `echo .obsidian/plugins/obsidian-livesync/data.json >> ~/WorkDev/.gitignore`
4. **Turn sync on.** The script's link sets no sync mode, so after setup nothing syncs by itself
   (files written to disk just sit there). Settings → Self-hosted LiveSync → 🔄 Sync Settings →
   **Synchronization Preset: LiveSync** → Apply.
5. Write a file into the vault from a terminal (e.g. `05_generated/livesync-test.md`); it should
   reach CouchDB within seconds. That is the path agent writes take.

## 7. Phone

1. Install Obsidian and Tailscale (connected). On first launch, decline Obsidian Sync and iCloud:
   create a vault named WorkDev **on this iPhone**.
2. Community plugins → turn off Restricted mode → install and **enable** Self-hosted LiveSync.
3. On the Mac: Cmd-P → **Copy settings as a new setup URI** (the plain one) → new URI passphrase →
   **Time-bound**. Send the link to the phone (AirDrop, Notes); type the passphrase yourself.
   Delete the link from Notes afterwards.
4. Open the link on the phone (or copy it and run **Use the copied setup URI**) → passphrase →
   **Join this device** → **Overwrite all with remote files** → **Delete local files if not on
   remote** (drops the new vault's `Welcome.md` instead of syncing it to the Mac).
5. Sync Settings → Synchronization Preset: **LiveSync** (the phone only syncs while Obsidian is in
   front anyway).

Test both ways: edit on the phone → see it on the Mac; edit on the Mac → see it on the phone.

## 8. Check it with the agent

1. Write a short daily note on the phone in `00_raw/daily/`.
2. On the Mac (Obsidian open), run ingest on it (`langgraph dev` → `ingest`).
3. Once 3d (apply) exists: approve, then see the updated pages on the phone.

## 9. Review page and chat on the phone

The review page and `/chat` run inside the Agent Server (`compose.yml`, published on 127.0.0.1 only).
Give it a second Tailscale HTTPS port:

```sh
uv run langgraph build -t llm-wiki:local
WIKI_VAULT_PATH=$HOME/WorkDev docker compose up -d --wait
tailscale serve --bg --https=8443 8123
```

Phone (Tailscale on): `https://<mac-name>.<tailnet>.ts.net:8443/review` and `…:8443/chat`. The page
has no login of its own: only devices on your tailnet reach it. Keep it that way (never `tailscale
funnel`). Stop: `tailscale serve --https=8443 off`.

## Undo

```sh
tailscale serve reset            # removes both the CouchDB and the review-page entries
docker rm -f couchdb-for-ols && rm -rf ~/.livesync
```
Then disable the plugin in Obsidian. The vault files on disk are untouched.
