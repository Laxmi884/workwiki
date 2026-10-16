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

## In my words

(for the user)
