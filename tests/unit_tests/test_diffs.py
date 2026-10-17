import pytest

from llm_wiki import diffs
from llm_wiki.diffs import RenderError, apply_edit, render_diffs, vault_reader
from llm_wiki.domain.changes import (
    AddAlias,
    AppendId,
    AppendToSection,
    CreateFile,
    ReplaceFile,
    sha256_text,
)

NOTE = "00_raw/daily/2026-10-12.md"
SAM = "20_people/person-sam-ortiz.md"
NEW = "20_people/person-jordan-lee.md"


def _append(text, heading="## Log", lines=("- new",), path=SAM):
    return apply_edit(text, AppendToSection(path=path, heading=heading, lines=lines))


# --- AppendToSection -------------------------------------------------------------


def test_appends_after_last_line_of_section_keeping_the_gap():
    text = "# Sam\n\n## Log\n- old\n\n## Projects\n- atlas\n"
    assert _append(text) == "# Sam\n\n## Log\n- old\n- new\n\n## Projects\n- atlas\n"


def test_subheadings_stay_inside_the_section():
    text = "## Log\n### 2026-10\n- old\n## Next\n"
    assert _append(text) == "## Log\n### 2026-10\n- old\n- new\n## Next\n"


def test_empty_section_gets_lines_right_after_heading():
    assert _append("## Log\n\n## Next\n") == "## Log\n- new\n\n## Next\n"


def test_missing_heading_is_added_at_the_end():
    assert _append("# Sam\nBio.") == "# Sam\nBio.\n\n## Log\n- new\n"
    assert _append("") == "## Log\n- new\n"


def test_heading_match_ignores_case_and_spacing():
    assert _append("##   log  \n- old\n") == "##   log  \n- old\n- new\n"


def test_heading_inside_code_fence_is_not_a_section():
    text = "```\n## Log\n```\n"
    assert _append(text) == "```\n## Log\n```\n\n## Log\n- new\n"


def test_crlf_files_stay_crlf():
    assert _append("## Log\r\n- old\r\n") == "## Log\r\n- old\r\n- new\r\n"


def test_last_line_without_newline():
    assert _append("## Log\n- old") == "## Log\n- old\n- new\n"


# --- AppendId --------------------------------------------------------------------


def _append_id(text, line_no, task_id="t-4kq9za"):
    return apply_edit(text, AppendId(path=NOTE, line_no=line_no, task_id=task_id))


def test_append_id_changes_only_that_line():
    text = "## Tasks\n- [ ] Ship it  \n- [ ] Other\n"
    assert _append_id(text, 2) == "## Tasks\n- [ ] Ship it ^t-4kq9za\n- [ ] Other\n"


@pytest.mark.parametrize(
    ("text", "line_no", "message"),
    [
        ("## Tasks\n", 1, "not a task line"),
        ("- [ ] a ^t-aaaaaa\n", 1, "already has ID"),
        ("- [ ] a\n", 5, "outside the file"),
    ],
)
def test_append_id_refuses(text, line_no, message):
    with pytest.raises(RenderError, match=message):
        _append_id(text, line_no)


def test_raw_note_guard_catches_any_other_change(monkeypatch):
    """Defence in depth: even if append_id misbehaved, the raw-note check refuses."""
    monkeypatch.setattr(diffs, "append_id", lambda line, _id: line.upper())
    out, warnings = render_diffs(
        [AppendId(path=NOTE, line_no=1, task_id="t-4kq9za")],
        {NOTE: "- [ ] ship\n"}.get,
    )
    assert out == []
    assert "changed beyond adding a task ID" in warnings[0]


# --- CreateFile and batches ------------------------------------------------------


def test_create_then_append_in_one_batch_regardless_of_order():
    edits = [
        AppendToSection(path=NEW, heading="## Log", lines=("- met",)),
        CreateFile(path=NEW, text="# Jordan Lee\n"),
    ]
    (diff,), warnings = render_diffs(edits, {}.get)
    assert warnings == []
    assert diff.before_sha256 is None
    assert diff.after_text == "# Jordan Lee\n\n## Log\n- met\n"
    assert diff.unified_diff.startswith("--- /dev/null\n+++ b/" + NEW)
    assert [e.kind for e in diff.edits] == ["create_file", "append_to_section"]


def test_one_bad_file_does_not_stop_the_others():
    alex = "20_people/person-alex.md"
    edits = [
        CreateFile(path=SAM, text="dup"),  # Sam's page exists
        AppendToSection(path=NEW, heading="## Log", lines=("x",)),  # Jordan doesn't
        AppendToSection(path=alex, heading="## Log", lines=("x",)),
    ]
    read = {SAM: "# Sam\n", alex: "# Alex\n"}.get
    out, warnings = render_diffs(edits, read)
    assert [d.path for d in out] == [alex]
    assert warnings == [
        f"{SAM}: file already exists; skipped",
        f"{NEW}: file does not exist; skipped",
    ]


# --- on the fixture vault (read only) --------------------------------------------


