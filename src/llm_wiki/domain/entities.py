"""Entity domain types: the people, projects, concepts and decisions notes link to."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# "<kind>-<lowercase words joined by single hyphens>", e.g. "person-alex-chen".
SLUG_PATTERN = r"^(person|project|concept|decision)-[a-z0-9]+(-[a-z0-9]+)*$"


class EntityKind(StrEnum):
    """What an entity is; also the slug prefix ("person-…")."""

    PERSON = "person"
    PROJECT = "project"
    CONCEPT = "concept"
    DECISION = "decision"


class Entity(BaseModel):
    """One entity page in the vault.

    Attributes:
        slug: The link target and file stem, e.g. "person-alex-chen".
        kind: Taken from the slug prefix.
        path: Vault-relative page path, e.g. "20_people/person-alex-chen.md".
        title: Frontmatter `title`, else the first `# ` heading, else the slug.
        aliases: Other names from frontmatter `aliases`, e.g. ("Alex", "AC").
    """

    model_config = ConfigDict(frozen=True)

    slug: str = Field(pattern=SLUG_PATTERN)
    kind: EntityKind
    path: str
    title: str
    aliases: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _kind_matches_slug(self) -> Entity:
        if not self.slug.startswith(f"{self.kind.value}-"):
            raise ValueError(f"slug {self.slug!r} does not start with {self.kind}-")
        return self
