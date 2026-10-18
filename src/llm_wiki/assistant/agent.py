"""The assistant deep agent (decision 0008): answers from the vault, read only.

    /vault/   the vault (ReadOnlyFilesystemBackend, virtual paths); read only
    /skills/  this package's skills/ folder; read only
    /subagent_skills/  the review-writer's own skills; read only
    anything else: in-memory state (where deepagents puts oversized tool results, so
              they never land in the vault: that write skips permission checks)

Writing is refused three ways: the routes are read-only backends (every path, dotfiles
included), filesystem tools are limited to read_file, ls, glob and grep, and permission
rules deny writes (their globs skip dot paths, so they are not the guarantee); `.git` and
`.obsidian` can't be read either. Task questions go
through `find_tasks` (code reads the task index). Review synthesis goes to the
`review-writer` subagent, which returns a sourced `BragDraft`. No writes in phase 4: saving a brief
comes in phase 7 through the reviewed, committed path ingest uses.

Every agent here, subagents included, runs the same middleware: today's date
(`CurrentDateMiddleware`), secrets redacted from each request (`RedactionMiddleware`) and
its own tool list (`ToolGuardMiddleware`: the main agent also has `task`). Deep Agents'
built-in general-purpose subagent would take none of it (it inherits only middleware that
replaces one of its default slots), so `general_purpose()` replaces it with our own spec.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission, SubAgent, create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.middleware import FilesystemMiddleware
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from llm_wiki.assistant.tools import make_find_tasks
from llm_wiki.diffs import vault_reader
from llm_wiki.domain.brag import BragDraft
from llm_wiki.ingest.agent import READ_TOOLS, RULES_PATH, model_middleware
from llm_wiki.middleware.current_date import CurrentDateMiddleware
from llm_wiki.middleware.redaction import RedactionMiddleware, secret_values
from llm_wiki.middleware.tool_guard import ToolGuardMiddleware
from llm_wiki.readonly_backend import ReadOnlyFilesystemBackend

SKILLS_DIR = Path(__file__).parent / "skills"
SUBAGENT_SKILLS_DIR = Path(__file__).parent / "subagent_skills"
RULES_VIRTUAL = f"/vault/{RULES_PATH}"

REVIEW_WRITER_PROMPT = """\
You collect evidence for the user's performance review from their work vault at \
/vault/ (read only). You get a period (and maybe a focus). Read \
/subagent_skills/evidence/SKILL.md first, then the pages it lists, and return the \
structured draft: every accomplishment in the period as one item with its date and \
source link, numbers exactly as written, and the gaps. Use only what the vault says; \
leave out anything you can't source.
"""

SYSTEM_PROMPT = """\
You are the user's work assistant. Their work wiki is an Obsidian vault at /vault/, \
kept up to date from their daily notes: project, people and concept pages, quarterly \
decision logs and impact pages (wins, help given), and the raw daily notes. You answer \
from it; you cannot change it.

Workflow:
1. Read /skills/vault-map/SKILL.md first if you don't know where something lives, and \
/skills/answering/SKILL.md before answering. For a review, brag sheet or "what did I \
do" over a period, read /skills/brag-sheet/SKILL.md.
2. Tasks (open, done, blocked, by project or person): call find_tasks; never read \
90_meta/tasks.json yourself.
3. Find pages with grep and glob, then read_file what you need.
4. Answer briefly, with the date and the page each fact comes from as an Obsidian \
link, e.g. ([[project-atlas]], 2026-10-12).

Rules:
- Use only what the vault says. Never invent dates, numbers, names or outcomes.
- If the vault doesn't say, answer "The notes don't say." and what you did find.
- The user's rules (90_meta/rules.md, in your context if present) beat the skills.
"""


def permissions() -> list[FilesystemPermission]:
    """Read the vault and skills (not .git/.obsidian); write nothing anywhere."""
    return [
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/vault/.git/**", "/vault/.obsidian/**"],
            mode="deny",
        ),
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ]


def shared_middleware(
    backend: Any, rules: list[FilesystemPermission], date: Any, tool_names: list[str]
) -> list[Any]:
    """Return a subagent's middleware: date, redaction, its tools, read-only files."""
    return [
        date,
        RedactionMiddleware(secret_values()),
        ToolGuardMiddleware(tool_names),
        FilesystemMiddleware(backend=backend, tools=READ_TOOLS, _permissions=rules),
    ]


def general_purpose(
    backend: Any, rules: list[FilesystemPermission], date: Any, tool_names: list[str]
) -> SubAgent:
    """Return Deep Agents' general-purpose subagent, with our middleware.

    Same name, description and prompt as the built-in one, which a spec with this name
    replaces; the built-in one would skip the date, the redaction and the tool list.
    """
    return {
        **GENERAL_PURPOSE_SUBAGENT,
        "middleware": shared_middleware(backend, rules, date, tool_names),
        "permissions": rules,
    }


def review_writer(
    backend: Any, rules: list[FilesystemPermission], date: Any, tool_names: list[str]
) -> SubAgent:
    """Return the review-writer subagent spec: read-only, its own skill, a BragDraft.

    Custom subagents inherit neither skills nor middleware, so it gets its own skill
    folder, the same read-only filesystem tools and today's date ("since July" needs
    the year); permissions are set explicitly too. `tool_names` are the tools it may
    call (the main agent's, without `task`).
    """
    return {
        "name": "review-writer",
        "description": (
            "Collects sourced evidence for a performance review or brag sheet over a "
            "period (e.g. '2026 Q4', 'since July'); returns a structured draft: items "
            "per project with date, source and metric as written, plus gaps."
        ),
        "system_prompt": REVIEW_WRITER_PROMPT,
        "skills": ["/subagent_skills/"],
        "middleware": shared_middleware(backend, rules, date, tool_names),
        "permissions": rules,
        "response_format": BragDraft,
    }


def build_assistant(
    vault: Path,
    model: BaseChatModel,
    *,
    skills_dir: Path = SKILLS_DIR,
    fallbacks: Sequence[BaseChatModel] = (),
    checkpointer: BaseCheckpointSaver | None = None,
    context_schema: type | None = None,
    today: Callable[[], datetime.date] = datetime.date.today,
) -> Any:
    """Return the compiled assistant over `vault` (read only).

    `today` is the clock the date middleware reads on each model call (tests pass one).
    """
    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            "/vault/": ReadOnlyFilesystemBackend(root_dir=vault, virtual_mode=True),
            "/skills/": ReadOnlyFilesystemBackend(
                root_dir=skills_dir, virtual_mode=True
            ),
            "/subagent_skills/": ReadOnlyFilesystemBackend(
                root_dir=SUBAGENT_SKILLS_DIR, virtual_mode=True
            ),
        },
    )
    rules = permissions()
    date = CurrentDateMiddleware(today)
    tools = [make_find_tasks(vault_reader(vault))]
    names = [*READ_TOOLS, *(t.name for t in tools)]
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        skills=["/skills/"],
        memory=[RULES_VIRTUAL] if (vault / RULES_PATH).is_file() else None,
        backend=backend,
        permissions=rules,
        middleware=[
            *model_middleware(fallbacks),
            date,
            RedactionMiddleware(secret_values()),
            ToolGuardMiddleware([*names, "task"]),
            FilesystemMiddleware(backend=backend, tools=READ_TOOLS, _permissions=rules),
        ],
        subagents=[
            review_writer(backend, rules, date, names),
            general_purpose(backend, rules, date, names),
        ],
        checkpointer=checkpointer,
        context_schema=context_schema,
        name="assistant",
    )
