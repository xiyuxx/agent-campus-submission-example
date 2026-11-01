"""Reject obvious credentials in staged files without printing secret values."""

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

TOKEN = re.compile(
    rb"(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})"
)
SECRET_NAME = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|GITHUB_FGT)", re.IGNORECASE)


def blocked_path(name: str) -> bool:
    path = PurePosixPath(name)
    base = path.name.lower()
    return (
        (base == ".env" or base.startswith(".env."))
        and base != ".env.example"
        or path.suffix.lower() in {".pem", ".key", ".db", ".sqlite", ".sqlite3"}
        or "runtime" in path.parts
    )


def local_secrets() -> list[bytes]:
    path = Path(".env")
    if not path.is_file():
        return []
    values = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        value = value.strip().strip("\"'")
        if SECRET_NAME.search(name) and len(value) >= 12:
            values.append(value.encode("utf-8"))
    return values


def contains_secret(content: bytes, secrets: list[bytes]) -> bool:
    return bool(TOKEN.search(content)) or any(value in content for value in secrets)


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def main() -> int:
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z").split(b"\0")
    secrets = local_secrets()
    failed = False
    for raw_name in filter(None, names):
        name = raw_name.decode("utf-8")
        if blocked_path(name):
            print(f"Blocked staged local/credential file: {name}", file=sys.stderr)
            failed = True
            continue
        content = git("show", f":{name}")
        if contains_secret(content, secrets):
            print(f"Possible credential in staged file: {name}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
