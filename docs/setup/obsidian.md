# Set up Obsidian and the vault

The vault is a normal folder of Markdown files that you open in [Obsidian](https://obsidian.md) (free).
The agent reads and writes the same folder on disk. Obsidian is where **you** write; the agent never needs
it to be open (only sync does, see [`livesync.md`](livesync.md)).

You can follow this for a brand-new vault or to check an existing one.

## 1. Create the folder and make it a git repository

Pick a folder **outside** iCloud Drive and outside `~/Documents`, `~/Desktop` and `~/Downloads`
(Docker on macOS cannot mount those, and iCloud rewrites files behind the agent's back). Example:
`~/Work`.

```sh
VAULT=$HOME/Work
mkdir -p "$VAULT"/{00_raw/daily,00_raw/attachments,05_generated,10_projects,15_concepts,20_people,30_decisions,40_impact,50_templates,90_meta}
cd "$VAULT" && git init
```

What each folder is for:

| Folder | Holds | Who writes it |
|---|---|---|
| `00_raw/daily/` | your daily notes, one file per day (`2026-10-18.md`) | **you** (the agent only adds task ID suffixes, and the rollover job creates the empty day) |
| `00_raw/attachments/` | pasted images and files | you |
| `05_generated/` | the open-tasks page, briefs, brag sheets | the agent |
| `10_projects/` `15_concepts/` `20_people/` `30_decisions/` `40_impact/` | the wiki pages | the agent, after you approve |
| `50_templates/` | the daily note template | you |
| `90_meta/` | the ingest ledger, remembered rules and aliases | the agent |

Git is how you get history and undo: every approved ingest becomes one commit in this repo.

## 2. Open it in Obsidian

Obsidian → **Open folder as vault** → choose the folder. (On a phone you do not open this folder; the
phone gets its own copy through sync, see [`README.md`](README.md).)

## 3. The daily note template

Create `50_templates/daily.md` with exactly this content (the agent's parser understands it;
`template: daily-v1` selects the parser version):

```markdown
---
type: daily
template: daily-v1
date: {{date:YYYY-MM-DD}}
---

## Carried over

## Tasks
- [ ] 

## Meetings
### Topic with [[person-name]]
- 

## Log
- 

## Helped
- 

## Learned
- 
```

## 4. Obsidian settings

Settings (the gear) →

1. **Core plugins** → turn on **Daily notes** and **Templates**.
2. **Daily notes** → *New file location*: `00_raw/daily` · *Template file location*: `50_templates/daily`
   · *Date format*: `YYYY-MM-DD`.
3. **Templates** → *Template folder location*: `50_templates`.
4. **Files and links** → *Default location for new attachments*: `In the folder specified below` →
   `00_raw/attachments`.

Those settings live in the vault's `.obsidian/` folder. Add this `.gitignore` so editor noise does not
land in git:

```sh
printf '%s\n' '.obsidian/workspace*.json' '.obsidian/cache' '.trash/' '.DS_Store' > .gitignore
```

Then make the first commit: `git add -A && git commit -m "Initial vault"`.

## 5. How to write a daily note

Open today's note with the **Open today's daily note** command (Cmd-P). If the morning rollover is
installed (see [`../scheduling.md`](../scheduling.md)), it already exists with yesterday's open tasks under
**Carried over**.

- **Tasks:** write `- [ ] Send the Atlas estimate`. Tick it when done. **Never type task IDs**; ingest adds
  an invisible ` ^t-xxxxxx` suffix after you approve, and ticks are matched by that ID.
- **Meetings:** one `###` heading per meeting, with `[[person-name]]` or `[[project-name]]` links. Tasks under
  it inherit the project.
- **Tags** (optional): `#win` (goes to your impact page and brag sheet), `#decision` (decision log),
  `#blocked`.
- **Log / Helped / Learned:** free bullets. Any other `##` heading is treated as Log. Sections are optional
  and can be in any order.

The format and the reasons are in
[`../wiki/decisions/0002-daily-note-format.md`](../wiki/decisions/0002-daily-note-format.md).

## 6. Check it

With the Docker server running (see the main [`README.md`](../../README.md)) and a note written:

1. Open `http://127.0.0.1:8123/review` and start an ingest of the note (or type `/ingest` in `/chat`).
2. Read the proposed changes, approve, then see new pages appear in Obsidian and one new commit in
   `git log` from `llm-wiki`.

## Tips

- Edit freely in Obsidian; the agent reads files fresh each run. Edits you make to a wiki page are kept:
  the next ingest builds on the page as it is.
- Do not rename or move the folders above; code resolves paths from them.
- Keep a **copy** of the vault for experiments and point `WIKI_VAULT_PATH` at the real one only when you
  mean it.
