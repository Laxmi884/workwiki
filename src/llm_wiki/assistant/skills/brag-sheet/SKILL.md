---
name: brag-sheet
description: Rules for performance-review synthesis - what counts as a win, where evidence comes from, metrics only as written, and when to hand the work to the review-writer subagent. Read for any review, brag sheet, accomplishments or "what did I do in <period>" request.
---

# Brag sheet and review synthesis

## Hand it to `review-writer`

For a brag sheet or review over a period (a quarter, a half, "since June"), call the
`task` tool with `review-writer` and the period and any focus the user gave. It returns
a structured draft with a source for every item. Then write it up for the user.

## Rules for the write-up

- **Evidence only.** Every claim comes from the draft (or a page you read) and keeps
  its date and `[[source]]` link. No claim without a source.
- **Metrics only as written.** If the notes say "cut load time from 4s to 1.5s", use
  that. Never estimate impact, invent percentages or turn "faster" into a number. Put
  missing numbers under "Gaps to fill" so the user can add them.
- **Group by project or theme**, then wins, help given to others, and decisions the
  user drove. Short bullets, strongest first.
- **Done tasks are not wins** unless the impact page lists them: the user picks wins
  in the ingest review.
- End with **Gaps to fill**: items with no outcome or number recorded, and periods
  with nothing on the impact page.
