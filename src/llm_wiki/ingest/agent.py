"""The ingest deep agent (decision 0006 spike): reads the wiki, stages checked edits.

    /vault/   the vault, read only (ReadOnlyFilesystemBackend, virtual paths)
    /skills/  this package's skills/ folder, read only
    anything else: in-memory state (where deepagents puts oversized tool results, so
              they never land in the vault: that write skips permission checks)

The routes are read-only backends (every write refused, dot paths included), write tools
are hidden (read_file, ls, glob, grep only), and permission rules deny writes too (their
globs skip dot paths, so they are not the guarantee). The agent's only way to change the wiki is the staging tools;
each call is checked by `staging` and becomes an edit in the agent's state, or a refusal
the agent reads and can fix.
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from deepagents import DeepAgentState, FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.middleware import FilesystemMiddleware
from langchain.agents.middleware import ModelFallbackMiddleware, ModelRetryMiddleware
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from llm_wiki.domain.changes import Edit
from llm_wiki.domain.daily import DailyNote
from llm_wiki.domain.task_index import DoneTask, TaskProposal
from llm_wiki.entity_index import EntityIndex, path_for
from llm_wiki.ingest import staging
from llm_wiki.ingest.staging import (
    CURRENT_STATE,
    Filed,
    StagingContext,
    StagingError,
)
from llm_wiki.readonly_backend import ReadOnlyFilesystemBackend
from llm_wiki.remember import RULES_PATH
from llm_wiki.retries import is_transient
from llm_wiki.routing import decision_log_path, impact_path

SKILLS_DIR = Path(__file__).parent / "skills"
READ_TOOLS = ["read_file", "ls", "glob", "grep"]
RULES_VIRTUAL = f"/vault/{RULES_PATH}"

SYSTEM_PROMPT = """\
You keep a personal work wiki (an Obsidian vault) up to date from the user's daily \
notes: a second brain the user and other agents read later. You get one note and file \
what is worth keeping with the staging tools. You cannot write files: staged changes go \
to the user for review, and code checks every call (a refused call says why; fix it or \
move on).

Workflow:
1. Read the skills that fit this note (the skills list says when to use each). Always \
read /skills/wiki-filing/SKILL.md.
2. The user's rules (90_meta/rules.md) are in your context. They come from the user's \
own reviews and beat the skills when they differ.
3. The message lists every page with its sections and current state. Read a page \
(/vault/<folder>/<slug>.md) only when you need its content, e.g. to avoid repeating a \
fact or to rewrite its Current state.
4. Stage everything with the tools: `file_fact`, `update_summary`, `propose_page`, \
`map_task` (tasks marked `project ?`), `ask_user`. Make several tool calls in one turn whenever you can (e.g. all facts of a \
meeting at once): every turn costs the user a request.
5. Finish with one short line saying what you filed. Do not use write_todos or task.

