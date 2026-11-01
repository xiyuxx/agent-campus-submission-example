"""Canonical values and public errors shared across explicit boundaries."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须带 UTC 偏移")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def now() -> str:
    return utc(datetime.now(UTC))


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


class BusinessError(Exception):
    def __init__(
        self, code: str, message: str, *, retryable: bool = False, details: dict | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}
