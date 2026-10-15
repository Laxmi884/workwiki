"""Render edits into approvable diffs: apply every edit for a file to its current text.

The core is pure: `render_diffs(edits, read)` gets file text through `read(path)`, which
returns None for a missing file. `vault_reader(vault)` is the real reader. Nothing here
writes; applying approved diffs is phase 3.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import yaml

from llm_wiki.domain.changes import (
    RAW_DAILY_FOLDER,
    AddAlias,
    AppendId,
    AppendToSection,
    CreateFile,
    Edit,
    FileDiff,
    ReplaceFile,
    ReplaceSection,
    UserText,
    sha256_text,
)
from llm_wiki.frontmatter import split_frontmatter
from llm_wiki.tasks_syntax import append_id

VaultReader = Callable[[str], str | None]

# "## Name": level = number of "#", name compared case- and spacing-insensitively.
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<name>.*?)[ \t]*$")
_FENCE_PREFIXES = ("```", "~~~")
_ID_SUFFIX_RE = re.compile(r" \^t-[0-9a-z]{6}$")


class RenderError(ValueError):
    """An edit cannot be applied to the file as it is now."""


def vault_reader(vault: Path) -> VaultReader:
    """Return a reader for files under `vault` that refuses paths escaping it.

    Text is decoded from bytes, so CRLF line endings and the hash stay exact
    (`Path.read_text` would turn CRLF into LF).
    """
    root = vault.resolve()

    def read(path: str) -> str | None:
        full = (root / path).resolve()  # follows symlinks
        if not full.is_relative_to(root):
            raise RenderError(f"{path} resolves outside the vault")
        if not full.is_file():
            return None
        return full.read_bytes().decode("utf-8")

    return read


# --- one edit --------------------------------------------------------------------


def _heading_key(line: str) -> tuple[int, str] | None:
    m = _HEADING_RE.match(line.rstrip("\r\n"))
    if m is None:
        return None
    return len(m.group("hashes")), " ".join(m.group("name").split()).casefold()


def _find_section(lines: Sequence[str], heading: str) -> tuple[int, int] | None:
    """Return (heading index, end index) of the first matching heading, else None.

    The section ends at the next heading of the same or a higher level. Headings
    inside code fences do not count.
    """
    wanted = _heading_key(heading)
    start: int | None = None
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(_FENCE_PREFIXES):
            in_fence = not in_fence
            continue
        key = None if in_fence else _heading_key(line)
        if key is None:
            continue
        if start is None and key == wanted:
            start = i
        elif start is not None and wanted is not None and key[0] <= wanted[0]:
            return start, i
    return None if start is None else (start, len(lines))


def _eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _with_final_eol(lines: list[str], eol: str) -> list[str]:
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines = [*lines[:-1], lines[-1] + eol]
    return lines


def _append_to_section(text: str, edit: AppendToSection) -> str:
    """Insert after the section's last non-blank line; add the heading at the end if missing."""
    eol = _eol(text)
    lines = text.splitlines(keepends=True)
    new = [line + eol for line in edit.lines]
    found = _find_section(lines, edit.heading)

    if found is None:
        lines = _with_final_eol(lines, eol)
        gap = [eol] if lines and lines[-1].strip() else []
        return "".join([*lines, *gap, edit.heading + eol, *new])

    start, end = found
    insert_at = start + 1
    for i in range(end - 1, start, -1):
        if lines[i].strip():
            insert_at = i + 1
            break
    head = _with_final_eol(lines[:insert_at], eol)
    return "".join([*head, *new, *lines[insert_at:]])


def _append_id(text: str, edit: AppendId) -> str:
    lines = text.splitlines(keepends=True)
    if not 1 <= edit.line_no <= len(lines):
        raise RenderError(
            f"line {edit.line_no} is outside the file ({len(lines)} lines)"
        )
    i = edit.line_no - 1
    try:
        lines[i] = append_id(lines[i], edit.task_id)
    except ValueError as exc:
        raise RenderError(f"line {edit.line_no}: {exc}") from exc
    return "".join(lines)


def apply_edit(text: str | None, edit: Edit) -> str:
    """Return the new text after one edit. `text` is None for a file that doesn't exist.

    Raises:
        RenderError: The edit doesn't fit the file (exists / missing / not a task line).
    """
    if isinstance(edit, ReplaceFile | UserText):
        return edit.text
    if isinstance(edit, CreateFile):
        if text is not None:
            raise RenderError("file already exists")
        return edit.text
    if text is None:
        raise RenderError("file does not exist")
    if isinstance(edit, AppendId):
        return _append_id(text, edit)
    if isinstance(edit, AddAlias):
        return _add_alias(text, edit)
    if isinstance(edit, ReplaceSection):
        return _replace_section(text, edit)
    return _append_to_section(text, edit)


