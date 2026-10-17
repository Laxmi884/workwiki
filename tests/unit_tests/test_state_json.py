"""Checkpoints hold plain JSON; nodes see models (ingest/state.py, `STORED_AS_JSON`).

So any checkpointer restores ingest threads, including strict ones that refuse every
class not on LangGraph's safe list: the local runner's, and the Agent Server's under
`LANGGRAPH_STRICT_MSGPACK`.
"""

import enum
import typing

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from llm_wiki.daily_syntax import parse_daily_note
from llm_wiki.diffs import vault_reader
from llm_wiki.ingest import nodes
from llm_wiki.ingest.graph import AgentIngestState
from llm_wiki.ingest.state import STORED_AS_JSON, decoded, encoded
from llm_wiki.runner import IngestRunner

from .scripted_model import AgentRounds, bi_tool_decision

NOTE = "00_raw/daily/2026-10-12.md"
PLAIN = (str, int, float, bool, type(None))


def _plain(value, where="state"):
    """Fail unless `value` is only dicts, lists and scalars (what JSON can hold)."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, PLAIN), f"{where}: key {key!r}"
            _plain(item, f"{where}.{key}")
    elif isinstance(value, list | tuple):
        for i, item in enumerate(value):
            _plain(item, f"{where}[{i}]")
    else:
        assert isinstance(value, PLAIN), f"{where} is a {type(value).__qualname__}"


def test_every_checkpoint_of_a_full_review_is_plain_json(vault_copy, monkeypatch):
    monkeypatch.setattr(
        nodes, "chat_model", AgentRounds([bi_tool_decision()], [bi_tool_decision()])
    )
    runner = IngestRunner.in_memory(context={"vault_path": str(vault_copy)})
    started = runner.start(NOTE)
    assert started.status == "review"
    revised = runner.resume(started.thread_id, {"action": "revise", "feedback": "x"})
    done = runner.resume(revised.thread_id, {"action": "approve"})
    assert done.status == "done" and done.written

    stored_fields = set()
    for item in runner.graph.checkpointer.list(None):
        values = item.checkpoint["channel_values"]
        _plain({k: v for k, v in values.items() if not k.startswith("__")})
        stored_fields |= set(values) & set(STORED_AS_JSON)
    assert stored_fields == set(STORED_AS_JSON), "every codec field was exercised"


def _holds_a_model(annotation) -> bool:
    if isinstance(annotation, type):
        return issubclass(annotation, BaseModel | enum.Enum)
    return any(_holds_a_model(arg) for arg in typing.get_args(annotation))


def test_every_model_typed_field_has_a_codec():
    hints = typing.get_type_hints(AgentIngestState, include_extras=True)
    needs = {name for name, hint in hints.items() if _holds_a_model(hint)}
    assert needs == set(STORED_AS_JSON)


def test_models_round_trip_through_the_codec(fixture_vault):
    note = parse_daily_note(NOTE, vault_reader(fixture_vault)(NOTE))
    stored = encoded({"note": note, "seen_lines": ["a"]})
    _plain(stored)
    assert decoded(stored) == {"note": note, "seen_lines": ["a"]}


def test_old_checkpoints_with_models_still_load(fixture_vault):
    """Threads saved before the change hold model objects; a strict serializer turns
    them into dicts, and the codec turns those back into the same models."""
    note = parse_daily_note(NOTE, vault_reader(fixture_vault)(NOTE))
    old = JsonPlusSerializer(allowed_msgpack_modules=True).dumps_typed({"note": note})
    restored = JsonPlusSerializer(allowed_msgpack_modules=None).loads_typed(old)
    assert not isinstance(restored["note"], BaseModel)
    assert decoded(restored)["note"] == note
