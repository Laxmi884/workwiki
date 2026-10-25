"""Daily-note rollover (decision 0001, write 2): code-only, create-if-absent, IDs intact."""

import datetime
import shutil
import subprocess

import pytest

from llm_wiki import rollover
from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.domain.changes import sha256_text
from llm_wiki.domain.common import SectionKind
from llm_wiki.domain.ledger import IngestLedger, LedgerEntry
from llm_wiki.domain.task_index import TaskIndex, TaskRecord
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.entity_index import load_index
from llm_wiki.ledger import LEDGER_PATH, dump_ledger
from llm_wiki.rollover import RolloverError, main, roll_over
from llm_wiki.task_tracking import TASK_INDEX_PATH, dump_task_index, update_task_index

DAY = datetime.date(2026, 10, 19)
NOTE = "00_raw/daily/2026-10-19.md"
OPEN, DOING, DONE = TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.DONE


@pytest.fixture
def vault(tmp_path, fixture_vault):
    """Template and entity pages from the fixture vault; no notes, no index yet."""
    root = tmp_path / "vault"
    for rel in (
        "50_templates/daily.md",
        "10_projects/project-atlas.md",
        "20_people/person-priya-nair.md",
    ):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_vault / rel, root / rel)
    return root


def _task(text, status=OPEN, project=None, first="2026-10-16", line=5, **more):
    first_seen = datetime.date.fromisoformat(first)
    return TaskRecord(
        text=text,
        status=status,
        project=project,
        mapped_by="link" if project else None,
        first_seen=first_seen,
        last_seen=first_seen,
        source=f"00_raw/daily/{first}.md",
        line=line,
        **more,
    )


def _write(vault, rel, text):
    (vault / rel).parent.mkdir(parents=True, exist_ok=True)
    (vault / rel).write_text(text, encoding="utf-8")


def _index(vault, **tasks):
    _write(vault, TASK_INDEX_PATH, dump_task_index(TaskIndex(tasks=tasks)))


def _ingested(vault, *rels):
    """Record notes in the ledger at their current text, as ingest would."""
    now = datetime.datetime(2026, 10, 18, tzinfo=datetime.UTC)
    notes = {
        rel: LedgerEntry(sha256=sha256_text((vault / rel).read_text()), ingested_at=now)
        for rel in rels
    }
    _write(vault, LEDGER_PATH, dump_ledger(IngestLedger(notes=notes)))


def _carried_section(text):
    return text.split("## Carried over\n", 1)[1].split("\n## ", 1)[0]


# --- the note ----------------------------------------------------------------------


def test_creates_the_note_with_open_tasks_grouped_by_project(vault):
    _index(
        vault,
        **{
            "t-aaaaaa": _task("Ship the export", project="project-atlas"),
            "t-bbbbbb": _task("Send UBM modernization", people=("person-priya-nair",)),
            "t-cccccc": _task("Write release notes", DOING, "project-atlas", line=6),
            "t-dddddd": _task("Closed already", DONE),
            "t-eeeeee": _task("Ask about #blocked CI", first="2026-10-17"),
        },
    )
    result = roll_over(vault, DAY, commit=False)

    text = (vault / NOTE).read_text()
    assert _carried_section(text) == (
        "### Atlas\n"
        "- [ ] Ship the export ^t-aaaaaa\n"
        "- [/] Write release notes ^t-cccccc\n"
        "### No project\n"
        "- [ ] Send UBM modernization ^t-bbbbbb\n"
        "- [ ] Ask about #blocked CI ^t-eeeeee\n"
    )
    assert "date: 2026-10-19\n" in text and "{{" not in text
    assert text.endswith("## Learned\n- \n")  # the rest of the template as it was
    assert result.created and result.carried == (
        "t-aaaaaa",
        "t-cccccc",
        "t-bbbbbb",
        "t-eeeeee",
    )
    assert (result.stale, result.commit, result.warnings) == ((), None, ())


def test_ingesting_the_new_note_keeps_project_people_and_status(vault):
    old = {
        "t-aaaaaa": _task("Ship the export", project="project-atlas"),
        "t-bbbbbb": _task("Send UBM", DOING, people=("person-priya-nair",)),
    }
    _index(vault, **old)
    roll_over(vault, DAY, commit=False)
    note = parse_daily_note(NOTE, (vault / NOTE).read_text())
    assert (note.date, note.warnings) == (DAY, ())
    assert {t.section for t in note.tasks if t.id} == {SectionKind.CARRIED_OVER}

    index, _ = update_task_index(TaskIndex(tasks=old), note, {}, load_index(vault))
    for task_id, before in old.items():
        after = index.tasks[task_id]
        assert (after.project, after.people, after.status, after.text) == (
            before.project,
            before.people,
            before.status,
            before.text,
        )
        assert (after.source, after.last_seen) == (NOTE, DAY)


