"""What Slack shows of each open review: one message per thread, with the token shown.

The notifier (`notifier.py`) and the DM handlers (`handlers.py`) share one `Announced`.
A handler records the review it shows and forgets the one it answers, so the notifier
announces only reviews started elsewhere, and clears the buttons of reviews answered
elsewhere. The record is a JSON file in `WIKI_STATE_DIR`, so a restart doesn't
announce everything again; a bad or unwritable file is logged and the bot goes on.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)


class Shown(BaseModel):
    """The Slack message showing a review: where it is and which token it shows."""

    model_config = ConfigDict(frozen=True)

    channel: str
    ts: str
    seen: str


class Announced:
    """Thread id → `Shown`, saved to `path` after each change (None: memory only)."""

    def __init__(self, path: Path | None) -> None:
        """Load `path`; a missing, empty or bad file counts as nothing shown."""
        self._path = path
        self._lock = threading.Lock()  # held only for dict and counter changes
        self._busy = 0
        self._shown: dict[str, Shown] = self._load()

    def get(self, thread: str) -> Shown | None:
        """Return the message showing `thread`'s review, if any."""
        with self._lock:
            return self._shown.get(thread)

    def items(self) -> list[tuple[str, Shown]]:
        """Return every (thread, message) shown."""
        with self._lock:
            return list(self._shown.items())

    def record(self, thread: str, shown: Shown) -> None:
        """Remember that `shown` shows `thread`'s review."""
        with self._lock:
            self._shown[thread] = shown
            self._save()

    def forget(self, thread: str) -> None:
        """Drop `thread` (answered, or its message is gone)."""
        with self._lock:
            if self._shown.pop(thread, None) is not None:
                self._save()

    @contextmanager
    def busy(self) -> Iterator[None]:
        """Mark a handler run (never waits: the notifier makes slow calls between checks)."""
        with self._lock:
            self._busy += 1
        try:
            yield
        finally:
            with self._lock:
                self._busy -= 1

    def idle(self) -> bool:
        """Return True when no handler run is in flight.

        A run's review isn't recorded until the run ends, so the notifier checks this
        before announcing, or it could announce that review a second time.
        """
        with self._lock:
            return self._busy == 0

    def _load(self) -> dict[str, Shown]:
        if self._path is None or not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
            return {thread: Shown.model_validate(v) for thread, v in raw.items()}
        except (OSError, ValueError, ValidationError) as exc:
            log.warning("slack: ignoring %s (%s)", self._path, type(exc).__name__)
            return {}

    def _save(self) -> None:
        """Write the whole file atomically (called with the lock held)."""
        if self._path is None:
            return
        data = {t: s.model_dump() for t, s in self._shown.items()}
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("slack: could not save %s (%s)", self._path, exc.strerror)
