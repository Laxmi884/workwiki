"""The ingest ledger (`90_meta/ingest-ledger.json`): which notes were ingested, owned by code."""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LedgerEntry(BaseModel):
    """One ingested note.

    Attributes:
        sha256: Hash of the note text when last ingested; unchanged → skip.
        ingested_at: When it was last ingested (UTC).
        lines: Keys of every prose line ingested so far (see `extraction.line_key`).
            On re-ingest only other lines may give facts.
    """

    model_config = ConfigDict(frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ingested_at: datetime.datetime
    lines: tuple[str, ...] = ()


class IngestLedger(BaseModel):
    """All ingested notes, keyed by vault-relative path."""

    model_config = ConfigDict(frozen=True)

    version: Literal[1] = 1
    notes: dict[str, LedgerEntry] = Field(default_factory=dict)
