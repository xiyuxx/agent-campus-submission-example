"""Run offline counterexamples and preserve actual pytest output; never read credentials."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from shadow_agent.service import source_metadata


def archive_text(path: Path, content: str):
    """Normalize whitespace only; leave actual test messages and outcomes intact."""
    path.write_text(
        "\n".join(line.rstrip() for line in content.splitlines()) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--all-tests", action="store_true")
    parser.add_argument(
        "--test-path", type=Path, default=Path("tests/integration/test_adversarial_workflows.py")
    )
    parser.add_argument("--runxfail", action="store_true", help="Expose known defects as failures")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(UTC)
    temporary = root / ".tmp" / ("adversarial-" + started.strftime("%Y%m%dT%H%M%S%f"))
    test_file = (root / args.test_path).resolve()
    if not test_file.is_relative_to(root / "tests") or not test_file.is_file():
        parser.error("--test-path must name an existing file within tests/")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests" if args.all_tests else str(test_file.relative_to(root)),
        "-q",
        "--tb=short",
        f"--basetemp={temporary}",
        f"--junitxml={output / 'junit.xml'}",
    ]
    if args.runxfail:
        command.append("--runxfail")
    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        check=False,
        timeout=180,
    )
    archive_text(output / "pytest.txt", result.stdout + result.stderr)
    for name, prefix in (
        ("batch", "test_catalog_change_during_*"),
        ("refresh", "test_removed_blocking_rule_*"),
        ("rule-refresh", "test_running_agent_*"),
        ("provider-recovery", "test_one_malformed_provider_*"),
    ):
        for artifact in temporary.glob(f"{prefix}/{name}"):
            shutil.copytree(artifact, output / "scenarios" / name)
    for artifact in temporary.glob("test_automatic_options_service*/automatic-options"):
        shutil.copytree(artifact, output / "scenarios" / artifact.parent.name)
    for artifact in (output / "scenarios").rglob("*"):
        if artifact.is_file() and artifact.suffix in {".json", ".jsonl", ".md", ".txt"}:
            artifact.write_text(
                artifact.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
            )
    cases = []
    if (output / "junit.xml").exists():
        archive_text(output / "junit.xml", (output / "junit.xml").read_text(encoding="utf-8"))
        for node in ET.parse(output / "junit.xml").getroot().iter("testcase"):
            outcome = next(
                (x.tag for x in node if x.tag in {"failure", "error", "skipped"}), "passed"
            )
            cases.append({"test": node.attrib["name"], "outcome": outcome})
    metadata = {
        **source_metadata(),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "mode": "offline-controlled-model",
        "real_model_calls": 0,
        "python": sys.version,
        "command": command,
        "test_sha256": hashlib.sha256(test_file.read_bytes()).hexdigest(),
        "test_sources_sha256": {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / "tests").rglob("*.py"))
        },
        "exit_code": result.returncode,
        "counts": {
            status: sum(x["outcome"] == status for x in cases)
            for status in ("passed", "failure", "error", "skipped")
        },
        "cases": cases,
    }
    (output / "run.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {"output": str(output), "exit_code": result.returncode, "counts": metadata["counts"]},
            ensure_ascii=False,
        )
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