def test_fixture_note_and_person_page(fixture_vault):
    read = vault_reader(fixture_vault)
    edits = [
        AppendId(path=NOTE, line_no=12, task_id="t-4kq9za"),
        AppendToSection(
            path=SAM,
            heading="## Log",
            lines=("- 2026-10-12: wants to own the search feature ([[2026-10-12]])",),
        ),
        AppendId(path=NOTE, line_no=17, task_id="t-8mw2pd"),
    ]
    (note_diff, sam_diff), warnings = render_diffs(edits, read)
    assert warnings == []

    before = read(NOTE)
    assert note_diff.before_sha256 == sha256_text(before)
    pairs = zip(before.splitlines(), note_diff.after_text.splitlines(), strict=True)
    changed = [(n, b) for n, (a, b) in enumerate(pairs, 1) if a != b]
    assert changed == [
        (12, "- [ ] Ship [[project-atlas]] beta to internal users ^t-4kq9za"),
        (17, "- [ ] Book 1:1 with [[person-sam-ortiz]] ^t-8mw2pd"),
    ]
    assert len(note_diff.edits) == 2

    assert sam_diff.after_text.endswith(
        "interested in search.\n\n## Log\n"
        "- 2026-10-12: wants to own the search feature ([[2026-10-12]])\n"
    )
    assert "+## Log" in sam_diff.unified_diff


# --- vault_reader ----------------------------------------------------------------


def test_reader_keeps_crlf_and_reports_missing(tmp_path):
    (tmp_path / "20_people").mkdir()
    (tmp_path / SAM).write_bytes(b"# Sam\r\n")
    read = vault_reader(tmp_path)
    assert read(SAM) == "# Sam\r\n"
    assert read(NEW) is None


def test_reader_refuses_symlink_out_of_vault(tmp_path):
    vault, outside = tmp_path / "vault", tmp_path / "secret.md"
    (vault / "20_people").mkdir(parents=True)
    outside.write_text("secret")
    (vault / SAM).symlink_to(outside)
    with pytest.raises(RenderError, match="outside the vault"):
        vault_reader(vault)(SAM)


def test_replace_file_creates_or_overwrites():
    edit = ReplaceFile(path="90_meta/tasks.json", text='{"version": 1}\n')
    assert apply_edit(None, edit) == '{"version": 1}\n'
    assert apply_edit("old", edit) == '{"version": 1}\n'


# --- AddAlias (review answers) ----------------------------------------------------

PAGE = "20_people/person-sam-ortiz.md"


# Only the aliases lines change; every other byte of the page stays (comments, quoting,
# key order), so the review's diff shows exactly the added name.
@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("aliases: [Sam]\n", "aliases: [Sam, Sammy]\n"),
        ("aliases: [ ]\n", "aliases: [Sammy]\n"),
        ("aliases: [Sam]  # nicknames\n", "aliases: [Sam, Sammy]  # nicknames\n"),
        ("aliases:\n  - Sam\n  - S.O.\n", "aliases:\n  - Sam\n  - S.O.\n  - Sammy\n"),
        ("aliases:\n- Sam\n", "aliases:\n- Sam\n- Sammy\n"),
        ("aliases: Sam\n", "aliases: [Sam, Sammy]\n"),  # one string
        ("aliases:\n", "aliases:\n  - Sammy\n"),  # key, no value
    ],
)
def test_add_alias_changes_only_the_aliases(before, after):
    head = '---\ntitle: "Sam Ortiz"   # full name\n'
    tail = "status: active\n---\n# Sam\n\nNotes.\n"
    text = head + before + tail
    got = apply_edit(text, AddAlias(path=PAGE, alias="Sammy"))
    assert got == head + after + tail


def test_add_alias_without_the_key_adds_it_last():
    text = "---\ntitle: Sam  # me\n---\n# Sam\n"
    got = apply_edit(text, AddAlias(path=PAGE, alias="Sammy"))
    assert got == "---\ntitle: Sam  # me\naliases:\n  - Sammy\n---\n# Sam\n"


def test_add_alias_without_frontmatter_adds_it():
    got = apply_edit("# Sam\n", AddAlias(path=PAGE, alias="Sammy"))
    assert got == "---\naliases:\n  - Sammy\n---\n# Sam\n"


def test_add_alias_quotes_what_yaml_needs_and_keeps_crlf():
    text = "---\r\naliases: [Sam]\r\n---\r\n# Sam\r\n"
    got = apply_edit(text, AddAlias(path=PAGE, alias="Sam: the PM, again"))
    assert got == "---\r\naliases: [Sam, 'Sam: the PM, again']\r\n---\r\n# Sam\r\n"


def test_add_alias_already_there_changes_nothing():
    text = "---\naliases: [sammy]\n---\n# Sam\n"
    assert apply_edit(text, AddAlias(path=PAGE, alias="Sammy")) == text


@pytest.mark.parametrize(
    "front",
    [
        ": [\n",  # broken YAML
        "aliases: [Sam,\n  S.O.]\n",  # a flow list over two lines
        "aliases: Sam  # nick\n",  # a commented scalar
    ],
)
def test_add_alias_refuses_what_it_cant_edit_safely(front):
    with pytest.raises(RenderError, match="can't add alias"):
        apply_edit(f"---\n{front}---\n# Sam\n", AddAlias(path=PAGE, alias="Sammy"))


def test_replace_section_replaces_body_or_adds_it_after_the_title():
    from llm_wiki.diffs import apply_edit
    from llm_wiki.domain.changes import ReplaceSection

    page = "---\ntype: project\n---\n# Atlas\n\nIntro.\n\n## Updates\n- a\n"
    edit = ReplaceSection(
        path="10_projects/project-atlas.md",
        heading="## Current state",
        lines=("GA Dec 9.",),
    )
    added = apply_edit(page, edit)
    assert added == (
        "---\ntype: project\n---\n# Atlas\n\n## Current state\nGA Dec 9.\n"
        "\nIntro.\n\n## Updates\n- a\n"
    )
    again = apply_edit(added, edit.model_copy(update={"lines": ("GA Dec 16.",)}))
    assert "GA Dec 16." in again and "GA Dec 9." not in again
    assert again.count("## Current state") == 1 and again.endswith("## Updates\n- a\n")
