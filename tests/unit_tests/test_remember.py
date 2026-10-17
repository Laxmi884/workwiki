"""Remember (0009 R3): rules and aliases → checked, then proposed edits (pure)."""

import json

import pytest

from llm_wiki.diffs import render_diffs
from llm_wiki.domain.changes import RULES_PAGE, AddAlias, RememberRules
from llm_wiki.ingest.state import STORED_AS_JSON
from llm_wiki.remember import (
    RULES_PATH,
    check_aliases,
    check_rules,
    remember_edits,
)

PAGES = {
    "person-sam-ortiz": ("person-sam-ortiz", "Sam Ortiz", "Sam", "sammy"),
    "project-atlas": ("project-atlas", "Atlas", "atlas beta"),
}


def _vault(files):
    return lambda path: files.get(path)


def test_first_rule_creates_the_rules_file():
    read = _vault({})
    edits = remember_edits(["Don't  file\nlunch plans"], {}, read)
    assert edits == [RememberRules(lines=("- Don't file lunch plans",))]
    (diff,), warnings = render_diffs(edits, read)
    assert not warnings and diff.path == RULES_PATH and diff.before_sha256 is None
    assert diff.after_text == RULES_PAGE + "- Don't file lunch plans\n"


def test_later_rules_are_appended_and_repeats_skipped():
    rules = "# Rules\n\n## Rules\n- Don't file lunch plans\n\n## Mine\n- keep\n"
    read = _vault({RULES_PATH: rules})
    edits = remember_edits(
        [
            "Don't file lunch plans",
            "Atlas is the analytics app",
            "Atlas is the analytics app",
        ],
        {},
        read,
    )
    assert edits == [RememberRules(lines=("- Atlas is the analytics app",))]
    (diff,), _ = render_diffs(edits, read)
    assert diff.after_text == (
        "# Rules\n\n## Rules\n- Don't file lunch plans\n"
        "- Atlas is the analytics app\n\n## Mine\n- keep\n"
    )
    assert remember_edits(["Don't file lunch plans"], {}, read) == []


def test_an_approved_rule_survives_a_checkpoint_round_trip():
    read = _vault({})
    edits = remember_edits(["Don't file lunch plans"], {}, read)
    diffs, _ = render_diffs(edits, read)
    for field, value in (("agent_edits", edits), ("diffs", diffs)):
        codec = STORED_AS_JSON[field]
        stored = codec.dump_python(value, mode="json")
        assert codec.validate_python(json.loads(json.dumps(stored))) == value


def test_an_alias_is_an_aliases_edit_on_its_page():
    page = "---\ntitle: Atlas\naliases: [Atlas]\n---\n# Atlas\n"
    read = _vault({"10_projects/project-atlas.md": page})
    edits = remember_edits([], {" stakeholders ": "project-atlas"}, read)
    assert edits == [
        AddAlias(path="10_projects/project-atlas.md", alias="stakeholders")
    ]
    (diff,), _ = render_diffs(edits, read)
    assert (
        diff.after_text
        == "---\ntitle: Atlas\naliases: [Atlas, stakeholders]\n---\n# Atlas\n"
    )


@pytest.mark.parametrize(
    ("rules", "error"),
    [
        (["  "], "a rule is empty"),
        (["x" * 301], "longer than 300 characters"),
    ],
)
def test_bad_rules(rules, error):
    assert error in check_rules(rules)


def test_good_rules_and_aliases():
    assert check_rules(["Don't file lunch plans"]) is None
    assert check_aliases({"Ortiz": "person-sam-ortiz"}, PAGES) is None
    assert check_aliases({}, PAGES) is None


@pytest.mark.parametrize(
    ("aliases", "error"),
    [
        ({" ": "project-atlas"}, "an alias is empty"),
        ({"Zed": "project-zed"}, "'project-zed' is not a page"),
        (
            {"SAMMY": "person-sam-ortiz"},
            "'SAMMY' is already a name of person-sam-ortiz",
        ),
        ({"sam": "project-atlas"}, "'sam' already names person-sam-ortiz"),
        (
            {"Ortiz": "person-sam-ortiz", "ortiz": "project-atlas"},
            "'ortiz' is given twice",
        ),
    ],
)
def test_bad_aliases(aliases, error):
    assert error in check_aliases(aliases, PAGES)
