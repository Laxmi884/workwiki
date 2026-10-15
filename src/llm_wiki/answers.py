"""Review answers → pages and aliases (phase 3c-2).

"new" makes an entity page whose alias is the name as written; an existing slug gets
the name as an extra alias. Either way the next note links the name by itself: the
answer is also the memory.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping

import yaml

from llm_wiki.domain.changes import AddAlias, CreateFile, Edit
from llm_wiki.domain.entities import Entity, EntityKind
from llm_wiki.entity_index import EntityIndex, path_for


def new_entity(kind: EntityKind, name: str, taken: Callable[[str], bool]) -> Entity:
    """Return a new entity for `name`, e.g. "Eugene" → person-eugene (-2 if taken)."""
    words = re.findall(r"[a-z0-9]+", name.lower())[:6] or ["unnamed"]
    base = f"{kind.value}-" + "-".join(words)
    slug, n = base, 2
    while taken(slug):
        slug, n = f"{base}-{n}", n + 1
    return Entity(
        slug=slug, kind=kind, path=path_for(slug), title=name, aliases=(name,)
    )


def entity_edits(
    new_entities: Iterable[Entity],
    new_aliases: Mapping[str, Iterable[str]],
    vault: EntityIndex,
) -> list[Edit]:
    """Return the edits that make answered pages and aliases real.

    `vault` is the index of pages on disk: aliases go only to pages that exist there
    (an alias for a page created in this review is already in its frontmatter).
    """
    edits: list[Edit] = []
    for e in new_entities:
        meta = {"type": e.kind.value, "title": e.title, "aliases": list(e.aliases)}
        text = f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)}---\n"
        edits.append(CreateFile(path=e.path, text=f"{text}# {e.title}\n"))
    for slug, aliases in new_aliases.items():
        if (page := vault.get(slug)) is not None:
            edits += [AddAlias(path=page.path, alias=a) for a in aliases]
    return edits
