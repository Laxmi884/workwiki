"""Facts → edits: code decides where each fact goes and how it reads (decision 0004).

- Names resolve to an entity slug when the note links it or exactly one page has it as
  slug, title or alias. Anything unresolved goes to the inbox (`05_generated/inbox.md`),
  never dropped; phase 3 turns those into review questions.
- Every line carries its origin, taken from the note by code: date, meeting (`###`
  heading above the line) and a link to the note.
- Decisions go to the quarter's decision log (`30_decisions/2026/decisions-2026-q4.md`)
  as a short block (date, meeting, people, project, rationale, source) and as a line on
  their project page. A decision marked "make page" in the review gets its own page
  (`30_decisions/decision-<slug>.md`) with those details; the log then keeps only its
  title, date and a link. Wins and help also go to the quarter's impact page
  (`40_impact/2026/impact-2026-q4.md`) for the brag sheet. Year folders keep a year's
  review in one place; logs there are not entity pages (the index reads only the
  top level of each entity folder).
- A linked entity without a page gets a stub page; the model can't create pages.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

import yaml

from llm_wiki.domain.changes import AppendToSection, CreateFile, Edit
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.entities import EntityKind
from llm_wiki.domain.facts import Decision, SectionFacts
from llm_wiki.domain.task_index import DoneTask
from llm_wiki.entity_index import EntityIndex, kind_of, path_for
from llm_wiki.inline_syntax import find_links

INBOX_PATH = "05_generated/inbox.md"

# Section headings on entity and impact pages.
UPDATES, DECISIONS, WINS, NOTES, HELP_GIVEN = (
    "## Updates",
    "## Decisions",
    "## Wins",
    "## Notes",
    "## Help given",
)


@dataclass(frozen=True)
class Origin:
    """Where a fact came from, read from the note by code.

    Attributes:
        date: The note's date.
        note: The note's file stem, for the `[[2026-10-12]]` link.
        meeting: The `###` heading above the line, without "### ", if any.
        quote: The line as written, without the bullet.
    """

    date: datetime.date
    note: str
    meeting: str | None
    quote: str

    def bullet(self, text: str) -> str:
        """Return `- 2026-10-12 · <meeting>: <text> ([[2026-10-12]])`."""
        where = f"{self.date} · {self.meeting}" if self.meeting else f"{self.date}"
        return f"- {where}: {text} ([[{self.note}]])"


def origin_of(note: DailyNote, line: int) -> Origin:
    """Return the origin of file line `line` (which must be in one of the sections)."""
    assert note.date is not None
    stem = note.path.rsplit("/", 1)[-1].removesuffix(".md")
    for section in note.sections:
        offset = line - section.start_line
        if 0 <= offset < len(section.lines):
            meeting = None
            for text in reversed(section.lines[1 : offset + 1]):
                if text.startswith("### "):
                    meeting = text[4:].strip()
                    break
            quote = re.sub(r"^\s*[-*+]\s+", "", section.lines[offset]).strip()
            return Origin(note.date, stem, meeting, quote)
    raise ValueError(f"line {line} is not in {note.path}")


def resolve(
    name: str | None, kind: EntityKind, entities: EntityIndex, linked: set[str]
) -> str | None:
    """Return the slug for `name`, or None if it isn't one known entity of `kind`.

    A slug counts if it has a page or is linked in the note (its page gets created).
    """
    if not name:
        return None
    name = name.strip().removeprefix("[[").removesuffix("]]").strip()
    if kind_of(name) is kind and (entities.get(name) or name in linked):
        return name
    matches = [e.slug for e in entities.lookup(name) if e.kind is kind]
    return matches[0] if len(matches) == 1 else None


def _quarter(date: datetime.date) -> str:
    return f"{date.year}-q{(date.month - 1) // 3 + 1}"


def impact_path(date: datetime.date) -> str:
    """Return the quarter's impact page, e.g. "40_impact/2026/impact-2026-q4.md"."""
    return f"40_impact/{date.year}/impact-{_quarter(date)}.md"


def decision_log_path(date: datetime.date) -> str:
    """Return the quarter's decision log, e.g. "30_decisions/2026/decisions-2026-q4.md"."""
    return f"30_decisions/{date.year}/decisions-{_quarter(date)}.md"


