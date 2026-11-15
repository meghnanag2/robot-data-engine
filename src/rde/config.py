"""Typed settings, read once from environment variables (set by Terraform)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    env: str
    data_bucket: str
    hf_base_url: str = "https://huggingface.co"
    hf_revision: str = "main"
    http_timeout_s: int = 300
    sample_every_s: float = 1.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        env=os.environ.get("RDE_ENV", "dev"),
        data_bucket=os.environ["DATA_BUCKET"],
        hf_revision=os.environ.get("HF_REVISION", "main"),
        sample_every_s=float(os.environ.get("SAMPLE_EVERY_S", "1.0")),
    )
