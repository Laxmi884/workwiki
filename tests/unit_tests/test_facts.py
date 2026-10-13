import pydantic
import pytest

from llm_wiki.domain.facts import Help, SectionFacts

# What a model might return for the Meetings section of the fixture note.
MEETINGS_REPLY = {
    "decisions": [
        {
            "line": 23,
            "title": "Keep old BI tool until Q1",
            "project": "Atlas",  # a name, not a slug: allowed, code resolves it
            "people": ["person-alex-chen", "Priya"],
        }
    ],
    "person_notes": [
        {"line": 29, "person": "person-sam-ortiz", "note": "Wants to own search."}
    ],
}


def test_model_reply_parses_with_missing_lists_empty():
    facts = SectionFacts.model_validate(MEETINGS_REPLY)
    assert facts.decisions[0].title == "Keep old BI tool until Q1"
    assert facts.decisions[0].rationale is None  # not stated, stays None
    assert (facts.wins, facts.helps, facts.learnings) == ([], [], [])
    assert facts.cited_lines() == {23, 29}


def test_empty_reply_is_valid():
    assert SectionFacts.model_validate({}).cited_lines() == set()


@pytest.mark.parametrize(
    "bad",
    [
        {"line": 0, "person": "Sam", "what": "x"},  # lines start at 1
        {"line": 5, "person": "Sam", "what": "x", "minutes": 0},  # no zero durations
        {"line": 5, "person": "Sam"},  # `what` is required
    ],
)
def test_help_rejects(bad):
    with pytest.raises(pydantic.ValidationError):
        Help.model_validate(bad)


def test_facts_are_frozen():
    facts = SectionFacts.model_validate(MEETINGS_REPLY)
    with pytest.raises(pydantic.ValidationError):
        facts.decisions[0].title = "changed"


def test_schema_carries_the_prompt_text():
    """Docstrings and descriptions reach the model through the JSON schema."""
    schema = SectionFacts.model_json_schema()
    decision = schema["$defs"]["Decision"]
    assert "Not an idea" in decision["description"]
    minutes = schema["$defs"]["Help"]["properties"]["minutes"]
    assert "never estimate" in minutes["description"]
    assert "Do not extract tasks" in schema["description"]