def _link(slug: str | None, name: str | None) -> str:
    return f"[[{slug}]]" if slug else (name or "")


class _Router:
    """Collects edits for one note; each page is created at most once."""

    def __init__(
        self, note: DailyNote, entities: EntityIndex, exists: Callable[[str], bool]
    ) -> None:
        assert note.date is not None
        self.note = note
        self.date = note.date
        self.entities = entities
        self.exists = exists
        self.linked = {
            slug for s in note.sections for slug in find_links("\n".join(s.lines))
        }
        self.edits: list[Edit] = []
        self.created: set[str] = set()

    def resolve(self, name: str | None, kind: EntityKind) -> str | None:
        return resolve(name, kind, self.entities, self.linked)

    def create(self, path: str, text: str) -> None:
        if path not in self.created and not self.exists(path):
            self.edits.append(CreateFile(path=path, text=text))
        self.created.add(path)

    def append(self, path: str, heading: str, *lines: str) -> None:
        self.edits.append(AppendToSection(path=path, heading=heading, lines=lines))

    def page(self, slug: str) -> str:
        """Return the slug's page path, creating a stub if it has no page yet."""
        path = path_for(slug)
        if self.entities.get(slug) is None:
            # person-jordan-lee → Jordan Lee
            title = " ".join(slug.split("-")[1:]).title()
            meta = {"type": slug.split("-", 1)[0], "title": title, "aliases": []}
            self.create(
                path, f"---\n{yaml.safe_dump(meta, sort_keys=False)}---\n# {title}\n"
            )
        return path

    def impact(self, heading: str, line: str) -> None:
        path = impact_path(self.date)
        quarter = f"{self.date.year} Q{(self.date.month - 1) // 3 + 1}"
        self.create(path, f"---\ntype: impact\n---\n# Impact {quarter}\n")
        self.append(path, heading, line)

    def inbox(self, origin: Origin, what: str) -> None:
        self.create(
            INBOX_PATH,
            "# Inbox\n\nFacts whose person, project or topic didn't match a page.\n",
        )
        self.append(INBOX_PATH, f"## {origin.date}", origin.bullet(what))


def decision_slug(title: str, taken: Callable[[str], bool]) -> str:
    """Return a free decision slug from the title, e.g. "decision-keep-the-old-bi-tool".

    At most 8 words; "-2", "-3", … is added while `taken(slug)` says it's in use.
    """
    words = re.findall(r"[a-z0-9]+", title.lower())[:8] or ["untitled"]
    base = "decision-" + "-".join(words)
    slug, n = base, 2
    while taken(slug):
        slug, n = f"{base}-{n}", n + 1
    return slug


def _when(r: _Router, d: Decision) -> str:
    o = origin_of(r.note, d.line)
    return f"- **When:** {o.date}" + (f", {o.meeting}" if o.meeting else "")


def _decision_details(r: _Router, d: Decision) -> list[str]:
    """Return the decision's detail bullets: when, people, project, rationale, source."""
    o = origin_of(r.note, d.line)
    project = r.resolve(d.project, EntityKind.PROJECT)
    people = ", ".join(_link(r.resolve(n, EntityKind.PERSON), n) for n in d.people)
    lines = [_when(r, d)]
    if people:
        lines.append(f"- **People:** {people}")
    if d.project:
        lines.append(f"- **Project:** {_link(project, d.project)}")
    lines += [
        f"- **Rationale:** {d.rationale or 'Not recorded in the note.'}",
        f'- **Source:** [[{o.note}]]: "{o.quote}"',
    ]
    return lines


def _decision_page(r: _Router, d: Decision) -> str:
    """Create the decision's own page (review "make page") and return its slug."""
    slug = decision_slug(
        d.title,
        lambda s: (
            r.entities.get(s) is not None
            or path_for(s) in r.created
            or r.exists(path_for(s))
        ),
    )
    meta = {"type": "decision", "title": d.title, "date": r.date, "status": "decided"}
    text = "\n".join(
        [
            f"---\n{yaml.safe_dump(meta, sort_keys=False)}---",
            f"# {d.title}",
            "",
            *_decision_details(r, d),
            "",
            "## Context",
            "",
            "## Consequences",
            "",
        ]
    )
    r.create(path_for(slug), text)
    return slug


