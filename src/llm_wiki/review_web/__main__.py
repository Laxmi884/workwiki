"""Run the review page on its own: `python -m llm_wiki.review_web [--port 2025]`.

Loads `.env` from the working directory first (provider keys, `WIKI_*`).
"""

from __future__ import annotations

import argparse

import uvicorn

from llm_wiki.config import load_env_file
from llm_wiki.review_web.app import app


def main() -> None:
    """Serve the review page on localhost."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2025)
    args = parser.parse_args()
    load_env_file()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
