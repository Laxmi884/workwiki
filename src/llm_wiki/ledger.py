"""The ingest ledger: skip unchanged notes; on an edited note only new lines give facts.

Pure: ledger text and notes come in, a new ledger comes out. The update is proposed
as a `ReplaceFile` edit like any other change, so it is only recorded when approved
(decision 0004, phase 3a).
"""

from __future__ import annotations

import datetime
import json

import pydantic

from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.ledger import IngestLedger, LedgerEntry
from llm_wiki.extraction import line_key, prose_lines, task_lines

LEDGER_PATH = "90_meta/ingest-ledger.json"


def load_ledger(text: str | None) -> tuple[IngestLedger | None, str | None]:
    """Parse the ledger. Missing file: empty ledger. Broken file: (None, problem).

    A broken ledger stops ingest: without it, already-ingested facts would be
    proposed again. It is never overwritten.
    """
    if text is None:
        return IngestLedger(), None
    try:
        return IngestLedger.model_validate_json(text), None
    except pydantic.ValidationError as exc:
        return None, (
            f"{LEDGER_PATH} is not a valid ingest ledger ({exc.error_count()} errors); "
            "fix or remove it, then ingest again"
        )


def dump_ledger(ledger: IngestLedger) -> str:
    """Serialise deterministically (sorted keys, 2-space indent) so diffs stay small."""
    data = ledger.model_dump(mode="json")
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def prose_keys(note: DailyNote) -> set[str]:
    """Return the keys of all prose lines in the note (what the model may read)."""
    tasks = task_lines(note)
    return {
        line_key(text)
        for section in note.sections
        for _, text in prose_lines(section, tasks)
    }


def record(
    ledger: IngestLedger,
    note: DailyNote,
    sha256: str,
    now: datetime.datetime,
) -> IngestLedger:
    """Return the ledger with `note` recorded as ingested now.

    Line keys are added to the ones already there, so a line deleted and later
    put back doesn't count as new.
    """
    old = ledger.notes.get(note.path)
    lines = prose_keys(note) | set(old.lines if old else ())
    entry = LedgerEntry(sha256=sha256, ingested_at=now, lines=tuple(sorted(lines)))
    return ledger.model_copy(update={"notes": {**ledger.notes, note.path: entry}})
