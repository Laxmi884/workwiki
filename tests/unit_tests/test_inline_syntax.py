import pytest

from llm_wiki.inline_syntax import find_links, find_tags


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ship [[project-atlas]] beta", ("project-atlas",)),
        ("Ask [[person-sam-ortiz|Sam]]", ("person-sam-ortiz",)),  # alias
        ("See [[project-atlas#Risks]]", ("project-atlas",)),  # heading
        ("See [[project-atlas^t-k3x9q2]]", ("project-atlas",)),  # block
        ("[[ project-atlas ]]", ("project-atlas",)),  # spaces trimmed
        ("[[a]] then [[b]] then [[a]]", ("a", "b")),  # order kept, each once
        ("![[diagram.png]]", ()),  # embed, not a link
        ("`[[not-a-link]]`", ()),  # inline code
        ("[[#Heading in this note]]", ()),  # no target
        ("[single] brackets", ()),
        ("", ()),
    ],
)
def test_find_links(text, expected):
    assert find_links(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Root cause found #win", ("win",)),
        ("#Decision keep BI tool", ("decision",)),  # lowercased
        ("#blocked waiting on #legal-review", ("blocked", "legal-review")),
        ("#win and #WIN again", ("win",)),  # each once
        ("nested #area/infra tag", ("area/infra",)),
        ("done #win.", ("win",)),  # trailing punctuation
        ("(#win)", ("win",)),
        ("fixes issue #42", ()),  # digits only
        ("#q3 planning", ("q3",)),
        ("see `#not-a-tag` in code", ()),
        ("https://x.test/#anchor", ()),
        ("[[project-atlas#Risks]]", ()),  # heading link, not a tag
        ("C# and a#b", ()),  # glued to a word
        ("## Heading", ()),
        ("it&#39;s", ()),  # HTML entity
        ("", ()),
    ],
)
def test_find_tags(text, expected):
    assert find_tags(text) == expected
