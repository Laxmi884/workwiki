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
- the text is one line, not too long, and links only known or proposed slugs;
- the same fact is not filed twice (already staged, or already on the page);
- a new page's title is named in the cited line;
- a summary ("## Current state") cites new note lines and stays short.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

import yaml

from llm_wiki.daily_syntax import is_fence
from llm_wiki.domain.changes import AppendToSection, CreateFile, Edit, ReplaceSection
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.entities import EntityKind
from llm_wiki.entity_index import EntityIndex, kind_of, path_for
from llm_wiki.extraction import line_key
from llm_wiki.inline_syntax import find_links
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

CURRENT_STATE = "## Current state"
MAX_SUMMARY_LINES = 6

# The usual sections per page kind (shown to the agent). Entity pages also accept any
# other short heading; impact and decision-log accept only these.
SECTIONS: dict[str, tuple[str, ...]] = {
    EntityKind.PERSON: (NOTES, HELP_GIVEN),
    EntityKind.PROJECT: (UPDATES, DECISIONS, WINS),
    EntityKind.CONCEPT: (NOTES,),
    EntityKind.DECISION: (NOTES,),
    IMPACT: (WINS, HELP_GIVEN),
    DECISION_LOG: (DECISIONS,),
}
FIXED_SECTIONS = (IMPACT, DECISION_LOG)
_HEADING = re.compile(r"^## [A-Za-z0-9][\w ,:&/()'.-]{0,39}$")

# Blank, or a bullet / checkbox with nothing after it.
_EMPTY = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s*(?:\[.\]\s*)?)?$")


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
    """

    note: DailyNote
    citable: dict[int, str]
    entities: EntityIndex
    read: Callable[[str], str | None]


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
    ctx: StagingContext, staged: Sequence[Edit], target: str
) -> tuple[str, str]:
    """Return (path, kind) for a target, or refuse."""
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
    if ctx.entities.get(target) is None and path not in proposed_paths(staged):
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
    path, kind = _target_path(ctx, staged, target)
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
    text = _check_text(text, _known_slugs(ctx, staged))
    if any(f.path == path and f.line == line for f in filed):
        raise StagingError(
            f"line {line} is already filed on {target}; one fact per line per page"
        )

    origin = origin_of(ctx.note, line)
    edits = _quarterly_page(ctx, staged, path)
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
) -> CreateFile:
    """Check a new entity page and return its stub.

    Raises:
        StagingError: Bad slug, the page exists, or the cited line doesn't name it.
    """
    quote = _check_line(ctx, line)
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
    words = [w for w in re.findall(r"\w+", title.lower()) if len(w) >= 3]
    if not any(
        re.search(rf"(?<!\w){re.escape(w)}(?!\w)", quote.lower()) for w in words
    ):
        raise StagingError(
            f"line {line} doesn't name {title!r}; cite the line where it is named"
        )
    meta = {"type": kind.value, "title": title, "aliases": []}
    return CreateFile(
        path=path, text=f"---\n{yaml.safe_dump(meta, sort_keys=False)}---\n# {title}\n"
    )


def check_question(ctx: StagingContext, question: str, line: int) -> str:
    """Check a question for the review and return it with its line."""
    _check_line(ctx, line)
    question = " ".join(question.split())
    if not question or len(question) > 300:
        raise StagingError("a question must be one line under 300 characters")
    return f"line {line}: {question}"


def finalize(
    staged: Sequence[Edit],
    filed: Sequence[Filed],
    withdrawn: Collection[tuple[int, str, int]] = (),
) -> tuple[list[Edit], list[Filed], list[str]]:
    """Re-check the whole staging once the agent is done.

    The model may make several tool calls in one turn; each sees the staging from
    before that turn, so per-call checks can miss repeats within the turn. Here each
    page is created once (first wins), a page's last summary wins, identical appends
    are dropped, and a line filed twice on one page keeps its first fact. A withdrawal
    `(line, path, n)` removes that line's facts on that page among the first `n`
    filings (those made before it), with their appended lines; a fact filed again
    after the withdrawal stays.

    Returns:
        (edits, filed, problems): problems count like refusals.
    """
    kept: list[Filed] = []
    problems: list[str] = []
    seen: set[tuple[int, str]] = set()
    drop: list[tuple[str, tuple[str, ...]]] = []  # (path, lines) of removed facts
    for i, f in enumerate(filed):
        key = (f.line, f.path)
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
    return edits, kept, problems
