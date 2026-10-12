import random
import re

import pytest

from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.tasks_syntax import (
    append_id,
    new_task_id,
    parse_task_line,
    status_from_char,
)


@pytest.mark.parametrize(
    ("line", "indent", "status_char", "text", "task_id"),
    [
        # Status characters
        ("- [ ] Email Alex about Q3", "", " ", "Email Alex about Q3", None),
        ("- [x] Ship report", "", "x", "Ship report", None),
        ("- [X] Call dentist", "", "X", "Call dentist", None),
        ("- [/] Draft roadmap", "", "/", "Draft roadmap", None),
        ("- [-] Old idea", "", "-", "Old idea", None),
        ("- [>] Deferred to next week", "", ">", "Deferred to next week", None),
        # Bullets and indentation
        ("* [ ] Star bullet", "", " ", "Star bullet", None),
        ("+ [ ] Plus bullet", "", " ", "Plus bullet", None),
        ("1. [ ] Numbered bullet", "", " ", "Numbered bullet", None),
        ("    - [ ] Nested with spaces", "    ", " ", "Nested with spaces", None),
        ("\t- [ ] Nested with tab", "\t", " ", "Nested with tab", None),
        # Task IDs
        ("- [x] Ship report ^t-k3x9q2", "", "x", "Ship report", "t-k3x9q2"),
        (
            "- [ ] Pay rent 📅 2026-10-15 ^t-k3x9q2   ",
            "",
            " ",
            "Pay rent 📅 2026-10-15",
            "t-k3x9q2",
        ),
        ("- [ ] Ship report ^t-k3x9q2\n", "", " ", "Ship report", "t-k3x9q2"),
        # Look like IDs but are not: stay in the text
        (
            "- [ ] Mentions ^t-k3x9q2 mid-line",
            "",
            " ",
            "Mentions ^t-k3x9q2 mid-line",
            None,
        ),
        ("- [ ] Wrong length ^t-abc12", "", " ", "Wrong length ^t-abc12", None),
        ("- [ ] Uppercase ID ^t-ABC123", "", " ", "Uppercase ID ^t-ABC123", None),
        (
            "* [X] Call dentist (dr kim) [t-l01042]",
            "",
            "X",
            "Call dentist (dr kim) [t-l01042]",
            None,
        ),
    ],
)
def test_parses_task_lines(line, indent, status_char, text, task_id):
    parsed = parse_task_line(line)
    assert parsed is not None
    assert (parsed.indent, parsed.status_char, parsed.text, parsed.task_id) == (
        indent,
        status_char,
        text,
        task_id,
    )


@pytest.mark.parametrize(
    "line",
    [
        "",
        "Just a sentence",
        "- plain bullet",
        "[ ] no bullet",
        "## Tasks",
        "- [ ]",
        "- [ ]    ",
        "- [ ] ^t-k3x9q2",
        "-[ ] no space after bullet",
        "- [/ ] two characters in checkbox",
    ],
)
def test_non_task_lines_return_none(line):
    assert parse_task_line(line) is None


@pytest.mark.parametrize(
    ("char", "status"),
    [
        (" ", TaskStatus.OPEN),
        ("x", TaskStatus.DONE),
        ("X", TaskStatus.DONE),
        ("/", TaskStatus.IN_PROGRESS),
        ("-", TaskStatus.CANCELLED),
        (">", TaskStatus.OPEN),  # unknown chars count as open
    ],
)
def test_status_from_char(char, status):
    assert status_from_char(char) is status


def test_new_task_id_format():
    assert re.fullmatch(r"t-[0-9a-z]{6}", new_task_id(set()))


def test_new_task_id_is_repeatable_with_seed():
    assert new_task_id(set(), random.Random(42)) == new_task_id(
        set(), random.Random(42)
    )


def test_new_task_id_skips_existing_ids():
    first = new_task_id(set(), random.Random(0))
    second = new_task_id({first}, random.Random(0))
    assert second != first
    assert re.fullmatch(r"t-[0-9a-z]{6}", second)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("- [ ] Email Alex", "- [ ] Email Alex ^t-k3x9q2"),
        ("    1. [/] Draft roadmap", "    1. [/] Draft roadmap ^t-k3x9q2"),
        ("\t* [X] Call dentist", "\t* [X] Call dentist ^t-k3x9q2"),
        ("- [ ] Pay rent 📅 2026-10-15", "- [ ] Pay rent 📅 2026-10-15 ^t-k3x9q2"),
        ("- [ ] Pay rent   ", "- [ ] Pay rent ^t-k3x9q2"),
        ("- [ ] Pay rent\n", "- [ ] Pay rent ^t-k3x9q2\n"),
        ("- [ ] Pay rent  \r\n", "- [ ] Pay rent ^t-k3x9q2\r\n"),
        (
            "- [ ] Mentions ^t-aaaaaa mid-line",
            "- [ ] Mentions ^t-aaaaaa mid-line ^t-k3x9q2",
        ),
    ],
)
def test_append_id(line, expected):
    assert append_id(line, "t-k3x9q2") == expected


@pytest.mark.parametrize(
    "line",
    [
        "- [ ] Email Alex",
        "    1. [/] Draft roadmap",
        "- [>] Deferred  ",
        "- [ ] Mentions ^t-aaaaaa mid-line",
    ],
)
def test_append_id_changes_nothing_but_the_id(line):
    before = parse_task_line(line)
    after = parse_task_line(append_id(line, "t-k3x9q2"))
    assert after is not None and before is not None
    assert after.task_id == "t-k3x9q2"
    assert (after.indent, after.status_char, after.text) == (
        before.indent,
        before.status_char,
        before.text,
    )


@pytest.mark.parametrize(
    ("line", "task_id"),
    [
        ("## Tasks", "t-k3x9q2"),  # not a task
        ("- plain bullet", "t-k3x9q2"),  # not a task
        ("- [ ] Ship report ^t-aaaaaa", "t-k3x9q2"),  # already has an ID
        ("- [ ] Ship report", "^t-k3x9q2"),  # caret belongs to the syntax, not the ID
        ("- [ ] Ship report", "t-abc"),  # wrong length
        ("- [ ] Ship report", "T-K3X9Q2"),  # uppercase
    ],
)
def test_append_id_rejects(line, task_id):
    with pytest.raises(ValueError):
        append_id(line, task_id)