Rules:
- Use only what the note says. Never invent details, numbers, reasons or people.
- Every fact cites the note line it comes from.
- Most lines give nothing worth keeping; skip routine work.
- Write each fact as 1-2 plain sentences in your own words, keeping names, dates, \
numbers and reasons from the line. Code adds the date, meeting and a link to the note.
"""


@dataclass(frozen=True)
class AgentContext:
    """Per-run input the staging tools read (immutable for the run)."""

    staging: StagingContext


class IngestAgentState(DeepAgentState):
    """The agent's state: deepagents' own plus what the staging tools collect.

    Attributes:
        staged: Accepted edits, in call order.
        filed: One record per accepted fact (line → page), for the comparison.
        questions: (note line, question) for the review.
        refusals: Every refused staging call, with the reason.
        withdrawn: (line, path, n) of facts the agent took back: n is how many facts
            were filed when it did, so only earlier ones are removed.
        mappings: (line, project or None, why) per accepted `map_task` call; the
            last per line wins (decision 0007).
    """

    staged: Annotated[list[Edit], operator.add]
    filed: Annotated[list[Filed], operator.add]
    questions: Annotated[list[tuple[int, str]], operator.add]
    refusals: Annotated[list[str], operator.add]
    withdrawn: Annotated[list[tuple[int, str, int]], operator.add]
    mappings: Annotated[list[tuple[int, str | None, str]], operator.add]


def _live(runtime: ToolRuntime) -> tuple[list[Edit], list[Filed]]:
    """Return the staging as it stands: withdrawn facts and their lines removed.

    Checks run against this view, so a withdrawn fact can be filed again elsewhere
    (or in another section of the same page). New pages stay in it while still
    empty: dropping them is for the end of the run, and mid-run it hid a page
    proposed one turn earlier ("call propose_page first", then a second proposal).
    """
    state = runtime.state
    edits, filed, _ = staging.finalize(
        state.get("staged", []),
        state.get("filed", []),
        state.get("withdrawn", []),
        drop_empty=False,
    )
    return edits, filed


def _reply(runtime: ToolRuntime, text: str, **update: Any) -> Command:
    message = ToolMessage(text, tool_call_id=runtime.tool_call_id)
    return Command(update={**update, "messages": [message]})


@tool
def file_fact(
    target: str,
    section: str,
    text: str,
    line: int,
    runtime: ToolRuntime[AgentContext],
    title: str | None = None,
) -> Command:
    """Stage one fact from the note for a wiki page.

    Args:
        target: An entity slug with a page (e.g. "person-sam-ortiz", "project-atlas"),
            or "impact" (the quarter's wins and help given) or "decision-log" (the
            quarter's decisions).
        section: The page section. Usual ones: "Notes" or "Help given" on a person;
            "Updates", "Decisions" or "Wins" on a project; "Notes" on a concept. Any
            other short heading works on person/project/concept pages (follow the
            user's rules). Fixed: "Wins" or "Help given" on impact; "Decisions" on
            decision-log.
        text: The fact, 1-2 sentences in your words. Code adds date, meeting and link.
        line: The note line number the fact comes from.
        title: Only for decision-log: the decision as a short title.
    """
    ctx = staging.with_mappings(
        runtime.context.staging, runtime.state.get("mappings", [])
    )
    try:
        edits, record = staging.stage_fact(
            ctx,
            *_live(runtime),
            target=target,
            section=section,
            text=text,
            line=line,
            title=title,
        )
    except StagingError as exc:
        return _reply(runtime, f"REFUSED: {exc}", refusals=[f"file_fact: {exc}"])
    note = f"staged on {record.path} ({record.section})"
    if ctx.entities.get(target) is None and record.path not in staging.proposed_paths(
        [*_live(runtime)[0], *edits]
    ):
        note += (
            f"; {target} has no page yet: call propose_page for it, or this fact is "
            "dropped"
        )
    return _reply(runtime, note, staged=edits, filed=[record])


@tool
def update_summary(
    target: str,
    lines: list[str],
    cites: list[int],
    runtime: ToolRuntime[AgentContext],
) -> Command:
    """Rewrite a page's "## Current state": the latest status in a few lines.

    Use it when the note changes what is true now (a date moved, a status or owner
    changed, a plan was decided). Write the whole new state, not only the change: it
    replaces the old one. Read the page first to keep what is still true.

    Args:
        target: An entity slug with a page, e.g. "project-atlas".
        lines: 1-6 short lines, e.g. ["GA: Dec 9 (moved from Dec 2).", "Owner: Priya"].
        cites: Note lines the new state comes from.
    """
    try:
        edit = staging.stage_summary(
            runtime.context.staging,
            _live(runtime)[0],
            target=target,
            lines=lines,
            cites=cites,
        )
    except StagingError as exc:
        return _reply(runtime, f"REFUSED: {exc}", refusals=[f"update_summary: {exc}"])
    return _reply(runtime, f"Current state of {target} staged", staged=[edit])


@tool
def withdraw_fact(
    line: int, target: str, runtime: ToolRuntime[AgentContext]
) -> Command:
    """Take back a fact you filed earlier in this review.

    Use it when feedback says a fact is wrong or on the wrong page; then file it again
    correctly if it is worth keeping.

    Args:
        line: The note line the fact cited.
        target: The target it was filed on (slug, "impact" or "decision-log").
    """
    try:
        path, _ = staging._target_path(
            runtime.context.staging, _live(runtime)[0], target
        )
    except StagingError as exc:
        return _reply(runtime, f"REFUSED: {exc}", refusals=[f"withdraw_fact: {exc}"])
    filed = _live(runtime)[1]
    if not any(f.line == line and f.path == path for f in filed):
        msg = f"nothing from line {line} is filed on {target}"
        return _reply(runtime, f"REFUSED: {msg}", refusals=[f"withdraw_fact: {msg}"])
    n = len(runtime.state.get("filed", []))
    return _reply(
        runtime, f"withdrawn line {line} from {target}", withdrawn=[(line, path, n)]
    )


@tool
def propose_page(
    slug: str, title: str, line: int, runtime: ToolRuntime[AgentContext]
) -> Command:
    """Stage a new page for a person, project or concept the note names.

    Args:
        slug: "person-<name>", "project-<name>" or "concept-<name>", lowercase words
            joined by "-", e.g. "person-marta-ruiz".
        title: The name as the note writes it, e.g. "Marta".
        line: A note line that names it.
    """
    ctx = runtime.context.staging
    try:
        edit = staging.stage_page(
            ctx, _live(runtime)[0], slug=slug, title=title, line=line
        )
    except StagingError as exc:
        return _reply(runtime, f"REFUSED: {exc}", refusals=[f"propose_page: {exc}"])
    return _reply(
        runtime,
        f"proposed {edit.path}; you can now file facts on {slug}",
        staged=[edit],
    )


@tool
def map_task(
    line: int, project: str, why: str, runtime: ToolRuntime[AgentContext]
) -> Command:
    """Propose which project a task belongs to (task lines marked `project ?`).

    Map a task only when the line, its parent bullet, its heading or a project page
    supports it; otherwise skip it (unmapped is fine). The user sees and can correct
    every proposal. Calling it again for the same line replaces the proposal.

    Args:
        line: The task's note line.
        project: A project slug with a page, or one you propose_page for; "none"
            takes back your earlier proposal for this line.
        why: A short reason the user reads, e.g. "under ### Delq model sync".
    """
    ctx = runtime.context.staging
    try:
        call = staging.stage_mapping(ctx, line=line, project=project, why=why)
    except StagingError as exc:
        return _reply(runtime, f"REFUSED: {exc}", refusals=[f"map_task: {exc}"])
    _, slug, _ = call
    if slug is None:
        return _reply(runtime, f"proposal for line {line} taken back", mappings=[call])
    note = f"proposed line {line} → {slug}"
    path = path_for(slug)
    if ctx.read(path) is None and path not in staging.proposed_paths(_live(runtime)[0]):
        note += (
            f"; {slug} has no page yet: call propose_page for it, or this is dropped"
        )
    return _reply(runtime, note, mappings=[call])


@tool
def ask_user(question: str, line: int, runtime: ToolRuntime[AgentContext]) -> Command:
    """Put a question in the review, e.g. who a name is or which project a line is about.

    Args:
        question: One short question.
        line: The note line it is about.
    """
    try:
        q = staging.check_question(runtime.context.staging, question, line)
    except StagingError as exc:
        return _reply(runtime, f"REFUSED: {exc}", refusals=[f"ask_user: {exc}"])
    return _reply(runtime, "question added to the review", questions=[q])


def model_middleware(
    fallbacks: Sequence[BaseChatModel] = (), initial_delay: float = 5.0
) -> list[Any]:
    """Retry each failed model call that may pass, then try the fallback models in order.

    Free providers often answer "overloaded" (OpenRouter raises it as a ValueError).
    Retrying here repeats one call and keeps the agent's progress. Only transient
    failures are retried (`retries.is_transient`: network, 429, 5xx); a bad key or no
    credits (401, 402) goes straight to the fallbacks instead of waiting out the
    backoff. The fallback middleware comes first so it wraps the retry: every model,
    fallbacks too, gets its retries. `on_failure="error"` matters: the default
    ("continue") would hand the agent the error as if the model had answered, ending
    the run quietly with half the work.
    """
    retry = ModelRetryMiddleware(
        max_retries=3,
        retry_on=is_transient,
        initial_delay=initial_delay,
        backoff_factor=2.0,
        on_failure="error",
    )
    return [ModelFallbackMiddleware(*fallbacks), retry] if fallbacks else [retry]


def build_ingest_agent(
    vault: Path,
    model: BaseChatModel,
    skills_dir: Path = SKILLS_DIR,
    fallbacks: Sequence[BaseChatModel] = (),
) -> Any:
    """Return the compiled ingest agent over `vault` (read only).

    `fallbacks` are tried in order when `model` still fails after its retries.
    """
    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            "/vault/": ReadOnlyFilesystemBackend(root_dir=vault, virtual_mode=True),
            "/skills/": ReadOnlyFilesystemBackend(
                root_dir=skills_dir, virtual_mode=True
            ),
        },
    )
    permissions = [
        FilesystemPermission(
            operations=["write"], paths=["/vault/**", "/skills/**"], mode="deny"
        )
    ]
    return create_deep_agent(
        model=model,
        tools=[
            file_fact,
            update_summary,
            withdraw_fact,
            propose_page,
            map_task,
            ask_user,
        ],
        system_prompt=SYSTEM_PROMPT,
        skills=["/skills/"],
        memory=[RULES_VIRTUAL] if (vault / RULES_PATH).is_file() else None,
        backend=backend,
        permissions=permissions,
        middleware=[
            *model_middleware(fallbacks),
            FilesystemMiddleware(
                backend=backend, tools=READ_TOOLS, _permissions=permissions
            ),
        ],
        state_schema=IngestAgentState,
        context_schema=AgentContext,
        name="ingest-agent",
        # Stateless inside the `stage` node (0010 S4): by default a graph invoked in a
        # node writes its own checkpoints (every message, `Filed` objects) into the
        # parent's thread store, yet a retry of `stage` restarts the agent anyway. Its
        # steps still stream under `stage`: config reaches it without being passed.
        checkpointer=False,
    )


def page_outlines(
    entities: EntityIndex, read: Callable[[str], str | None], extra: Sequence[str] = ()
) -> str:
    """Return one block per page: slug, title, aliases, sections, Current state.

    `extra` adds non-entity pages (the quarter's impact page and decision log) by path.
    """
    blocks = []
    for e in entities.entities.values():
        also = f" (also: {', '.join(e.aliases)})" if e.aliases else ""
        blocks.append(_outline(f"{e.slug}: {e.title}{also}", read(e.path) or ""))
    for path in extra:
        if (text := read(path)) is not None:
            blocks.append(_outline(path, text))
    return "\n".join(blocks) or "(none)"


def _outline(name: str, text: str) -> str:
    sections = [
        line[3:].strip() for line in text.splitlines() if line.startswith("## ")
    ]
    out = [f"- {name}"]
    if sections:
        out.append(f"  sections: {', '.join(sections)}")
    state = _section_body(text, CURRENT_STATE)
    if state:
        out.append("  current state: " + " / ".join(state))
    return "\n".join(out)


def _section_body(text: str, heading: str) -> list[str]:
    lines, body, inside = text.splitlines(), [], False
    for line in lines:
        if line.startswith("## "):
            inside = line.strip() == heading
            continue
        if inside and line.strip() and not line.startswith("_Updated"):
            body.append(line.strip().removeprefix("- "))
    return body


def build_brief(
    note: DailyNote,
    text: str,
    citable: dict[int, str],
    task_ids: dict[int, str],
    task_projects: dict[int, str | None],
    done_tasks: Sequence[DoneTask],
    entities: EntityIndex,
    read: Callable[[str], str | None],
    *,
    feedback: Sequence[str] = (),
    previous: Sequence[Filed] = (),
    dropped: Sequence[str] = (),
    open_tasks: Mapping[int, Sequence[str]] | None = None,
    proposals: Sequence[TaskProposal] = (),
) -> str:
    """Return the first message: the numbered note, task facts, known pages, feedback.

    Lines that may be cited are marked `|`, others `~`. Task lines carry what code
    knows: ID, status, project. An open task (`open_tasks`) shows `project ?`, with
    its rule-3 candidates if any; `proposals` are the agent's earlier ones (revise).
    (`done_tasks` is unused since wins became the user's pick in the review; kept for
    the signature.)
    """
    del done_tasks
    tasks = {t.line_no: t for t in note.tasks}
    rows = []
    for n, raw in enumerate(text.splitlines(), start=1):
        extra = ""
        if n in tasks:
            t = tasks[n]
            bits = [f"task {t.id or task_ids.get(n, '?')}", t.status.value]
            if task_projects.get(n):
                bits.append(f"project {task_projects[n]}")
            elif open_tasks is not None and n in open_tasks:
                options = open_tasks[n]
                bits.append(
                    f"project ? (one of {', '.join(options)})"
                    if options
                    else "project ?"
                )
            extra = f"   [{'; '.join(bits)}]"
        rows.append(f"{n:>4} {'|' if n in citable else '~'} {raw}{extra}")
    parts = [
        f"Note: {note.path} (date {note.date})",
        "Pages (slug: title, sections, current state):\n"
        + page_outlines(
            entities,
            read,
            [impact_path(note.date), decision_log_path(note.date)] if note.date else [],
        ),
        "Lines (`|` may be cited, `~` may not):\n" + "\n".join(rows),
    ]
    if previous:
        filed = "\n".join(
            f"- line {f.line} → {f.path} {f.section}: {f.text}" for f in previous
        )
        parts.append(f"Your filing so far (still staged):\n{filed}")
    if proposals:
        mapped = "\n".join(
            f"- line {m.line} → {m.project} ({m.why})" for m in proposals
        )
        parts.append(f"Your task → project proposals so far (still staged):\n{mapped}")
    if dropped:
        lost = "\n".join(f"- {d}" for d in dropped)
        parts.append(
            "Code dropped these last round (not filed). If the user's feedback says "
            f"where they belong, file them there now:\n{lost}"
        )
    if feedback:
        notes = "\n".join(f"- {f}" for f in feedback)
        parts.append(
            f"The user reviewed it and says:\n{notes}\nChange only what the feedback "
            "asks: file what is missing, withdraw_fact what is wrong and file it again "
            "where it belongs. Everything else stays staged as it is."
        )
    return "\n\n".join(parts)
