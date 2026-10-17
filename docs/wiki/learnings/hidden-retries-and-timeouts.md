---
phase: 3
date: 2026-10-16
tags: [models, retries, openrouter, reliability]
---

# Hidden retry layers and request timeouts

## What it is

A model call can be retried in several layers: the provider SDK, LangChain's retry middleware, the
graph's `RetryPolicy`. If an inner layer retries quietly and there is no request timeout, one hung
provider can stall a run for minutes with nothing in the logs.

## How we used it

- First real 0007 run hung 20 minutes. Cause: the OpenRouter SDK (used by `langchain-openrouter`)
  retries timeouts and connection errors itself with backoff, up to `max_retries` × 150 s (≈5 min per
  call), and **one hour** when `max_retries=0` (no config means its own default).
- Fix (`models.py`, `model_setup.py`): `WIKI_MODEL_TIMEOUT` (180 s) per request via
  `timeout_kwargs` (OpenRouter takes milliseconds, others seconds, Ollama none) and
  `without_sdk_retries` (SDK strategy "none"), so failures reach our logged `ModelRetryMiddleware`.
  Checked live: a 0.5 s timeout failed in 1.2 s with `httpx.ReadTimeout`.

## Paid-media reference

None specific; the lesson is general: keep retries in the one layer you can see.

## Gotchas

- Read the installed SDK source when docs are thin; the default was only visible there.
- Free providers also return plain 400 "Provider returned error" when overloaded; that's not our bug.
- Which errors to retry (2026-10-16, `src/llm_wiki/retries.py:is_transient`): LangGraph's default `retry_on` retries every error type it doesn't list (so OpenRouter 401/402) and no `OSError` (so not Python's `TimeoutError`); `ModelRetryMiddleware`'s default retries every `Exception`. Ours retries network errors, 408/429/5xx (status on the error, or OpenRouter's `ValueError` "... (code: 503)"), "no choices" and "overloaded"; nothing else. Used by the model middleware (a 402 now goes straight to the fallback) and the `stage` node (one rerun of the agent, after 10 s).
- A request timeout is not a deadline (2026-10-16 real run, seen in LangSmith): with `WIKI_MODEL_TIMEOUT=180`, DeepSeek Flash calls on OpenRouter ran 206 s and 460 s+ while reasoning (one returned 13.8k output tokens). Likely the connection stays active, so the timeout only catches a silent provider. Slowness there came from extra turns (a staging bug made the agent loop) and long reasoning, not from a hang: read the trace before blaming the provider.
- Slow isn't failed: long reasoning needs a smaller effort, not a retry. `WIKI_REASONING_EFFORT` (2026-10-17)
  sends `ChatOpenRouter(reasoning={"effort": ...})` for OpenRouter models only; unset keeps the provider default.
  Measured 2026-10-17 (DeepSeek Flash; user approved the paid test, $0.03): one call unset 71–135 s / 3.4k–8.3k
  reasoning tokens, `low` 82–86 s / ~3.5k steady, `none` 4.7 s / 0. Full ingest of the 2026-10-14 note on a scratch
  vault: unset 4 min 35 s (2026-10-16), `low` 110 s (10 calls, 0 refusals, closest to the approved review), `none`
  47 s (asked instead of creating Risk culture, nothing on loss history). Neither wrote "Current state"
  summaries. `low` set in `.env`. Notebook 3.7 section 4b.

## In my words

(for the user)
