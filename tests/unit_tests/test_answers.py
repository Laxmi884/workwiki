"""Review answers → new pages and aliases."""

from llm_wiki.answers import entity_edits, new_entity
from llm_wiki.domain.changes import AddAlias, CreateFile
from llm_wiki.domain.entities import EntityKind
from llm_wiki.entity_index import build_index, with_proposed

SAM = ("20_people/person-sam-ortiz.md", "---\ntitle: Sam Ortiz\naliases: [Sam]\n---\n")
VAULT = build_index([SAM])


def test_new_entity_slug_title_and_alias():
    eugene = new_entity(EntityKind.PERSON, "Eugene Park", lambda s: False)
    assert (eugene.slug, eugene.path, eugene.title, eugene.aliases) == (
        "person-eugene-park",
        "20_people/person-eugene-park.md",
        "Eugene Park",
        ("Eugene Park",),
    )
    taken = {"person-sam", "person-sam-2"}
    assert (
        new_entity(EntityKind.PERSON, "Sam", taken.__contains__).slug == "person-sam-3"
    )


def test_edits_create_new_pages_and_add_aliases_to_existing_ones():
    eugene = new_entity(EntityKind.PERSON, "Eugene", lambda s: False)
    edits = entity_edits(
        [eugene],
        {"person-sam-ortiz": ["Sammy"], "person-eugene": ["Gene"]},  # Gene: page is new
        VAULT,
    )
    assert edits == [
        CreateFile(
            path="20_people/person-eugene.md",
            text="---\ntype: person\ntitle: Eugene\naliases:\n- Eugene\n---\n# Eugene\n",
        ),
        AddAlias(path="20_people/person-sam-ortiz.md", alias="Sammy"),
    ]


def test_proposed_pages_and_aliases_are_known_before_they_are_written():
    eugene = new_entity(EntityKind.PERSON, "Eugene", lambda s: False)
    index = with_proposed(VAULT, [eugene], {"person-sam-ortiz": ["Sammy"]})
    assert [e.slug for e in index.lookup("eugene")] == ["person-eugene"]
    assert [e.slug for e in index.lookup("Sammy")] == ["person-sam-ortiz"]
    assert VAULT.lookup("Sammy") == ()  # the vault index itself is unchanged
