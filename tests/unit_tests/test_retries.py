"""Which failures are retried (retries.is_transient), and where the rule is used."""

import anthropic
import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import RetryPolicy
from openrouter.errors import OpenRouterError

from llm_wiki.ingest.agent import model_middleware
from llm_wiki.ingest.graph import builder
from llm_wiki.ingest.staging import StagingError
from llm_wiki.retries import is_transient

REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


def openrouter(status: int) -> OpenRouterError:
    return OpenRouterError(f"HTTP {status}", httpx.Response(status, request=REQUEST))


def body_error(code: int) -> ValueError:
    return ValueError(
        f"OpenRouter API returned an error: Provider returned error (code: {code})"
    )


def wrapped(cause: BaseException) -> Exception:
    try:
        raise cause
    except BaseException as exc:
        try:
            raise RuntimeError("the agent run failed") from exc
        except RuntimeError as outer:
            return outer


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("reset by peer"),
        TimeoutError("read timed out"),
        httpx.ConnectError("refused", request=REQUEST),
        httpx.ReadTimeout("slow", request=REQUEST),
        openrouter(429),
        openrouter(502),
        openrouter(503),
        openrouter(408),
        body_error(429),
        body_error(503),
        ValueError("OpenRouter API returned a response with no choices. This may ..."),
        ValueError("Upstream error: Service temporarily overloaded (503)"),
        anthropic.RateLimitError(
            "slow down", response=httpx.Response(429, request=REQUEST), body=None
        ),
        anthropic.InternalServerError(
            "oops", response=httpx.Response(529, request=REQUEST), body=None
        ),
        anthropic.APIConnectionError(request=REQUEST),
        httpx.HTTPStatusError(
            "bad gateway",
            request=REQUEST,
            response=httpx.Response(502, request=REQUEST),
        ),
        wrapped(httpx.ReadTimeout("slow", request=REQUEST)),
    ],
    ids=lambda e: f"{type(e).__name__}:{str(e)[:30]}",
)
def test_failures_that_may_pass_are_retried(exc):
    assert is_transient(exc)


@pytest.mark.parametrize(
    "exc",
    [
        openrouter(400),
        openrouter(401),
        openrouter(402),
        openrouter(404),
        body_error(402),
        body_error(400),
        anthropic.AuthenticationError(
            "bad key", response=httpx.Response(401, request=REQUEST), body=None
        ),
        httpx.HTTPStatusError(
            "gone", request=REQUEST, response=httpx.Response(404, request=REQUEST)
        ),
        ValueError("line 503 is not a task"),
        StagingError("line 23: no such line"),
        KeyError("note"),
        RuntimeError("a bug"),
        wrapped(openrouter(402)),
    ],
    ids=lambda e: f"{type(e).__name__}:{str(e)[:30]}",
)
def test_failures_that_would_only_fail_again_are_not(exc):
    assert not is_transient(exc)


def test_the_stage_node_retries_with_this_rule():
    policy = builder.nodes["stage"].retry_policy
    if not isinstance(policy, RetryPolicy):  # a NamedTuple, or a sequence of them
        (policy,) = policy
    assert policy.retry_on is is_transient and policy.max_attempts == 2


class Failing(GenericFakeChatModel):
    """A model whose every call raises `error`."""

    error: Exception
    calls: int = 0

    def _generate(self, *args, **kwargs):
        self.calls += 1
        raise self.error


def run(model, middleware) -> str:
    agent = create_agent(model=model, middleware=middleware)
    return agent.invoke({"messages": [HumanMessage("hi")]})["messages"][-1].text


def test_out_of_credits_goes_straight_to_the_fallback():
    primary = Failing(messages=iter([]), error=openrouter(402))
    backup = GenericFakeChatModel(messages=iter([AIMessage("backup")]))
    assert run(primary, model_middleware([backup], initial_delay=0)) == "backup"
    assert primary.calls == 1  # no retries for a 402


def test_a_rate_limit_is_retried_before_the_fallback():
    primary = Failing(messages=iter([]), error=openrouter(429))
    backup = GenericFakeChatModel(messages=iter([AIMessage("backup")]))
    assert run(primary, model_middleware([backup], initial_delay=0)) == "backup"
    assert primary.calls == 4  # first try + 3 retries
