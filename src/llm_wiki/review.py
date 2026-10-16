"""The review step's logic: build the request, check an answer, list what was approved.

Pure, so the `review` node only pauses and loops; see `ingest/nodes.py`.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

import pydantic

from llm_wiki.domain.changes import AppendToSection, Edit, FileDiff, is_wiki_page
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.facts import NameScan, SectionFacts
from llm_wiki.domain.review import (
    ReviewAnswer,
    ReviewDecision,
    ReviewFile,
    ReviewInboxItem,
    ReviewPerson,
    ReviewRequest,
    ReviewTask,
    ReviewWin,
)
from llm_wiki.domain.task_index import DoneTask, TaskProposal
from llm_wiki.entity_index import EntityIndex, kind_of, name_key, path_for
from llm_wiki.names import is_known_person
from llm_wiki.routing import INBOX_PATH, origin_of
from llm_wiki.task_tracking import TASK_INDEX_PATH

MAX_REVISIONS = 3


def _plain_line(quote: str) -> str:
    """Drop a task's checkbox and block ID: "[ ] Ping Eugene ^t-abc123" → "Ping Eugene"."""
    return re.sub(r"\s\^t-[0-9a-z]{6}$", "", re.sub(r"^\[.\]\s*", "", quote))


def people_questions(
    note: DailyNote,
    scan: NameScan | None,
    facts: Mapping[str, SectionFacts],
    entities: EntityIndex,
    ignored: Collection[str] = (),
) -> tuple[ReviewPerson, ...]:
    """Names with no page, from the name scan and from facts, in note order.

    Asked again each round against `entities` (vault + answered pages), so an answered
    name drops out; ignored names never come back in this run.
    """
    skip = {name_key(n) for n in ignored}
    lines_of: dict[str, tuple[str, set[int]]] = {}
    found = [(n, p.name) for p in (scan.people if scan else []) for n in p.lines]
    found += [
        pair for key in sorted(facts, key=int) for pair in facts[key].person_names()
    ]
    for line, raw in found:
        name = " ".join(raw.split())
        key = name_key(name)
        if not name or kind_of(name) is not None or key in skip:
            continue
        if is_known_person(name, entities):
            continue
        lines_of.setdefault(key, (name, set()))[1].add(line)
    ordered = sorted(lines_of.values(), key=lambda item: min(item[1]))
    return tuple(
        ReviewPerson(
            id=f"p{i}",
            name=name,
            lines=tuple(sorted(lines)),
            context=_plain_line(origin_of(note, min(lines)).quote),
        )
        for i, (name, lines) in enumerate(ordered, start=1)
    )


def task_questions(
    note: DailyNote,
    open_tasks: Collection[int],
    proposals: Sequence[TaskProposal] = (),
    chosen: Mapping[int, str | None] | None = None,
) -> tuple[ReviewTask, ...]:
    """Every task rules 1–3 left open, in note order, with what it will be stored as.

    The user's answer beats the model's proposal; a task with neither has no project.
    IDs follow the open tasks, so they stay the same across review rounds.
    """
    chosen = chosen or {}
    proposed = {p.line: p for p in proposals}
    text = {t.line_no: t.text for t in note.tasks}
    out: list[ReviewTask] = []
    for i, line in enumerate(sorted(open_tasks), start=1):
        common = {"id": f"m{i}", "line": line, "text": _plain_line(text.get(line, ""))}
        if line in chosen:
            out.append(ReviewTask(**common, project=chosen[line], by="user"))
        elif line in proposed:
            p = proposed[line]
            out.append(ReviewTask(**common, project=p.project, by="model", why=p.why))
        else:
            out.append(ReviewTask(**common))
    return tuple(out)


def build_request(
    note: str,
    diffs: Sequence[FileDiff],
    facts: Mapping[str, SectionFacts],
    fact_edits: Iterable[Edit],
    warnings: Sequence[str],
    *,
    decision_pages: Collection[int] = (),
    revisions: int = 0,
    can_revise: bool = False,
    edited: Collection[str] = (),
    people: Sequence[ReviewPerson] = (),
    done_tasks: Sequence[DoneTask] = (),
    chosen_wins: Collection[int] = (),
    tasks: Sequence[ReviewTask] = (),
) -> ReviewRequest:
    """Turn the rendered diffs and facts into what the user sees.

    Files are listed by path, so their IDs stay the same across review rounds while the
    same files are proposed. Warnings are listed once each (a revise round can repeat
    earlier ones).
    """
    diffs = sorted(diffs, key=lambda d: d.path)
    files = tuple(
        ReviewFile(
            id=f"f{i}",
            path=d.path,
            change="new" if d.before_sha256 is None else "changed",
            digest=d.digest,
            diff=d.unified_diff,
            editable=is_wiki_page(d.path),
            after_text=d.after_text if is_wiki_page(d.path) else "",
        )
        for i, d in enumerate(diffs, start=1)
    )
    found = [d for key in sorted(facts, key=int) for d in facts[key].decisions]
    decisions = tuple(
        ReviewDecision(
            id=f"d{i}", title=d.title, line=d.line, page=d.line in decision_pages
        )
        for i, d in enumerate(found, start=1)
    )
    inbox_lines = [
        line
        for edit in fact_edits
        if isinstance(edit, AppendToSection) and edit.path == INBOX_PATH
        for line in edit.lines
    ]
    inbox = tuple(
        ReviewInboxItem(id=f"i{i}", text=line.removeprefix("- "))
        for i, line in enumerate(inbox_lines, start=1)
    )
    return ReviewRequest(
        note=note,
        files=files,
        decisions=decisions,
        inbox=inbox,
        warnings=tuple(dict.fromkeys(warnings)),
        revisions_left=max(MAX_REVISIONS - revisions, 0) if can_revise else 0,
        can_revise=can_revise,
        edited=tuple(p for p in edited if p in {d.path for d in diffs}),
        people=tuple(people),
        wins=tuple(
            ReviewWin(
                id=f"w{i}",
                line=t.line,
                text=t.text,
                project=t.project,
                chosen=t.line in chosen_wins,
            )
            for i, t in enumerate(done_tasks, start=1)
        ),
        tasks=tuple(tasks),
    )


