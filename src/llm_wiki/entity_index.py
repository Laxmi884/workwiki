"""Entity pages in the vault: slug → path, page parsing, and the alias index."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from llm_wiki.domain.entities import SLUG_PATTERN, Entity, EntityKind
from llm_wiki.frontmatter import split_frontmatter
from llm_wiki.inline_syntax import plain_text

# Each kind lives in exactly one folder; entity writes can only land here.
FOLDER_BY_KIND: Mapping[EntityKind, str] = {
    EntityKind.PERSON: "20_people",
    EntityKind.PROJECT: "10_projects",
    EntityKind.CONCEPT: "15_concepts",
    EntityKind.DECISION: "30_decisions",
}

_SLUG_RE = re.compile(SLUG_PATTERN)

# "# Title", exactly level 1.
_TITLE_RE = re.compile(r"^#[ \t]+(?P<title>.+?)[ \t]*$")


class EntityPathError(ValueError):
    """A slug that does not name an entity page (bad shape or unknown kind)."""


def kind_of(slug: str) -> EntityKind | None:
    """Return the kind for a well-formed slug, else None."""
    if not _SLUG_RE.fullmatch(slug):
        return None
    return EntityKind(slug.split("-", 1)[0])


def path_for(slug: str) -> str:
    """Return the vault-relative page path for a slug.

    The model passes slugs; only code turns them into paths. The slug pattern allows
    no "/", "." or uppercase, so the result is always `<kind folder>/<slug>.md`.

    Raises:
        EntityPathError: The slug is malformed or has an unknown kind.
    """
    kind = kind_of(slug)
    if kind is None:
        raise EntityPathError(f"not an entity slug: {slug!r}")
    return f"{FOLDER_BY_KIND[kind]}/{slug}.md"


def _name_key(name: str) -> str:
    """Compare names case- and spacing-insensitively."""
    return " ".join(name.split()).casefold()


@dataclass(frozen=True, slots=True)
class EntityIndex:
    """All entity pages, looked up by slug, title or alias.

    Build it with `build_index` or `load_index`, not directly.
    """

    entities: Mapping[str, Entity]  # slug → entity, in path order
    by_name: Mapping[str, tuple[str, ...]]  # name key → slugs (slug, title, aliases)
    warnings: tuple[str, ...] = ()

    def get(self, slug: str) -> Entity | None:
        """Return the entity for `slug`, or None if there is no such page."""
        return self.entities.get(slug)

    def lookup(self, name: str) -> tuple[Entity, ...]:
        """Entities whose slug, title or an alias equals `name`. Two or more: ambiguous."""
        return tuple(self.entities[s] for s in self.by_name.get(_name_key(name), ()))

    def alias_mentions(
        self, text: str, kind: EntityKind | None = None
    ) -> tuple[Entity, ...]:
        """Entities with an alias in `text` as a whole word or phrase (mapping rule 3).

        Links and inline code are ignored: "[[project-atlas-v2]]" does not mention "Atlas".
        """
        text = plain_text(text)
        return tuple(
            entity
            for entity in self.entities.values()
            if (kind is None or entity.kind is kind)
            and any(_contains_phrase(text, alias) for alias in entity.aliases)
        )


def _contains_phrase(text: str, phrase: str) -> bool:
    """Return whether `phrase` is in `text` as whole words (any case, any spacing)."""
    words = (re.escape(w) for w in phrase.split())
    pattern = r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _aliases(raw: Any, path: str, warnings: list[str]) -> tuple[str, ...]:
    """Frontmatter `aliases` as a tuple: a list, a single string, or missing."""
    if raw is None:
        return ()
    items = raw if isinstance(raw, list) else [raw]
    aliases = []
    for item in items:
        if isinstance(item, (str, int, float)) and str(item).strip():
            aliases.append(str(item).strip())
        else:
            warnings.append(f"{path}: ignored alias {item!r}")
    return tuple(dict.fromkeys(aliases))


def _title(metadata: dict[str, Any], body: str, slug: str) -> str:
    """Frontmatter `title`, else the first `# ` heading, else the slug."""
    title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in body.splitlines():
        m = _TITLE_RE.match(line)
        if m is not None:
            return m.group("title")
    return slug


def _entity_from_page(path: str, text: str, warnings: list[str]) -> Entity | None:
    slug = PurePosixPath(path).stem
    kind = kind_of(slug)
    if kind is None:
        warnings.append(f"{path}: file name is not an entity slug; skipped")
        return None
    if path_for(slug) != path:
        warnings.append(f"{path}: should be at {path_for(slug)}; skipped")
        return None

    split = split_frontmatter(text)
    if split.error is not None:
        warnings.append(f"{path}: {split.error}")
    return Entity(
        slug=slug,
        kind=kind,
        path=path,
        title=_title(split.metadata, split.body, slug),
        aliases=_aliases(split.metadata.get("aliases"), path, warnings),
    )


def build_index(pages: Iterable[tuple[str, str]]) -> EntityIndex:
    """Build the index from (vault-relative path, text) pairs. Never raises on bad pages."""
    warnings: list[str] = []
    entities: dict[str, Entity] = {}
    for path, text in sorted(pages):
        entity = _entity_from_page(path, text, warnings)
        if entity is not None:
            entities[entity.slug] = entity

    by_name: dict[str, list[str]] = {}
    for entity in entities.values():
        for name in (entity.slug, entity.title, *entity.aliases):
            slugs = by_name.setdefault(_name_key(name), [])
            if entity.slug not in slugs:
                slugs.append(entity.slug)
    for key, slugs in by_name.items():
        if len(slugs) > 1:
            warnings.append(f"name {key!r} is shared by {', '.join(slugs)}")

    return EntityIndex(
        entities=entities,
        by_name={key: tuple(slugs) for key, slugs in by_name.items()},
        warnings=tuple(warnings),
    )


def load_index(vault: Path) -> EntityIndex:
    """Read every `*.md` page in the entity folders of `vault` and index them."""
    pages: list[tuple[str, str]] = []
    unreadable: list[str] = []
    for folder in FOLDER_BY_KIND.values():
        for file in sorted((vault / folder).glob("*.md")):
            rel = file.relative_to(vault).as_posix()
            try:
                pages.append((rel, file.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError) as exc:
                unreadable.append(f"{rel}: unreadable ({exc.__class__.__name__})")
    index = build_index(pages)
    return dataclasses.replace(index, warnings=(*unreadable, *index.warnings))
