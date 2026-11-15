"""Hugging Face Hub client for dataset repos (public, no token needed)."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int


def with_retries(fn: Callable[[], T], attempts: int = 3, base_delay_s: float = 1.0) -> T:
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(base_delay_s * 2**i)
    raise RuntimeError("unreachable")


class HuggingFaceDatasets:
    def __init__(
        self, base_url: str = "https://huggingface.co", revision: str = "main", timeout_s: int = 300
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.revision = revision
        self.timeout_s = timeout_s

    def list_files(self, repo_id: str) -> list[RemoteFile]:
        url = f"{self.base_url}/api/datasets/{repo_id}/tree/{self.revision}?recursive=true"

        def _get() -> list[RemoteFile]:
            with urllib.request.urlopen(url, timeout=60) as resp:
                items = json.load(resp)
            return [RemoteFile(i["path"], int(i.get("size", 0))) for i in items if i.get("type") == "file"]

        return with_retries(_get)

    @contextmanager
    def open(self, repo_id: str, path: str) -> Iterator[BinaryIO]:
        quoted = urllib.parse.quote(path)
        url = f"{self.base_url}/datasets/{repo_id}/resolve/{self.revision}/{quoted}"
        resp = with_retries(lambda: urllib.request.urlopen(url, timeout=self.timeout_s))
        try:
            yield resp
        finally:
            resp.close()
