import json
import subprocess
import sys

import pytest


def work(repo, tmp_path, *args, stdin=None, at="2026-11-01T00:00:00Z"):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "shadow_agent",
            "work",
            "--db",
            str(repo.path),
            "--root",
            str(tmp_path / "work"),
            "--as-of",
            at,
            *map(str, args),
        ],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def check(repo, tmp_path, material="M01"):
    return work(
        repo,
        tmp_path,
        "--action",
        "check",
        "--material",
        material,
        "--at",
        "2026-11-03T18:00:00+08:00",
        "--timezone",
        "Asia/Shanghai",
        "--constraint",
        "hard",
        "--candidate",
        "2026-11-03T19:00:00+08:00",
    )


def test_operator_journey_check_confirm_edit_cancel_export(repo, tmp_path):
    first = check(repo, tmp_path)
    assert first.returncode == 3, first.stdout + first.stderr
    result = json.loads(first.stdout)
    draft = result["runs"][0]["result"]["items"][0]["draft"]
    path = tmp_path / "pending.json"
    exported = work(repo, tmp_path, "--action", "export", "--file", path)
    assert json.loads(exported.stdout)["count"] == 0
    confirmed = work(
        repo, tmp_path, "--action", "confirm", "--material", "M01", "--decision", "accept"
    )
    assert confirmed.returncode == 0, confirmed.stdout
    ready = tmp_path / "ready.json"
    assert work(repo, tmp_path, "--action", "export", "--file", ready).returncode == 0
    handoff = json.loads(ready.read_text("utf-8"))
    assert handoff["items"][0]["draft_id"] == draft["id"]
    assert handoff["items"][0]["confirmation_status"] == "confirmed"
    assert work(repo, tmp_path, "--action", "export", "--file", ready).returncode == 2
    edited = work(
        repo, tmp_path, "--action", "edit", "--material", "M01", "--at", "2026-11-03T20:00:00+08:00"
    )
    assert edited.returncode == 0, edited.stdout
    item = json.loads(edited.stdout)["runs"][0]["result"]["items"][0]
    assert item["suggested_at"] == "2026-11-03T20:00:00+08:00"
    assert item["confirmation_status"] == "not_required"
    assert item["draft"]["id"] != draft["id"]
    assert work(repo, tmp_path, "--action", "cancel", "--material", "M01").returncode == 0
    after = tmp_path / "cancelled.json"
    work(repo, tmp_path, "--action", "export", "--file", after)
    assert json.loads(after.read_text("utf-8"))["items"] == []
    assert work(repo, tmp_path, "--action", "check", "--material", "M01").returncode == 3
    assert work(repo, tmp_path, "--action", "reopen", "--material", "M01").returncode == 0


def test_supplement_and_continue_is_a_single_user_action(repo, tmp_path):
    assert check(repo, tmp_path, "M02").returncode == 3
    fields = tmp_path / "fields.json"
    fields.write_text(
        json.dumps({"copyright_status": "authorized", "cover_ref": "synthetic://cover"}),
        encoding="utf-8",
    )
    continued = work(
        repo, tmp_path, "--action", "supplement", "--material", "M02", "--fields", fields
    )
    assert continued.returncode == 3, continued.stdout
    result = json.loads(continued.stdout)
    item = result["continued"]["runs"][0]["result"]["items"][0]
    assert item["review_status"] == "passed"
    assert item["draft"]["save_status"] == "saved"


def test_interactive_flow_hides_ids_and_handles_invalid_selection(repo, tmp_path):
    path = tmp_path / "interactive-export.json"
    stdin = (
        "99\n1\n1\n2026-11-03T18:00:00+08:00\nAsia/Shanghai\nhard\n"
        "2026-11-03T19:00:00+08:00\n1\n4\n1\ne\n" + str(path) + "\n\n"
    )
    result = work(repo, tmp_path, stdin=stdin)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["closed"]
    assert "请选择一至三个不同的有效序号" in result.stderr
    assert "等你确认" in result.stderr and "可交接" in result.stderr
    assert "TASKREQ-" not in result.stderr and "RUN-" not in result.stderr
    assert json.loads(path.read_text("utf-8"))["items"][0]["confirmation_status"] == "confirmed"


def test_export_checks_business_time_at_execution(repo, tmp_path):
    check(repo, tmp_path)
    work(repo, tmp_path, "--action", "confirm", "--material", "M01", "--decision", "accept")
    path = tmp_path / "expired.json"
    result = work(repo, tmp_path, "--action", "export", "--file", path, at="2026-11-03T11:00:00Z")
    assert result.returncode == 0
    assert json.loads(path.read_text("utf-8"))["items"] == []


@pytest.mark.parametrize(
    "at,zone",
    [
        ("9999-12-31T23:59:59-12:00", "Etc/GMT+12"),
        ("0001-01-01T00:00:00+14:00", "Etc/GMT-14"),
    ],
)
def test_extreme_dates_return_invalid_input_without_traceback(repo, tmp_path, at, zone):
    result = work(
        repo,
        tmp_path,
        "--action",
        "check",
        "--material",
        "M01",
        "--at",
        at,
        "--timezone",
        zone,
        "--constraint",
        "hard",
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_INPUT"
    assert "Traceback" not in result.stderr


def test_batch_limit_and_partial_result_survive_a_cancelled_item(repo, tmp_path):
    invalid = work(repo, tmp_path, "--action", "check", "--material", "M01", "--material", "M01")
    assert invalid.returncode == 2
    check(repo, tmp_path)
    work(repo, tmp_path, "--action", "cancel", "--material", "M01")
    batch = work(repo, tmp_path, "--action", "check", "--material", "M01", "--material", "M02")
    assert batch.returncode == 3
    runs = json.loads(batch.stdout)["runs"]
    assert runs[0]["error"]["code"] == "TASK_CANCELLED"
    assert runs[1]["result"]["items"][0]["review_status"] == "needs_info"
