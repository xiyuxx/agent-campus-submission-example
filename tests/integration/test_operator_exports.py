import csv
import json

import pytest
from test_storage import decide, save

from shadow_agent.common import BusinessError
from shadow_agent.handoff import export_handoff
from shadow_agent.operator_language import display_time
from shadow_agent.work import ask_supplement, describe


def ready(repo, job):
    draft = save(repo, job)["draft"]
    repo.confirm_draft(decide(draft))
    return next(row for row in repo.task_worklist() if row["material_id"] == "M01")


def test_csv_and_json_share_version_and_show_actual_local_time(repo, job, tmp_path):
    row = ready(repo, job)
    csv_path, json_path = tmp_path / "handoff.csv", tmp_path / "handoff.json"
    a, b = export_handoff(repo, csv_path), export_handoff(repo, json_path)
    assert a["export_version"] == b["export_version"]
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    assert len(records) == 1
    assert records[0]["本地发布时间"] == display_time(row["draft"]["at_utc"])
    assert records[0]["确认状态"] == "已确认" and records[0]["保存状态"] == "已保存本地草稿"
    assert records[0]["草稿版本"] == "1"
    assert records[0]["交接版本"] == a["export_version"]
    assert "静态" in records[0]["执行提醒"]
    assert (
        json.loads(json_path.read_text("utf-8"))["items"][0]["draft_id"] == row["draft"]["draft_id"]
    )


def test_cancel_changes_export_version_without_rewriting_old_file(repo, job, tmp_path):
    row = ready(repo, job)
    path = tmp_path / "before.csv"
    first = export_handoff(repo, path)
    original = path.read_bytes()
    repo.cancel_task(row["task_id"], expected_revision=row["revision"])
    second = export_handoff(repo, tmp_path / "after.csv")
    assert second["count"] == 0 and first["export_version"] != second["export_version"]
    assert path.read_bytes() == original
    with pytest.raises(BusinessError):
        export_handoff(repo, path)


def test_unrelated_update_preserves_export_version_and_confirmation(repo, job, tmp_path):
    ready(repo, job)
    first = export_handoff(repo, tmp_path / "first.json")
    version = repo.read_material_basic("M02").material.material_version
    repo.supplement_material("M02", {"title": "另一项更新"}, expected_material_version=version)
    second = export_handoff(repo, tmp_path / "second.csv")
    assert first["export_version"] == second["export_version"] and second["count"] == 1


def test_csv_escapes_formula_like_titles_and_quotes_multiline_values(repo, job, catalog, tmp_path):
    catalog.materials[0].title = '=HYPERLINK("https://example.invalid")\n第二行,逗号'
    repo.apply_catalog_snapshot(catalog, update=True)
    ready(repo, job)
    path = tmp_path / "formula.csv"
    export_handoff(repo, path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]["标题"].startswith("'=HYPERLINK")
    assert "\n第二行,逗号" in rows[0]["标题"]


def test_human_summary_hides_protocol_labels_and_converts_pending_times(repo, job, capsys):
    draft = save(repo, job)["draft"]
    row = next(row for row in repo.task_worklist() if row["material_id"] == "M01")
    describe(row)
    text = capsys.readouterr().err
    assert display_time(draft["at_utc"]) in text and "必须遵守" in text and "待确认" in text
    assert "active" not in text and "hard" not in text and "pending" not in text


def test_guided_missing_fields_are_explicit_user_answers_without_json(repo, monkeypatch):
    row = next(row for row in repo.task_worklist() if row["material_id"] == "M02")
    row["last_result"] = {
        "risk_reasons": [
            {"field": "copyright_status", "severity": "needs_info"},
            {"field": "cover_ref", "severity": "needs_info"},
        ]
    }
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return "1" if "版权" in prompt else "synthetic://cover/M02"

    monkeypatch.setattr("shadow_agent.work.ask", answer)
    result = ask_supplement(repo, row)
    assert result == {"copyright_status": "authorized", "cover_ref": "synthetic://cover/M02"}
    assert all("JSON" not in prompt for prompt in prompts)
    assert repo.read_material_basic("M02").material.copyright_status is None
