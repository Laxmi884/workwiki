"""The model step: one prose section of a daily note → `SectionFacts` (decision 0004).

Code prepares the input and checks the output; the model only reads prose.

- Input: the section's prose lines, each numbered with its file line. Tasks (code
  handles them), blank lines, the `##` heading and fenced code are left out.
- Hints: known entity slugs, the section's focus, and every `#win`/`#decision` line.
- Re-ingest (phase 3a): lines already ingested (`seen`, from the ledger) are shown
  marked `~` as context; only new lines may be cited, and sections without new lines
  skip the model.
- Checks: repeated facts and facts citing a line that wasn't shown (or isn't new) are
  dropped (a warning). Invalid output or a tagged line without a fact of its kind is sent back
  once with the problems listed; what is still wrong after that becomes a warning.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from llm_wiki.daily_syntax import is_fence
from llm_wiki.domain.common import SectionKind
from llm_wiki.domain.daily import DailyNote, Section
from llm_wiki.domain.facts import SectionFacts
from llm_wiki.entity_index import EntityIndex
from llm_wiki.inline_syntax import find_tags

# Carried over and Tasks hold only tasks: code-only (task_tracking).
MODEL_SECTIONS = frozenset(
    {SectionKind.MEETINGS, SectionKind.LOG, SectionKind.HELPED, SectionKind.LEARNED}
)

_FOCUS = {
    SectionKind.MEETINGS: "Meeting notes. Look for decisions, person notes and "
    "project updates. The ### heading names the meeting and who was there.",
    SectionKind.LOG: "A work log. Look for wins, decisions, project updates and "
    "learnings. Most log lines are routine and give nothing.",
    SectionKind.HELPED: "Help the author gave. One `helps` item per person helped.",
    SectionKind.LEARNED: "Things the author learned. One `learnings` item per line.",
}

SYSTEM_PROMPT = """\
You read one section of a work daily note and return the facts worth keeping in a \
personal wiki, as JSON matching the schema.

Rules:
- Use only what the lines say. Never invent details, numbers, reasons or people.
- Every fact has `line`: the number shown before the line it comes from.
- Most lines give nothing. Leave a list empty when nothing fits.
- Names: if a person, project or concept is in the known entities, use its slug \
(e.g. "person-sam-ortiz"); [[slug]] in a line is already a slug. Otherwise write the \
name as written.
- A decision is something that was decided. News or a changed date is a project \
update, not a decision.
- A person note is something about that person: goals, wishes, preferences.
- A line tagged #decision must give a decision; a line tagged #win must give a win.
- Lines shown with `~` instead of `|` are already in the wiki: read them as context,
never cite them.

Example. Lines:
  41 | ### Sync with [[person-dana-wu]] on [[project-orbit]]
  42 | - Launch slipped to May because of the vendor
  43 | - Agreed to drop the iOS widget for v1 #decision
  44 | - Dana wants to mentor a new hire
Answer:
{"decisions": [{"line": 43, "title": "Drop the iOS widget for v1", \
"rationale": null, "project": "project-orbit", "people": ["person-dana-wu"]}],
 "wins": [], "helps": [], "learnings": [],
 "person_notes": [{"line": 44, "person": "person-dana-wu", \
"note": "Wants to mentor a new hire."}],
 "project_updates": [{"line": 42, "project": "project-orbit", \
"note": "Launch slipped to May because of the vendor."}]}"""

_TAGS_NEEDED = {"decision": "decisions", "win": "wins"}  # tag → SectionFacts field


def prose_lines(section: Section, task_lines: set[int]) -> list[tuple[int, str]]:
    """Return (file line, text) for the lines the model reads."""
    lines: list[tuple[int, str]] = []
    in_fence = False
    for i, text in enumerate(section.lines):
        line_no = section.start_line + i
        if is_fence(text):
            in_fence = not in_fence
            continue
        if in_fence or not text.strip() or line_no in task_lines:
            continue
        if i == 0 and section.heading:  # the `##` heading itself
            continue
        lines.append((line_no, text.rstrip()))
    return lines


def line_key(text: str) -> str:
    """Return a short key for a prose line: same words, same key (indent/spacing ignored)."""
    normal = " ".join(text.split())
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:16]


def new_lines(
    lines: Sequence[tuple[int, str]], seen: Collection[str]
) -> list[tuple[int, str]]:
    """Return the lines whose key isn't in `seen` (not ingested before)."""
    return [(n, text) for n, text in lines if line_key(text) not in seen]


def task_lines(note: DailyNote) -> set[int]:
    """Return the file lines of the note's tasks."""
    return {task.line_no for task in note.tasks}


def needs_model(section: Section, tasks: set[int], seen: Collection[str] = ()) -> bool:
    """Return whether the section has new prose for the model to read."""
    return section.kind in MODEL_SECTIONS and bool(
        new_lines(prose_lines(section, tasks), seen)
    )


