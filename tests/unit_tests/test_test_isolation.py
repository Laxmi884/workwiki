"""Tests leave the process environment as they found it, and never trace (conftest).

A CLI test calls `main()`, which copies `.env` into `os.environ` (`load_env_file`).
That used to leak into every later test, `LANGSMITH_TRACING_V2` included; tracing then
serialized the scripted model's answer iterator and used it up (2026-10-25). The two
tests below run in file order: the first leaks, the second checks nothing leaked.
"""

import os

from langsmith.utils import tracing_is_enabled

from llm_wiki.config import load_env_file


def test_a_cli_test_loads_an_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_WIKI_LEAK_PROBE=1\nLANGSMITH_TRACING_V2=true\n")
    load_env_file(env)
    assert os.environ["LLM_WIKI_LEAK_PROBE"] == "1"
    assert not tracing_is_enabled(), "tracing stays off even when a .env turns it on"


def test_the_next_test_sees_none_of_it():
    assert "LLM_WIKI_LEAK_PROBE" not in os.environ
    assert os.environ.get("LANGSMITH_TRACING_V2") == "false"
    assert not tracing_is_enabled()
