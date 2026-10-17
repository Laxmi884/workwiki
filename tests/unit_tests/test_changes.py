import pydantic
import pytest

from llm_wiki.domain.changes import (
    AppendId,
    AppendToSection,
    CreateFile,
    FileDiff,
    ReplaceFile,
    sha256_text,
)

NOTE = "00_raw/daily/2026-10-12.md"
SAM = "20_people/person-sam-ortiz.md"


# --- where each edit may write --------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/Users/x/Work/20_people/a.md",  # absolute
        "20_people/../00_raw/daily/a.md",  # traversal
        "./20_people/a.md",
        "20_people//a.md",
        "20_people\\a.md",
        "",
    ],
)
def test_unclean_paths_are_rejected(path):
    with pytest.raises(pydantic.ValidationError, match="vault-relative"):
        CreateFile(path=path, text="")


@pytest.mark.parametrize(
    "path",
    [
        "00_raw/daily/2026-10-12.md",  # raw notes: only AppendId
        "50_templates/daily.md",
        ".obsidian/app.json",
        "index.md",
        "20_people/person-sam-ortiz.txt",  # markdown only
    ],
)
def test_agent_writes_only_to_agent_folders(path):
    with pytest.raises(pydantic.ValidationError, match="may not write"):
        AppendToSection(path=path, heading="## Log", lines=("x",))
    with pytest.raises(pydantic.ValidationError, match="may not write"):
        CreateFile(path=path, text="x")


def test_append_id_only_into_raw_daily_notes():
    assert AppendId(path=NOTE, line_no=12, task_id="t-4kq9za").kind == "append_id"
    with pytest.raises(pydantic.ValidationError, match="task IDs go only"):
        AppendId(path=SAM, line_no=3, task_id="t-4kq9za")
    with pytest.raises(pydantic.ValidationError):
        AppendId(path=NOTE, line_no=12, task_id="T-ABC")  # malformed ID


def test_appended_lines_must_be_single_lines():
    with pytest.raises(pydantic.ValidationError, match="single line"):
        AppendToSection(path=SAM, heading="## Log", lines=("a\nb",))
    with pytest.raises(pydantic.ValidationError):
        AppendToSection(path=SAM, heading="Log", lines=("a",))  # not a heading


# --- edits round-trip as plain dicts (graph state, JSON) -------------------------


def test_edits_parse_from_dicts_by_kind():
    diff = FileDiff.model_validate(
        {
            "path": SAM,
            "before_sha256": None,
            "after_text": "x",
            "unified_diff": "",
            "edits": [
                {"kind": "create_file", "path": SAM, "text": "# Sam Ortiz\n"},
                {
                    "kind": "append_to_section",
                    "path": SAM,
                    "heading": "## Log",
                    "lines": ["- 2026-10-12: wants to own search"],
                },
            ],
        }
    )
    assert [type(e) for e in diff.edits] == [CreateFile, AppendToSection]
    assert FileDiff.model_validate(diff.model_dump(exclude={"digest"})) == diff


# --- digest ----------------------------------------------------------------------


def _diff(before, after, path=SAM):
    return FileDiff(
        path=path,
        before_sha256=before,
        after_text=after,
        unified_diff="",
        edits=(CreateFile(path=path, text=after),),
    )


def test_digest_binds_path_before_and_after():
    before = sha256_text("old\n")
    base = _diff(before, "new\n")
    assert base.digest == _diff(before, "new\n").digest  # stable
    variants = {
        base.digest,
        _diff(before, "new!\n").digest,  # different result
        _diff(sha256_text("other\n"), "new\n").digest,  # file changed since
        _diff(None, "new\n").digest,  # new file vs existing
        _diff(before, "new\n", path="20_people/person-x.md").digest,  # other file
    }
    assert len(variants) == 5


def test_digest_is_in_the_dump():
    assert "digest" in _diff(None, "x").model_dump()


def test_diff_needs_at_least_one_edit():
    with pytest.raises(pydantic.ValidationError):
        FileDiff(path=SAM, before_sha256=None, after_text="", unified_diff="", edits=())


# --- ReplaceFile ----------------------------------------------------------------


def test_replace_file_only_for_code_owned_files():
    assert ReplaceFile(path="90_meta/tasks.json", text="{}").kind == "replace_file"
    assert ReplaceFile(path="05_generated/open-tasks.md", text="# Open tasks\n")
    for path in (
        "90_meta/notes.md",
        "20_people/person-sam.json",
        "00_raw/x.json",
        "05_generated/brief.md",  # only the open-tasks page is code-owned there
    ):
        with pytest.raises(pydantic.ValidationError, match="whole-file rewrites"):
            ReplaceFile(path=path, text="{}")


def test_the_open_tasks_page_is_not_editable_in_the_review():
    from llm_wiki.domain.changes import is_wiki_page

    assert not is_wiki_page("05_generated/open-tasks.md")
    assert is_wiki_page("05_generated/brief.md")
