---
phase: 1
date: 2026-10-12
tags: [safety, pydantic, regex, domain]
---

# Entity slugs and the alias index

## What it is

Every person, project, concept and decision has one page, named by a **slug** such as `person-alex-chen`.
The model only ever passes slugs; code turns a slug into a path. Because the slug's prefix picks exactly
one folder and the slug pattern forbids `/`, `.` and uppercase, the model cannot make code write outside
the entity folders. The **alias index** maps every name (slug, title, aliases) to slugs, so code can
resolve "Sam" or find "atlas beta" in a task without the model.

## How we used it

- `src/llm_wiki/domain/entities.py`: `EntityKind`, frozen `Entity` (slug validated by `SLUG_PATTERN`,
  validator checks `kind` matches the slug prefix).
- `src/llm_wiki/entity_index.py`:
  - `path_for(slug)`: `person-…` → `20_people/…md`, etc.; anything else raises `EntityPathError`.
  - `build_index(pages)`: pure, from (path, text) pairs. Title = frontmatter `title`, else `# ` heading,
    else slug. Aliases = list or single string. Wrong folder, bad file name, bad YAML → warnings.
  - `load_index(vault)`: thin I/O over the four entity folders only.
  - `lookup(name)`: exact name, any case/spacing. Returns a tuple: two or more means ambiguous, and the
    caller decides (decision 0002 rule 3 → rule 4). Shared names are also warned at build time.
  - `alias_mentions(text, kind)`: aliases as whole words in prose, after `plain_text` blanks out links
    and inline code.
- Dev vault `~/WorkDev` has 5 sample entity pages; running the index on the sample note found the
  "alias inside a link" bug before it reached the fixture tests.

## Paid-media reference

`src/paid_media_agent/tools/artifacts.py:71` `_path_for`: validate the ID with a regex, map kind → one
directory, then check the resolved path stays under the root. We get the same guarantee from the slug
pattern alone because our paths are vault-relative strings; the resolved-path check (symlinks) belongs to
the write tools in phase 3.

## Gotchas

- Whole-word matching needs lookarounds `(?<!\w)…(?!\w)`, not `\b`: `\b` fails next to aliases that
  start or end with punctuation (`C++`).
- Search prose, not markup: `[[project-atlas-v2]]` contains "atlas". Blank links first.
- Return ambiguity as data (a tuple of matches), never pick the first silently.
- A bad page must not hide the others: skip it with a warning, keep indexing.

## In my words

