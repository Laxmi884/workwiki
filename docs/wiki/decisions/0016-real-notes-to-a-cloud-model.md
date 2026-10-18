---
id: 0016
date: 2026-10-17
status: accepted
---

# 0016: Real notes go to a cloud model

## Context

Until now only invented data reached cloud models: the fixture vault, the eval corpus, and `~/WorkDev`. Using the
agent on the real vault `~/Work` means the note text, the page outlines and the pages the agent opens are sent to the
model provider. The local option (Ollama, `gemma4:12b` on an 18 GB Mac) missed person notes in testing, and the eval
baselines (2026-10-17) are for DeepSeek V4.1 Flash through OpenRouter.

## Options

- **Cloud model** (`openrouter:deepseek/deepseek-v4.1-flash`): measured (ingest 0.84 first pass, Q&A 0.956), about
  a cent per ingest. The work notes leave the Mac.
- **Local model only:** the notes stay on the Mac, but it isn't good enough yet, and no eval has been run on it.

## Decision

The user agreed on 2026-10-17 to send real notes to the cloud model. What is sent and what isn't:
- Sent to the model on every call: the note, an outline of every wiki page, and the pages the agent reads.
  `RedactionMiddleware` removes credential-shaped strings first; names and work content go as written.
- Not covered by this decision: LangSmith tracing, which would send the same content again, unredacted. It stays an
  open decision until the user chooses.
- Dev runs keep using `~/WorkDev`; only real use points at `~/Work`.

## Why

Measured quality beats an untested local model, and the user judged the notes fine to share with the provider. The
`init_chat_model` setup (0003) keeps a switch to a local model a one-line change once one passes the eval.
