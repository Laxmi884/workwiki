"""The review page (0009) and the chat box (0010 S3), both on `Backend`.

    GET  /review                       open reviews
    GET  /review/{thread_id}           one review: facts beside note lines, files, diffs
    POST /review/{thread_id}/answer    {"answer": <ReviewAnswer dict | "approve" | "reject">}
    GET  /chat                         one chat for both agents ("/…" → ingest, else ask)
    POST /chat/send                    {"message": str, "history": [[role, text], ...]}

Mounted on the LangGraph server through `langgraph.json` `"http": {"app": ...}`, or
standalone: `uv run python -m llm_wiki.review_web` (loads `.env`). Every call goes
through `get_backend()` (`WIKI_BACKEND`: local runner or the Agent Server), which
checks answers (ids, digests, revise limit) and serves one vault (`WIKI_VAULT_PATH`).
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from llm_wiki.backend import (
    Backend,
    BackendError,
    ChatTurn,
    NoOpenReview,
    UnknownThread,
    get_backend,
)
from llm_wiki.chat import session
from llm_wiki.review_web.view import book_note, build_view

log = logging.getLogger(__name__)

env = Environment(
    loader=PackageLoader("llm_wiki.review_web", "templates"),
    autoescape=select_autoescape(["html"]),
)


@functools.cache
def backend() -> Backend:
    """Return this process's backend (one per process: it holds the SQLite file)."""
    return get_backend()


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


def _page(
    name: str, *, nav: str, open_reviews: list[str] | None, **values: Any
) -> HTMLResponse:
    """Render a page under the shared nav bar.

    `nav` names the bar's current entry ("chat", "reviews", or "review" for one review);
    `open_reviews` are the open reviews' thread ids, `None` when the backend can't say.
    """
    return HTMLResponse(
        env.get_template(name).render(nav=nav, open_reviews=open_reviews, **values)
    )


def _open_reviews() -> list[str] | None:
    """Return the open reviews' thread ids for the nav bar; `None` if the backend is down.

    Only the count and the "next review" link need it, so a backend failure leaves the
    page usable instead of failing it.
    """
    try:
        return [r.thread_id for r in backend().pending()]
    except BackendError as exc:
        log.warning("open reviews unavailable: %s", exc)
        return None


def index(request: Request) -> HTMLResponse:
    """List the open reviews, newest first."""
    rows = [
        {"thread": r.thread_id, "note": r.review.note if r.review else None}
        for r in backend().pending()
    ]
    return _page(
        "index.html",
        nav="reviews",
        open_reviews=[row["thread"] for row in rows],
        rows=rows,
    )


def review_page(request: Request) -> HTMLResponse:
    """Show one review (or how it ended)."""
    thread = request.path_params["thread_id"]
    try:
        result = backend().review(thread)
    except UnknownThread:
        return _page(
            "missing.html", nav="review", open_reviews=_open_reviews(), thread=thread
        )
    view = build_view(result.review) if result.review is not None else None
    return _page(
        "review.html",
        nav="review",
        open_reviews=_open_reviews(),
        thread=thread,
        result=result,
        review=result.review,
        view=view,
        diff_lines=diff_lines,
        book_note=book_note,
    )


def _crashed(exc: Exception) -> JSONResponse:
    """Return an unexpected failure (e.g. a missing API key in a local run) as JSON.

    Without this Starlette answers a plain-text 500 that the page's script can't read.
    """
    log.exception("request failed")
    return JSONResponse(
        {"error": f"{type(exc).__name__}: {exc} (details in the server log)"},
        status_code=500,
    )


def _answer(thread: str, answer: Any) -> JSONResponse:
    try:
        result = backend().answer(thread, answer)
    except UnknownThread:
        return JSONResponse({"error": f"no such review: {thread}"}, status_code=404)
    except NoOpenReview as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except BackendError as exc:  # e.g. the Agent Server is down or the run failed
        return JSONResponse({"error": str(exc)}, status_code=502)
    except Exception as exc:  # noqa: BLE001  (the page shows it; the log has the trace)
        return _crashed(exc)
    error = result.review.error if result.review else None
    return JSONResponse({"status": result.status, "error": error})


async def answer(request: Request) -> JSONResponse:
    """Send the page's answer to the backend; the page then reloads.

    The backend blocks (SQLite or HTTP, the vault, a whole agent round on revise), so it
    runs in a worker thread; the plain `def` routes run there too (Starlette does that).
    """
    body = await request.json()
    return await run_in_threadpool(
        _answer, request.path_params["thread_id"], body.get("answer")
    )


def chat_page(request: Request) -> HTMLResponse:
    """Show the chat box; the browser keeps the conversation."""
    return _page(
        "chat.html",
        nav="chat",
        open_reviews=_open_reviews(),
        vault=str(backend().vault),
    )


def _chat_reply(message: str, history: list[ChatTurn]) -> JSONResponse:
    try:
        return JSONResponse({"reply": session.reply(backend(), message, history)})
    except BackendError as exc:  # e.g. the Agent Server is down or the run failed
        return JSONResponse({"error": str(exc)}, status_code=502)
    except Exception as exc:  # noqa: BLE001  (the chat shows it; the log has the trace)
        return _crashed(exc)


async def chat_send(request: Request) -> JSONResponse:
    """Answer one chat message (a command or a question) in a worker thread."""
    body = await request.json()
    message = str(body.get("message", "")).strip()
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)
    history: list[ChatTurn] = [
        ("user" if role == "user" else "assistant", str(text))
        for role, text in body.get("history", [])
        if role in ("user", "assistant")
    ]
    return await run_in_threadpool(_chat_reply, message, history)


app = Starlette(
    routes=[
        Route("/review", index),
        Route("/review/{thread_id}", review_page),
        Route("/review/{thread_id}/answer", answer, methods=["POST"]),
        Route("/chat", chat_page),
        Route("/chat/send", chat_send, methods=["POST"]),
    ]
)
