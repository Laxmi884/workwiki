# ruff: noqa: T201  (a CLI prints what it did)
"""Daily-note rollover (decision 0001, write 2): create today's note, open tasks carried.

uv run python -m llm_wiki.rollover
uv run python -m llm_wiki.rollover --vault ~/WorkDev --date 2026-10-19

Creates `00_raw/daily/<date>.md` from `50_templates/daily.md` **only if it does not
exist**, with `## Carried over` holding the open and in-progress tasks from the task
index, IDs intact, grouped under a `###` heading per project ("No project" last). Code
only, no model, so no approval; the new file is committed alone (`vault_git`).

The index knows only ingested notes. A note that changed since its last ingest (ledger
hash differs, or never ingested) is folded in by task ID first, so a task ticked
yesterday isn't carried; its new tasks have no ID yet and are carried after its ingest.

Tasks open more than 14 days (since first seen) are not carried: they are returned as
stale, for the morning brief (phase 7); the CLI prints them for now.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from llm_wiki import clock, task_tracking, vault_git
from llm_wiki.config import get_settings, load_env_file
from llm_wiki.daily_syntax import parse_daily_note, split_sections
from llm_wiki.domain.changes import RAW_DAILY_FOLDER, sha256_text
from llm_wiki.domain.common import SectionKind
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.ledger import IngestLedger
from llm_wiki.domain.task_index import TaskIndex, TaskRecord
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.entity_index import EntityIndex, load_index
from llm_wiki.frontmatter import split_frontmatter
from llm_wiki.ledger import LEDGER_PATH, load_ledger

TEMPLATE_PATH = "50_templates/daily.md"
STALE_AFTER_DAYS = 14
NO_PROJECT = "No project"

_CHAR = {TaskStatus.OPEN: " ", TaskStatus.IN_PROGRESS: "/"}

# Obsidian core-template placeholders: {{date}}, {{date:YYYY-MM-DD}}, {{title}}, ...
_PLACEHOLDER = re.compile(r"\{\{\s*(?P<name>\w+)\s*(?::(?P<format>[^}]*))?\}\}")
# The Moment.js date tokens we fill in; longest first.
_DATE_TOKEN = re.compile(r"YYYY|MMMM|MMM|MM|DD|dddd|ddd")
_STRFTIME = {
    "YYYY": "%Y",
    "MMMM": "%B",
    "MMM": "%b",
    "MM": "%m",
    "DD": "%d",
    "dddd": "%A",
    "ddd": "%a",
}

Keyed = tuple[str, TaskRecord]  # (task ID, record)


class RolloverError(Exception):
    """The note can't be created: no template, a broken task index, ..."""


