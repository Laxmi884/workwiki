import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, SystemMessage

from llm_wiki.assistant.agent import build_assistant
from llm_wiki.ingest import nodes
from llm_wiki.ingest.graph import builder as agent_builder
from llm_wiki.middleware.redaction import (
    REDACTED,
    RedactionMiddleware,
    find_secrets,
    redact,
    redact_content,
    secret_values,
)
from llm_wiki.runner import IngestRunner

from .scripted_model import ToolScript, call

# Fake, built at runtime so secret scanners don't flag the test file.
GITLAB = "glpat-" + "Ab3dEf6hIj9kLm2nOp5q"
OPENROUTER = "sk-or-v1-" + "0123456789abcdef" * 4
JWT = (
    "eyJ"
    + "hbGciOiJIUzI1NiJ9"
    + ".eyJ"
    + "zdWIiOiIxMjM0NTY3ODkwIn0"
    + ".dozjgNryP4J3jVmNHl0w5N"
)


@pytest.mark.parametrize(
    ("text", "left"),
    [
        (
            f"jfrog CI token {GITLAB} for the runner",
            "jfrog CI token [REDACTED_SECRET] for the runner",
        ),
        (f"OPENROUTER={OPENROUTER}", "OPENROUTER=[REDACTED_SECRET]"),
        (
            "db: postgres://app:Hunter2pass@db.local:5432/x",
            "db: postgres://app:[REDACTED_SECRET]@db.local:5432/x",
        ),
        ("password: Winter2026!", "password: [REDACTED_SECRET]"),
        ("api_key='" + "a1" * 10 + "'", "api_key='[REDACTED_SECRET]'"),
        (f"Authorization: Bearer {JWT}", "Authorization: Bearer [REDACTED_SECRET]"),
        (
            "AWS AKIA" + "IOSFODNN7EXAMPLE" + " in the vault",
            "AWS [REDACTED_SECRET] in the vault",
        ),
        (
            "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----\nafter",
            "[REDACTED_SECRET]\nafter",
        ),
    ],
)
def test_secrets_are_replaced(text, left):
    assert redact(text) == left


@pytest.mark.parametrize(
    "text",
    [
        "- [ ] Ship beta ^t-q7m2xa",
        "password: reset it tomorrow",  # a word, not a generated value
        "token: TBD",
        "see https://gitlab.com/risk/analytics and http://localhost:8080",
        "digest 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "cure rate for Q1 2026 by book year; 202503 data",
        "sk-learn and sk-short",
    ],
)
def test_ordinary_note_text_is_left_alone(text):
    assert find_secrets(text) == []


def test_overlapping_matches_merge_into_one():
    text = f"token={OPENROUTER}"  # both the key=value rule and the sk- rule match
    (match,) = find_secrets(text)
    assert match.value == OPENROUTER
    assert redact(text) == "token=[REDACTED_SECRET]"


def test_known_values_come_from_key_like_variables():
    env = {
        "OPENROUTER_API_KEY": "plainvalue99",
        "LANGSMITH_TOKEN": "x" * 9,
        "WIKI_MODEL": "a" * 20,
        "MY_KEY": "short",
    }
    values = secret_values(env)
    assert set(values) == {"plainvalue99", "x" * 9}
    assert (
        redact("leaked plainvalue99 twice: plainvalue99", values)
        == f"leaked {REDACTED} twice: {REDACTED}"
    )


def test_content_blocks_are_redacted_and_unchanged_content_is_kept():
    blocks = [{"type": "text", "text": f"key {GITLAB}"}, {"type": "image", "url": "x"}]
    assert redact_content(blocks) == [
        {"type": "text", "text": f"key {REDACTED}"},
        {"type": "image", "url": "x"},
    ]
    clean = [{"type": "text", "text": "nothing here"}]
    assert redact_content(clean) is clean
    assert redact_content("plain") == "plain"


class _Seen(ToolScript):
    """Keeps the text of every message sent to the model."""

    seen: list[str] = []

    def _generate(self, messages, *args, **kwargs):  # type: ignore[override]
        self.seen.extend(str(m.content) for m in messages)
        return super()._generate(messages, *args, **kwargs)


def test_ingest_never_sends_a_secret_from_the_note_or_a_page(vault_copy, monkeypatch):
    note = vault_copy / "00_raw/daily/2026-10-12.md"
    note.write_text(
        note.read_text().replace(
            "- Priya raised flaky CI", f"- Priya raised flaky CI, runner token {GITLAB}"
        )
    )
    page = vault_copy / "10_projects/project-atlas.md"
    page.write_text(page.read_text() + f"\nDeploy key: {OPENROUTER}\n")
    script = _Seen(
        seen=[],
        messages=iter(
            [
                call("read_file", file_path="/vault/10_projects/project-atlas.md"),
                AIMessage("Filed nothing."),
            ]
        ),
    )
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: script)
    monkeypatch.setattr(nodes, "fallback_models", lambda runtime: [])
    runner = IngestRunner.in_memory(
        context={"vault_path": str(vault_copy)}, graph_builder=agent_builder
    )
    assert runner.start("00_raw/daily/2026-10-12.md").status == "review"

    sent = "\n".join(script.seen)
    assert "Priya raised flaky CI, runner token [REDACTED_SECRET]" in sent  # the brief
    assert f"Deploy key: {REDACTED}" in sent  # the page read
    assert GITLAB not in sent and OPENROUTER not in sent
    assert GITLAB in note.read_text()  # the note itself is untouched


def test_assistant_redacts_the_question_pages_and_the_general_purpose_subagent(
    vault_copy, monkeypatch
):
    monkeypatch.setenv("EXAMPLE_SERVICE_TOKEN", "own-value-123")
    page = vault_copy / "10_projects/project-atlas.md"
    page.write_text(page.read_text() + f"\nRunner token {GITLAB}\n")
    model = _Seen(
        seen=[],
        messages=iter(
            [
                call("read_file", file_path="/vault/10_projects/project-atlas.md"),
                call(
                    "task",
                    description="Check the Atlas page",
                    subagent_type="general-purpose",
                ),
                call(
                    "read_file", file_path="/vault/10_projects/project-atlas.md"
                ),  # the subagent
                AIMessage("sub: found it"),
                AIMessage("Done."),
            ]
        ),
    )
    agent = build_assistant(vault_copy, model)
    out = agent.invoke(
        {"messages": [("user", "Is own-value-123 or " + OPENROUTER + " in my notes?")]}
    )

    sent = "\n".join(model.seen)
    for secret in (GITLAB, OPENROUTER, "own-value-123"):
        assert secret not in sent, secret
    assert (
        sent.count(f"Runner token {REDACTED}") >= 2
    )  # main agent's read, then the subagent's
    assert OPENROUTER in out["messages"][0].content  # the thread keeps the user's text


def test_the_request_is_cleaned_and_the_state_is_not_touched():
    model = _Seen(seen=[], messages=iter([AIMessage("ok")]))
    agent = create_agent(
        model,
        system_prompt=SystemMessage(f"Rules: never print {OPENROUTER}"),
        middleware=[RedactionMiddleware()],
    )
    out = agent.invoke({"messages": [("user", f"my token is {GITLAB}")]})
    assert model.seen == [f"Rules: never print {REDACTED}", f"my token is {REDACTED}"]
    assert (
        out["messages"][0].content == f"my token is {GITLAB}"
    )  # the user's own text, kept
