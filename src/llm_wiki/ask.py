# ruff: noqa: T201  (a CLI prints its answer)
"""Ask the assistant one question from the terminal (decision 0008; the CLI is phase 7).

uv run python -m llm_wiki.ask "What's open on Atlas?"
uv run python -m llm_wiki.ask --vault ~/WorkDev "Q4 brag sheet"

Loads `.env` from the working directory first (provider keys, `WIKI_*`).
"""

from __future__ import annotations

import argparse
import sys

from llm_wiki.assistant.agent import build_assistant
from llm_wiki.assistant.graph import run_choice
from llm_wiki.config import load_env_file
from llm_wiki.model_setup import configured_fallbacks, configured_model


def ask(question: str, vault: str | None = None, model: str | None = None) -> str:
    """Return the assistant's answer to `question` (one turn, nothing saved)."""
    context = {k: v for k, v in (("vault_path", vault), ("model", model)) if v}
    path, override = run_choice(context)  # type: ignore[arg-type]
    agent = build_assistant(
        path, configured_model(override), fallbacks=configured_fallbacks()
    )
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return str(result["messages"][-1].content)


def main(argv: list[str] | None = None) -> int:
    """Parse the command line, ask, print the answer."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--vault", help="vault folder (default: WIKI_VAULT_PATH)")
    parser.add_argument("--model", help='"provider:model" (default: WIKI_MODEL)')
    args = parser.parse_args(argv)
    load_env_file()
    print(ask(args.question, args.vault, args.model))
    return 0


if __name__ == "__main__":
    sys.exit(main())