class Rollover(BaseModel):
    """What one rollover did.

    Attributes:
        path: Vault path of the day's note.
        created: False when the note already existed (nothing written).
        carried: IDs written under `## Carried over`, in note order.
        stale: Open tasks first seen more than 14 days ago, oldest first; not carried.
        commit: The vault commit of the new note, if one was made.
        warnings: Changed notes, template placeholders left as written, commit trouble.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    created: bool
    carried: tuple[str, ...] = ()
    stale: tuple[Keyed, ...] = ()
    commit: str | None = None
    warnings: tuple[str, ...] = ()


# --- pure: which tasks ----------------------------------------------------------


def caught_up_index(
    index: TaskIndex,
    notes: Iterable[tuple[DailyNote, str]],
    ledger: IngestLedger,
    entities: EntityIndex,
    day: datetime.date,
) -> tuple[TaskIndex, list[str]]:
    """Fold in, by task ID, the notes before `day` that changed since their ingest.

    `notes` are (note, sha256 of its text). Uses ingest's own `update_task_index`
    with no new IDs, so statuses follow the same rules; tasks without an ID are only
    counted.
    """
    warnings: list[str] = []
    dated = [(n, sha) for n, sha in notes if n.date is not None and n.date < day]
    for note, sha in sorted(dated, key=lambda item: (item[0].date, item[0].path)):
        entry = ledger.notes.get(note.path)
        if entry is not None and entry.sha256 == sha:
            continue
        index, _ = task_tracking.update_task_index(index, note, {}, entities)
        new = sum(1 for task in note.tasks if task.id is None)
        how = "changed since its last ingest" if entry else "not ingested yet"
        warning = f"{note.path} {how}; its ticks were read"
        if new:
            warning += (
                f", but {new} new task(s) have no ID yet: ingest it to carry them"
            )
        warnings.append(warning)
    return index, warnings


def split_open(index: TaskIndex, day: datetime.date) -> tuple[list[Keyed], list[Keyed]]:
    """Return (carried, stale): open and in-progress tasks, oldest first.

    Stale: first seen more than `STALE_AFTER_DAYS` days before `day`.
    """
    open_tasks = sorted(
        ((tid, t) for tid, t in index.tasks.items() if t.status in _CHAR),
        key=lambda item: (item[1].first_seen, item[1].source, item[1].line, item[0]),
    )
    carried: list[Keyed] = []
    stale: list[Keyed] = []
    for task_id, task in open_tasks:
        old = (day - task.first_seen).days > STALE_AFTER_DAYS
        (stale if old else carried).append((task_id, task))
    return carried, stale


# --- pure: the note's text ------------------------------------------------------


def render_carried(carried: Sequence[Keyed], entities: EntityIndex) -> str:
    """Return the task lines with a `### <project title>` heading per project.

    Projects by title, then "No project"; tasks keep the order of `carried`.
    """

    def title(slug: str | None) -> str:
        entity = entities.get(slug) if slug else None
        return entity.title if entity else (slug or NO_PROJECT)

    groups: dict[str | None, list[Keyed]] = {}
    for task_id, task in carried:
        groups.setdefault(task.project, []).append((task_id, task))
    order = sorted(
        groups, key=lambda slug: (slug is None, title(slug).casefold(), slug or "")
    )
    return "".join(
        f"### {title(slug)}\n"
        + "".join(f"- [{_CHAR[t.status]}] {t.text} ^{tid}\n" for tid, t in groups[slug])
        for slug in order
    )


def _date_text(fmt: str, day: datetime.date) -> str | None:
    """Return `day` in a Moment.js format, or None if the format has other tokens."""
    if re.search(r"[A-Za-z]", _DATE_TOKEN.sub("", fmt)):
        return None
    return _DATE_TOKEN.sub(lambda m: day.strftime(_STRFTIME[m.group()]), fmt)


def _fill_placeholders(template: str, day: datetime.date) -> tuple[str, list[str]]:
    left: list[str] = []

    def fill(match: re.Match[str]) -> str:
        name, fmt = match["name"], match["format"]
        value = None
        if name == "date":
            value = _date_text((fmt or "YYYY-MM-DD").strip(), day)
        elif name == "title" and fmt is None:
            value = day.isoformat()  # Obsidian's title is the file name
        if value is None:
            left.append(match.group())
        return match.group() if value is None else value

    text = _PLACEHOLDER.sub(fill, template)
    warnings = [f"{TEMPLATE_PATH}: left {p} as written" for p in dict.fromkeys(left)]
    return text, warnings


def render_note(
    template: str, day: datetime.date, carried: Sequence[Keyed], entities: EntityIndex
) -> tuple[str, list[str]]:
    """Return the day's note: the template, placeholders filled, tasks carried.

    The tasks go right after the template's `## Carried over` heading.

    Raises:
        RolloverError: There are tasks to carry but no `## Carried over` heading.
    """
    text, warnings = _fill_placeholders(template, day)
    if not carried:
        return text, warnings
    split = split_frontmatter(text)
    heading = next(
        (
            s.start_line
            for s in split_sections(split.body, split.body_start_line)
            if s.kind is SectionKind.CARRIED_OVER
        ),
        None,
    )
    if heading is None:
        raise RolloverError(f"{TEMPLATE_PATH} has no '## Carried over' heading")
    lines = text.splitlines(keepends=True)
    if not lines[heading - 1].endswith("\n"):
        lines[heading - 1] += "\n"
    lines.insert(heading, render_carried(carried, entities))
    return "".join(lines), warnings


# --- I/O -------------------------------------------------------------------------


def _read(vault: Path, rel: str) -> str | None:
    path = vault / rel
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _notes(vault: Path) -> list[tuple[DailyNote, str]]:
    out = []
    for file in sorted((vault / RAW_DAILY_FOLDER).glob("*.md")):
        text = file.read_text(encoding="utf-8")
        rel = file.relative_to(vault).as_posix()
        out.append((parse_daily_note(rel, text), sha256_text(text)))
    return out


def roll_over(vault: Path, day: datetime.date, *, commit: bool = True) -> Rollover:
    """Create the note for `day` unless it exists; return what was done.

    Stale tasks are reported either way. The written file is read back and its
    `## Carried over` task IDs compared with what was carried.

    Raises:
        RolloverError: Broken task index, no template, or no `## Carried over` heading.
    """
    rel = f"{RAW_DAILY_FOLDER}{day.isoformat()}.md"
    index, problem = task_tracking.load_task_index(
        _read(vault, task_tracking.TASK_INDEX_PATH)
    )
    if index is None:
        raise RolloverError(problem)
    ledger, problem = load_ledger(_read(vault, LEDGER_PATH))
    warnings: list[str] = []
    if ledger is None:  # treat every note as changed; folding by ID is safe to repeat
        ledger, warnings = IngestLedger(), [f"{problem}; every note was read"]
    entities = load_index(vault)
    index, caught_up = caught_up_index(index, _notes(vault), ledger, entities, day)
    warnings += caught_up
    carried, stale = split_open(index, day)
    target = vault / rel
    not_written = Rollover(
        path=rel, created=False, stale=tuple(stale), warnings=tuple(warnings)
    )
    if target.exists():
        return not_written

    template = _read(vault, TEMPLATE_PATH)
    if template is None:
        raise RolloverError(f"no daily template at {TEMPLATE_PATH}")
    text, filled = render_note(template, day, carried, entities)
    warnings += filled

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="") as file:  # never overwrite
            file.write(text)
    except FileExistsError:  # Obsidian made it a moment ago
        return not_written

    written = parse_daily_note(rel, target.read_text(encoding="utf-8"))
    ids = [t.id for t in written.tasks if t.section is SectionKind.CARRIED_OVER]
    if sorted(map(str, ids)) != sorted(task_id for task_id, _ in carried):
        warnings.append(  # e.g. the template had tasks of its own there
            f"{rel}: Carried over holds {len(ids)} task(s), {len(carried)} were carried"
        )

    sha = None
    if commit:
        sha, failed = vault_git.commit_paths(
            vault, [rel], f"rollover: {day.isoformat()}"
        )
        warnings += failed
    return Rollover(
        path=rel,
        created=True,
        carried=tuple(t for t in ids if t is not None),
        stale=tuple(stale),
        commit=sha,
        warnings=tuple(warnings),
    )


def main(argv: list[str] | None = None) -> int:
    """Parse the command line, roll over, print what happened."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", help="vault folder (default: WIKI_VAULT_PATH)")
    parser.add_argument(
        "--date",
        type=datetime.date.fromisoformat,
        help="the day to create, YYYY-MM-DD (default: today in WIKI_TIMEZONE)",
    )
    parser.add_argument("--no-commit", action="store_true", help="don't git-commit it")
    args = parser.parse_args(argv)
    load_env_file()
    vault = (
        Path(args.vault).expanduser().resolve()
        if args.vault
        else get_settings().vault_path
    )
    day = args.date or clock.today()
    try:
        result = roll_over(vault, day, commit=not args.no_commit)
    except RolloverError as exc:
        print(f"rollover: {exc}", file=sys.stderr)
        return 1

    if result.created:
        done = f"Created {result.path}: {len(result.carried)} task(s) carried"
        print(done + (f" (commit {result.commit[:7]})." if result.commit else "."))
    else:
        print(f"{result.path} already exists; nothing written.")
    if result.stale:
        print(f"\nStale (open more than {STALE_AFTER_DAYS} days, not carried):")
        for task_id, task in result.stale:
            print(f"- {task.text} · since {task.first_seen.isoformat()} · {task_id}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
