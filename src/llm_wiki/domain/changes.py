"""Proposed vault changes: small `Edit`s, merged per file into an approvable `FileDiff`.

Nodes emit edits; `render_diffs` (phase 2b) applies all edits for one file to its current
text and produces one `FileDiff`. Phase 3 shows diffs for approval and applies them only
if the file still hashes to `before_sha256`.

Where each edit may write is part of its type (CLAUDE.md, decision 0001):
- `AppendId` only touches `00_raw/daily/` (the one code-only write to raw notes).
- `AppendToSection` and `CreateFile` only touch the agent-owned folders.
- `ReplaceFile` only rewrites code-owned `90_meta/*.json` files (e.g. the task index).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)

RAW_DAILY_FOLDER = "00_raw/daily/"
META_FOLDER = "90_meta/"
WRITABLE_FOLDERS = (
    "05_generated/",
    "10_projects/",
    "15_concepts/",
    "20_people/",
    "30_decisions/",
    "40_impact/",
    "90_meta/",
)


def _vault_relative(path: str) -> str:
    """Accept only clean vault-relative POSIX paths (no leading "/", "..", "." or backslash)."""
    parts = PurePosixPath(path).parts
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or ".." in parts
        or "." in parts
        or path != PurePosixPath(path).as_posix()  # catches "a//b" and "a/"
    ):
        raise ValueError(f"not a clean vault-relative path: {path!r}")
    return path


def _writable_markdown(path: str) -> str:
    if not path.startswith(WRITABLE_FOLDERS) or not path.endswith(".md"):
        raise ValueError(f"agents may not write {path!r}")
    return path


def _raw_daily_note(path: str) -> str:
    if not path.startswith(RAW_DAILY_FOLDER) or not path.endswith(".md"):
        raise ValueError(f"task IDs go only into {RAW_DAILY_FOLDER}*.md, not {path!r}")
    return path


def _meta_json(path: str) -> str:
    if not path.startswith(META_FOLDER) or not path.endswith(".json"):
        raise ValueError(
            f"whole-file rewrites only for {META_FOLDER}*.json, not {path!r}"
        )
    return path


VaultPath = Annotated[str, AfterValidator(_vault_relative)]
WritablePath = Annotated[VaultPath, AfterValidator(_writable_markdown)]
RawDailyPath = Annotated[VaultPath, AfterValidator(_raw_daily_note)]
MetaJsonPath = Annotated[VaultPath, AfterValidator(_meta_json)]


class AppendId(BaseModel):
    """Append ` ^t-xxxxxx` to one task line of a raw daily note (decision 0001)."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["append_id"] = "append_id"
    path: RawDailyPath
    line_no: int = Field(ge=1)
    task_id: str = Field(pattern=r"^t-[0-9a-z]{6}$")


class AppendToSection(BaseModel):
    """Append lines at the end of a heading's section, adding the heading if missing.

    Attributes:
        heading: The full heading line, e.g. "## Log".
        lines: Lines to append, one string per line.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["append_to_section"] = "append_to_section"
    path: WritablePath
    heading: str = Field(pattern=r"^#{1,6} \S")
    lines: tuple[str, ...] = Field(min_length=1)

    @field_validator("lines")
    @classmethod
    def _single_lines(cls, lines: tuple[str, ...]) -> tuple[str, ...]:
        if any("\n" in line or "\r" in line for line in lines):
            raise ValueError("each appended line must be a single line")
        return lines


class CreateFile(BaseModel):
    """Create a new file. Rendering fails if the file already exists."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["create_file"] = "create_file"
    path: WritablePath
    text: str


class ReplaceFile(BaseModel):
    """Write a whole code-owned file (create or overwrite), e.g. `90_meta/tasks.json`."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["replace_file"] = "replace_file"
    path: MetaJsonPath
    text: str


# One of the four, chosen by `kind` when parsing a dict.
Edit = Annotated[
    AppendId | AppendToSection | CreateFile | ReplaceFile, Field(discriminator="kind")
]


def sha256_text(text: str) -> str:
    """Return the hex sha256 of `text` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FileDiff(BaseModel):
    """The full proposed change to one file: what a reviewer approves.

    Attributes:
        path: Vault-relative file path.
        before_sha256: Hash of the current text; None for a new file. Apply refuses to
            write if the file no longer matches.
        after_text: The complete new file text.
        unified_diff: Human-readable diff of before → after.
        edits: The edits that produced this diff, in order.
        digest: Hash binding path, before and after; approval is for this digest.
    """

    model_config = ConfigDict(frozen=True)

    path: VaultPath
    before_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    after_text: str
    unified_diff: str
    edits: tuple[Edit, ...] = Field(min_length=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def digest(self) -> str:
        """Return the approval digest: sha256 over path, before hash and after hash."""
        material = {
            "path": self.path,
            "before_sha256": self.before_sha256,
            "after_sha256": sha256_text(self.after_text),
        }
        return sha256_text(json.dumps(material, sort_keys=True, separators=(",", ":")))
