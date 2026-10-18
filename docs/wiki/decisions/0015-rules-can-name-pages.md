---
id: 0015
date: 2026-10-17
status: accepted
---

# 0015: A page the user's rules link may take any line

## Context

The naming check (0006, `staging._names_entity`) lets a fact go on a person or project page only when something
names that page: the line, its parent bullets, its `###` heading, the task's mapped project, or the user's revise
feedback. The 6b eval baseline (2026-10-17) found that a remembered rule could never be followed. Rule P4 says
"Interview notes go on [[project-hiring-q4]] under ## Interviews", but "Interviewed Omar again" names no project,
so every filing was refused and the agent asked instead. Interviews scored 0.0 after the rule was taught.

## Options

- **a. Rules can name pages.** A page that `90_meta/rules.md` links as `[[slug]]` passes the check for any line.
  - Pro: rules work as the user wrote them.
  - Pro: only pages the user linked on purpose are affected.
  - Con: code can't tell which lines the rule means, so a rule-linked page could take a line that doesn't belong
    there. The review still shows every filing.
- **b. Keep it strict.** The agent asks every time and the user answers.
  - Pro: no change.
  - Con: the same question every day for a recurring kind of note.
- **c. Aliases only.** Add a word like "interview" as an alias of the page.
  - Pro: no code change.
  - Con: aliases match whole words, so "interview: Lena" works but "Interviewed Lena again" doesn't.

## Decision

Option a, chosen by the user on 2026-10-17. Only an explicit link counts: a page named in a rule by its title, or
by an alias without `[[ ]]`, does not.

## Why

A rule is the user's own durable "this kind of fact goes there". The naming check exists to stop the model from
guessing, and a linked rule isn't a guess. What's left (a rule-linked page taking an unrelated line) is visible in the
review, where every filing is approved.
