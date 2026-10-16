"""3d: apply writes exactly the approved diffs, and nothing if a file changed."""

import pytest

from llm_wiki import apply as apply_mod
from llm_wiki.apply import ApplyConflict, ApplyError, apply_diffs, only_ids_added
from llm_wiki.domain.changes import (
    AppendId,
    FileDiff,
    ReplaceFile,
    UserText,
    sha256_text,
)
from llm_wiki.domain.facts import Decision, SectionFacts
from llm_wiki.ingest import nodes
from llm_wiki.runner import IngestRunner

from .scripted_model import BySection

PAGE = "10_projects/project-x.md"
NOTE = "00_raw/daily/2026-10-12.md"


def page_diff(before: str | None, after: str, path: str = PAGE) -> FileDiff:
    return FileDiff(
        path=path,
        before_sha256=None if before is None else sha256_text(before),
        after_text=after,
        unified_diff="",
        edits=(UserText(path=path, text=after),),
    )


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "10_projects").mkdir()
    (tmp_path / PAGE).write_text("# X\n")
    return tmp_path


def test_writes_only_approved_diffs(vault):
    change = page_diff("# X\n", "# X\n\n## Updates\n- a\n")
    new = page_diff(None, "# Y\n", "10_projects/project-y.md")
    assert apply_diffs(vault, [change, new], [new.digest]) == [new.path]
    assert (vault / PAGE).read_text() == "# X\n"
    assert (vault / new.path).read_text() == "# Y\n"


def test_a_changed_file_blocks_every_write(vault):
    change = page_diff("# X\n", "# X\n- a\n")
    new = page_diff(None, "# Y\n", "10_projects/project-y.md")
    (vault / PAGE).write_text("# X edited on the phone\n")
    with pytest.raises(ApplyConflict) as exc:
        apply_diffs(vault, [new, change], [new.digest, change.digest])
    assert exc.value.paths == (PAGE,)
    assert not (vault / new.path).exists(), "nothing is written on a conflict"


def test_a_new_file_must_still_be_missing(vault):
    with pytest.raises(ApplyConflict):
        apply_diffs(vault, [d := page_diff(None, "# Z\n")], [d.digest])


def test_applying_twice_is_a_no_op(vault):
    change = page_diff("# X\n", "# X\n- a\n")
    apply_diffs(vault, [change], [change.digest])
    assert apply_diffs(vault, [change], [change.digest]) == [PAGE]
    assert (vault / PAGE).read_text() == "# X\n- a\n"


def test_unknown_digest_is_refused(vault):
    with pytest.raises(ApplyError, match="match no proposed diff"):
        apply_diffs(vault, [], ["0" * 64])


def test_a_symlink_out_of_the_vault_is_refused(vault, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (vault / "20_people").symlink_to(outside)
    d = page_diff(None, "# P\n", "20_people/person-p.md")
    with pytest.raises(ApplyError, match="outside the vault"):
        apply_diffs(vault, [d], [d.digest])
    assert not (outside / "person-p.md").exists()


def test_raw_notes_only_get_task_ids(vault):
    (vault / "00_raw/daily").mkdir(parents=True)
    before = "## Tasks\n- [ ] a\n"
    (vault / NOTE).write_text(before)
    bad = FileDiff(
        path=NOTE,
        before_sha256=sha256_text(before),
        after_text="## Tasks\n- [x] a ^t-abc123\n",
        unified_diff="",
        edits=(AppendId(path=NOTE, line_no=2, task_id="t-abc123"),),
    )
    with pytest.raises(ApplyError, match="only get task IDs"):
        apply_diffs(vault, [bad], [bad.digest])
    assert (vault / NOTE).read_text() == before


@pytest.mark.parametrize(
    ("after", "ok"),
    [
        ("- [ ] a ^t-abc123\nb", True),
        ("- [ ] a\nb", True),
        ("- [x] a ^t-abc123\nb", False),
        ("- [ ] a ^t-ABC123\nb", False),
        ("- [ ] a ^t-abc123\nb\nc", False),
    ],
)
def test_only_ids_added(after, ok):
    assert only_ids_added("- [ ] a\nb", after) is ok


def test_ledger_is_written_last(vault, monkeypatch):
    order = []
    real = apply_mod._write
    monkeypatch.setattr(
        apply_mod, "_write", lambda t, text: (order.append(t.name), real(t, text))
    )
    ledger = FileDiff(
        path="90_meta/ingest-ledger.json",
        before_sha256=None,
        after_text="{}",
        unified_diff="",
        edits=(ReplaceFile(path="90_meta/ingest-ledger.json", text="{}"),),
    )
    page = page_diff("# X\n", "# X\n- a\n")
    apply_diffs(vault, [ledger, page], [ledger.digest, page.digest])
    assert order == ["project-x.md", "ingest-ledger.json"]


ANSWERS = {
    "Meetings": SectionFacts(
        decisions=[Decision(line=23, title="Keep the old BI tool until Q1")]
    )
}


@pytest.fixture
def runner(vault_copy, monkeypatch):
    model = BySection(dict(ANSWERS), SectionFacts())
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: model)
    return IngestRunner.in_memory(context={"vault_path": str(vault_copy)})


def test_approve_writes_and_the_note_is_then_skipped(runner, vault_copy):
    started = runner.start(NOTE)
    done = runner.resume(started.thread_id, {"action": "approve", "reject": ["f2"]})
    assert done.status == "done"
    rejected = next(f.path for f in started.review.files if f.id == "f2")
    assert rejected not in done.written and not (vault_copy / rejected).exists()
    assert "90_meta/ingest-ledger.json" in done.written
    assert "^t-" in (vault_copy / NOTE).read_text()
    assert runner.start(NOTE).status == "skipped"


def test_reject_writes_nothing(runner, vault_copy):
    before = (vault_copy / NOTE).read_text()
    done = runner.resume(runner.start(NOTE).thread_id, "reject")
    assert done.written == () and (vault_copy / NOTE).read_text() == before
    assert not (vault_copy / "90_meta/ingest-ledger.json").exists()


def test_page_changed_during_review_gives_a_fresh_review(runner, vault_copy):
    note_before = (vault_copy / NOTE).read_text()
    started = runner.start(NOTE)
    log = next(f for f in started.review.files if f.path.startswith("30_decisions"))
    page = vault_copy / log.path
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# Decisions\n\nwritten elsewhere meanwhile\n")

    again = runner.resume(started.thread_id, "approve")
    assert again.status == "review"
    assert any("nothing written" in w and log.path in w for w in again.warnings)
    # The page now exists, so render skips creating it again and says so.
    assert log.path not in [f.path for f in again.review.files]
    assert (vault_copy / NOTE).read_text() == note_before, "nothing was written"

    done = runner.resume(started.thread_id, "approve")
    assert done.status == "done" and NOTE in done.written
    assert (vault_copy / NOTE).read_text() != note_before  # task IDs added
    assert page.read_text() == "# Decisions\n\nwritten elsewhere meanwhile\n"


def test_note_changed_during_review_ends_without_writing(runner, vault_copy):
    started = runner.start(NOTE)
    note = vault_copy / NOTE
    edited = "- [ ] added on the phone\n" + note.read_text()
    note.write_text(edited)

    done = runner.resume(started.thread_id, "approve")
    assert done.status == "done" and done.written == ()
    assert any("run again" in w for w in done.warnings)
    assert note.read_text() == edited