def _replace_section(text: str, edit: ReplaceSection) -> str:
    """Replace the section's body; if missing, add it after the first `# ` title."""
    eol = _eol(text)
    lines = _with_final_eol(text.splitlines(keepends=True), eol)
    body = [line + eol for line in edit.lines]
    found = _find_section(lines, edit.heading)
    if found is not None:
        start, end = found
        gap = [eol] if end < len(lines) else []
        return "".join([*lines[: start + 1], *body, *gap, *lines[end:]])
    title = next(
        (i for i, line in enumerate(lines) if (_heading_key(line) or (0,))[0] == 1),
        None,
    )
    if title is None:
        gap = [eol] if lines and lines[-1].strip() else []
        return "".join([*lines, *gap, edit.heading + eol, *body])
    rest = lines[title + 1 :]
    return "".join(
        [
            *lines[: title + 1],
            eol,
            edit.heading + eol,
            *body,
            *([eol] if rest and rest[0].strip() else []),
            *rest,
        ]
    )


def _add_alias(text: str, edit: AddAlias) -> str:
    """Add `edit.alias` to the frontmatter `aliases` list; the body is kept as is."""
    split = split_frontmatter(text)
    if split.error is not None:
        raise RenderError(f"can't add alias: {split.error}")
    meta = dict(split.metadata)
    aliases = meta.get("aliases") or []
    aliases = list(aliases) if isinstance(aliases, list) else [aliases]
    if any(str(a).casefold() == edit.alias.casefold() for a in aliases):
        return text
    meta["aliases"] = [*aliases, edit.alias]
    return f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)}---\n" + (
        split.body
    )


# --- one file --------------------------------------------------------------------


def _check_only_ids_added(before: str, after: str) -> None:
    """Decision 0001: a raw note may only gain ` ^t-xxxxxx` at the end of task lines."""
    old = before.splitlines(keepends=True)
    new = after.splitlines(keepends=True)
    if len(old) != len(new):
        raise RenderError("raw note: line count changed")
    for n, (a, b) in enumerate(zip(old, new, strict=True), start=1):
        if a == b:
            continue
        a_body, b_body = a.rstrip("\r\n"), b.rstrip("\r\n")
        ending_kept = a[len(a_body) :] == b[len(b_body) :]
        suffix = _ID_SUFFIX_RE.search(b_body)
        if not (ending_kept and suffix and b_body[: suffix.start()] == a_body.rstrip()):
            raise RenderError(f"raw note: line {n} changed beyond adding a task ID")


def render_file(
    path: str, before: str | None, edits: Sequence[Edit]
) -> FileDiff | None:
    """Apply `edits` (all for `path`) to `before`. Returns None if nothing changes.

    `CreateFile` edits run first, so a page can be created and appended to in one batch;
    `UserText` (the user's own text) runs last and wins.

    Raises:
        RenderError: An edit doesn't fit, or a raw note would change beyond task IDs.
    """
    rank = {CreateFile: 0, UserText: 2}
    ordered = sorted(edits, key=lambda e: rank.get(type(e), 1))  # stable
    text = before
    for edit in ordered:
        text = apply_edit(text, edit)
    if text is None or text == before:
        return None
    if path.startswith(RAW_DAILY_FOLDER):
        _check_only_ids_added(before or "", text)

    diff = difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        text.splitlines(keepends=True),
        fromfile="/dev/null" if before is None else f"a/{path}",
        tofile=f"b/{path}",
    )
    return FileDiff(
        path=path,
        before_sha256=None if before is None else sha256_text(before),
        after_text=text,
        unified_diff="".join(diff),
        edits=tuple(ordered),
    )


# --- all files -------------------------------------------------------------------


def render_diffs(
    edits: Iterable[Edit], read: VaultReader
) -> tuple[list[FileDiff], list[str]]:
    """Group edits by file (first-seen order) and render one diff per changed file.

    A file whose edits don't apply is skipped with a warning; the others still render.
    """
    by_path: dict[str, list[Edit]] = {}
    for edit in edits:
        by_path.setdefault(edit.path, []).append(edit)

    diffs: list[FileDiff] = []
    warnings: list[str] = []
    for path, file_edits in by_path.items():
        try:
            diff = render_file(path, read(path), file_edits)
        except RenderError as exc:
            warnings.append(f"{path}: {exc}; skipped")
            continue
        if diff is not None:
            diffs.append(diff)
    return diffs, warnings