def _unknown(kind: str, given: Sequence[str], valid: Sequence[str]) -> str | None:
    unknown = [i for i in given if i not in valid]
    if not unknown:
        return None
    return f"unknown {kind} ids {unknown}; valid ids: {', '.join(valid) or 'none'}"


def _check_people(
    request: ReviewRequest, people: Mapping[str, str], known: Collection[str]
) -> str | None:
    if error := _unknown("person", list(people), [p.id for p in request.people]):
        return error
    for pid, value in people.items():
        if value not in ("new", "ignore") and value not in known:
            return (
                f'{pid}: {value!r} is not a person page; answer "new", "ignore" '
                "or an existing person slug"
            )
    return None


def _check_tasks(
    request: ReviewRequest, tasks: Mapping[str, str], known: Collection[str]
) -> str | None:
    if error := _unknown("task", list(tasks), [t.id for t in request.tasks]):
        return error
    for tid, value in tasks.items():
        if value != "none" and value not in known:
            return (
                f'{tid}: {value!r} is not a project page; answer "none" or a project '
                "slug (existing or proposed in this review)"
            )
    return None


def _check_rejected_projects(
    request: ReviewRequest, reject: Collection[str]
) -> str | None:
    """Refuse leaving out a new page the task index will point at (0007).

    A project that tasks are mapped to, or a person the task index links tasks to.
    """
    for f in request.files:
        if f.id not in reject or f.change != "new":
            continue
        users = [
            t.id for t in request.tasks if t.project and path_for(t.project) == f.path
        ]
        if users:
            return (
                f"{f.id} is the new page {f.path}, which tasks {', '.join(users)} are "
                'mapped to; answer those tasks first (another project or "none")'
            )
        if _tasks_link(request, f.path):
            return (
                f"{f.id} is the new page {f.path}, which tasks in {TASK_INDEX_PATH} "
                "link to; keep it, or revise (e.g. say it isn't a person page)"
            )
    return None


def _tasks_link(request: ReviewRequest, path: str) -> bool:
    """Return whether the task index rewrite adds a link to the page at `path`."""
    slug = f'"{path.rsplit("/", 1)[-1].removesuffix(".md")}"'
    return any(
        line.startswith("+") and slug in line
        for f in request.files
        if f.path == TASK_INDEX_PATH
        for line in f.diff.splitlines()
    )


def check_answer(
    request: ReviewRequest,
    raw: Any,
    known_people: Collection[str] = (),
    known_projects: Collection[str] = (),
) -> tuple[ReviewAnswer | None, str | None]:
    """Validate the user's answer. Returns (answer, None) or (None, why not).

    "approve" and "reject" may be sent as plain strings. `known_people` and
    `known_projects` are the person and project slugs an "answer" may name.
    """
    if isinstance(raw, str):
        raw = {"action": raw.strip().lower()}
    try:
        answer = ReviewAnswer.model_validate(raw)
    except pydantic.ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'answer'}: {e['msg']}"
            for e in exc.errors()
        )
        return None, f"invalid answer ({problems})"

    file_ids = [f.id for f in request.files]
    if answer.action == "approve":
        error = _unknown("file", answer.reject, file_ids)
        if error is None:
            error = _check_rejected_projects(request, answer.reject)
    elif answer.action == "revise":
        if not request.can_revise:
            error = "nothing to revise: the note has no prose for the model to read"
        elif request.revisions_left == 0:
            error = (
                f"revise limit reached ({MAX_REVISIONS}); approve, edit, make_page "
                "or reject"
            )
        else:
            error = None
    elif answer.action == "make_page":
        error = _unknown(
            "decision", answer.decisions, [d.id for d in request.decisions]
        )
    elif answer.action == "edit":
        error = _unknown("file", [answer.file or ""], file_ids)
        if error is None:
            (target,) = [f for f in request.files if f.id == answer.file]
            if not target.editable:
                error = (
                    f"{target.id} is {target.path}, which only code writes; "
                    "edit a wiki page instead"
                )
    elif answer.action == "answer":
        error = _check_people(request, answer.people or {}, known_people)
        if error is None:
            error = _unknown(
                "win", list(answer.wins or ()), [w.id for w in request.wins]
            )
        if error is None:
            error = _check_tasks(request, answer.tasks or {}, known_projects)
    else:
        error = None
    return (None, error) if error else (answer, None)


def approved_digests(request: ReviewRequest, answer: ReviewAnswer) -> list[str]:
    """Return the digests of the files the answer approves (none for "reject")."""
    if answer.action != "approve":
        return []
    return [f.digest for f in request.files if f.id not in answer.reject]
