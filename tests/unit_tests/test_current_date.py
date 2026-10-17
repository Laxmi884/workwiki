import datetime

from langchain_core.messages import AIMessage, SystemMessage

from llm_wiki.assistant.agent import build_assistant
from llm_wiki.middleware.current_date import date_line, with_line

from .scripted_model import ToolScript, call


def test_date_line_names_the_weekday_and_quarter():
    line = date_line(datetime.date(2026, 10, 17))
    assert line.startswith("Today is Saturday, 2026-10-17 (2026 Q4).")
    assert "Today is Thursday, 2026-01-01 (2026 Q1)." in date_line(
        datetime.date(2026, 1, 1)
    )
    assert "(2026 Q3)" in date_line(datetime.date(2026, 9, 30))


def test_with_line_appends_and_keeps_the_prompt_as_it_was():
    plain = SystemMessage(content="You are helpful.\n")
    assert with_line(plain, "Today.").content == "You are helpful.\n\nToday."
    assert plain.content == "You are helpful.\n"  # a new message, the old one unchanged
    blocks = SystemMessage(content=[{"type": "text", "text": "A"}])
    assert with_line(blocks, "Today.").content == [
        {"type": "text", "text": "A"},
        {"type": "text", "text": "Today."},
    ]
    assert with_line(None, "Today.").content == "Today."


class _Recording(ToolScript):
    """Keeps the system message of every model call."""

    seen: list[str] = []

    def _generate(self, messages, *args, **kwargs):  # type: ignore[override]
        self.seen.append(
            str(messages[0].content) if messages[0].type == "system" else ""
        )
        return super()._generate(messages, *args, **kwargs)


def test_the_assistant_and_its_subagent_see_today_and_the_thread_does_not(vault_copy):
    draft = {
        "period": "2026-10-12 to 2026-10-17",
        "sections": [],
        "gaps": ["Nothing filed."],
    }
    model = _Recording(
        seen=[],
        messages=iter(
            [
                call(
                    "task",
                    description="Wins 2026-10-12 to 2026-10-17",
                    subagent_type="review-writer",
                ),
                call("BragDraft", **draft),  # the subagent's answer
                AIMessage("Nothing filed last week."),
            ]
        ),
    )
    agent = build_assistant(
        vault_copy, model, today=lambda: datetime.date(2026, 10, 17)
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "What did I do last week?"}]}
    )

    assert len(model.seen) == 3  # main, subagent, main
    assert all("Today is Saturday, 2026-10-17 (2026 Q4)." in s for s in model.seen)
    assert (
        "You collect evidence" in model.seen[1]
    )  # the review-writer's own prompt, stamped
    assert not any("Today is" in str(m.content) for m in result["messages"])


def test_one_built_graph_follows_the_clock(vault_copy):
    days = iter([datetime.date(2026, 10, 17), datetime.date(2026, 10, 18)])
    model = _Recording(seen=[], messages=iter([AIMessage("a"), AIMessage("b")]))
    agent = build_assistant(vault_copy, model, today=lambda: next(days))
    for _ in range(2):
        agent.invoke({"messages": [{"role": "user", "content": "hi"}]})
    assert "Saturday, 2026-10-17" in model.seen[0]
    assert "Sunday, 2026-10-18" in model.seen[1]
