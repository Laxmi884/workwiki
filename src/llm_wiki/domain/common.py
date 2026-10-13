"""Enums shared across domain modules."""

from __future__ import annotations

from enum import StrEnum


class SectionKind(StrEnum):
    """The fixed daily-note sections (decision 0002). Each one feeds a different extractor."""

    CARRIED_OVER = "carried_over"
    TASKS = "tasks"
    MEETINGS = "meetings"
    LOG = "log"  # also any unknown section, and text before the first heading
    HELPED = "helped"
    LEARNED = "learned"
