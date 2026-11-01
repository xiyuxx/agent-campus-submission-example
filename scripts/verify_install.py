"""Build current wheel, install locked dependencies into a fresh venv, verify CLI."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from shadow_agent.service import source_metadata


def verify(runtime: Path, output: Path):
    runtime.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    records = []

    def check(label, command, expected=0):
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=180,
        )
        (output / (label + ".txt")).write_text(
            result.stdout + result.stderr, encoding="utf-8", newline="\n"
        )
        records.append(
            {"check": label, "exit_code": result.returncode, "expected_exit_code": expected}
        )
        if result.returncode != expected:
            raise RuntimeError(f"{label} failed; see recorded log")
        return result.stdout

    report = {"source": source_metadata(), "checks": records}
    try:
        wheels = runtime / "wheels"
        check(
            "wheel",
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                ".",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(wheels),
            ],
        )
        check("venv", [sys.executable, "-m", "venv", str(runtime / "venv")])
        executable = runtime / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        check(
            "locked-dependencies",
            [str(executable), "-m", "pip", "install", "-r", "requirements.lock"],
        )
        wheel = next(wheels.glob("*.whl"))
        report["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        check("package", [str(executable), "-m", "pip", "install", "--no-deps", str(wheel)])
        check("pip-check", [str(executable), "-m", "pip", "check"])
        isolated = check(
            "isolated-import",
            [
                str(executable),
                "-I",
                "-c",
                "import json,shadow_agent; from shadow_agent.service import source_metadata; "
                "print(json.dumps(dict(path=shadow_agent.__file__,source=source_metadata())))",
            ],
        )
        installed = json.loads(isolated)
        assert Path(installed["path"]).resolve().is_relative_to((runtime / "venv").resolve())
        report["installed_source_sha256"] = installed["source"]["source_sha256"]
        assert report["installed_source_sha256"] == report["source"]["source_sha256"]
        check(
            "demo",
            [str(executable), "-I", "-m", "shadow_agent", "demo", "--root", str(runtime / "demo")],
            expected=4,
        )
        check(
            "work-list",
            [
                str(executable),
                "-I",
                "-m",
                "shadow_agent",
                "work",
                "--db",
                str(runtime / "demo/agent.db"),
                "--action",
                "list",
                "--as-of",
                "2026-11-01T00:00:00Z",
            ],
        )
        report["passed"] = True
    finally:
        (output / "verification.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"checks": len(records), "passed": report.get("passed", False)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    verify(args.runtime, args.output)
