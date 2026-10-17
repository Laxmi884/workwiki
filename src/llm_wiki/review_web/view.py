"""What the review page shows: a ReviewRequest, as plain data for the template.

Pure: the page template only formats this. Everything comes from the review itself
(decision 0010: any backend returns it), facts with the note line they cite.
Approval stays per file (0009: whole files first): a card's checkbox is its file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from llm_wiki.domain.review import ReviewFile, ReviewRequest

KINDS = {
    "10_projects/": "project",
    "15_concepts/": "concept",
    "20_people/": "person",
    "30_decisions/": "decisions",
    "40_impact/": "impact",
}


@dataclass(frozen=True)
class FactRow:
    """One filed fact and the note line it came from."""

    text: str
    line: int
    quote: str


@dataclass
class PageCard:
    """One wiki page file: its facts by section, and the exact change behind them."""

    file: ReviewFile
    title: str
    kind: str
    sections: list[tuple[str, list[FactRow]]] = field(default_factory=list)
    edited: bool = False

    @property
    def fact_count(self) -> int:
        """Return how many facts the card shows."""
        return sum(len(rows) for _, rows in self.sections)


@dataclass
class ReviewView:
    """Everything the page shows."""

    title: str
    lede: str
    pages: list[PageCard]
    bookkeeping: list[ReviewFile]
    task_ids: int
    attention: list[str]
    edited: list[str] = field(default_factory=list)  # titles of pages with your text


def _title(f: ReviewFile) -> str:
    for line in f.after_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return f.path.rsplit("/", 1)[-1].removesuffix(".md")


def _kind(path: str) -> str:
    return next((k for prefix, k in KINDS.items() if path.startswith(prefix)), "page")


def build_view(review: ReviewRequest) -> ReviewView:
    """Return the page's data from the review."""
    pages: list[PageCard] = []
    bookkeeping: list[ReviewFile] = []
    for f in review.files:
        if not f.editable:  # the raw note and code-owned json
            bookkeeping.append(f)
            continue
        card = PageCard(f, _title(f), _kind(f.path), edited=f.path in review.edited)
        by_section: dict[str, list[FactRow]] = {}
        for fact in review.facts:
            if fact.path == f.path:
                row = FactRow(fact.text, fact.line, fact.quote)
                by_section.setdefault(fact.section.removeprefix("## "), []).append(row)
        card.sections = list(by_section.items())
        pages.append(card)
    facts = sum(c.fact_count for c in pages)
    new = sum(c.file.change == "new" for c in pages)
    lede = f"{facts} {'fact' if facts == 1 else 'facts'} for {len(pages)} pages"
    lede += f", {new} of them new." if new else "."
    if review.can_revise and review.revisions_left:
        lede += f" You can ask for {review.revisions_left} more revisions."
    date = review.note_date
    title = f"Daily note, {date:%A %-d %B %Y}" if date else review.note
    return ReviewView(
        title,
        lede,
        pages,
        bookkeeping,
        review.task_ids_added,
        list(review.warnings),
        [c.title for c in pages if c.edited],
    )


def book_note(path: str) -> str:
    """Say what a bookkeeping file is, in the user's words."""
    if path.startswith("00_raw/"):
        return "your note: only task IDs are added"
    if path.endswith("tasks.json"):
        return "task index: projects and status"
    if path.endswith("ingest-ledger.json"):
        return "ingest log: marks this note as filed"
    return "code-owned"
