"""Explicit business projections; internal identities never become model arguments/data."""

from datetime import datetime
from zoneinfo import ZoneInfo

from .contracts import ToolResult


def local_time(value, timezone):
    if value is None or not timezone:
        return value
    zone = None if timezone == "local" else ZoneInfo(timezone)
    return datetime.fromisoformat(value).astimezone(zone).isoformat()


def plan_view(draft, timezone):
    if not draft:
        return None
    return {
        "validity": draft["validity"],
        "suggested_at": local_time(draft["at_utc"], timezone)
        if draft["validity"] == "active"
        else None,
        "confirmation_status": draft["confirmation_status"],
        "pending_items": [
            {"kind": x["kind"], "message": x["message"]} for x in draft["pending_items"]
        ],
    }


def tool_view(result: ToolResult, timezone: str | None) -> dict:
    """Project by tool and explicit field whitelist, including error paths."""
    raw = result.data
    if not result.ok:
        data = {"message": raw.get("message", "工具未完成"), "executed": raw.get("executed", False)}
        if "current_draft" in raw:
            data["current_plan"] = plan_view(raw["current_draft"], timezone)
        if "recovery_action" in raw:
            data["recovery_action"] = raw["recovery_action"]
    elif result.tool == "read_material":
        material = raw["material"]
        data = {
            "detail": raw["detail"],
            "material": {
                **{k: material[k] for k in ("title", "type", "caption", "copyright_status")},
                **{
                    label: material[key] is not None
                    for key, label in (
                        ("source_asset_id", "source_asset_provided"),
                        ("media_ref", "media_provided"),
                        ("cover_ref", "cover_provided"),
                        ("license_ref", "license_provided"),
                    )
                },
            },
            "missing_fields": raw["missing_fields"],
            "supplemented": raw["supplemented"],
            "supplement_available": raw.get("supplement_available", False),
        }
    elif result.tool == "check_rules":
        data = {
            "review_status": raw["review_status"],
            **{
                k: raw[k]
                for k in (
                    "blocking_missing_fields",
                    "other_missing_fields",
                    "supplement_available",
                    "supplemented",
                )
                if k in raw
            },
            "matches": [
                {k: match[k] for k in ("field", "severity", "message")} for match in raw["matches"]
            ],
        }
    elif result.tool == "query_slots":

        def slot_view(slot):
            return {
                "at": local_time(slot["at"], timezone),
                "available": slot["available"],
                "direction": slot.get("direction", "requested"),
                "conflict_sources": sorted({x["source"] for x in slot["conflicts"]}),
            }

        alternatives = raw["alternatives"]
        data = {
            "requested_slot": slot_view(raw["requested"]["slots"][0]),
            "alternatives_searched": alternatives is not None,
            "alternative_source": alternatives["source"] if alternatives else None,
            "alternatives": [slot_view(slot) for slot in alternatives["slots"]]
            if alternatives
            else [],
        }
    else:
        data = {
            **{k: raw[k] for k in ("save_status", "validity", "confirmation_status")},
            "suggested_at": local_time(raw["suggested_at"], timezone),
            "pending_items": [
                {"kind": x["kind"], "message": x["message"]} for x in raw["pending_items"]
            ],
            "pending_total_count": raw["pending_total_count"],
        }
    return {
        "tool": result.tool,
        "ok": result.ok,
        "code": result.code,
        "retryable": result.retryable,
        "data": data,
    }
