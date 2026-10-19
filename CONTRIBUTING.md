# Contributing

Thanks for looking. This started as a personal learning project, so issues and small, focused
pull requests are welcome; large changes are easier to land if you open an issue first.

## Setup

```
uv sync
uv run pytest tests/unit_tests        # no API key needed
```

Tests use a scripted model and the invented fixture vault in `tests/fixtures/vault/`. Never point
them, or a manual run, at a real vault. Make a copy of the fixture vault and set `WIKI_VAULT_PATH`.

## Before you open a PR

```
uv run pytest tests/unit_tests
uv run ruff check src tests && uv run ruff format --check src tests
```

If you change server behaviour, also run `WIKI_SERVER_TESTS=1 uv run pytest tests/server`
(see `AGENTS.md`).

## Ground rules

These come from the design decisions in [`docs/wiki/decisions/`](docs/wiki/decisions/); read the
one that covers the area you are changing.

- Code does deterministic work (task IDs, matching, parsing, path checks); the model extracts and
  writes. Don't move a check into a prompt.
- Missing values stay `None`. Never invent metrics.
- `00_raw/` is read-only to agents, with the two code-only exceptions in decision 0001.
- Every write is proposed, approved as an exact diff, applied once and read back.
- New behaviour needs a test, and a mutation check where it guards a rule (undo the fix, a test
  must fail).

## Security

Don't put real notes, API keys or a vault path in an issue or PR. To report a vulnerability, open
a private security advisory on GitHub instead of a public issue.
