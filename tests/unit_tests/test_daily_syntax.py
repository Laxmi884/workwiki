from llm_wiki.daily_syntax import split_sections
from llm_wiki.domain.daily import SectionKind
from llm_wiki.frontmatter import split_frontmatter

NOTE = """\
---
type: daily
date: 2026-10-12
---

## Tasks
- [ ] Email Alex

## Meetings
### Roadmap sync with [[person-alex-chen]]
- Agreed to ship v2 #decision
## Random thoughts
- coffee machine broken
"""

# Same text as ~/Work/50_templates/daily.md (template only, no personal data).
TEMPLATE = """\
---
type: daily
template: daily-v1
date: 2026-10-12
---

## Carried over

## Tasks
- [ ]

## Meetings
### Topic with [[person-name]]
-

## Log
-

## Helped
-

## Learned
-
"""


def _sections(text):
    split = split_frontmatter(text)
    return split_sections(split.body, split.body_start_line)


def test_splits_example_note():
    sections = _sections(NOTE)
    assert [(s.kind, s.heading, s.start_line) for s in sections] == [
        (SectionKind.TASKS, "Tasks", 6),
        (SectionKind.MEETINGS, "Meetings", 9),
        (SectionKind.LOG, "Random thoughts", 12),
    ]
    assert sections[0].lines == ("## Tasks", "- [ ] Email Alex", "")
    assert sections[1].lines[1] == "### Roadmap sync with [[person-alex-chen]]"


def test_every_section_line_maps_to_its_file_line():
    for text in (NOTE, TEMPLATE):
        file_lines = text.splitlines()
        for section in _sections(text):
            for i, line in enumerate(section.lines):
                assert file_lines[section.start_line + i - 1] == line


def test_template_gives_six_sections_in_order():
    sections = _sections(TEMPLATE)
    assert [s.kind for s in sections] == [
        SectionKind.CARRIED_OVER,
        SectionKind.TASKS,
        SectionKind.MEETINGS,
        SectionKind.LOG,
        SectionKind.HELPED,
        SectionKind.LEARNED,
    ]
    # The empty "- [ ] " line stays in the section; 1b-3 drops it. Compare stripped:
    # editors trim trailing spaces, so this file can't rely on them.
    assert "- [ ]" in [line.strip() for line in sections[1].lines]


def test_preamble_becomes_log_section():
    sections = split_sections("Morning, slept badly\n## Tasks\n- [ ] a\n")
    assert [(s.kind, s.heading, s.start_line) for s in sections] == [
        (SectionKind.LOG, "", 1),
        (SectionKind.TASKS, "Tasks", 2),
    ]


def test_blank_preamble_is_dropped():
    sections = split_sections("\n\n## Tasks\n")
    assert [(s.kind, s.start_line) for s in sections] == [(SectionKind.TASKS, 3)]


def test_heading_matching_ignores_case_spacing_and_closing_hashes():
    body = "## tasks\n##   Carried   Over  \n## LEARNED ##\n"
    assert [s.kind for s in split_sections(body)] == [
        SectionKind.TASKS,
        SectionKind.CARRIED_OVER,
        SectionKind.LEARNED,
    ]
    assert [s.heading for s in split_sections(body)] == [
        "tasks",
        "Carried   Over",
        "LEARNED",
    ]


def test_not_level_two_headings_stay_inside_section():
    body = "## Tasks\n#Tasks\n### Sub\n#### Deep\n# Title\n  ## indented\n"
    sections = split_sections(body)
    assert len(sections) == 1
    assert len(sections[0].lines) == 6


def test_duplicate_sections_are_kept_in_order():
    body = "## Tasks\n- [ ] a\n## Log\n- x\n## Tasks\n- [ ] b\n"
    sections = split_sections(body)
    assert [(s.kind, s.start_line) for s in sections] == [
        (SectionKind.TASKS, 1),
        (SectionKind.LOG, 3),
        (SectionKind.TASKS, 5),
    ]


def test_heading_inside_code_fence_is_not_a_section():
    body = "## Log\n```bash\n## not a heading\n```\n~~~\n## nor this\n~~~\n## Tasks\n"
    sections = split_sections(body)
    assert [(s.kind, s.start_line) for s in sections] == [
        (SectionKind.LOG, 1),
        (SectionKind.TASKS, 8),
    ]


def test_empty_section_is_kept():
    sections = split_sections("## Helped\n## Tasks\n")
    assert [s.lines for s in sections] == [("## Helped",), ("## Tasks",)]


def test_empty_body():
    assert split_sections("") == []
