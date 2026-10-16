"""The review page (0009, step R1): see what an ingest would change, and answer it.

    GET  /review                       open reviews (and the latest finished ones)
    GET  /review/{thread_id}           one review: files with diffs and checkboxes
    POST /review/{thread_id}/answer    {"answer": <ReviewAnswer dict | "approve" | "reject">}

Mounted on the LangGraph server through `langgraph.json` `"http": {"app": ...}`, or
standalone: `uv run --env-file .env python -m llm_wiki.review_web`. Every answer goes
through the same `IngestRunner` as the wiki chat, so the runner checks it (ids,
digests, revise limit) and the thread's own vault is used (`runner.vault_of`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from llm_wiki.config import get_settings
from llm_wiki.review_web.view import ReviewView, book_note, build_view
from llm_wiki.runner import IngestRunner, RunResult, shared_runner

MAX_LISTED = 15

env = Environment(
    loader=PackageLoader("llm_wiki.review_web", "templates"),
    autoescape=select_autoescape(["html"]),
)


def reader() -> IngestRunner:
    """Return a runner that can read any thread (they share one SQLite file)."""
    return shared_runner(get_settings().vault_path)


def runner_for(vault: Path) -> IngestRunner:
    """Return the runner that may answer reviews of `vault`."""
    return shared_runner(vault)


def diff_lines(diff: str) -> list[tuple[str, str]]:
    """Return (css class, text) per unified-diff line, for colouring."""
    out = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            kind = "meta"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "del"
        else:
            kind = "ctx"
        out.append((kind, line))
    return out


def recent_threads(runner: IngestRunner) -> list[str]:
    """Return ingest thread ids, newest first (from the checkpointer)."""
    seen: dict[str, None] = {}
    for item in runner.graph.checkpointer.list(None):  # type: ignore[union-attr]
        thread = item.config["configurable"]["thread_id"]
        if thread.startswith("ingest-"):
            seen.setdefault(thread, None)
    return list(seen)


def _page(name: str, **values: Any) -> HTMLResponse:
    return HTMLResponse(env.get_template(name).render(**values))


def index(request: Request) -> HTMLResponse:
    """List recent ingest reviews, open ones first."""
    runner = reader()
    rows = []
    for thread in recent_threads(runner)[:MAX_LISTED]:
        result = runner.status(thread)
        note = result.review.note if result.review else None
        rows.append({"thread": thread, "status": result.status, "note": note})
    return _page("index.html", rows=rows)


def review_page(request: Request) -> HTMLResponse:
    """Show one review (or how it ended)."""
    thread = request.path_params["thread_id"]
    runner = reader()
    vault = runner.vault_of(thread)
    if vault is None:
        return _page("missing.html", thread=thread)
    result = runner.status(thread)
    view = build_view(result.review) if result.review is not None else None
    return _page(
        "review.html",
        thread=thread,
        result=result,
        review=result.review,
        view=view,
        diff_lines=diff_lines,
        book_note=book_note,
        project_names=project_names(vault, view),
    )


def project_names(vault: Path, view: ReviewView | None) -> dict[str, str]:
    """Return slug → title for pages in the vault and pages this review creates."""
    from llm_wiki.entity_index import load_index  # noqa: PLC0415

    names = {slug: e.title for slug, e in load_index(vault).entities.items()}
    for card in view.pages if view else []:
        names.setdefault(
            card.file.path.rsplit("/", 1)[-1].removesuffix(".md"), card.title
        )
    return names


def _answer(thread: str, answer: Any) -> JSONResponse:
    vault = reader().vault_of(thread)
    if vault is None:
        return JSONResponse({"error": f"no such review: {thread}"}, status_code=404)
    try:
        result: RunResult = runner_for(vault).resume(thread, answer)
    except ValueError as exc:  # nothing waiting, or another vault
        return JSONResponse({"error": str(exc)}, status_code=409)
    error = result.review.error if result.review else None
    return JSONResponse({"status": result.status, "error": error})


async def answer(request: Request) -> JSONResponse:
    """Send the page's answer to the runner; the page then reloads.

    The runner blocks (SQLite, the vault, a whole agent round on revise), so it runs in
    a worker thread; the plain `def` routes above run there too (Starlette does that).
    """
    body = await request.json()
    return await run_in_threadpool(
        _answer, request.path_params["thread_id"], body.get("answer")
    )


app = Starlette(
    routes=[
        Route("/review", index),
        Route("/review/{thread_id}", review_page),
        Route("/review/{thread_id}/answer", answer, methods=["POST"]),
    ]
)