def tagged_lines(lines: Sequence[tuple[int, str]]) -> dict[str, list[int]]:
    """Return {"decision": [...], "win": [...]}: lines carrying each required tag."""
    found: dict[str, list[int]] = {tag: [] for tag in _TAGS_NEEDED}
    for line_no, text in lines:
        for tag in find_tags(text):
            if tag in found:
                found[tag].append(line_no)
    return found


def _entity_hints(entities: EntityIndex) -> str:
    rows = []
    for entity in entities.entities.values():
        also = f" (also: {', '.join(entity.aliases)})" if entity.aliases else ""
        rows.append(f"- {entity.slug}: {entity.title}{also}")
    return "\n".join(rows) or "(none)"


def build_messages(
    section: Section,
    lines: Sequence[tuple[int, str]],
    entities: EntityIndex,
    previous: SectionFacts | None = None,
    feedback: Sequence[str] = (),
    seen: Collection[str] = (),
) -> list[BaseMessage]:
    """Build the prompt. With `previous` and `feedback` it asks for a revised answer.

    Lines in `seen` are marked `~` (context only), new lines `|`.
    """
    numbered = "\n".join(
        f"{n:>4} {'~' if line_key(text) in seen else '|'} {text}" for n, text in lines
    )
    parts = [
        f"Section: {section.heading or '(before the first heading)'}",
        _FOCUS[section.kind],
        f"Known entities:\n{_entity_hints(entities)}",
        f"Lines:\n{numbered}",
    ]
    required = [
        f"- line {n} is tagged #{tag}: it must give one of `{_TAGS_NEEDED[tag]}`"
        for tag, nums in tagged_lines(new_lines(lines, seen)).items()
        for n in nums
    ]
    if required:
        parts.append("Required:\n" + "\n".join(required))
    if previous is not None:
        parts.append(f"Your previous answer:\n{previous.model_dump_json()}")
    if feedback:
        notes = "\n".join(f"- {f}" for f in feedback)
        parts.append(
            f"The user's feedback:\n{notes}\n"
            "Return the full corrected answer, not only the changes."
        )
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage("\n\n".join(parts))]


def check_facts(
    facts: SectionFacts,
    lines: Sequence[tuple[int, str]],
    seen: Collection[str] = (),
) -> tuple[SectionFacts, list[str], list[str]]:
    """Keep facts citing new shown lines, without repeats; list uncovered tagged lines.

    Returns:
        (kept facts, what was dropped, tagged lines left uncovered)
    """
    citable = new_lines(lines, seen)
    shown = {n for n, _ in lines}
    allowed = {n for n, _ in citable}
    dropped: list[str] = []
    kept: dict[str, list[Any]] = {}
    for field in SectionFacts.model_fields:
        kept[field] = []
        repeats = 0
        for fact in getattr(facts, field):
            if fact in kept[field]:
                repeats += 1
            elif fact.line in allowed:
                kept[field].append(fact)
            elif fact.line in shown:
                dropped.append(f"{field} fact cites line {fact.line}, already ingested")
            else:
                dropped.append(f"{field} fact cites line {fact.line}, not in section")
        if repeats:
            dropped.append(f"{field}: {repeats} repeated facts removed")
    checked = SectionFacts(**kept)

    missing = [
        f"line {n} is tagged #{tag} but has no item in `{field}`"
        for tag, field in _TAGS_NEEDED.items()
        for n in tagged_lines(citable)[tag]
        if n not in {fact.line for fact in getattr(checked, field)}
    ]
    return checked, dropped, missing


def extract_section(
    model: BaseChatModel,
    section: Section,
    tasks: set[int],
    entities: EntityIndex,
    *,
    previous: SectionFacts | None = None,
    feedback: Sequence[str] = (),
    seen: Collection[str] = (),
    retries: int = 1,
) -> tuple[SectionFacts, list[str]]:
    """Ask the model for the section's facts; check them and retry with the problems.

    Returns:
        (facts, warnings). Facts are empty if the model never gave valid output.
    """
    lines = prose_lines(section, tasks)
    messages = build_messages(section, lines, entities, previous, feedback, seen)
    structured = model.with_structured_output(SectionFacts, include_raw=True)

    # Code repairs repeats and stray citations (`repaired`); only what it can't fix
    # (invalid output, an uncovered tagged line) is sent back to the model.
    facts, repaired, problems = SectionFacts(), [], []
    for _ in range(retries + 1):
        result = structured.invoke(messages)
        parsed = result.get("parsed")
        if not isinstance(parsed, SectionFacts):
            problems = [f"invalid output ({result.get('parsing_error')})"]
        else:
            facts, repaired, problems = check_facts(parsed, lines, seen)
        if not problems:
            break
        messages = [
            *messages,
            HumanMessage(
                "Your answer had problems:\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\nReturn the full corrected answer."
            ),
        ]
    return facts, [f"section {section.heading!r}: {p}" for p in repaired + problems]