def _route_decision(r: _Router, d: Decision, own_page: bool) -> None:
    """Add the decision to the quarter's log and, if known, to its project page.

    With `own_page` the details go on the decision's page and the log keeps a short
    entry (title, when, page link); otherwise the log holds the full block.
    """
    path = decision_log_path(r.date)
    quarter = f"{r.date.year} Q{(r.date.month - 1) // 3 + 1}"
    r.create(path, f"---\ntype: decision-log\n---\n# Decisions {quarter}\n")
    if own_page:
        slug = _decision_page(r, d)
        entry = [_when(r, d), f"- **Page:** [[{slug}]]"]
        see = slug
    else:
        entry = _decision_details(r, d)
        see = path.rsplit("/", 1)[-1].removesuffix(".md")
    r.append(path, f"## {r.date}", "", f"### {d.title}", *entry)
    if project := r.resolve(d.project, EntityKind.PROJECT):
        o = origin_of(r.note, d.line)
        r.append(r.page(project), DECISIONS, o.bullet(f"{d.title} (see [[{see}]])"))


def _route_win(r: _Router, line: int, text: str, project_name: str | None) -> None:
    """Put a win on the quarter's impact page and, if known, on its project page."""
    o = origin_of(r.note, line)
    project = r.resolve(project_name, EntityKind.PROJECT)
    suffix = f" ({_link(project, project_name)})" if project_name else ""
    r.impact(WINS, o.bullet(text + suffix))
    if project:
        r.append(r.page(project), WINS, o.bullet(text))


def route_facts(
    note: DailyNote,
    facts: dict[str, SectionFacts],
    entities: EntityIndex,
    exists: Callable[[str], bool],
    decision_pages: Collection[int] = (),
    task_wins: Sequence[DoneTask] = (),
) -> tuple[list[Edit], list[str]]:
    """Turn every section's facts into edits. `exists(path)` checks the vault.

    `decision_pages` holds the note lines of decisions the user wants on their own
    page (review "make page"); `task_wins` the done tasks the user picked as wins, in
    their words.

    Returns:
        (edits, warnings). Sections are routed in note order, facts in answer order.
    """
    if note.date is None:
        return [], [f"{note.path}: no date, so its facts are not routed"]
    r = _Router(note, entities, exists)
    found = {d.line for f in facts.values() for d in f.decisions}
    warnings = [
        f"{note.path}:{line}: no decision found on this line any more; no page made"
        for line in sorted(set(decision_pages) - found)
    ]
    for key in sorted(facts, key=int):
        f = facts[key]
        for d in f.decisions:
            _route_decision(r, d, own_page=d.line in decision_pages)
        for w in f.wins:
            _route_win(r, w.line, w.text, w.project)
        for p in f.person_notes:
            o = origin_of(note, p.line)
            if person := r.resolve(p.person, EntityKind.PERSON):
                r.append(r.page(person), NOTES, o.bullet(p.note))
            else:
                r.inbox(o, f"note about {p.person!r}: {p.note}")
        for h in f.helps:
            o = origin_of(note, h.line)
            minutes = f" ({h.minutes} min)" if h.minutes else ""
            person = r.resolve(h.person, EntityKind.PERSON)
            r.impact(
                HELP_GIVEN,
                o.bullet(f"Helped {_link(person, h.person)}: {h.what}{minutes}"),
            )
            if person:
                r.append(r.page(person), HELP_GIVEN, o.bullet(h.what + minutes))
        for lrn in f.learnings:
            o = origin_of(note, lrn.line)
            if concept := r.resolve(lrn.topic, EntityKind.CONCEPT):
                r.append(r.page(concept), NOTES, o.bullet(lrn.summary))
            else:
                r.inbox(o, f"learning ({lrn.topic}): {lrn.summary}")
        for u in f.project_updates:
            o = origin_of(note, u.line)
            if project := r.resolve(u.project, EntityKind.PROJECT):
                r.append(r.page(project), UPDATES, o.bullet(u.note))
            else:
                r.inbox(o, f"update on {u.project!r}: {u.note}")
    for t in task_wins:
        _route_win(r, t.line, t.text, t.project)
    return r.edits, warnings
