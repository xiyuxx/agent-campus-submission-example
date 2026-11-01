"""Run every offline test module in isolated processes; retain all results, no retries/skips."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic
from xml.etree import ElementTree


def verify(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    modules = sorted(Path("tests").rglob("test_*.py"))
    records = []
    for index, module in enumerate(modules):
        label = f"{index:02d}-{module.stem}"
        junit = output / f"{label}.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-q",
            "--tb=short",
            "--basetemp",
            str(Path(".tmp") / (output.name + "-" + label)),
            "--junitxml",
            str(junit),
        ]
        started = monotonic()
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=180,
        )
        for stream in ("stdout", "stderr"):
            (output / f"{label}-{stream}.txt").write_text(
                getattr(process, stream), encoding="utf-8", newline="\n"
            )
        counts = {}
        if junit.exists():
            suites = list(ElementTree.parse(junit).getroot().iter("testsuite"))
            counts = {
                key: sum(int(suite.attrib.get(key, 0)) for suite in suites)
                for key in ("tests", "failures", "errors", "skipped")
            }
        records.append(
            {
                "module": module.as_posix(),
                "exit_code": process.returncode,
                "seconds": round(monotonic() - started, 3),
                "counts": counts,
                "junit": junit.name,
            }
        )
        print(json.dumps(records[-1]), flush=True)
    report = {
        "method": "one pytest process per module; no retry, skip or xfail override",
        "modules": records,
        "passed": all(row["exit_code"] == 0 for row in records),
        "tests": sum(row["counts"].get("tests", 0) for row in records),
    }
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    sys.exit(verify(parser.parse_args().output))