def test_no_open_tasks_gives_the_plain_template(vault, fixture_vault):
    result = roll_over(vault, DAY, commit=False)  # no index at all: empty
    template = (fixture_vault / "50_templates/daily.md").read_text()
    expected = template.replace("{{date:YYYY-MM-DD}}", "2026-10-19")
    assert (vault / NOTE).read_text() == expected
    assert result.carried == ()


def test_never_overwrites_an_existing_note(vault):
    _index(vault, **{"t-aaaaaa": _task("Ship the export")})
    _write(vault, NOTE, "my own note\n")
    result = roll_over(vault, DAY, commit=False)
    assert (vault / NOTE).read_text() == "my own note\n"
    assert (result.created, result.carried) == (False, ())


def test_tasks_open_more_than_14_days_are_stale_not_carried(vault):
    _index(
        vault,
        **{
            "t-aaaaaa": _task("Fourteen days", first="2026-10-05"),
            "t-bbbbbb": _task("Fifteen days", first="2026-10-04"),
            "t-cccccc": _task("Done long ago", DONE, first="2026-09-01"),
        },
    )
    result = roll_over(vault, DAY, commit=False)
    assert result.carried == ("t-aaaaaa",)
    assert [(tid, t.text) for tid, t in result.stale] == [("t-bbbbbb", "Fifteen days")]
    assert "t-bbbbbb" not in (vault / NOTE).read_text()


# --- the index may be behind the notes ----------------------------------------------


def test_a_note_changed_since_ingest_is_read_by_id(vault):
    _index(
        vault,
        **{
            "t-aaaaaa": _task("Ship the export", first="2026-10-17"),
            "t-bbbbbb": _task("Still open", first="2026-10-17", line=6),
        },
    )
    note = "00_raw/daily/2026-10-17.md"
    _write(
        vault,
        note,
        "## Tasks\n- [ ] Ship the export ^t-aaaaaa\n- [ ] Still open ^t-bbbbbb\n",
    )
    _ingested(vault, note)
    _write(  # then ticked one and added one, no ingest since
        vault,
        note,
        "## Tasks\n- [x] Ship the export ^t-aaaaaa\n- [ ] Still open ^t-bbbbbb\n"
        "- [ ] A new task\n",
    )
    index_before = (vault / TASK_INDEX_PATH).read_text()
    result = roll_over(vault, DAY, commit=False)
    assert result.carried == ("t-bbbbbb",)  # the tick was read, no ingest needed
    assert result.warnings == (
        "00_raw/daily/2026-10-17.md changed since its last ingest; its ticks were "
        "read, but 1 new task(s) have no ID yet: ingest it to carry them",
    )
    assert (vault / TASK_INDEX_PATH).read_text() == index_before  # index untouched


def test_notes_on_or_after_the_day_are_not_read(vault):
    _index(vault, **{"t-aaaaaa": _task("Ship the export")})
    _write(vault, "00_raw/daily/2026-10-20.md", "## Tasks\n- [x] Ship it ^t-aaaaaa\n")
    result = roll_over(vault, DAY, commit=False)
    assert (result.carried, result.warnings) == (("t-aaaaaa",), ())


def test_a_note_never_ingested_is_read_and_says_so(vault):
    _index(vault, **{"t-aaaaaa": _task("Ship the export")})
    _write(vault, "00_raw/daily/2026-10-18.md", "## Tasks\n- [x] Ship it ^t-aaaaaa\n")
    result = roll_over(vault, DAY, commit=False)
    assert result.carried == ()
    assert result.warnings == (
        "00_raw/daily/2026-10-18.md not ingested yet; its ticks were read",
    )


# --- the template -------------------------------------------------------------------


def test_placeholders_obsidian_uses_are_filled_others_left_with_a_warning(vault):
    _write(
        vault,
        rollover.TEMPLATE_PATH,
        "---\ndate: {{date}}\n---\n# {{date:dddd, MMMM DD}} / {{title}} {{time}} "
        "{{date:Do}}\n## Carried over\n",
    )
    result = roll_over(vault, DAY, commit=False)
    text = (vault / NOTE).read_text()
    assert text.startswith(
        "---\ndate: 2026-10-19\n---\n# Monday, October 19 / 2026-10-19 {{time}} "
        "{{date:Do}}\n"
    )
    assert result.warnings == (
        "50_templates/daily.md: left {{time}} as written",
        "50_templates/daily.md: left {{date:Do}} as written",
    )


