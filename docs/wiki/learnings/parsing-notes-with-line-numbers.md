---
phase: 1
date: 2026-10-12
tags: [parsing, regex, pydantic, testing]
---

# Parsing notes with line numbers

## What it is

Turning a daily note into a `DailyNote` (date, template, sections, tasks with context) while keeping every
task's exact file line. The line number matters because ingest later appends ` ^t-xxxxxx` to that exact
line (decision 0001). The parser is forgiving: messy notes produce warnings, never exceptions.

## How we used it

A pipeline of small pure functions, each tested on its own:

1. `frontmatter.py` `split_frontmatter`: splits YAML from the body and returns `body_start_line`, so body
   line i is file line `body_start_line + i`. Errors (bad YAML, unclosed `---`) come back as `error`.
2. `daily_syntax.py` `split_sections`: splits on `##` only. Unknown headings and the preamble become Log.
   Headings inside ```` ``` ````/`~~~` fences are ignored. `Section.start_line` keeps the line mapping.
3. `daily_syntax.py` `_section_tasks`: walks a section's lines with three pieces of state: current `###`
   subheading, `in_fence`, and a stack of open bullets `(indent width, text)`. For each list item:
   pop while the top's width is ≥ this width, the remaining top is the parent, then push this item.
   Plain bullets are pushed too, so they can be parents.
4. `inline_syntax.py` `find_links` / `find_tags`: per-line regexes. Inline code is blanked first; links are
   blanked before looking for tags so `[[a#b]]` is not a tag.
5. `parse_daily_note`: glues it together; date = frontmatter, else filename; drift → warning.

Dev vault `~/WorkDev/00_raw/daily/2026-10-12.md` is a realistic sample to run it on by hand.

## Paid-media reference

`src/paid_media_agent/domain/` keeps models free of parsing. Its tools return problems as data in results
rather than raising, the same idea as `DailyNote.warnings`.

## Gotchas

- Tabs: indent width uses `expandtabs(4)`, otherwise a tab (1 char) looks shallower than 4 spaces.
- `datetime.datetime` is a subclass of `datetime.date`: check `datetime` first.
- A field named `date` with type `date` clashes inside a Pydantic class: `import datetime`, use `datetime.date`.
- Circular import (`tasks.py` ↔ `daily.py`): move the shared type (`SectionKind`) to `domain/common.py`.
- `python-frontmatter` strips leading body lines (loses line numbers) and raises on `{{date}}`; we wrote our own split.
- Regex lookbehind `(?<![\w#/&])` rules out `a#b`, `##`, URLs and `&#39;` in one place; `#42` is filtered after.

## In my words

