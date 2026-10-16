---
name: people-and-projects
description: How to tell which person or project a name in a daily note refers to, when to propose a new page, and when to ask the user instead. Read before filing facts about people or projects.
---

# People and projects

## Finding the page for a name

1. A `[[slug]]` link in the line names the page directly.
2. Otherwise match the name against "Known pages" in the message: slug, title or an
   alias ("Priya" → person-priya-nair, "atlas beta" → project-atlas). `grep` the vault
   for the name if unsure.
3. A page's aliases count: "Alex" → person-alex-chen if listed as an alias.
4. Context counts: a line under `### 1:1 with Marta` is about Marta; a sub-bullet
   belongs to its parent bullet's project; a task line shows its project in `[...]`.
5. A first name shared by two pages is ambiguous: ask, don't guess.

## Which project a line is about

Put a fact on an existing project page only when the line, its parent bullet or its
`###` heading names the project (title, alias or `[[slug]]`), or the task shows the
project in `[...]`. Code refuses anything else. A page being the only project in the
vault is not a reason: most work belongs to projects that have no page yet.

When no existing project is named:
- the note names a piece of work the user tracks, or several lines are about the same
  work ("loss history workflow" in two tasks) → `propose_page` a project, then file;
- you can't tell → `ask_user` ("Which project is line 9 about, or is it ad hoc?");
- a one-off task (a quick fix, an errand) → no page at all.

New project pages follow the same rule: a fact goes on one only if its line, parent or
heading names it. So:
- title a new project with the words the note uses ("delq analysis", not "Delinquency
  analysis"), so the lines that mean it also name it;
- one project per piece of work; never a catch-all named after a team, area or tool
  ("Risk analytics", "GitLab"), and never a project just to hold unrelated lines;
- lines about the work that don't name it: ask once which project they belong to.

## New pages

- Propose a person page for a human the note names (a 1:1, someone who asked or
  helped, someone a decision is about) who has no page. Slug: `person-<name as
  written>`, e.g. `person-marta` for "Marta"; title as written.
- Tools, systems, products, reports, teams and acronyms are not people ("Gitlab",
  "PowerBI", "ELTV", "UBM", "the data team").
- Propose a project page for a clearly named piece of work the user tracks ("payments
  v2 is now a project", "Atlas Search"). One-off chores are not projects.
- When a full name appears later for someone who has a page under a short name, keep
  using the existing page.
- Never invent a full name or a role that the note doesn't give.

## Asking

Use `ask_user` (sparingly: each question costs the user time) when a name could be a person or a system, when two pages fit, or when
you can't tell which project a line is about. One question per name.
