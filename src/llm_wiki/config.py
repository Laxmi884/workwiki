"""Process settings read from the environment and `.env` (prefix `WIKI_`).

`Settings` reads only `WIKI_*` names and keeps them to itself. Provider SDKs read their
keys (`OPENROUTER_API_KEY`, ...) from the process environment, so a command we start
ourselves calls `load_env_file()` first; `langgraph dev` loads `.env` on its own
(`"env"` in `langgraph.json`).
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from llm_wiki.models import ModelSpec


class Settings(BaseSettings):
    """Typed settings. Load once at startup with `Settings()`.

    Attributes:
        vault_path: `WIKI_VAULT_PATH`. The Obsidian vault to read and write; required,
            no default, so a missing setting can never fall back to the real vault.
        task_ids: `WIKI_TASK_IDS`. Where task IDs live (decision 0001). Only "inline"
            (block IDs on the task line) is built; "sidecar" is rejected until it is.
        model: `WIKI_MODEL`. Default "provider:model", e.g. "ollama:llama3.1:8b" or
            "anthropic:claude-haiku-5-5". A run's context `model` overrides it.
        state_dir: `WIKI_STATE_DIR`. Where run state lives (the runner's SQLite
            checkpoints); outside the vault, so sync never copies it. Default
            "~/.llm-wiki".
        fallback_models: `WIKI_FALLBACK_MODELS`. Comma-separated "provider:model"
            specs tried in order when the run's model keeps failing (outage,
            overload). Empty: no fallback.
        review_url: `WIKI_REVIEW_URL`. Where the review page is served, for links in
            chat messages. Default "http://127.0.0.1:2024" (`langgraph dev`).
        model_timeout: `WIKI_MODEL_TIMEOUT`. The request timeout passed to the
            provider, so a provider that never answers fails (and is retried) instead
            of stalling the run. It does not cap a call's total time: on 2026-10-16
            OpenRouter calls that were still reasoning ran 206 s and over 460 s with
            the default (likely the connection stays active while the model works).
            Not applied to local Ollama models. Default 180.
        backend: `WIKI_BACKEND`. How surfaces reach the agents (decision 0010):
            "local" (this process, our SQLite) or "server" (an Agent Server at
            `server_url`). Default "local".
        server_url: `WIKI_SERVER_URL`. The Agent Server for the "server" backend.
            Default "http://127.0.0.1:2024" (`langgraph dev`).
    """

    model_config = SettingsConfigDict(
        env_prefix="WIKI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    vault_path: Path
    task_ids: Literal["inline"] = "inline"
    model: str | None = None
    fallback_models: Annotated[tuple[str, ...], NoDecode] = ()
    model_timeout: float = Field(default=180.0, gt=0)
    review_url: str = "http://127.0.0.1:2024"
    state_dir: Path = Path("~/.llm-wiki")
    backend: Literal["local", "server"] = "local"
    server_url: str = "http://127.0.0.1:2024"

    @field_validator("vault_path")
    @classmethod
    def _existing_dir(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"vault folder does not exist: {path}")
        return path

    @field_validator("state_dir")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()

    @model_validator(mode="after")
    def _state_outside_vault(self) -> Settings:
        if self.state_dir.resolve().is_relative_to(self.vault_path):
            raise ValueError("state_dir must be outside the vault (sync would copy it)")
        return self

    @field_validator("model")
    @classmethod
    def _model_spec(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return ModelSpec.parse(value).spec  # fail at startup, not mid-run

    @field_validator("fallback_models", mode="before")
    @classmethod
    def _fallback_specs(cls, value: str | Sequence[str]) -> tuple[str, ...]:
        items = value.split(",") if isinstance(value, str) else value
        return tuple(ModelSpec.parse(s).spec for s in items if s.strip())


@cache
def get_settings() -> Settings:
    """Return the process settings, read once from the environment and `.env`."""
    return Settings()


def load_env_file(path: str | Path = ".env") -> bool:
    """Copy `path` into the process environment; variables already set win.

    Returns whether the file existed. The default is the same `.env` (relative to the
    working directory) that `Settings` reads.
    """
    return load_dotenv(path, override=False)