def test_a_template_without_carried_over_refuses_and_writes_nothing(vault):
    _index(vault, **{"t-aaaaaa": _task("Ship the export")})
    _write(vault, rollover.TEMPLATE_PATH, "## Tasks\n- [ ] \n")
    with pytest.raises(RolloverError, match="no '## Carried over' heading"):
        roll_over(vault, DAY, commit=False)
    assert not (vault / NOTE).exists()


def test_carried_over_heading_found_case_insensitively_and_not_in_code(vault):
    _index(vault, **{"t-aaaaaa": _task("Ship the export")})
    _write(
        vault,
        rollover.TEMPLATE_PATH,
        "```\n## Carried over\n```\n##  carried   OVER\n## Tasks\n",
    )
    roll_over(vault, DAY, commit=False)
    assert (vault / NOTE).read_text() == (
        "```\n## Carried over\n```\n##  carried   OVER\n"
        "### No project\n- [ ] Ship the export ^t-aaaaaa\n## Tasks\n"
    )


def test_missing_template_or_broken_index_refuses(vault):
    _write(vault, TASK_INDEX_PATH, "{not json")
    with pytest.raises(RolloverError, match="not a valid task index"):
        roll_over(vault, DAY, commit=False)
    (vault / TASK_INDEX_PATH).unlink()
    (vault / rollover.TEMPLATE_PATH).unlink()
    with pytest.raises(RolloverError, match="no daily template"):
        roll_over(vault, DAY, commit=False)
    assert not (vault / NOTE).exists()


# --- git and the CLI ---------------------------------------------------------------


def _git(vault, *args):
    return subprocess.run(
        ["git", *args], cwd=vault, capture_output=True, text=True, check=True
    ).stdout


def test_commits_only_the_new_note(vault, monkeypatch):
    monkeypatch.delenv("WIKI_GIT_AUTHOR", raising=False)
    _index(vault, **{"t-aaaaaa": _task("Ship the export")})
    _git(vault, "init", "-q")
    _git(vault, "add", "-A")
    _git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    _write(vault, "20_people/person-priya-nair.md", "my unsaved edit\n")

    result = roll_over(vault, DAY)

    assert result.commit == _git(vault, "rev-parse", "HEAD").strip()
    assert _git(vault, "show", "--name-only", "--format=%an|%s", "HEAD").split() == [
        "llm-wiki|rollover:",
        "2026-10-19",
        NOTE,
    ]
    assert _git(vault, "status", "--porcelain").strip() == (
        "M 20_people/person-priya-nair.md"
    )


def test_cli_prints_what_it_did_and_the_stale_list(vault, capsys):
    _index(
        vault,
        **{
            "t-aaaaaa": _task("Ship the export"),
            "t-bbbbbb": _task("Old one", first="2026-09-30"),
        },
    )
    args = ["--vault", str(vault), "--date", "2026-10-19", "--no-commit"]
    assert main(args) == 0
    assert capsys.readouterr().out == (
        f"Created {NOTE}: 1 task(s) carried.\n"
        "\nStale (open more than 14 days, not carried):\n"
        "- Old one · since 2026-09-30 · t-bbbbbb\n"
    )
    assert main(args) == 0
    assert capsys.readouterr().out.startswith(
        f"{NOTE} already exists; nothing written."
    )


@pytest.mark.parametrize("date", ["2026-10-24", "2026-10-25"])  # Saturday, Sunday
def test_weekdays_only_skips_the_weekend_without_reading(vault, date):
    # no task index: a broken one would raise, so the skip comes before any read
    result = roll_over(vault, datetime.date.fromisoformat(date), weekdays_only=True)
    assert result.weekend is True and result.created is False
    assert not (vault / f"00_raw/daily/{date}.md").exists()


def test_weekdays_only_still_rolls_over_a_weekday(vault):
    _index(vault, **{"t-aaaaaa": _task("Ship the export")})
    result = roll_over(vault, DAY, commit=False, weekdays_only=True)
    assert result.created is True and result.weekend is False


def test_cli_weekdays_says_it_skipped(vault, capsys):
    args = ["--vault", str(vault), "--date", "2026-10-24", "--weekdays", "--no-commit"]
    assert main(args) == 0
    assert capsys.readouterr().out == (
        "00_raw/daily/2026-10-24.md: a Saturday, and only weekdays roll over;"
        " nothing written.\n"
    )


def test_cli_error_exits_1(vault, capsys):
    (vault / rollover.TEMPLATE_PATH).unlink()
    assert main(["--vault", str(vault), "--date", "2026-10-19"]) == 1
    assert "no daily template" in capsys.readouterr().err
