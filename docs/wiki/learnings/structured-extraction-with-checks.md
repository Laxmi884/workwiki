---
phase: 2
date: 2026-10-13
tags: [langchain, structured-output, testing]
---

# Structured extraction with code checks

## What it is

The model fills a Pydantic schema (`with_structured_output`) instead of writing free text. Code
prepares exactly what the model sees and checks what comes back, repairing what it can and sending
back only what it can't.

## How we used it

- `src/llm_wiki/extraction.py`: `prose_lines` (numbered lines, no tasks/heading/fences),
  `build_messages` (rules, one worked example, known slugs, required `#win`/`#decision` lines,
  previous answer + feedback for revise), `check_facts`, `extract_section` (one retry).
- `include_raw=True` returns `{raw, parsed, parsing_error}`, so a bad answer becomes a retry or a
  warning instead of an exception.
- `src/llm_wiki/ingest/nodes.py`: `extract` node (one section, keyed by start line) and
  `chat_model` (context `model`, else `WIKI_MODEL`); `merge_facts` reducer in `ingest/state.py`.
- `tests/unit_tests/scripted_model.py`: replays scripted answers and records every prompt, so tests
  check both what the model was told and what code did with the answer.

## Paid-media reference

`src/paid_media_agent/testing/scripted_model.py` is a full `BaseChatModel` that scripts tool calls.
Ours is smaller: extraction only needs `with_structured_output(...).invoke(...)`.

## Gotchas

- Same prompt, two models (2026-10-13, clean fixture note): `nemotron-3-ultra` (OpenRouter, free)
  got every fact on the first call; `gemma4:12b` (Ollama) found the decision and the win but missed
  Sam's person note, added noise updates, and dropped "30 min".
- Ollama models "think" by default: hidden reasoning used the whole token budget (empty answer,
  minutes per call). `reasoning=False` fixed it.
- Small models under JSON-schema decoding at temperature 0 can loop, repeating a list item until
  the token limit (invalid JSON). Temperature and `repeat_penalty` did not help; `max_length` on the
  lists (schema `maxItems`) forces the list to close. Code then removes the repeats.
- Retry only for what code can't repair (invalid output, uncovered tagged line). Retrying for
  repeats doubled the time and gave the same repeats.

## In my words

