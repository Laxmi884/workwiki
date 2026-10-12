"""Split a Markdown note into YAML frontmatter and body, keeping file line numbers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

_FENCE = "---"


@dataclass(frozen=True, slots=True)
class SplitNote:
    """A note split into frontmatter and body.

    `body_start_line` is the 1-based file line where `body` begins, so body line k
    (0-based) is file line `body_start_line + k`. `error` is set when frontmatter
    was present but unusable; `metadata` is then empty.
    """

    metadata: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    body_start_line: int = 1
    error: str | None = None


def split_frontmatter(text: str) -> SplitNote:
    """Split `text` into frontmatter and body. Never raises on bad input."""
    text = text.removeprefix("﻿")  # BOM some editors add
    lines = text.splitlines(keepends=True)

    if not lines or lines[0].strip() != _FENCE:
        return SplitNote(body=text)

    close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if close is None:
        return SplitNote(body=text, error="frontmatter has no closing '---'")

    yaml_text = "".join(lines[1:close])
    body = "".join(lines[close + 1 :])
    body_start_line = close + 2  # line after the closing fence, 1-based

    try:
        loaded = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return SplitNote(
            body=body, body_start_line=body_start_line, error=f"invalid YAML: {exc}"
        )

    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        return SplitNote(
            body=body,
            body_start_line=body_start_line,
            error=f"frontmatter is {type(loaded).__name__}, not key/value pairs",
        )

    return SplitNote(metadata=loaded, body=body, body_start_line=body_start_line)
