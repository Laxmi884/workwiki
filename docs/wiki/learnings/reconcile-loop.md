---
phase: 7
date: 2026-10-18
tags: [slack, notifier, idempotency]
---

# Reconcile loop (notifier)

## What it is

When nothing pushes you an event, ask for the current state on a timer and make the outside world match it. Compare
"what is open" with "what Slack shows" and fix the three differences: new, gone, changed. A pass run twice does nothing the second time.

## How we used it

`src/llm_wiki/slack/notifier.py` (`Notifier.run_once`) compares `backend.pending()` with `Announced`
(`slack/announced.py`, saved in `WIKI_STATE_DIR/slack-announced.json`). Handlers record the review they show, so Slack-started
reviews aren't announced twice. Notebook 8.5.

## Paid-media reference

No notifier there. Closest: `surfaces/slack/service.py` (approvals) and `surfaces/runner.py`.

## Gotchas

- An outage, a capped list or a review being answered right now all look like "not open". Clear only on proof the review finished.
- A transient Slack error is not "message gone": keep the record and retry, or you re-announce.
- Don't hold a lock across slow calls; the branch review found a button press could wait minutes behind a pass.
- One bad item must not stop the pass.

## In my words

(leave for the user)
