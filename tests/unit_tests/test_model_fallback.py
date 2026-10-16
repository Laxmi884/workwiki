"""Retry and fallback around the ingest agent's model calls (agent.model_middleware)."""

from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from llm_wiki.ingest.agent import model_middleware


class Overloaded(GenericFakeChatModel):
    """Fails like OpenRouter on a busy free model: a ValueError, `fails` times."""

    fails: int = 99
    calls: int = 0

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls <= self.fails:
            raise ValueError("Upstream error: Service temporarily overloaded (503)")
        return super()._generate(*args, **kwargs)


def reply(text: str) -> Any:
    return iter([AIMessage(text)])


def run(model: Any, middleware: list[Any]) -> str:
    agent = create_agent(model=model, middleware=middleware)
    return agent.invoke({"messages": [HumanMessage("hi")]})["messages"][-1].text


def test_retry_recovers_from_a_passing_overload():
    primary = Overloaded(messages=reply("primary"), fails=2)
    assert run(primary, model_middleware(initial_delay=0)) == "primary"
    assert primary.calls == 3


def test_fallback_answers_when_primary_stays_down():
    primary = Overloaded(messages=reply("primary"))
    backup = GenericFakeChatModel(messages=reply("backup"))
    assert run(primary, model_middleware([backup], initial_delay=0)) == "backup"
    assert primary.calls == 4  # first try + 3 retries, then the fallback


def test_all_down_raises_instead_of_ending_quietly():
    primary = Overloaded(messages=reply("primary"))
    with pytest.raises(ValueError, match="overloaded"):
        run(primary, model_middleware(initial_delay=0))
