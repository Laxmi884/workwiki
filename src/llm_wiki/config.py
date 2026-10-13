"""Process settings read from the environment and `.env` (prefix `WIKI_`)."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    @field_validator("vault_path")
    @classmethod
    def _existing_dir(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"vault folder does not exist: {path}")
        return path

    @field_validator("model")
    @classmethod
    def _model_spec(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return ModelSpec.parse(value).spec  # fail at startup, not mid-run


@cache
def get_settings() -> Settings:
    """Return the process settings, read once from the environment and `.env`."""
    return Settings()
