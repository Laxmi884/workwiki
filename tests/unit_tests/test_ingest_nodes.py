"""The code-only ingest nodes run on the fixture vault (read only) and on tmp copies."""

import shutil
from types import SimpleNamespace

import pytest

from llm_wiki.diffs import render_diffs, vault_reader
from llm_wiki.ingest import nodes
from llm_wiki.task_tracking import TASK_INDEX_PATH, load_task_index

NOTE = "00_raw/daily/2026-10-12.md"


def _run(vault, note_path=NOTE):
    """Run load_note → assign_ids → track_tasks, merging updates like the graph will."""
    runtime = SimpleNamespace(context={"vault_path": str(vault)})
    state = {"note_path": note_path, "warnings": []}
    for node in (nodes.load_note, nodes.assign_ids, nodes.track_tasks):
        update = node(state, runtime)
        warnings = state["warnings"] + update.pop("warnings", [])
        state |= update | {"warnings": warnings}
    return state


def _copy(fixture_vault, tmp_path):
    vault = tmp_path / "vault"
    shutil.copytree(fixture_vault, vault)
    (vault / "90_meta").mkdir()
    return vault


def test_fixture_note_gives_id_edits_and_a_new_task_index(fixture_vault):
    state = _run(fixture_vault)
    assert state["warnings"] == []
    assert len(state["note_sha256"]) == 64
    assert len(state["task_ids"]) == 10  # 12 tasks, 2 already have IDs

    diffs, warnings = render_diffs(state["task_edits"], vault_reader(fixture_vault))
    assert warnings == []
    note_diff, index_diff = diffs
    assert note_diff.path == NOTE
    added = [
        line for line in note_diff.unified_diff.splitlines() if line.startswith("+")
    ]
    assert sum(" ^t-" in line for line in added) == 10
    assert index_diff.path == TASK_INDEX_PATH
    assert index_diff.before_sha256 is None  # created on first ingest

    index, _ = load_task_index(index_diff.after_text)
    assert len(index.tasks) == 12
    assert index.tasks["t-q7m2xa"].project == "project-atlas"


def test_ids_already_used_in_other_notes_are_avoided(tmp_path, fixture_vault):
    vault = _copy(fixture_vault, tmp_path)
    taken = _run(vault)["task_ids"][12]  # the ID line 12 would get

    other = vault / "00_raw/daily/2026-10-01.md"
    other.write_text(f"## Tasks\n- [ ] old ^{taken}\n")
    assert taken not in _run(vault)["task_ids"].values()


def test_existing_index_is_updated_not_recreated(tmp_path, fixture_vault):
    vault = _copy(fixture_vault, tmp_path)
    index_edit = _run(vault)["task_edits"][-1]
    (vault / TASK_INDEX_PATH).write_text(index_edit.text)  # as if approved

    state = _run(vault, "00_raw/daily/2026-10-13.md")
    diffs, _ = render_diffs(state["task_edits"], vault_reader(vault))
    (index_diff,) = [d for d in diffs if d.path == TASK_INDEX_PATH]
    assert index_diff.before_sha256 is not None
    index, _ = load_task_index(index_diff.after_text)
    assert index.tasks["t-q7m2xa"].status.value == "done"  # open 10-12, done 10-13
    assert len(index.tasks) == 12 + 5  # 4 new IDs + carried t-r8n3wd


def test_broken_index_is_left_alone(tmp_path, fixture_vault):
    vault = _copy(fixture_vault, tmp_path)
    (vault / TASK_INDEX_PATH).write_text("{broken")
    state = _run(vault)
    assert all(e.path != TASK_INDEX_PATH for e in state["task_edits"])
    assert any("not tracked" in w for w in state["warnings"])


def test_missing_note_raises(fixture_vault):
    with pytest.raises(FileNotFoundError, match="note not found"):
        _run(fixture_vault, "00_raw/daily/1999-01-01.md")
