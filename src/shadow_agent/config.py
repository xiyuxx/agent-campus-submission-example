"""Explicit budgets and opt-in model settings; never expose credentials in repr/logs."""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

from .common import BusinessError


@dataclass(frozen=True)
class Limits:
    model_attempts: int = 16
    tool_attempts: int = 16
    item_seconds: float = 180
    request_seconds: float = 540
    protocol_feedbacks: int = 2
    schedule_requeries: int = 2

    def __post_init__(self):
        if not (
            1 <= self.model_attempts <= 16
            and 1 <= self.tool_attempts <= 16
            and 0 < self.item_seconds <= 180
            and 0 < self.request_seconds <= 540
            and 0 <= self.protocol_feedbacks <= 2
            and 0 <= self.schedule_requeries <= 2
        ):
            raise BusinessError("INVALID_LIMITS", "预算必须在模型16次/工具16次/单项180秒/总540秒内")


@dataclass
class Budget:
    limits: Limits
    request_deadline: float
    clock: Callable[[], float] = monotonic
    models: int = 0
    tools: int = 0
    started: float = field(init=False)
    deadline: float = field(init=False)

    def __post_init__(self):
        self.started = self.clock()
        self.deadline = min(self.request_deadline, self.started + self.limits.item_seconds)

    def remaining(self) -> float:
        return max(0.0, self.deadline - self.clock())

    def ensure(self) -> None:
        if self.remaining() <= 0:
            raise BusinessError("BUDGET_EXHAUSTED", "执行时间预算耗尽")

    def model_attempt(self) -> None:
        self.ensure()
        if self.models >= self.limits.model_attempts:
            raise BusinessError("BUDGET_EXHAUSTED", "模型调用预算耗尽")
        self.models += 1

    def tool_attempt(self) -> None:
        self.ensure()
        if self.tools >= self.limits.tool_attempts:
            raise BusinessError("BUDGET_EXHAUSTED", "工具调用预算耗尽")
        self.tools += 1

    def summary(self) -> dict:
        return {
            "model_attempts_left": self.limits.model_attempts - self.models,
            "tool_attempts_left": self.limits.tool_attempts - self.tools,
            "seconds_left": round(self.remaining(), 3),
        }


@dataclass(frozen=True)
class ResourceLimits:
    request_bytes: int = 256 * 1024
    tool_result_bytes: int = 32 * 1024
    arguments_bytes: int = 8 * 1024
    response_bytes: int = 256 * 1024
    calls_per_response: int = 16
    max_output_tokens: int = 2048


@dataclass(frozen=True)
class ModelSettings:
    api_key: str = field(repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"

    @classmethod
    def load(cls, env_file: Path | None = None):
        allowed = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")
        values = {}
        if env_file is not None:
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                name, separator, value = line.strip().removeprefix("export ").partition("=")
                if separator and name.strip() in allowed:
                    values[name.strip()] = value.strip().strip("\"'")
        values.update({name: os.environ[name] for name in allowed if os.environ.get(name)})
        if not values.get("DEEPSEEK_API_KEY"):
            raise BusinessError(
                "MISSING_API_KEY", "live 需要配置 DEEPSEEK_API_KEY；可显式 --env-file .env"
            )
        base = values.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        if not base.startswith("https://"):
            raise BusinessError("INVALID_CONFIG", "live 模型地址必须使用 HTTPS")
        return cls(
            values["DEEPSEEK_API_KEY"],
            base.rstrip("/"),
            values.get("DEEPSEEK_MODEL") or "deepseek-flash",
        )
