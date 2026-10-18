---
phase: 5
date: 2026-10-17
tags: [langchain, middleware, deepagents, safety]
---

# Tool allow-list

## What it is

Each agent gets a list of the tools it may use, enforced in code by middleware. `wrap_model_call` removes every
other tool from what the model is offered, and `wrap_tool_call` refuses any other call with an error
`ToolMessage`, so the tool never runs. Anything nobody listed is denied, including tools a framework upgrade adds.

## How we used it

- `src/llm_wiki/middleware/tool_guard.py`: `tool_name` (a `BaseTool` or a provider dict), `ToolGuardMiddleware(allowed, refusals_key=None)`.
- `src/llm_wiki/ingest/agent.py`: allowed = read tools + its six staging tools, no `task`. `refusals_key="refusals"` puts each refusal in the list the review counts.
- `src/llm_wiki/assistant/agent.py`: the main agent keeps `task` (for the review-writer); the review-writer gets the same list without `task`. Each list is built from the agent's tool objects, so a newly added tool can't be silently left out.
- Why it was needed: a scripted ingest run could call `task`, and the general-purpose subagent (which gets all the agent's tools) staged a fact with stats showing `{'task': 1}` and 0 refusals. Only a prompt line stood in the way.
- Tests: `tests/unit_tests/test_tool_guard.py`. Notebook 4.3.

## Paid-media reference

`middleware/authorization.py`: `ToolSurfacePolicy` (allowed names plus hidden built-ins) and `InvocationGuardMiddleware` (same two hooks, plus "mutations only through propose/execute"). Ours has no write tools at all, so the list is the only rule.

## Ready-made options (checked 2026-10-17)

Neither LangChain nor Deep Agents ships an allow-list. `ToolCallLimitMiddleware(tool_name=..., run_limit=0)` refuses one name but keeps it offered. `LLMToolSelectorMiddleware` lets a model pick. Deep Agents' `HarnessProfile(excluded_tools=...)` (beta, `register_harness_profile`) hides and refuses names, but it's a deny-list registered process-wide per provider/model, so it can't treat our two agents (same model) differently, and its docs say it is "not a security surface". Its `_ToolExclusionMiddleware` uses the same two hooks as ours.

## Gotchas

- Hiding alone is not enough: a model can call a tool it wasn't offered, and the tool node runs any registered tool it's asked for.
- `wrap_tool_call` also sees calls to names nothing registered (`request.tool` is `None`). LangChain used to answer them with "not a valid tool" and nothing counted them; now they count as refusals.
- Structured output (`BragDraft`) never reaches the tool node, so it needn't be on the list.
- The built-in general-purpose subagent can't take this middleware (it only inherits middleware that replaces one of its default slots). Since 5c it is replaced by our own spec with the review-writer's list.
- Prompts went stale: they still forbade `write_todos` after Deep Agents 0.7 removed it.

## In my words

