"""Validate the first line of a local Git commit message."""

from pathlib import Path
import re
import sys


TITLE = re.compile(
    r"^(feat|fix|docs|test|refactor|perf|build|ci|chore|revert)"
    r"(?:\([a-z0-9][a-z0-9_-]*\))?!?: \S.*$"
)


def valid_title(title: str) -> bool:
    return len(title) <= 72 and bool(TITLE.fullmatch(title))


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: check_commit_message.py MESSAGE_FILE", file=sys.stderr)
        return 1
    lines = Path(sys.argv[1]).read_text(encoding="utf-8-sig").splitlines()
    if not lines or not valid_title(lines[0]):
        print("Commit title must use type(scope): description and be <= 72 characters.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
