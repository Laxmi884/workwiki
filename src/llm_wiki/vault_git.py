"""Commit what apply wrote to the vault's git repo (phase 3e): those files and nothing else.

Every applied review becomes one commit, so `git revert <commit>` undoes it. Only the
paths apply wrote are staged and committed (`git commit -- <paths>` commits just those),
so the user's own uncommitted edits elsewhere in the vault stay out of it. The commit is
best effort: the files are already written, so a missing repo or a failed commit is a
warning, not an error.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=vault, capture_output=True, text=True, check=False
    )


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
    if Path(top.stdout.strip()).resolve() != vault.resolve():
        return None, [
            f"the vault is inside another git repository ({top.stdout.strip()}); "
            "nothing committed"
        ]
    added = _git(vault, "add", "--", *paths)
    if added.returncode != 0:
        return None, [f"git add failed: {added.stderr.strip()}"]
    if _git(vault, "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return None, []  # the files already match the last commit
    result = _git(vault, "commit", "--quiet", "-m", message, "--", *paths)
    if result.returncode != 0:
        return None, [f"git commit failed: {result.stderr.strip()}"]
    return _git(vault, "rev-parse", "HEAD").stdout.strip(), []
