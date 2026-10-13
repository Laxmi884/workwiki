"""Process settings read from the environment and `.env` (prefix `WIKI_`)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings. Load once at startup with `Settings()`.

    Attributes:
        vault_path: `WIKI_VAULT_PATH`. The Obsidian vault to read and write; required,
            no default, so a missing setting can never fall back to the real vault.
        task_ids: `WIKI_TASK_IDS`. Where task IDs live (decision 0001). Only "inline"
            (block IDs on the task line) is built; "sidecar" is rejected until it is.
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

    @field_validator("vault_path")
    @classmethod
    def _existing_dir(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"vault folder does not exist: {path}")
        return path
