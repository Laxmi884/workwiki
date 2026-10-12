import datetime

from llm_wiki.frontmatter import split_frontmatter

DAILY = """\
---
type: daily
template: daily-v1
date: 2026-10-12
---

## Tasks
- [ ] Email Alex
"""


def test_splits_metadata_and_body():
    note = split_frontmatter(DAILY)
    assert note.error is None
    assert note.metadata == {
        "type": "daily",
        "template": "daily-v1",
        "date": datetime.date(2026, 10, 12),  # YAML turns dates into date objects
    }
    assert note.body == "\n## Tasks\n- [ ] Email Alex\n"
    assert note.body_start_line == 6


def test_body_lines_map_to_file_lines():
    note = split_frontmatter(DAILY)
    file_lines = DAILY.splitlines()
    for k, line in enumerate(note.body.splitlines()):
        assert file_lines[note.body_start_line + k - 1] == line


def test_leading_blank_lines_in_body_are_kept():
    note = split_frontmatter("---\na: 1\n---\n\n\n## Tasks\n")
    assert note.body == "\n\n## Tasks\n"
    assert note.body_start_line == 4


def test_no_frontmatter_whole_text_is_body():
    text = "## Tasks\n- [ ] Email Alex\n"
    note = split_frontmatter(text)
    assert (note.metadata, note.body, note.body_start_line, note.error) == (
        {},
        text,
        1,
        None,
    )


def test_empty_text():
    note = split_frontmatter("")
    assert (note.metadata, note.body, note.body_start_line, note.error) == (
        {},
        "",
        1,
        None,
    )


def test_empty_frontmatter():
    note = split_frontmatter("---\n---\nbody\n")
    assert (note.metadata, note.body, note.body_start_line, note.error) == (
        {},
        "body\n",
        3,
        None,
    )


def test_unrendered_template_keeps_body_and_reports_error():
    text = "---\ntype: daily\ndate: {{date:YYYY-MM-DD}}\n---\n## Tasks\n"
    note = split_frontmatter(text)
    assert note.metadata == {}
    assert note.error is not None and "invalid YAML" in note.error
    assert note.body == "## Tasks\n"
    assert note.body_start_line == 5


def test_unclosed_frontmatter_is_all_body():
    text = "---\ntype: daily\n## Tasks\n"
    note = split_frontmatter(text)
    assert note.metadata == {}
    assert note.body == text
    assert note.body_start_line == 1
    assert note.error is not None and "closing" in note.error


def test_frontmatter_that_is_not_a_mapping():
    note = split_frontmatter("---\n- a\n- b\n---\nbody\n")
    assert note.metadata == {}
    assert note.error is not None and "list" in note.error
    assert note.body == "body\n"


def test_windows_line_endings():
    note = split_frontmatter("---\r\ntype: daily\r\n---\r\nbody\r\n")
    assert note.metadata == {"type": "daily"}
    assert note.body == "body\r\n"
    assert note.body_start_line == 4


def test_byte_order_mark_is_ignored():
    note = split_frontmatter("﻿---\ntype: daily\n---\nbody\n")
    assert note.metadata == {"type": "daily"}
    assert note.body_start_line == 4
