"""Actual bounded event records; no raw model response or reasoning content."""

import hashlib
import os
from pathlib import Path
from time import monotonic

from .common import dumps, new_id, now


def bounded(value, limit: int = 1024):
    if isinstance(value, str) and len(value) > limit:
        return {
            "text": value[:limit],
            "truncated": True,
            "sha256": hashlib.sha256(value.encode()).hexdigest(),
        }
    if isinstance(value, list):
        if len(value) > 100:
            return {
                "items": [bounded(x, limit) for x in value[:100]],
                "truncated": True,
                "total_count": len(value),
            }
        return [bounded(x, limit) for x in value]
    if isinstance(value, dict):
        return {k: bounded(v, limit) for k, v in value.items()}
    return value


class TraceSink:
    def __init__(self, directory: Path, run_id: str, mode: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.path = self.directory / "trace.jsonl"
        self.path.touch(exist_ok=False)
        self.run_id, self.mode = run_id, mode
        self.started = monotonic()
        self.step = 0

    def emit(self, event_type: str, *, material_id=None, budget=None, **data) -> str:
        self.step += 1
        event_id = new_id("EV")
        event = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "event_id": event_id,
            "step": self.step,
            "timestamp": now(),
            "mode": self.mode,
            "event_type": event_type,
            "material_id": material_id,
            "elapsed_ms": round((monotonic() - self.started) * 1000),
            "budget_remaining": budget.summary() if budget else None,
            **data,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(dumps(bounded(event)) + "\n")
            stream.flush()
        return event_id

    def check_writable(self) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.flush()


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class NullTrace:
    def emit(self, event_type: str, **data) -> str:
        return "OFFLINE-TEST-EVENT"

    def check_writable(self) -> None:
        pass
