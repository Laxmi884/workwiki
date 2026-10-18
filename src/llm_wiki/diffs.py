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
    RULES_HEADING,
    RULES_PAGE,
    AddAlias,
    AppendId,
    CreateFile,
    Edit,
    FileDiff,
    RememberRules,
    ReplaceFile,
    ReplaceSection,
    UserText,
    sha256_text,
)
from llm_wiki.frontmatter import split_frontmatter
from llm_wiki.tasks_syntax import append_id, line_changed_beyond_ids

VaultReader = Callable[[str], str | None]

# "## Name": level = number of "#", name compared case- and spacing-insensitively.
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<name>.*?)[ \t]*$")
_FENCE_PREFIXES = ("```", "~~~")


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


def _append_to_section(text: str, heading: str, added: Sequence[str]) -> str:
    """Insert after the section's last non-blank line; add the heading at the end if missing."""
    eol = _eol(text)
    lines = text.splitlines(keepends=True)
    new = [line + eol for line in added]
    found = _find_section(lines, heading)

    if found is None:
        lines = _with_final_eol(lines, eol)
        gap = [eol] if lines and lines[-1].strip() else []
        return "".join([*lines, *gap, heading + eol, *new])

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
    if isinstance(edit, RememberRules):
        return _append_to_section(
            RULES_PAGE if text is None else text, RULES_HEADING, edit.lines
        )
    if text is None:
        raise RenderError("file does not exist")
    if isinstance(edit, AppendId):
        return _append_id(text, edit)
    if isinstance(edit, AddAlias):
        return _add_alias(text, edit)
    if isinstance(edit, ReplaceSection):
        return _replace_section(text, edit)
    return _append_to_section(text, edit.heading, edit.lines)


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


_ALIASES_KEY = re.compile(r"^aliases[ \t]*:(?P<rest>.*)$")
_FLOW_LIST = re.compile(r"^[ \t]*\[(?P<items>.*)\](?P<tail>[ \t]*(?:#.*)?)$")
_BLOCK_ITEM = re.compile(r"^(?P<indent>[ \t]*)-[ \t]")


def _yaml_item(value: str) -> str:
    """Return `value` as a YAML list item, quoted only if it needs it ("a: b" → 'a: b')."""
    flow = yaml.safe_dump(
        [value], default_flow_style=True, allow_unicode=True, width=10**6
    )
    return flow.strip()[1:-1]


def _alias_lines(front: list[str], alias: str, eol: str) -> list[str]:
    """Return the frontmatter lines (between the fences) with `alias` added.

    Edits the `aliases` lines as text: a one-line flow list or scalar gets the name
    appended in place, a block list gets one more item at its indent, and a missing
    key gets a block list (Obsidian's style). Every other line is kept as is.
    """
    item = _yaml_item(alias)
    for i, line in enumerate(front):
        m = _ALIASES_KEY.match(line.rstrip("\r\n"))
        if m is None:
            continue
        rest = m.group("rest")
        if flow := _FLOW_LIST.match(rest):
            items = flow.group("items").strip()
            joined = f"{items}, {item}" if items else item
            return [
                *front[:i],
                f"aliases: [{joined}]{flow.group('tail')}{eol}",
                *front[i + 1 :],
            ]
        if rest.strip():  # one scalar
            return [
                *front[:i],
                f"aliases: [{rest.strip()}, {item}]{eol}",
                *front[i + 1 :],
            ]
        last, indent = i, "  "
        for j in range(i + 1, len(front)):
            if (block := _BLOCK_ITEM.match(front[j])) is None:
                break
            last, indent = j, block.group("indent")
        return [*front[: last + 1], f"{indent}- {item}{eol}", *front[last + 1 :]]
    return [*front, f"aliases:{eol}", f"  - {item}{eol}"]


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _add_alias(text: str, edit: AddAlias) -> str:
    """Add `edit.alias` to the frontmatter `aliases`, changing nothing else.

    Only the aliases lines are rewritten (comments, quoting and key order stay), and
    the result is parsed back: unless exactly the alias was added, the edit is
    refused rather than risk changing other frontmatter.

    Raises:
        RenderError: Broken frontmatter, or a shape this can't edit safely (e.g. a
            flow list over several lines).
    """
    split = split_frontmatter(text)
    if split.error is not None:
        raise RenderError(f"can't add alias: {split.error}")
    aliases = _as_list(split.metadata.get("aliases"))
    if any(str(a).casefold() == edit.alias.casefold() for a in aliases):
        return text

    eol = _eol(text)
    lines = text.splitlines(keepends=True)
    close = (
        next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if lines and lines[0].lstrip("\ufeff").strip() == "---"
        else None
    )
    if close is None:  # no frontmatter yet
        new = f"---{eol}aliases:{eol}  - {_yaml_item(edit.alias)}{eol}---{eol}" + text
    else:
        front = _alias_lines(lines[1:close], edit.alias, eol)
        new = "".join([lines[0], *front, *lines[close:]])

    after = split_frontmatter(new)
    expected = {**split.metadata, "aliases": [*aliases, edit.alias]}
    if (
        after.error is not None
        or after.metadata != expected
        or after.body != split.body
    ):
        raise RenderError(
            f"can't add alias {edit.alias!r} without changing other frontmatter; "
            "add it to the page by hand"
        )
    return new


# --- one file --------------------------------------------------------------------


def _check_only_ids_added(before: str, after: str) -> None:
    """Decision 0001: a raw note may only gain ` ^t-xxxxxx` at the end of task lines."""
    line = line_changed_beyond_ids(before, after)
    if line == 0:
        raise RenderError("raw note: line count changed")
    if line is not None:
        raise RenderError(f"raw note: line {line} changed beyond adding a task ID")


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
