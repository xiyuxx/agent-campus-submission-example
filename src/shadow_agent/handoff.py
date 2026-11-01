"""Lightweight JSON/CSV snapshots; exports are never proof of delivery or publishing."""

import csv
import json
from pathlib import Path

from .common import BusinessError
from .operator_language import CONFIRM_LABELS, REVIEW_LABELS, display_time


def spreadsheet_text(value):
    text = str(value or "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def export_handoff(repo, path: Path | None):
    if path is None or path.exists() or path.suffix.lower() not in (".json", ".csv"):
        raise BusinessError("INVALID_INPUT", "请提供新的 .csv 或 .json 交接文件路径")
    result = repo.handoff()
    result["generated_at_local"] = display_time(result["generated_at"])
    path.parent.mkdir(parents=True, exist_ok=True)
    is_csv = path.suffix.lower() == ".csv"
    with path.open("x", encoding="utf-8-sig" if is_csv else "utf-8", newline="") as stream:
        if is_csv:
            fields = [
                "素材编号",
                "标题",
                "平台",
                "账号",
                "本地发布时间",
                "审核结论",
                "风险说明",
                "确认状态",
                "草稿版本",
                "保存状态",
                "交接版本",
                "导出时间",
                "执行提醒",
            ]
            writer = csv.writer(stream)
            writer.writerow(fields)
            for item in result["items"]:
                writer.writerow(
                    [
                        spreadsheet_text(value)
                        for value in (
                            item["material_id"],
                            item["title"],
                            item["platform"],
                            item["account_id"],
                            display_time(item["suggested_at"]),
                            "风险已人工确认"
                            if item["review_status"] == "needs_review"
                            and item["confirmation_status"] == "confirmed"
                            else REVIEW_LABELS[item["review_status"]],
                            "；".join(risk["message"] for risk in item["risk_reasons"]),
                            CONFIRM_LABELS[item["confirmation_status"]],
                            item["revision"],
                            "已保存本地草稿",
                            result["export_version"],
                            result["generated_at_local"],
                            result["notice"],
                        )
                    ]
                )
        else:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return {
        "file": str(path),
        "count": len(result["items"]),
        "notice": result["notice"],
        "export_version": result["export_version"],
        "generated_at_local": result["generated_at_local"],
    }
