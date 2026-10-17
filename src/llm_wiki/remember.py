"""Remember, in the review (decision 0009 R3): rules and aliases → proposed edits.

Pure. The user's own words become edits like any other change, approved with the
rest of the review and never written silently:

- a rule → a line under "## Rules" in `90_meta/rules.md` (created if missing), which
  both agents read as memory (`RememberRules`, the only edit type that may write
  there);
- an alias → the page's frontmatter `aliases:` (`AddAlias`), which task mapping reads.

Code decides which is which: the review has a separate input for each (user choice,
2026-10-16), so no model guesses.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from llm_wiki.domain.changes import RULES_PATH, AddAlias, Edit, RememberRules
from llm_wiki.entity_index import name_key, path_for

MAX_RULE = 300
MAX_NAME = 80


def clean(text: str) -> str:
    """Return `text` on one line with single spaces (newlines too)."""
    return " ".join(text.split())


def check_rules(rules: Sequence[str]) -> str | None:
    """Return why the rules can't be remembered, or None."""
    for rule in rules:
        if not clean(rule):
            return "a rule is empty"
        if len(clean(rule)) > MAX_RULE:
            return f"a rule is longer than {MAX_RULE} characters: {rule[:40]!r}…"
    return None


def check_aliases(
    aliases: Mapping[str, str], names_of: Mapping[str, Sequence[str]]
) -> str | None:
    """Return why the aliases can't be remembered, or None.

    Args:
        aliases: name → page slug, as answered.
        names_of: page slug → its current names (slug, title, aliases); the pages
            an alias may name.
    """
    taken = {name_key(n): slug for slug, names in names_of.items() for n in names}
    seen: set[str] = set()
    for name, slug in aliases.items():
        key = name_key(name)
        if not key:
            return "an alias is empty"
        if len(clean(name)) > MAX_NAME:
            return f"an alias is longer than {MAX_NAME} characters: {name[:40]!r}…"
        if key in seen:
            return f"{clean(name)!r} is given twice"
        seen.add(key)
        if slug not in names_of:
            return (
                f"{clean(name)!r}: {slug!r} is not a page in this review or the vault"
            )
        if (owner := taken.get(key)) == slug:
            return f"{clean(name)!r} is already a name of {slug}"
        if owner is not None:
            return (
                f"{clean(name)!r} already names {owner}; a name shared by two pages "
                "links neither"
            )
    return None


def remember_edits(
    rules: Sequence[str],
    aliases: Mapping[str, str],
    read: Callable[[str], str | None],
) -> list[Edit]:
    """Return the edits that remember `rules` and `aliases` (name → slug).

    Rules already in the file (as a "- rule" line) are skipped; rendering creates
    the file if it is missing. An alias edit on a page proposed in this review lands
    on the proposed text (render applies edits in order).
    """
    edits: list[Edit] = []
    current = read(RULES_PATH)
    have = set((current or "").splitlines())
    lines = tuple(dict.fromkeys(f"- {clean(r)}" for r in rules))
    lines = tuple(line for line in lines if line not in have)
    if lines:
        edits.append(RememberRules(lines=lines))
    edits += [
        AddAlias(path=path_for(slug), alias=clean(name))
        for name, slug in aliases.items()
    ]
    return edits
