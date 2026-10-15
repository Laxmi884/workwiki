---
id: 0004
date: 2026-10-12
status: accepted
---

# 0004: Ingest graph design: code around a narrow model step, edits → diffs, review loop

## Context

Phase 2 turns one daily note into proposed vault changes. The model must stay narrow (read prose only),
every write must be checkable and approvable, and the user must be able to correct missed or wrong
extractions, not only approve or reject.

## Options

- One agent with file tools reads the note and edits pages: flexible, but unpredictable steps and hard
  to review.
- Fixed graph: code parses and routes, model extracts structured facts per section, code renders exact
  diffs; review step with approve / reject / edit / revise-with-feedback: chosen.

## Decision

- Nodes: `load_note` → `assign_ids` → `track_tasks` → fan-out (`Send`, one per prose section) →
  `extract` → `route` → `render_diffs` → (phase 3) `review` (`interrupt`) → `apply`.
- Model step: `SectionFacts` (decisions, wins, person notes, help, learnings, project updates), each
  fact citing its `line`. Names may be slugs or plain names; code resolves them. Tasks are code only.
- State: `facts` is a dict keyed per section with a merge-by-key reducer (parallel-safe, a re-run
  replaces its own key); `route` rebuilds `edits` from all facts each round; `feedback` holds the
  user's revise messages.
- Writes: nodes emit small `Edit`s (`AppendId`, `AppendToSection`, `CreateFile`); `render_diffs` merges
  them per file into one `FileDiff` (before sha256, after text, unified diff, digest). Allowed folders
  are part of each edit's type.
- Review answers (phase 3): approve, approve some, reject, edit (user text replaces a diff),
  revise (feedback → re-extract with previous facts + feedback, max 3 rounds).
- Task index: code-owned `90_meta/tasks.json`. Mapping rules 1–3 in v1; rule 4 (model proposal) later.
- Every model-written line links its source note, e.g. `([[2026-10-12]])`. Facts not in the note go
  into the note, not into feedback.
- Task records keep `people` (person links) and `tags` from the task line (added 2026-10-13), so
  tasks without a project can be found by person or `#blocked`.
- People clarification (added 2026-10-13, built in 2d/2e/3): unlinked names (alias matches by code,
  unknown names from the model, ambiguous matches) become questions in the same review `interrupt`:
  this person / other existing person / new person / not a person. `00_raw/` stays untouched; the
  answer goes into `tasks.json` and facts. Confirming a name adds it as an alias on the person page
  automatically, so it is not asked again. Names resolved by alias are still listed in the review
  (shown, not asked) so a wrong match can be changed. A name that becomes an alias of two people is
  ambiguous and asked each time; a `[[link]]` in the note always wins without a question.
  Later: the model may use context (e.g. "Sam" in the Atlas sync → whoever is on Atlas) to
  pre-select an answer, never to skip the question (same rule as mapping rule 4).
- Routing (added 2026-10-13, 2e): decisions go into a quarterly log
  `30_decisions/<year>/decisions-<year>-q<n>.md` as a short block under the date (`### title`, then
  When, People, Project, Rationale, Source with the quoted line), not one file per decision; in
  phase 3 review a decision can be marked "make page" to get its own page. Logs live in year folders
  (`40_impact/<year>/impact-<year>-q<n>.md` likewise) so a year's review is one folder. Updates,
  wins and decisions go under `## Updates` / `## Wins` / `## Decisions` on project pages; person
  notes and help under `## Notes` / `## Help given` on person pages; learnings under `## Notes` on
  concept pages; wins and help also on the quarter's impact page. Names that don't
  resolve go to `05_generated/inbox.md` (never dropped; phase 3 questions). A linked entity without
  a page gets a stub; a slug the model made up does not.
- Review loop (added 2026-10-14, 3c-1): besides approve/reject the review takes revise
  (re-extract every prose section with all feedback so far; max 3), make_page and edit, one
  action per answer; each loops back only as far as needed (extract / route / render) and ends
  at a fresh review, so approval is always for the final digests. Make page: the decision's
  details move to `30_decisions/decision-<slug>.md` (slug from the title, `-2` if taken, plus
  empty Context/Consequences headings); the quarterly log keeps title, When and a page link.
  Edit: the user sends a wiki page's full new text (not raw notes, not `90_meta/*.json`); a
  later revise or make_page drops edits with a warning. Review files are listed by path so
  their IDs stay stable across rounds.
- Ingest ledger (added 2026-10-13, 3a): `90_meta/ingest-ledger.json` keeps per note its hash (as
  it will be after its task IDs are added) and a key per prose line ingested. Unchanged note → the
  run stops; edited note → the model sees whole sections but may cite only new lines (old ones
  marked `~`, context only), sections without new lines skip the model. A reworded line counts as
  new; deleted lines don't remove wiki entries. A broken ledger stops ingest and is never
  overwritten. The ledger update is a proposed diff, recorded only when approved. In 3d it moves
  to `apply`: built last from the note as actually written, so it stays true when only some
  diffs are approved (e.g. the note's ID diff rejected).
- Approved = committed (added 2026-10-13, built in 3d/3e): approval records each diff's digest.
  Before writing, the diff must still have the approved digest and the file must still match
  `before_sha256` (otherwise that file is skipped and reported). After writing, each file is read
  back and must match the approved after-hash. After the git commit, each file is read from the
  commit (`HEAD`) and checked again; the commit message lists files with short digests.
- Fact context comes from code, not the model (added 2026-10-13, built in 2e): from a fact's
  `line`, code adds the note date, the meeting (`###` heading above the line), who was there
  (links in that heading) and a link to the note; decision pages also quote the line (a link to
  the exact line would need a block ID in `00_raw/`, which only tasks may get). The model's own fields keep the detail the
  note gives (names, dates, numbers, reasons; one to three sentences).

## Why

Deterministic work stays testable code, the model only does what code cannot (read prose), and every
change is an exact, hashed diff the user can approve, edit or send back with feedback.
