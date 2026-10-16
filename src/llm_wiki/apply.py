"""Write approved diffs to the vault (phase 3d): only what was approved, only once.

Rules (CLAUDE.md, decision 0001):
- Only diffs whose digest the user approved are written; a digest that matches no diff
  is an error (the approval is for something else).
- Before anything is written, every approved file must still match the text it was
  reviewed against (`before_sha256`; None = must not exist). If one doesn't, nothing
  is written: `ApplyConflict` lists them and the run shows fresh diffs instead.
- A file already holding exactly the approved text counts as written (a retried apply
  after a crash halfway is safe).
- `00_raw/` gets nothing but ` ^t-xxxxxx` suffixes on existing lines, checked again here.
- Each file is replaced atomically (temp file + rename), then read back and compared
  with the approved hash.
- Order: wiki pages, then the raw note, then `90_meta/tasks.json`, the ingest ledger
  last. If a write fails halfway, the ledger hasn't recorded the note yet, so the next
  run reads it again (and repeats are refused by the staging checks).
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Collection, Sequence
from pathlib import Path

from llm_wiki.domain.changes import RAW_DAILY_FOLDER, FileDiff, sha256_text
from llm_wiki.ledger import LEDGER_PATH
from llm_wiki.task_tracking import TASK_INDEX_PATH

_ID_SUFFIX = re.compile(r" \^t-[0-9a-z]{6}$")


class ApplyError(RuntimeError):
    """Apply can't go on: unknown approval, unsafe path, bad raw-note change, bad write."""


class ApplyConflict(ApplyError):
    """Approved files changed since the review; nothing was written.

    Attributes:
        paths: The vault-relative paths that no longer match.
    """

    def __init__(self, paths: Sequence[str]) -> None:
        """Record the changed paths."""
        self.paths = tuple(paths)
        super().__init__(f"changed since the review: {', '.join(self.paths)}")


def _order(path: str) -> int:
    if path == LEDGER_PATH:
        return 3
    if path == TASK_INDEX_PATH:
        return 2
    return 1 if path.startswith(RAW_DAILY_FOLDER) else 0


def _target(vault: Path, path: str) -> Path:
    """Return the file for `path`, refusing anything that resolves outside the vault."""
    target = (vault / path).resolve()
    if not target.is_relative_to(vault.resolve()):
        raise ApplyError(f"{path} resolves outside the vault")
    return target


def _read(target: Path) -> str | None:
    return target.read_text(encoding="utf-8") if target.is_file() else None


def only_ids_added(before: str, after: str) -> bool:
    """Return True if `after` only adds ` ^t-xxxxxx` to lines of `before`."""
    old, new = before.split("\n"), after.split("\n")
    return len(old) == len(new) and all(
        n == o or (n.startswith(o) and _ID_SUFFIX.fullmatch(n[len(o) :]))
        for o, n in zip(old, new, strict=True)
    )


def _write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def apply_diffs(
    vault: Path, diffs: Sequence[FileDiff], approved: Collection[str]
) -> list[str]:
    """Write the approved diffs; return the paths written (or already up to date).

    Raises:
        ApplyConflict: An approved file changed since the review; nothing written.
        ApplyError: An approval matches no diff, a path escapes the vault, a raw note
            would change beyond task IDs, or a file reads back different.
    """
    by_digest = {d.digest: d for d in diffs}
    unknown = sorted(set(approved) - set(by_digest))
    if unknown:
        raise ApplyError(f"approved digests match no proposed diff: {unknown}")
    chosen = sorted((by_digest[a] for a in set(approved)), key=lambda d: _order(d.path))

    todo: list[tuple[FileDiff, Path]] = []
    conflicts: list[str] = []
    for diff in chosen:
        target = _target(vault, diff.path)
        current = _read(target)
        now = None if current is None else sha256_text(current)
        if now == sha256_text(diff.after_text):
            continue  # already written (e.g. a retried apply)
        if now != diff.before_sha256:
            conflicts.append(diff.path)
            continue
        if diff.path.startswith(RAW_DAILY_FOLDER) and (
            current is None or not only_ids_added(current, diff.after_text)
        ):
            raise ApplyError(f"{diff.path}: raw notes may only get task IDs")
        todo.append((diff, target))
    if conflicts:
        raise ApplyConflict(conflicts)

    for diff, target in todo:
        _write(target, diff.after_text)
        written = _read(target)
        if written is None or sha256_text(written) != sha256_text(diff.after_text):
            raise ApplyError(f"{diff.path}: read back differs from the approved text")
    return [d.path for d in chosen]
