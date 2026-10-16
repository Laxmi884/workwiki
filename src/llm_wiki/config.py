"""Process settings read from the environment and `.env` (prefix `WIKI_`)."""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Annotated, Literal

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
        model_timeout: `WIKI_MODEL_TIMEOUT`. Seconds one model request may take before
            it fails (and is retried); a provider that never answers would otherwise
            stall the run. Not applied to local Ollama models. Default 180.
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
    state_dir: Path = Path("~/.llm-wiki")

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
