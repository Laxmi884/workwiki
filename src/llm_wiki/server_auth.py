"""Who may call the Agent Server: clients that send `WIKI_SERVER_TOKEN` (Slack step 2).

`langgraph.json` `"auth": {"path": "./src/llm_wiki/server_auth.py:auth"}` runs this on
every API request. Clients (`ServerBackend`, the review page inside the server, the
Slack bot, `schedule`) send `Authorization: Bearer <token>`. An unset token refuses
everything, so a missing setting never means "open". The review page's own routes
(`http.app`) are not covered: a phone's browser can't send the header; they stay
tailnet-only and refuse cross-site posts (review_web `_json_body`).
"""

from __future__ import annotations

import hmac
import os

from langgraph_sdk import Auth

auth = Auth()


def check_bearer(authorization: str | None, expected: str | None) -> None:
    """Raise 401 unless `authorization` is "Bearer <expected>" (constant-time compare)."""
    if not expected:
        raise Auth.exceptions.HTTPException(
            status_code=401, detail="the server has no WIKI_SERVER_TOKEN set"
        )
    scheme, _, token = (authorization or "").partition(" ")
    if scheme != "Bearer" or not hmac.compare_digest(token.encode(), expected.encode()):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Unauthorized")


@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    """Let the request in as the one user, or refuse it with 401."""
    check_bearer(authorization, os.environ.get("WIKI_SERVER_TOKEN"))
    return {"identity": "wiki-user"}
