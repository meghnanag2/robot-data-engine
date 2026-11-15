"""Thin S3 wrapper: the only module that talks to S3 directly."""

from __future__ import annotations

import json
from typing import Any, BinaryIO

import boto3
from botocore.exceptions import ClientError


class ObjectStore:
    def __init__(self, bucket: str, client: Any | None = None) -> None:
        self.bucket = bucket
        self.s3 = client or boto3.client("s3")

    def size_of(self, key: str) -> int | None:
        try:
            return self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def upload_stream(self, stream: BinaryIO, key: str) -> None:
        self.s3.upload_fileobj(stream, self.bucket, key)

    def put_bytes(self, key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)

    def put_json(self, key: str, obj: Any) -> None:
        self.put_bytes(key, json.dumps(obj, default=str).encode(), "application/json")

    def put_jsonl(self, key: str, rows: list[dict]) -> None:
        body = "\n".join(json.dumps(r, default=str) for r in rows).encode()
        self.put_bytes(key, body, "application/x-ndjson")

    def download(self, key: str, local_path: str) -> None:
        self.s3.download_file(self.bucket, key, local_path)

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"
