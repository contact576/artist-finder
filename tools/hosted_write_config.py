"""Create the ephemeral Google Ads config used by the hosted monthly worker.

Only environment-variable names and the destination path may be printed. Secret values are
written with restrictive permissions and never logged.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "config.json"
ENV_TO_FIELD = {
    "GOOGLE_ADS_DEVELOPER_TOKEN": "developer_token",
    "GOOGLE_ADS_CLIENT_ID": "client_id",
    "GOOGLE_ADS_CLIENT_SECRET": "client_secret",
    "GOOGLE_ADS_REFRESH_TOKEN": "refresh_token",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "login_customer_id",
    "GOOGLE_ADS_CUSTOMER_ID": "customer_id",
}


def build_config(environment: Mapping[str, str]) -> dict:
    missing = [name for name in ENV_TO_FIELD if not str(environment.get(name, "")).strip()]
    if missing:
        raise ValueError("missing required environment variables: " + ", ".join(missing))
    google_ads = {
        field: str(environment[name]).strip()
        for name, field in ENV_TO_FIELD.items()
    }
    google_ads["api_version"] = (
        str(environment.get("GOOGLE_ADS_API_VERSION", "")).strip() or "v25"
    )
    return {
        "google_ads": google_ads,
        "bandsintown_app_id": "",
        "apify_token": "",
    }


def write_config(path: Path, config: dict) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.remove(temporary)


def _selftest() -> int:
    fake = {
        name: f"fixture-{index}"
        for index, name in enumerate(ENV_TO_FIELD, start=1)
    }
    fake["GOOGLE_ADS_API_VERSION"] = "v25"
    with tempfile.TemporaryDirectory(prefix="artist-hosted-config-") as directory:
        path = Path(directory) / "config.json"
        config = build_config(fake)
        write_config(path, config)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        checks = [
            ("all credential fields mapped", set(loaded["google_ads"]) == set(ENV_TO_FIELD.values()) | {"api_version"}),
            ("API version retained", loaded["google_ads"]["api_version"] == "v25"),
            ("non-Ads credentials absent", loaded["bandsintown_app_id"] == "" and loaded["apify_token"] == ""),
            ("missing secret rejected", _missing_rejected(fake)),
            ("temporary file removed", not list(path.parent.glob("*.tmp"))),
        ]
    ok = all(result for _, result in checks)
    for label, result in checks:
        print(f"  [{'ok ' if result else 'FAIL'}] {label}")
    print(f"\n  {'ALL CHECKS PASS' if ok else 'SELF-TEST FAILED'}")
    return 0 if ok else 1


def _missing_rejected(fake: Mapping[str, str]) -> bool:
    incomplete = dict(fake)
    incomplete.pop("GOOGLE_ADS_REFRESH_TOKEN", None)
    try:
        build_config(incomplete)
    except ValueError as exc:
        return "GOOGLE_ADS_REFRESH_TOKEN" in str(exc)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return _selftest()
    try:
        path = write_config(args.output, build_config(os.environ))
    except (OSError, ValueError) as exc:
        print(f"configuration_error={exc}", file=os.sys.stderr)
        return 2
    print(f"config_ready={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
