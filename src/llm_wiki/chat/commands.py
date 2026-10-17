"""Slash commands for the chat front door: text → what to do (pure, no I/O).

    /ingest [YYYY-MM-DD | path]     start an ingest (default: today's note)
    /review                         show the open review again
    /approve [except f2 f5]         approve all files, or all but some
    /reject                         write nothing
    /revise <what to change>        the agent reads the note again with this feedback
    /edit f3                        (next lines: the page's full new text)
    /tasks m1=project-x m2=none     task → project answers
    /wins w1 w2  |  /wins none      pick done tasks as wins
    /remember <rule>                remember a rule (proposed in the review)
    /alias Sammy = person-sam-ortiz remember a name for a page (proposed too)
    /help

Answers become the same `ReviewAnswer` dicts the runner checks, so every rule of the
review (digests, ids, limits) still applies; this module only translates.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any, Literal

from llm_wiki.domain.changes import RAW_DAILY_FOLDER

HELP = """\
Commands:
- `/ingest [YYYY-MM-DD]`: ingest a daily note (default: today's)
- `/review`: show the open review again
- `/approve` · `/approve except f2 f5` · `/reject`
- `/revise <what to change>`: the agent reads the note again with your feedback
- `/edit f3` then, on the next lines, the page's full new text
- `/tasks m1=project-x m2=none`: task → project answers
- `/wins w1 w2` or `/wins none`: pick done tasks as wins
- `/remember <rule>`: a rule both agents follow (saved if you approve)
- `/alias Sammy = person-sam-ortiz`: another name for a page (saved if you approve)
Anything without a leading `/` is a question for the assistant."""

Kind = Literal["ingest", "review", "answer", "remember", "help", "error"]


@dataclass(frozen=True)
class ChatCommand:
    """A parsed slash command.

    Attributes:
        kind: "ingest" (start), "review" (show), "answer" (resume the review with
            `answer`), "remember" (add `rule` or `alias` to what the review already
            remembers), "help", or "error" (`message` says why).
        note_path: For "ingest": the vault-relative note.
        answer: For "answer": the `ReviewAnswer` dict (or "approve"/"reject").
        message: For "error": what was wrong.
        rule: For "remember": a rule to add.
        alias: For "remember": (name, page slug) to add.
    """

    kind: Kind
    note_path: str | None = None
    answer: dict[str, Any] | str | None = None
    message: str = ""
    rule: str | None = None
    alias: tuple[str, str] | None = None


def is_command(text: str) -> bool:
    """Return whether a chat message is a slash command."""
    return text.lstrip().startswith("/")


def _note_path(arg: str, today: datetime.date) -> str | None:
    folder = RAW_DAILY_FOLDER.rstrip("/")
    if not arg:
        return f"{folder}/{today.isoformat()}.md"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", arg):
        return f"{folder}/{arg}.md"
    if arg.endswith(".md") and ".." not in arg and not arg.startswith("/"):
        return arg
    return None


def _error(message: str) -> ChatCommand:
    return ChatCommand("error", message=f"{message}\n\n{HELP}")


def parse_command(text: str, today: datetime.date | None = None) -> ChatCommand:
    """Translate one slash-command message."""
    today = today or datetime.date.today()
    first, _, rest_lines = text.strip().partition("\n")
    name, _, arg = first.strip().partition(" ")
    name, arg = name.lower(), arg.strip()

    if name == "/help":
        return ChatCommand("help", message=HELP)
    if name == "/ingest":
        path = _note_path(arg, today)
        if path is None:
            return _error(f"`/ingest {arg}`: give a date (YYYY-MM-DD) or a note path")
        return ChatCommand("ingest", note_path=path)
    if name == "/review":
        return ChatCommand("review")
    if name == "/reject":
        return ChatCommand("answer", answer="reject")
    if name == "/approve":
        if not arg:
            return ChatCommand("answer", answer="approve")
        words = arg.replace(",", " ").split()
        ids = words[1:] if words[0].lower() == "except" else []
        # "/approve f2" could mean "only f2": require "except" so it can't be misread
        if not ids or not all(re.fullmatch(r"f\d+", i) for i in ids):
            return _error("`/approve except f2 f5`: list the file ids to leave out")
        return ChatCommand("answer", answer={"action": "approve", "reject": ids})
    if name == "/revise":
        feedback = " ".join(f"{arg}\n{rest_lines}".split())
        if not feedback:
            return _error(
                "`/revise` needs what to change, e.g. `/revise line 9 is Atlas`"
            )
        return ChatCommand("answer", answer={"action": "revise", "feedback": feedback})
    if name == "/edit":
        if not re.fullmatch(r"f\d+", arg) or not rest_lines.strip():
            return _error("`/edit f3`, then the page's full new text on the next lines")
        body = rest_lines.rstrip() + "\n"
        return ChatCommand(
            "answer", answer={"action": "edit", "file": arg, "text": body}
        )
    if name == "/tasks":
        pairs = [w.split("=", 1) for w in arg.replace(",", " ").split()]
        if not pairs or not all(len(p) == 2 and p[0] and p[1] for p in pairs):
            return _error(
                "`/tasks m1=project-x m2=none`: task id = project slug or none"
            )
        return ChatCommand("answer", answer={"action": "answer", "tasks": dict(pairs)})
    if name == "/wins":
        ids = arg.replace(",", " ").split()
        if not ids:
            return _error("`/wins w1 w2` (or `/wins none`)")
        picked = [] if ids == ["none"] else ids
        return ChatCommand("answer", answer={"action": "answer", "wins": picked})
    if name == "/remember":
        rule = " ".join(f"{arg}\n{rest_lines}".split())
        if not rule:
            return _error("`/remember <rule>`, e.g. `/remember Don't file lunch plans`")
        return ChatCommand("remember", rule=rule)
    if name == "/alias":
        alias, _, slug = arg.partition("=")
        alias, slug = " ".join(alias.split()), slug.strip()
        if not alias or not slug or " " in slug:
            return _error("`/alias Sammy = person-sam-ortiz`: a name = a page slug")
        return ChatCommand("remember", alias=(alias, slug))
    return _error(f"unknown command `{name}`")
