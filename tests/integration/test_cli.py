import json
import subprocess
import sys
from pathlib import Path


def cli(*args, stdin=None):
    if args[0] in ("run", "confirm", "review"):
        args = (*args, "--as-of", "2020-01-01T00:00:00Z")
    return subprocess.run(
        [sys.executable, "-m", "shadow_agent", *map(str, args)],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def test_dry_run_does_not_create_or_modify_database(tmp_path):
    data = Path(__file__).resolve().parents[2] / "data"
    database = tmp_path / "agent.db"
    preview = cli("seed", "--data", data, "--db", database, "--dry-run")
    assert preview.returncode == 0
    assert json.loads(preview.stdout)["dry_run"]
    assert not database.exists()
    seeded = cli("seed", "--data", data, "--db", database)
    assert seeded.returncode == 0
    previous = database.read_bytes()
    assert cli("seed", "--data", data, "--db", database, "--dry-run").returncode == 0
    assert database.read_bytes() == previous
    assert cli("seed", "--data", data, "--db", database).returncode == 2


def test_bad_seed_is_atomic_and_missing_files_are_errors(tmp_path):
    database = tmp_path / "agent.db"
    assert cli("seed", "--data", tmp_path, "--db", database).returncode == 2
    assert not database.exists()


def test_inspect_and_confirmation_cli_share_state(repo, job, tmp_path):
    input_path = tmp_path / "job.json"
    input_path.write_text(job.model_dump_json(), encoding="utf-8")
    run = cli(
        "run",
        "--input",
        input_path,
        "--db",
        repo.path,
        "--mode",
        "mock",
        "--output",
        tmp_path / "run",
    )
    result = json.loads(run.stdout)
    draft = result["items"][0]["draft"]
    decision = {
        "operation_id": "CLI-C1",
        "draft_id": draft["id"],
        "expected_revision": 1,
        "decisions": [{"item_id": "change_time", "decision": "accept"}],
    }
    decision_path = tmp_path / "confirm.json"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    confirmed = cli("confirm", "--db", repo.path, "--input", decision_path)
    assert confirmed.returncode == 0
    inspected = cli("inspect-draft", "--db", repo.path, "--id", draft["id"])
    assert json.loads(inspected.stdout)["confirmation_status"] == "confirmed"
    assert cli("review", "--db", repo.path, stdin="").returncode == 0
    changed = job.model_copy(deep=True)
    changed.items[0].publish_requirement.candidate_times = []
    input_path.write_text(changed.model_dump_json(), encoding="utf-8")
    conflict = cli(
        "run",
        "--input",
        input_path,
        "--db",
        repo.path,
        "--mode",
        "mock",
        "--output",
        tmp_path / "conflict",
    )
    assert conflict.returncode == 2
    assert json.loads(conflict.stdout)["items"][0]["error"]["code"] == "INPUT_CONFLICT"


def test_packaged_fixtures_match_source():
    root = Path(__file__).resolve().parents[2]
    for path in (root / "data").glob("*.json"):
        assert path.read_bytes() == (root / "src/shadow_agent/fixtures" / path.name).read_bytes()


def test_nonexistent_database_is_explicit_error(tmp_path, job):
    path = tmp_path / "request.json"
    path.write_text(job.model_dump_json(), encoding="utf-8")
    result = cli(
        "run",
        "--input",
        path,
        "--db",
        tmp_path / "missing.db",
        "--mode",
        "mock",
        "--output",
        tmp_path / "output",
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["task_status"] == "failed"
