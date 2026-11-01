"""Human-facing local times and field labels; absolute timestamps remain explicit."""

import re
from datetime import UTC, datetime, tzinfo

from .contracts import parse_time

FIELD_LABELS = {
    "source_asset_id": "来源素材编号",
    "title": "标题",
    "type": "素材类型",
    "media_ref": "素材文件或链接",
    "caption": "正文或文案",
    "cover_ref": "封面文件或链接",
    "copyright_status": "版权授权状态",
    "license_ref": "授权凭证",
    "requested_at": "发布时间",
    "timezone": "时区",
    "constraint": "时间要求",
    "candidate_times": "可建议的时间范围",
}
REVIEW_LABELS = {
    "passed": "审核通过",
    "needs_info": "资料待补齐",
    "needs_review": "存在需人工确认的风险",
    "blocked": "审核阻断",
}
CONFIRM_LABELS = {
    "confirmed": "已确认",
    "pending": "待确认",
    "not_required": "无需额外确认",
    "rejected": "已拒绝",
}
VALIDITY_LABELS = {"active": "有效", "stale": "已失效", "rejected": "已拒绝"}


def humanize(message: str) -> str:
    return re.sub(r"[a-z_]+", lambda match: FIELD_LABELS.get(match[0], match[0]), message)


def local_iso(value: str) -> str:
    return parse_time(value).astimezone().isoformat()


def display_time(value: str | None, *, zone: tzinfo | None = None) -> str:
    if not value:
        return "待补充"
    when = parse_time(value).astimezone(zone)
    precision = "microseconds" if when.microsecond else "seconds" if when.second else "minutes"
    return when.isoformat(sep=" ", timespec=precision) + "（本机时间）"


def parse_operator_time(value: str, *, zone: tzinfo | None = None) -> str:
    """Use the OS local offset at that date; reject ambiguous/nonexistent wall times."""
    if not re.search(r"[T ]\d{2}:\d{2}", value):
        raise ValueError("请填写日期和时间，例如 2026-11-03 22:00")
    when = datetime.fromisoformat(value)
    if when.tzinfo is not None:
        return parse_time(when).isoformat()
    choices = {}
    for fold in (0, 1):
        local = (
            when.replace(tzinfo=zone, fold=fold) if zone else when.replace(fold=fold).astimezone()
        )
        absolute = local.astimezone(UTC)
        returned = absolute.astimezone(zone) if zone else absolute.astimezone()
        if returned.replace(tzinfo=None) == when:
            choices[absolute] = local
    if len(choices) != 1:
        raise ValueError("本地时间不存在或有夏令时歧义，请提供带明确偏移的时间")
    return parse_time(next(iter(choices.values()))).isoformat()


def pending_message(pending: dict, draft: dict) -> str:
    if pending["id"] == "change_time":
        original = draft["payload"]["original_requirement"]["requested_at"]
        return f"是否同意从 {display_time(original)} 改为 {display_time(draft['at_utc'])}？"
    return humanize(pending["message"])
