"""Allowlist and stage only non-secret monthly refresh artifacts."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import PurePosixPath

ALLOWED_EXACT = {
    "data/artist_roster.json",
    "data/artist_candidates.json",
    "data/demand.json",
}
MEASUREMENT_PREFIX = "data/search_measurements/"


def is_allowed(path: str) -> bool:
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts:
        return False
    if normalized in ALLOWED_EXACT:
        return True
    return (
        normalized.startswith(MEASUREMENT_PREFIX)
        and normalized.endswith(".json")
        and len(pure.parts) == 3
    )


def _git_paths(*arguments: str) -> set[str]:
    raw = subprocess.check_output(["git", *arguments, "-z"])
    return {
        item.decode("utf-8", errors="strict")
        for item in raw.split(b"\0")
        if item
    }


def changed_paths() -> set[str]:
    paths = set()
    paths.update(_git_paths("diff", "--name-only", "--no-renames"))
    paths.update(_git_paths("diff", "--cached", "--name-only", "--no-renames"))
    paths.update(_git_paths("ls-files", "--others", "--exclude-standard"))
    return paths


def validate_and_stage() -> int:
    paths = changed_paths()
    unexpected = sorted(path for path in paths if not is_allowed(path))
    if unexpected:
        print("unexpected refresh paths:", file=__import__("sys").stderr)
        for path in unexpected:
            print(f"  {path}", file=__import__("sys").stderr)
        return 2
    subprocess.run(
        [
            "git", "add", "--",
            "data/artist_roster.json",
            "data/artist_candidates.json",
            "data/demand.json",
            "data/search_measurements",
        ],
        check=True,
    )
    staged = _git_paths("diff", "--cached", "--name-only", "--no-renames")
    unsafe_staged = sorted(path for path in staged if not is_allowed(path))
    if unsafe_staged:
        print("unsafe staged refresh paths", file=__import__("sys").stderr)
        return 3
    print(f"staged_refresh_paths={len(staged)}")
    return 0


def _selftest() -> int:
    checks = [
        ("roster allowed", is_allowed("data/artist_roster.json")),
        ("candidate inbox allowed", is_allowed("data/artist_candidates.json")),
        ("demand allowed", is_allowed("data/demand.json")),
        ("immutable manifest allowed", is_allowed("data/search_measurements/search_measurement_20260822.json")),
        ("config rejected", not is_allowed("data/config.json")),
        ("README rejected", not is_allowed("data/search_measurements/README.md")),
        ("raw data rejected", not is_allowed("data/raw/2026-08-22.json")),
        ("traversal rejected", not is_allowed("data/search_measurements/../config.json")),
    ]
    ok = all(result for _, result in checks)
    for label, result in checks:
        print(f"  [{'ok ' if result else 'FAIL'}] {label}")
    print(f"\n  {'ALL CHECKS PASS' if ok else 'SELF-TEST FAILED'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return _selftest()
    if args.stage:
        return validate_and_stage()
    parser.error("choose --stage or --self-test")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
