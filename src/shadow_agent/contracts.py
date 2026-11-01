"""One set of input, tool and result contracts; model text never becomes trusted state."""

import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from .common import utc


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


Identifier = Annotated[str, Field(min_length=1)]
Positive = Annotated[int, Field(gt=0)]


def parse_time(value: Any) -> datetime:
    if isinstance(value, str):
        fraction = re.search(r"[.,](\d+)", value)
        if fraction and any(x != "0" for x in fraction[1][6:]):
            raise ValueError("时间精度不可超出微秒")
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须是带 UTC 偏移的 ISO 8601 时间")
    try:
        value.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError("时间超出可表示的 UTC 范围") from exc
    return value


Timestamp = Annotated[datetime, BeforeValidator(parse_time)]
ReviewStatus = Literal["passed", "needs_info", "needs_review", "blocked"]
TaskStatus = Literal["completed", "needs_input", "blocked", "incomplete", "failed"]
SaveStatus = Literal["not_attempted", "saved", "reused", "updated", "failed", "unknown"]


class PublishRequirement(Contract):
    requested_at: Timestamp | None = None
    timezone: Identifier | None = None
    constraint: Literal["hard", "soft"] | None = None
    candidate_times: list[Timestamp] | None = None
    reschedule_preference: Literal["nearest", "prefer_before", "prefer_after"] = "nearest"

    @model_validator(mode="after")
    def validate_timezone(self):
        if self.timezone == "local":
            # Absolute instants are already validated. Local is a display/input policy,
            # not an assertion that a stored offset matches a future host setting.
            return self
        if self.timezone:
            try:
                zone = ZoneInfo(self.timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("未知 IANA 时区；请确认 tzdata 已安装") from exc
            for value in [self.requested_at, *(self.candidate_times or [])]:
                if value:
                    try:
                        offset = value.astimezone(zone).utcoffset()
                    except OverflowError as exc:
                        raise ValueError("时间超出指定时区可表示范围") from exc
                    if value.utcoffset() != offset:
                        raise ValueError("时间偏移与指定日期的 IANA 时区不一致")
        return self

    def missing(self) -> list[str]:
        return [x for x in ("requested_at", "timezone", "constraint") if getattr(self, x) is None]

    def canonical(self) -> dict:
        return {
            **(
                {"reschedule_preference": self.reschedule_preference}
                if self.reschedule_preference != "nearest"
                else {}
            ),
            "requested_at": utc(self.requested_at) if self.requested_at else None,
            "timezone": self.timezone,
            "constraint": self.constraint,
            "candidate_times": None
            if self.candidate_times is None
            else sorted({utc(x) for x in self.candidate_times}),
        }


class RequestItem(Contract):
    material_id: Identifier
    publish_requirement: PublishRequirement
    new_publication: bool = False


class Request(Contract):
    schema_version: Literal["1.0"] = "1.0"
    request_id: Identifier
    items: Annotated[list[RequestItem], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def unique_items(self):
        if len({x.material_id for x in self.items}) != len(self.items):
            raise ValueError("items 不得重复 material_id")
        return self

    def canonical(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "items": [
                {
                    "material_id": x.material_id,
                    "publish_requirement": x.publish_requirement.canonical(),
                    **({"new_publication": True} if x.new_publication else {}),
                }
                for x in sorted(self.items, key=lambda x: x.material_id)
            ],
        }


class BusinessFacts(Contract):
    source_asset_id: str | None = None
    title: str | None = None
    type: Literal["video", "image", "text"] | None = None
    media_ref: str | None = None
    caption: str | None = None
    cover_ref: str | None = None
    copyright_status: Literal["authorized", "unauthorized"] | None = None
    license_ref: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def blank_unknown(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


FACT_FIELDS = tuple(BusinessFacts.model_fields)


class Material(BusinessFacts):
    material_id: Identifier
    material_version: Positive
    platform: Identifier
    account_id: Identifier

    @field_validator("material_id", "platform", "account_id", mode="before")
    @classmethod
    def require_identity(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("运行身份不可缺失")
        return value


class Supplement(Contract):
    material_id: Identifier
    supplement_version: Positive
    source_id: Identifier
    fields: dict[str, str | None]

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value):
        return BusinessFacts.model_validate(value).model_dump(exclude_unset=True)


class Rule(Contract):
    rule_id: Identifier
    version: Positive
    field: str
    operator: Literal["missing", "equals", "contains"]
    value: str | None
    severity: Literal["needs_info", "needs_review", "blocked"]
    message: Identifier

    @model_validator(mode="after")
    def supported_rule(self):
        if self.field not in FACT_FIELDS:
            raise ValueError("不支持的规则字段")
        if self.operator == "missing":
            if self.value is not None:
                raise ValueError("missing 的 value 必须为 null")
        elif not self.value:
            raise ValueError("equals/contains 必须提供非空值")
        elif self.operator == "contains" and self.field in ("type", "copyright_status"):
            raise ValueError("contains 仅支持自由文本字段")
        elif self.operator == "equals":
            BusinessFacts.model_validate({self.field: self.value})
        return self


class Slot(Contract):
    platform: Identifier
    account_id: Identifier
    at: Timestamp


class ScheduleEntry(Slot):
    entry_id: Identifier
    material_id: Identifier


class Schedule(Contract):
    schedule_version: Positive
    allowed_slots: list[Slot]
    entries: list[ScheduleEntry]


class Catalog(Contract):
    materials: list[Material]
    supplements: list[Supplement]
    rules: Annotated[list[Rule], Field(min_length=1)]
    schedule: Schedule

    @model_validator(mode="after")
    def relations(self):
        if any(rule.rule_id.startswith("SYS-MINIMUM-") for rule in self.rules):
            raise ValueError("SYS-MINIMUM- 是系统最低完整性规则的保留前缀")
        for entries, key in (
            (self.materials, lambda x: x.material_id),
            (self.supplements, lambda x: x.material_id),
            (self.rules, lambda x: x.rule_id),
            (self.schedule.entries, lambda x: x.entry_id),
            (self.schedule.entries, lambda x: (x.platform, x.account_id, utc(x.at))),
            (self.schedule.allowed_slots, lambda x: (x.platform, x.account_id, utc(x.at))),
        ):
            if len({key(x) for x in entries}) != len(entries):
                raise ValueError("导入包含重复身份或时段")
        materials = {x.material_id for x in self.materials}
        if any(x.material_id not in materials for x in self.supplements):
            raise ValueError("补充资料必须关联同一导入中的素材")
        return self

    def canonical(self) -> dict:
        result = self.model_dump(mode="json")
        for name, key in (
            ("materials", "material_id"),
            ("supplements", "material_id"),
            ("rules", "rule_id"),
        ):
            result[name].sort(key=lambda x: x[key])
        for name in ("entries", "allowed_slots"):
            for entry in result["schedule"][name]:
                entry["at"] = utc(parse_time(entry["at"]))
            result["schedule"][name].sort(key=lambda x: (x["platform"], x["account_id"], x["at"]))
        return result


class Decision(Contract):
    item_id: Identifier
    decision: Literal["accept", "reject", "skip"]


class Confirmation(Contract):
    schema_version: Literal["1.0"] = "1.0"
    operation_id: Identifier
    draft_id: Identifier
    expected_revision: Positive
    decisions: Annotated[list[Decision], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_decisions(self):
        if len({x.item_id for x in self.decisions}) != len(self.decisions):
            raise ValueError("同一事项不能出现多个决定")
        if {x.decision for x in self.decisions} >= {"accept", "reject"}:
            raise ValueError("不能同时接受和拒绝")
        return self

    def canonical(self) -> dict:
        value = self.model_dump(mode="json")
        value["decisions"].sort(key=lambda x: x["item_id"])
        return value


class MaterialArgs(Contract):
    """Model-facing arguments: identity and evidence are bound locally."""

    reason_summary: Annotated[str, Field(max_length=300)] = ""


class ReadArgs(MaterialArgs):
    detail: Literal["basic", "supplement"]


class SlotArgs(MaterialArgs):
    """One query returns the original slot and any necessary alternatives."""


class SaveArgs(MaterialArgs):
    suggested_at: Timestamp


class FinalProposal(Contract):
    action: Literal["finish", "needs_input", "block"]
    reason_summary: Annotated[str, Field(max_length=300)]


class ToolResult(Contract):
    observation_id: str
    tool: str
    ok: bool
    code: str
    retryable: bool = False
    catalog_revision: int | None = None
    data: dict = Field(default_factory=dict)


class PendingItem(Contract):
    id: str
    kind: Literal["input", "confirm"]
    message: str


class DraftResult(Contract):
    id: str | None = None
    revision: int | None = None
    validity: Literal["active", "stale", "rejected"] | None = None
    save_status: SaveStatus = "not_attempted"
    error: dict | None = None


class ProposedPlan(Contract):
    suggested_at: str
    pending_items: list[PendingItem] = Field(default_factory=list)
    persistence_status: Literal["not_saved", "saved", "unknown"] = "not_saved"
    validity: Literal["requires_recheck", "current", "invalidated"] = "requires_recheck"
    error: dict | None = None


class ItemResult(Contract):
    material_id: str
    material_version: int | None = None
    review_status: ReviewStatus | None = None
    risk_reasons: list[dict] = Field(default_factory=list)
    original_requested_at: str | None
    constraint: Literal["hard", "soft"] | None
    suggested_at: str | None = None
    confirmation_status: Literal["not_required", "pending", "confirmed", "rejected"]
    pending_items: list[PendingItem] = Field(default_factory=list)
    draft: DraftResult = Field(default_factory=DraftResult)
    task_status: TaskStatus
    evidence_ids: list[str] = Field(default_factory=list)
    error: dict | None = None
    proposed_plan: ProposedPlan | None = None
    related_drafts: list[dict] = Field(default_factory=list)


class RequestResult(Contract):
    schema_version: Literal["1.0", "1.1"] = "1.1"
    run_id: str
    request_id: str
    mode: Literal["mock", "live"]
    task_status: TaskStatus
    items: list[ItemResult]


class MaterialSnapshot(Contract):
    material: Material
    catalog_revision: Positive
    import_id: str
    sources: dict[str, str]
    supplement_version: int | None = None
    supplemented: bool = False

    def facts_hash(self) -> str:
        from .common import digest

        return digest(
            {
                "material": self.material.model_dump(mode="json"),
                "sources": self.sources,
            }
        )


class RuleCheck(Contract):
    catalog_revision: Positive
    material_sha256: str
    rules: list[Rule]
    review_status: ReviewStatus
    matches: list[dict]


class SlotObservation(Contract):
    catalog_revision: Positive
    query: Literal["requested", "alternatives"]
    source: Literal["requested", "explicit", "default", "none"]
    slots: list[dict]
    checked_slots: list[dict] = Field(default_factory=list)


class ScheduleOptions(Contract):
    requested: SlotObservation
    alternatives: SlotObservation | None = None

    def evidence(self) -> list[SlotObservation]:
        return [self.requested] + ([self.alternatives] if self.alternatives else [])

    def available_slots(self) -> list[dict]:
        return [
            slot
            for observation in self.evidence()
            for slot in observation.slots
            if slot["available"]
        ]
