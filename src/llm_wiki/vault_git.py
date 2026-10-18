"""Commit what apply wrote to the vault's git repo (phase 3e): those files and nothing else.

Every applied review becomes one commit, so `git revert <commit>` undoes it. Only the
paths apply wrote are staged and committed (`git commit -- <paths>` commits just those),
so the user's own uncommitted edits elsewhere in the vault stay out of it. The commit is
best effort: the files are already written, so a missing repo or a failed commit is a
warning, not an error; a failed commit unstages what it staged.

Commits carry their own identity, `WIKI_GIT_AUTHOR` ("Name <email>", default
"llm-wiki <llm-wiki@localhost>"), never the machine's git config: a server container has
none, and `git log --author=llm-wiki` then finds every commit the agent made.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

DEFAULT_AUTHOR = "llm-wiki <llm-wiki@localhost>"
_AUTHOR = re.compile(r"^(?P<name>[^<>]+?)\s*<(?P<email>[^<>\s]+)>$")


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=vault, capture_output=True, text=True, check=False
    )


def commit_author() -> tuple[str, str]:
    """Return (name, email) from `WIKI_GIT_AUTHOR`, or the default.

    Raises:
        ValueError: The setting isn't "Name <email>".
    """
    value = os.environ.get("WIKI_GIT_AUTHOR", "").strip() or DEFAULT_AUTHOR
    match = _AUTHOR.match(value)
    if match is None:
        raise ValueError(
            f"WIKI_GIT_AUTHOR must look like 'Name <email>', not {value!r}"
        )
    return match["name"], match["email"]


def commit_paths(
    vault: Path, paths: Sequence[str], message: str
) -> tuple[str | None, list[str]]:
    """Commit exactly `paths` (vault-relative) with `message`.

    Returns:
        (commit sha, or None when nothing was committed; warnings).
    """
    if not paths:
        return None, []
    top = _git(vault, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return None, ["the vault is not a git repository; nothing committed"]
    # samefile, not ==: on macOS "~/workdev" is "~/WorkDev", but git prints the latter
    if not Path(top.stdout.strip()).samefile(vault):
        return None, [
            f"the vault is inside another git repository ({top.stdout.strip()}); "
            "nothing committed"
        ]
    try:
        name, email = commit_author()
    except ValueError as exc:
        return None, [f"{exc}; nothing committed"]
    added = _git(vault, "add", "--", *paths)
    if added.returncode != 0:
        return None, [f"git add failed: {added.stderr.strip()}"]
    if _git(vault, "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return None, []  # the files already match the last commit
    result = _git(
        vault,
        "-c",
        f"user.name={name}",  # the committer; --author alone leaves it to git config
        "-c",
        f"user.email={email}",
        "commit",
        "--quiet",
        f"--author={name} <{email}>",
        "-m",
        message,
        "--",
        *paths,
    )
    if result.returncode != 0:
        _git(vault, "reset", "--quiet", "--", *paths)  # leave the index as it was
        return None, [f"git commit failed: {result.stderr.strip()}"]
    return _git(vault, "rev-parse", "HEAD").stdout.strip(), []
