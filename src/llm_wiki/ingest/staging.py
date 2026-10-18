"""Checks for what the ingest agent stages: the agent proposes, code checks (0006).

The agent never names a path or writes a file. It calls a staging tool with a target
(an entity slug, or "impact" / "decision-log"), a page section, one line of text and the
note line the fact comes from. Code checks the call and turns it into an edit, adding the
origin it reads from the note (date, meeting, link), or refuses with a reason the agent
can act on. Refusals are counted: they are the spike's "wrong or invented" measure.

Checks:
- the cited line is in the note, is new (not in the ledger) and holds content (no
  headings, blank lines, empty bullets or fenced code);
- the target resolves through `path_for` (so only entity folders) or is one of the two
  quarterly pages; the page exists or was proposed in this run;
- the section fits the page: impact and decision-log have fixed sections; entity pages
  take any short `##` heading (the user's preferences name them) except "Current
  state", which only `stage_summary` rewrites;
- a fact goes on a person or project page (existing or proposed in this run) only if
  the cited line, a parent bullet or its `###` heading names it (title, alias or link),
  code or the agent's own proposal (`map_task`, shown in the review) mapped the task to
  that project, or the user's revise feedback names the page;
- a fact may name a page proposed in the same turn (parallel calls can't see each
  other); `finalize` checks it then, and drops it if no page came or it isn't named;
- "## Wins" takes only lines tagged #win: other wins are the user's pick in the review;
- the text is one line, not too long, and links only known or proposed slugs;
- the same fact is not filed twice (already staged, or already on the page);
- a new page's title is named in the cited line (a `###` heading counts here);
- a summary ("## Current state") cites new note lines and stays short;
- a task → project proposal (decision 0007) is for an open task line (rules 1–3 and
  the index left it unmapped, the user hasn't answered it), names a project page that
  exists or is proposed in this run, and stays within the rule-3 candidates when the
  task's aliases matched several projects.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field

import yaml

from llm_wiki.daily_syntax import is_fence
from llm_wiki.domain.changes import AppendToSection, CreateFile, Edit, ReplaceSection
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.entities import EntityKind
from llm_wiki.domain.task_index import TaskProposal
from llm_wiki.entity_index import (
    EntityIndex,
    build_index,
    contains_phrase,
    kind_of,
    name_key,
    path_for,
    with_created,
)
from llm_wiki.inline_syntax import find_links
from llm_wiki.note_lines import line_key
from llm_wiki.routing import (
    DECISIONS,
    HELP_GIVEN,
    NOTES,
    UPDATES,
    WINS,
    decision_log_path,
    impact_path,
    origin_of,
)

IMPACT, DECISION_LOG = "impact", "decision-log"
MAX_TEXT = 400
MAX_WHY = 120
MAX_ALIASES = 5
MAX_NAME = 80
NO_PROJECT = "none"

CURRENT_STATE = "## Current state"
MAX_SUMMARY_LINES = 6

# The usual sections per page kind (shown to the agent). Entity pages also accept any
# other short heading; impact and decision-log accept only these.
SECTIONS: dict[str, tuple[str, ...]] = {
    EntityKind.PERSON: (NOTES, HELP_GIVEN),
    EntityKind.PROJECT: (UPDATES, DECISIONS, WINS),
    EntityKind.CONCEPT: (NOTES,),
    EntityKind.DECISION: (NOTES,),
    IMPACT: (WINS,),
    DECISION_LOG: (DECISIONS,),
}
FIXED_SECTIONS = (IMPACT, DECISION_LOG)
_HEADING = re.compile(r"^## [A-Za-z0-9][\w ,:&/()'.-]{0,39}$")

# Blank, or a bullet / checkbox with nothing after it.
_EMPTY = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s*(?:\[.\]\s*)?)?$")

_WIN_TAG = re.compile(r"(?<![\w#])#win\b", re.IGNORECASE)


class StagingError(ValueError):
    """A staging call that code refused; the message goes back to the agent."""


@dataclass(frozen=True)
class Filed:
    """One accepted fact: which note line went to which page, and the lines it added.

    `added` (the exact appended lines) lets a withdrawal or a repeat remove precisely
    this fact's edit.
    """

    line: int
    path: str
    section: str
    text: str
    added: tuple[str, ...] = ()


@dataclass(frozen=True)
class StagingContext:
    """What the checks need, fixed for one run.

    Attributes:
        note: The parsed note.
        citable: File line → text for every line a fact may cite.
        entities: The vault's entity index.
        read: Vault-relative path → current text, or None if there is no such file.
        task_projects: Task line → the project code mapped it to (track_tasks).
        feedback: The user's revise messages; a page they name may take any line.
        open_tasks: Task line → rule-3 candidates (or ()) for tasks the model may
            map to a project; lines the user answered in the review are left out.
    """

    note: DailyNote
    citable: dict[int, str]
    entities: EntityIndex
    read: Callable[[str], str | None]
    task_projects: dict[int, str | None] = field(default_factory=dict)
    feedback: tuple[str, ...] = ()
    open_tasks: Mapping[int, Sequence[str]] = field(default_factory=dict)


def citable_lines(note: DailyNote, seen: Collection[str] = ()) -> dict[int, str]:
    """Return file line → text for content lines not ingested before.

    Headings, blank lines, empty bullets and fenced code are left out; task lines count
    (a task's sub-bullets and status can carry facts).
    """
    lines: dict[int, str] = {}
    for section in note.sections:
        in_fence = False
        for i, text in enumerate(section.lines):
            if is_fence(text):
                in_fence = not in_fence
                continue
            if in_fence or _EMPTY.match(text) or text.lstrip().startswith("#"):
                continue
            if line_key(text) in seen:
                continue
            lines[section.start_line + i] = text.rstrip()
    return lines


def proposed_paths(staged: Sequence[Edit]) -> set[str]:
    """Return the paths of pages created by staged edits."""
    return {e.path for e in staged if isinstance(e, CreateFile)}


def _context(note: DailyNote, line: int) -> list[str]:
    """Return the line, its parent bullets (less indented, upwards) and its ### heading."""
    for section in note.sections:
        i = line - section.start_line
        if not 0 <= i < len(section.lines):
            continue
        rows = section.lines
        out = [rows[i]]
        indent = len(rows[i]) - len(rows[i].lstrip())
        for above in reversed(rows[:i]):
            if above.startswith("### "):
                out.append(above)
                break
            if above.strip() and len(above) - len(above.lstrip()) < indent:
                out.append(above)
                indent = len(above) - len(above.lstrip())
        return out
    return []


def _names(ctx: StagingContext, staged: Sequence[Edit], slug: str) -> list[str]:
    """Return the names of a page: its title and aliases, existing or proposed."""
    entity = ctx.entities.get(slug)
    if entity is None:
        created = [
            (e.path, e.text)
            for e in staged
            if isinstance(e, CreateFile) and e.path == path_for(slug)
        ]
        entity = build_index(created).get(slug)
    return [entity.title, *entity.aliases] if entity is not None else []


def _names_entity(
    ctx: StagingContext, staged: Sequence[Edit], slug: str, line: int
) -> bool:
    """Return True if the task maps to `slug`, or the line's context or feedback names it."""
    if ctx.task_projects.get(line) == slug:
        return True
    phrases = _names(ctx, staged, slug)
    texts = [*_context(ctx.note, line), *ctx.feedback]
    return any(
        f"[[{slug}]]" in t or slug in t or any(contains_phrase(t, p) for p in phrases)
        for t in texts
    )


def _check_named(
    ctx: StagingContext, staged: Sequence[Edit], slug: str, line: int
) -> None:
    if _names_entity(ctx, staged, slug, line):
        return
    names = ", ".join(_names(ctx, staged, slug))
    advice = (
        "If it's other work the user tracks, propose_page the project this line names; "
        "if unsure, ask_user; a one-off task needs no page"
        if kind_of(slug) is EntityKind.PROJECT
        else "Use only what the note says about this person"
    )
    raise StagingError(
        f"line {line} doesn't name {slug} ({names}), nor do its parent bullet or "
        f"heading. {advice}"
    )


def _heading_line(note: DailyNote, line: int) -> str | None:
    """Return the text of a `###` heading at `line`, else None."""
    for section in note.sections:
        i = line - section.start_line
        if 0 <= i < len(section.lines) and section.lines[i].startswith("### "):
            return section.lines[i]
    return None


def _check_name_line(ctx: StagingContext, line: int) -> str:
    """Like `_check_line`, but a `###` heading counts: names often sit there."""
    if line not in ctx.citable and (heading := _heading_line(ctx.note, line)):
        return heading
    return _check_line(ctx, line)


def _check_line(ctx: StagingContext, line: int) -> str:
    if line not in ctx.citable:
        raise StagingError(
            f"line {line} can't be cited: it is not a new content line of the note "
            "(headings, blank lines, code blocks and lines marked ~ can't be cited)"
        )
    return ctx.citable[line]


def _known_slugs(ctx: StagingContext, staged: Sequence[Edit]) -> set[str]:
    proposed = {
        p.rsplit("/", 1)[-1].removesuffix(".md") for p in proposed_paths(staged)
    }
    return {*ctx.entities.entities, *proposed}


def _check_text(text: str, known: Collection[str]) -> str:
    text = " ".join(text.split())
    if not text:
        raise StagingError("text is empty")
    if len(text) > MAX_TEXT:
        raise StagingError(f"text is {len(text)} characters; keep it under {MAX_TEXT}")
    unknown = [s for s in find_links(text) if s not in known]
    if unknown:
        raise StagingError(
            f"text links {', '.join(unknown)}, which has no page; use a known slug, "
            "call propose_page first, or write the name without [[ ]]"
        )
    return text


def _target_path(
    ctx: StagingContext, staged: Sequence[Edit], target: str, pending: bool = False
) -> tuple[str, str]:
    """Return (path, kind) for a target, or refuse.

    With `pending`, an entity slug without a page is accepted (it may be proposed in the
    same turn); the caller says so and `finalize` drops it if it never gets a page.
    """
    assert ctx.note.date is not None
    if target == IMPACT:
        return impact_path(ctx.note.date), IMPACT
    if target == DECISION_LOG:
        return decision_log_path(ctx.note.date), DECISION_LOG
    kind = kind_of(target)
    if kind is None:
        raise StagingError(
            f"{target!r} is not a target: use an entity slug like person-sam-ortiz, "
            f"or {IMPACT!r} or {DECISION_LOG!r}"
        )
    path = path_for(target)
    if (
        not pending
        and ctx.entities.get(target) is None
        and path not in proposed_paths(staged)
    ):
        raise StagingError(
            f"{target} has no page; call propose_page first if the note names it"
        )
    return path, kind


def _quarterly_page(
    ctx: StagingContext, staged: Sequence[Edit], path: str
) -> list[Edit]:
    """Return a CreateFile for a quarterly page that neither vault nor staging has."""
    if ctx.read(path) is not None or path in proposed_paths(staged):
        return []
    assert ctx.note.date is not None
    d = ctx.note.date
    quarter = f"{d.year} Q{(d.month - 1) // 3 + 1}"
    if path == impact_path(d):
        text = f"---\ntype: impact\n---\n# Impact {quarter}\n"
    else:
        text = f"---\ntype: decision-log\n---\n# Decisions {quarter}\n"
    return [CreateFile(path=path, text=text)]


def _already_there(
    ctx: StagingContext, staged: Sequence[Edit], path: str, line: str
) -> bool:
    staged_lines = {
        x
        for e in staged
        if isinstance(e, AppendToSection) and e.path == path
        for x in e.lines
    }
    return line in staged_lines or line in (ctx.read(path) or "").splitlines()


def stage_fact(
    ctx: StagingContext,
    staged: Sequence[Edit],
    filed: Sequence[Filed],
    *,
    target: str,
    section: str,
    text: str,
    line: int,
    title: str | None = None,
) -> tuple[list[Edit], Filed]:
    """Check one fact and return the edits that file it.

    Raises:
        StagingError: The call breaks a rule; the message says which.
    """
    _check_line(ctx, line)
    path, kind = _target_path(ctx, staged, target, pending=True)
    heading = (
        section.strip()
        if section.strip().startswith("## ")
        else f"## {section.strip()}"
    )
    if kind in FIXED_SECTIONS and heading not in SECTIONS[kind]:
        allowed = ", ".join(SECTIONS[kind])
        raise StagingError(
            f"section {heading!r} is not allowed on {target}; use one of: {allowed}"
        )
    if heading.casefold() == CURRENT_STATE.casefold():
        raise StagingError("rewrite Current state with update_summary, not file_fact")
    if not _HEADING.match(heading):
        raise StagingError(
            f"section {heading!r} is not a short '## Heading' (letters, digits, spaces)"
        )
    if heading.casefold() == WINS.casefold() and not _WIN_TAG.search(ctx.citable[line]):
        raise StagingError(
            f"line {line} isn't tagged #win: the user picks wins in the review. File "
            "finished work as an Update on its project page"
        )
    named_kind = kind in (EntityKind.PROJECT, EntityKind.PERSON)
    if named_kind and _names(ctx, staged, target):  # pending pages: checked in finalize
        _check_named(ctx, staged, target, line)
    text = _check_text(text, _known_slugs(ctx, staged))
    if any(f.path == path and f.line == line for f in filed):
        raise StagingError(
            f"line {line} is already filed on {target}; one fact per line per page"
        )

    origin = origin_of(ctx.note, line)
    edits = _quarterly_page(ctx, staged, path) if kind in FIXED_SECTIONS else []
    if kind == DECISION_LOG:
        if not title or "\n" in title or len(title) > 120:
            raise StagingError("a decision-log entry needs a short one-line title")
        title = title.strip()
        if _already_there(ctx, staged, path, f"### {title}"):
            raise StagingError(f"decision {title!r} is already in the decision log")
        when = f"- **When:** {origin.date}" + (
            f", {origin.meeting}" if origin.meeting else ""
        )
        block = (
            "",
            f"### {title}",
            when,
            f"- **Rationale:** {text}",
            f'- **Source:** [[{origin.note}]]: "{origin.quote}"',
        )
        edits.append(
            AppendToSection(path=path, heading=f"## {origin.date}", lines=block)
        )
    else:
        bullet = origin.bullet(text)
        if _already_there(ctx, staged, path, bullet):
            raise StagingError(f"that exact line is already on {target}")
        edits.append(AppendToSection(path=path, heading=heading, lines=(bullet,)))
    added = edits[-1].lines  # the AppendToSection just built
    return edits, Filed(line=line, path=path, section=heading, text=text, added=added)


def stage_summary(
    ctx: StagingContext,
    staged: Sequence[Edit],
    *,
    target: str,
    lines: Sequence[str],
    cites: Sequence[int],
) -> ReplaceSection:
    """Check a new "## Current state" for an entity page and return the edit.

    The lines replace the old summary; code adds when and from which note.

    Raises:
        StagingError: Not an entity page with a page, bad lines, or bad citations.
    """
    if target in FIXED_SECTIONS:
        raise StagingError(
            "only person, project and concept pages have a Current state"
        )
    path, _ = _target_path(ctx, staged, target)
    if not cites:
        raise StagingError("cite at least one note line the summary is based on")
    for line in cites:
        _check_line(ctx, line)
    known = _known_slugs(ctx, staged)
    clean = [_check_text(text, known) for text in lines if text.strip()]
    if not 1 <= len(clean) <= MAX_SUMMARY_LINES:
        raise StagingError(f"a summary has 1 to {MAX_SUMMARY_LINES} lines")
    clean = [t if t.startswith(("- ", "* ")) else f"- {t}" for t in clean]
    stem = ctx.note.path.rsplit("/", 1)[-1].removesuffix(".md")
    footer = f"_Updated {ctx.note.date} from [[{stem}]]_"
    return ReplaceSection(path=path, heading=CURRENT_STATE, lines=(*clean, "", footer))


def stage_page(
    ctx: StagingContext,
    staged: Sequence[Edit],
    *,
    slug: str,
    title: str,
    line: int,
    aliases: Sequence[str] = (),
) -> CreateFile:
    """Check a new entity page and return its stub.

    `aliases` are other names the note uses for it ("Marta" for "Marta Ruiz"); they
    go in the page's `aliases:`, so lines that use only that name can be filed on it,
    in this review and later ones.

    Raises:
        StagingError: Bad slug, the page exists, the cited line doesn't name it, or
            an alias the note doesn't use or that already names another page.
    """
    quote = _check_name_line(ctx, line)
    kind = kind_of(slug)
    if kind not in (EntityKind.PERSON, EntityKind.PROJECT, EntityKind.CONCEPT):
        raise StagingError(
            f"{slug!r} is not a person-, project- or concept- slug "
            "(lowercase words joined by '-')"
        )
    path = path_for(slug)
    if ctx.entities.get(slug) is not None or ctx.read(path) is not None:
        raise StagingError(f"{slug} already has a page; file facts on it instead")
    if path in proposed_paths(staged):
        raise StagingError(f"{slug} is already proposed")
    title = " ".join(title.split())
    if not title or len(title) > 80:
        raise StagingError("title must be one short line")
    words = re.findall(r"\w+", title.lower())
    words = [w for w in words if len(w) >= 3] or words  # "k6", "Go": short names too
    if not any(
        re.search(rf"(?<!\w){re.escape(w)}(?!\w)", quote.lower()) for w in words
    ):
        raise StagingError(
            f"line {line} doesn't name {title!r}; cite the line where it is named"
        )
    meta = {
        "type": kind.value,
        "title": title,
        "aliases": _check_aliases(ctx, staged, title, aliases),
    }
    return CreateFile(
        path=path, text=f"---\n{yaml.safe_dump(meta, sort_keys=False)}---\n# {title}\n"
    )


def _check_aliases(
    ctx: StagingContext, staged: Sequence[Edit], title: str, aliases: Sequence[str]
) -> list[str]:
    """Return a new page's aliases, cleaned; each must be a name the note uses.

    Raises:
        StagingError: Too many, empty or long, the title again or twice, not in the
            note, or already a name of another page (existing or proposed): a name
            shared by two pages links neither.
    """
    if len(aliases) > MAX_ALIASES:
        raise StagingError(f"at most {MAX_ALIASES} aliases")
    note = "\n".join(line for s in ctx.note.sections for line in s.lines)
    known = with_created(ctx.entities, staged)
    out: list[str] = []
    seen = {name_key(title)}
    for raw in aliases:
        alias = " ".join(str(raw).split())
        if not alias or len(alias) > MAX_NAME:
            raise StagingError("an alias must be a short name, e.g. a first name")
        if name_key(alias) in seen:
            raise StagingError(f"{alias!r} is the title or given twice")
        if not contains_phrase(note, alias):
            raise StagingError(
                f"the note never says {alias!r}; aliases are names the note uses"
            )
        if owners := known.lookup(alias):
            raise StagingError(
                f"{alias!r} already names {owners[0].slug}; a name shared by two pages "
                "links neither, so leave it out"
            )
        seen.add(name_key(alias))
        out.append(alias)
    return out


def stage_mapping(
    ctx: StagingContext, *, line: int, project: str, why: str
) -> tuple[int, str | None, str]:
    """Check a task → project proposal; return (line, project or None, why).

    `project` "none" takes back an earlier proposal for the line (None). A project
    without a page is accepted here (it may be proposed in the same turn);
    `finalize_mappings` drops the proposal if no page came.

    Raises:
        StagingError: Not an open task line, not a project slug, outside the
            candidates, or a bad reason.
    """
    if line not in ctx.open_tasks:
        mapped = ctx.task_projects.get(line)
        why_not = (
            f"code already mapped it to {mapped}"
            if mapped
            else "it is not a task line, or its project is already settled"
        )
        raise StagingError(f"line {line} can't be mapped: {why_not}")
    _check_line(ctx, line)
    if project.strip().lower() == NO_PROJECT:
        return line, None, ""
    project = project.strip()
    if kind_of(project) is not EntityKind.PROJECT:
        raise StagingError(
            f"{project!r} is not a project slug (project-<name>); use {NO_PROJECT!r} "
            "to take a proposal back"
        )
    candidates = ctx.open_tasks[line]
    if candidates and project not in candidates:
        raise StagingError(
            f"line {line} names aliases of {', '.join(candidates)}; pick one of them "
            "or skip it"
        )
    why = " ".join(why.split())
    if not why or len(why) > MAX_WHY:
        raise StagingError(f"why must be one short phrase (under {MAX_WHY} chars)")
    return line, project, why


def finalize_mappings(
    calls: Sequence[tuple[int, str | None, str]],
    edits: Sequence[Edit],
    ctx: StagingContext,
) -> tuple[list[TaskProposal], list[str]]:
    """Return the proposals that stand once the agent is done, and what was dropped.

    The last call per line wins ("none" removes it). A proposal for a project with no
    page in the vault or among `edits` (finalized) is dropped.
    """
    last = {line: (project, why) for line, project, why in calls}
    kept: list[TaskProposal] = []
    problems: list[str] = []
    created = proposed_paths(edits)
    for line in sorted(last):
        project, why = last[line]
        if project is None:
            continue
        path = path_for(project)
        if ctx.read(path) is None and path not in created:
            problems.append(
                f"finalize: dropped task line {line} → {project}: no page was proposed"
            )
            continue
        kept.append(TaskProposal(line=line, project=project, why=why))
    return kept, problems


def with_mappings(
    ctx: StagingContext, calls: Sequence[tuple[int, str | None, str]]
) -> StagingContext:
    """Return `ctx` with the agent's current proposals added to `task_projects`.

    A task the agent mapped to a project then counts as naming it, so its facts may
    go on that page. The last call per line wins; "none" removes the proposal.
    """
    last = {line: project for line, project, _ in calls}
    if not last:
        return ctx
    projects = {**ctx.task_projects}
    for line, project in last.items():
        if line in ctx.open_tasks:  # never over code's own mapping
            projects[line] = project
    return dataclasses.replace(ctx, task_projects=projects)


def task_people_pages(ctx: StagingContext, staged: Sequence[Edit]) -> set[str]:
    """Return the paths of person pages proposed in `staged` that the note's tasks name.

    Names count as in `task_tracking.task_people`: on the task line, its parent bullet
    or its `###` heading ("### 1:1 with Joanna"). Such a page is kept even with no
    fact on it, since the task index links those tasks to it.
    """
    from llm_wiki.task_tracking import task_people  # noqa: PLC0415

    proposed = {
        p for p in proposed_paths(staged) if kind_of(_slug(p)) is EntityKind.PERSON
    }
    if not proposed:
        return set()
    entities = with_created(ctx.entities, staged)
    named = {s for task in ctx.note.tasks for s in task_people(task, entities)[0]}
    return {p for p in proposed if _slug(p) in named}


def _slug(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".md")


def mapped_pages(calls: Sequence[tuple[int, str | None, str]]) -> set[str]:
    """Return the paths of the projects the last proposal per line names."""
    last = {line: project for line, project, _ in calls}
    return {path_for(p) for p in last.values() if p is not None}


def check_question(ctx: StagingContext, question: str, line: int) -> tuple[int, str]:
    """Check a question for the review and return (line, question)."""
    _check_name_line(ctx, line)
    question = " ".join(question.split())
    if not question or len(question) > 300:
        raise StagingError("a question must be one line under 300 characters")
    return line, question


def _orphan(
    ctx: StagingContext, staged: Sequence[Edit], pages: set[str], f: Filed
) -> str | None:
    """Return why a fact on a page proposed in its own turn can't stay, else None."""
    if ctx.read(f.path) is not None:
        return None  # an existing page: checked when filed
    if f.path not in pages:
        return "no page was proposed for it"
    slug = f.path.rsplit("/", 1)[-1].removesuffix(".md")
    if kind_of(slug) in (EntityKind.PROJECT, EntityKind.PERSON) and not _names_entity(
        ctx, staged, slug, f.line
    ):
        return "the line doesn't name that page"
    return None


def finalize(
    staged: Sequence[Edit],
    filed: Sequence[Filed],
    withdrawn: Collection[tuple[int, str, int]] = (),
    ctx: StagingContext | None = None,
    keep: Collection[str] = (),
    drop_empty: bool = True,
) -> tuple[list[Edit], list[Filed], list[str]]:
    """Re-check the whole staging once the agent is done.

    The model may make several tool calls in one turn; each sees the staging from
    before that turn, so per-call checks can miss repeats within the turn. Here each
    page is created once (first wins), a page's last summary wins, identical appends
    are dropped, and a line filed twice on one page keeps its first fact. A withdrawal
    `(line, path, n)` removes that line's facts on that page among the first `n`
    filings (those made before it), with their appended lines; a fact filed again
    after the withdrawal stays. A new page left with nothing on it is dropped, unless
    it is in `keep` (a task is mapped to it, or a task names that person), or
    `drop_empty` is False (the tools' live view mid-run, where a page proposed in an
    earlier turn must stay visible until something lands on it). With
    `ctx`, facts filed in the same turn as their page's propose_page are checked now:
    dropped if no page came, or if their line doesn't name it.

    Returns:
        (edits, filed, problems): problems count like refusals.
    """
    kept: list[Filed] = []
    problems: list[str] = []
    seen: set[tuple[int, str]] = set()
    drop: list[tuple[str, tuple[str, ...]]] = []  # (path, lines) of removed facts
    pages = proposed_paths(staged)
    for i, f in enumerate(filed):
        key = (f.line, f.path)
        if ctx is not None and (why := _orphan(ctx, staged, pages, f)):
            problems.append(f"finalize: dropped line {f.line} on {f.path}: {why}")
            drop.append((f.path, tuple(f.added)))
            continue
        if any((w[0], w[1]) == key and i < w[2] for w in withdrawn):
            drop.append((f.path, tuple(f.added)))
            continue
        if key in seen:
            problems.append(
                f"finalize: line {f.line} filed twice on {f.path}; kept the first"
            )
            drop.append((f.path, tuple(f.added)))
            continue
        seen.add(key)
        kept.append(f)

    last_summary = {
        (e.path, e.heading): i
        for i, e in enumerate(staged)
        if isinstance(e, ReplaceSection)
    }
    edits: list[Edit] = []
    created: set[str] = set()
    appended: set[tuple[str, str, tuple[str, ...]]] = set()
    for i, edit in enumerate(staged):
        if isinstance(edit, CreateFile):
            if edit.path in created:
                continue
            created.add(edit.path)
        elif isinstance(edit, ReplaceSection):
            if last_summary[(edit.path, edit.heading)] != i:
                continue  # a later summary for this page replaces it
        elif isinstance(edit, AppendToSection):
            key3 = (edit.path, edit.heading, edit.lines)
            if key3 in appended:
                continue
            if (edit.path, edit.lines) in drop:
                drop.remove((edit.path, edit.lines))  # one removal per dropped fact
                continue
            appended.add(key3)
        edits.append(edit)
    has_content = {e.path for e in edits if not isinstance(e, CreateFile)}
    empty = [e.path for e in edits if isinstance(e, CreateFile)]
    empty = [p for p in empty if drop_empty and p not in has_content and p not in keep]
    problems += [f"finalize: dropped {p}: a new page with nothing on it" for p in empty]
    edits = [e for e in edits if not (isinstance(e, CreateFile) and e.path in empty)]
    return edits, kept, problems
