import pydantic
import pytest

from llm_wiki.domain.entities import Entity, EntityKind
from llm_wiki.entity_index import (
    EntityPathError,
    build_index,
    kind_of,
    load_index,
    path_for,
)

ALEX = (
    "20_people/person-alex-chen.md",
    "---\ntitle: Alex Chen\naliases: [Alex, AC]\n---\n# Ignored heading\n",
)
ATLAS = (
    "10_projects/project-atlas.md",
    "---\naliases:\n  - Atlas\n  - atlas beta\n---\n# Project Atlas\n",
)
FLAGS = ("15_concepts/concept-feature-flags.md", "No frontmatter, no heading.\n")


# --- slug → path ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("slug", "path"),
    [
        ("person-alex-chen", "20_people/person-alex-chen.md"),
        ("project-atlas", "10_projects/project-atlas.md"),
        ("concept-feature-flags", "15_concepts/concept-feature-flags.md"),
        ("decision-keep-bi-tool", "30_decisions/decision-keep-bi-tool.md"),
        ("project-q3-2026", "10_projects/project-q3-2026.md"),
    ],
)
def test_path_for(slug, path):
    assert path_for(slug) == path


@pytest.mark.parametrize(
    "slug",
    [
        "alex-chen",  # no kind
        "team-platform",  # unknown kind
        "person-",  # nothing after the kind
        "person-Alex",  # uppercase
        "person-alex--chen",  # double hyphen
        "person-alex-",  # trailing hyphen
        "person-../../etc/passwd",  # path traversal
        "person-alex/chen",
        "person-alex.md",
        "person-alex chen",
        "person-alex\n",
        "",
    ],
)
def test_path_for_rejects_bad_slugs(slug):
    assert kind_of(slug) is None
    with pytest.raises(EntityPathError):
        path_for(slug)


def test_entity_kind_must_match_slug():
    with pytest.raises(pydantic.ValidationError):
        Entity(slug="person-atlas", kind=EntityKind.PROJECT, path="x", title="x")


# --- pages → entities ----------------------------------------------------------


def test_build_index_reads_title_and_aliases():
    index = build_index([ALEX, ATLAS, FLAGS])
    assert index.warnings == ()
    got = [(e.slug, e.kind, e.title, e.aliases) for e in index.entities.values()]
    assert got == [
        ("project-atlas", EntityKind.PROJECT, "Project Atlas", ("Atlas", "atlas beta")),
        ("concept-feature-flags", EntityKind.CONCEPT, "concept-feature-flags", ()),
        ("person-alex-chen", EntityKind.PERSON, "Alex Chen", ("Alex", "AC")),
    ]
    assert index.get("person-alex-chen").path == ALEX[0]


def test_single_string_alias():
    index = build_index([("20_people/person-sam.md", "---\naliases: Sammy\n---\n")])
    assert index.get("person-sam").aliases == ("Sammy",)


def test_bad_pages_are_skipped_with_warnings():
    index = build_index(
        [
            ("20_people/Alex Chen.md", ""),  # not a slug
            ("20_people/project-atlas.md", ""),  # wrong folder
            ("20_people/person-sam.md", "---\naliases: [{a: 1}, Sam]\n---\n"),
            ("20_people/person-kai.md", "---\ntitle: [unclosed\n---\n"),
        ]
    )
    assert list(index.entities) == ["person-kai", "person-sam"]
    assert index.get("person-sam").aliases == ("Sam",)
    joined = "\n".join(index.warnings)
    assert "not an entity slug" in joined
    assert "should be at 10_projects/project-atlas.md" in joined
    assert "ignored alias" in joined
    assert "person-kai.md: invalid YAML" in joined


# --- lookups -------------------------------------------------------------------


def test_lookup_by_slug_title_or_alias_ignoring_case_and_spacing():
    index = build_index([ALEX, ATLAS])
    for name in ["person-alex-chen", "Alex Chen", "alex   chen", "ALEX", "ac"]:
        assert [e.slug for e in index.lookup(name)] == ["person-alex-chen"]
    assert index.lookup("Priya") == ()


def test_shared_alias_is_ambiguous_and_warned():
    index = build_index(
        [
            ("20_people/person-sam-ortiz.md", "---\naliases: [Sam]\n---\n"),
            ("20_people/person-sam-lee.md", "---\naliases: [Sam]\n---\n"),
        ]
    )
    assert {e.slug for e in index.lookup("sam")} == {
        "person-sam-ortiz",
        "person-sam-lee",
    }
    assert any("'sam' is shared by" in w for w in index.warnings)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ship the Atlas beta", ["project-atlas"]),
        ("ship ATLAS   BETA notes", ["project-atlas"]),  # case and spacing
        ("Atlas: fix login", ["project-atlas"]),  # punctuation is a boundary
        ("Read the Atlassian docs", []),  # inside a longer word
        ("Ask AC about it", []),  # Alex is a person, filtered by kind
        ("Plan [[project-atlas-v2]] launch", []),  # inside a link
        ("Run `atlas deploy`", []),  # inside inline code
    ],
)
def test_alias_mentions_for_projects(text, expected):
    index = build_index([ALEX, ATLAS])
    found = index.alias_mentions(text, kind=EntityKind.PROJECT)
    assert [e.slug for e in found] == expected


def test_alias_mentions_any_kind():
    index = build_index([ALEX, ATLAS])
    found = index.alias_mentions("AC wants the atlas beta")
    assert [e.slug for e in found] == ["project-atlas", "person-alex-chen"]


ALEX_KIM = (
    "20_people/person-alex-kim.md",
    "---\ntitle: Alex Kim\naliases: [Alex]\n---\n",
)


@pytest.mark.parametrize(
    ("text", "expected", "ambiguous"),
    [
        ("Send notes to Alex Chen", ["person-alex-chen"], []),  # title
        ("ask AC and alex kim", ["person-alex-chen", "person-alex-kim"], []),
        ("Ping Alex", [], ["alex"]),  # shared alias: nobody
        ("Ship the Atlas beta", [], []),  # projects are filtered out
        ("Read [[person-alex-chen]] notes", [], []),  # links are not names
    ],
)
def test_name_mentions_for_people(text, expected, ambiguous):
    index = build_index([ALEX, ALEX_KIM, ATLAS])
    found, unclear = index.name_mentions(text, EntityKind.PERSON)
    assert [e.slug for e in found] == expected
    assert list(unclear) == ambiguous


# --- loading from disk ---------------------------------------------------------


def test_load_index_reads_entity_folders_only(tmp_path):
    for rel, text in [ALEX, ATLAS, ("00_raw/daily/person-x.md", ""), ("README.md", "")]:
        file = tmp_path / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
    (tmp_path / "20_people/person-bad.md").write_bytes(b"\xff\xfe not utf-8")

    index = load_index(tmp_path)
    assert list(index.entities) == ["project-atlas", "person-alex-chen"]
    assert any("person-bad.md: unreadable" in w for w in index.warnings)


def test_load_index_on_empty_vault(tmp_path):
    index = load_index(tmp_path)
    assert (dict(index.entities), index.warnings) == ({}, ())
