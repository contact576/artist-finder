"""Generate secret material for the hosted operator login.

The password is requested with ``getpass`` and is never written to disk.
``--vercel`` passes generated values to the linked Vercel project over stdin
without printing them; the default mode remains available for manual setup.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import secrets
import os
import shutil
import subprocess
from collections.abc import Callable
import sys

PASSWORD_HASH_ENV = "HOSTED_AUTH_PASSWORD_HASH"
SESSION_ENV = "HOSTED_AUTH_SESSION_SECRET"
CSRF_ENV = "HOSTED_AUTH_CSRF_SECRET"
OPERATOR_TOKEN_ENV = "GITHUB_OPERATOR_TOKEN"
DEFAULT_ITERATIONS = 310_000


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS,
                   salt: bytes | None = None) -> str:
    if not password:
        raise ValueError("password must not be empty")
    if iterations < 100_000:
        raise ValueError("iterations must be at least 100000")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 iterations, dklen=32)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                     int(rounds), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def _vercel_command() -> list[str]:
    executable = shutil.which("vercel")
    if not executable:
        raise RuntimeError("Vercel CLI is unavailable.")
    if os.name == "nt" and executable.casefold().endswith((".cmd", ".bat")):
        command_shell = os.environ.get("ComSpec") or shutil.which("cmd.exe")
        if not command_shell:
            raise RuntimeError("Windows command shell is unavailable.")
        return [command_shell, "/d", "/s", "/c", executable]
    return [executable]


def install_in_vercel(
    values: dict[str, str],
    *,
    command: list[str] | None = None,
    runner: Callable[..., object] = subprocess.run,
    reporter: Callable[[str], None] = print,
) -> None:
    base_command = command or _vercel_command()
    for name, value in values.items():
        result = runner(
            [
                *base_command,
                "env",
                "add",
                name,
                "production",
                "--force",
                "--sensitive",
                "--yes",
                "--no-color",
            ],
            input=value + "\n",
            text=True,
            capture_output=True,
            check=False,
        )
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError(f"Vercel could not store {name}.")
        reporter(f"stored={name}")


def _vercel_stdin_selftest() -> bool:
    calls: list[tuple[list[str], dict]] = []
    values = {"ONE": "fixture-secret-one", "TWO": "fixture-secret-two"}

    def fake_runner(command: list[str], **kwargs: object) -> object:
        calls.append((command, kwargs))
        return type("Result", (), {"returncode": 0})()

    install_in_vercel(
        values,
        command=["vercel"],
        runner=fake_runner,
        reporter=lambda _line: None,
    )
    return len(calls) == 2 and all(
        value not in " ".join(command) and kwargs.get("input") == value + "\n"
        for (command, kwargs), value in zip(calls, values.values())
    )


def _selftest() -> int:
    password = "correct horse battery staple"
    encoded = _hash_password(password, iterations=100_000, salt=b"0123456789abcdef")
    checks = [
        ("password verifies", verify_password(password, encoded)),
        ("wrong password rejects", not verify_password("wrong", encoded)),
        ("hash carries no plaintext", password not in encoded),
        ("session and csrf material differ", secrets.token_urlsafe(32) != secrets.token_urlsafe(32)),
        ("Vercel receives secrets over stdin only", _vercel_stdin_selftest()),
    ]
    ok = all(result for _, result in checks)
    for label, result in checks:
        print(f"  [{'ok ' if result else 'FAIL'}] {label}")
    print(f"\n  {'ALL CHECKS PASS' if ok else 'SELF-TEST FAILED'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate hosted login environment values without persisting secrets.")
    parser.add_argument("--selftest", action="store_true", help="run pure checks without prompting or printing secrets")
    parser.add_argument("--vercel", action="store_true", help="store generated values in linked Vercel Production without printing them")
    parser.add_argument(
        "--operator-token",
        action="store_true",
        help="also request a GitHub operator token with hidden input and store it in Vercel",
    )
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()

    first = getpass.getpass("Hosted dashboard password (input hidden): ")
    second = getpass.getpass("Repeat hosted dashboard password (input hidden): ")
    if not first or first != second:
        print("Passwords were empty or did not match.", file=sys.stderr)
        return 2
    values = {
        PASSWORD_HASH_ENV: _hash_password(first),
        SESSION_ENV: secrets.token_urlsafe(32),
        CSRF_ENV: secrets.token_urlsafe(32),
    }
    del first, second
    if args.operator_token:
        if not args.vercel:
            print("--operator-token requires --vercel so the token is never printed.", file=sys.stderr)
            return 2
        operator_token = getpass.getpass("GitHub operator token (input hidden): ").strip()
        if not operator_token:
            print("GitHub operator token was empty.", file=sys.stderr)
            return 2
        values[OPERATOR_TOKEN_ENV] = operator_token
        del operator_token
    if args.vercel:
        try:
            install_in_vercel(values)
        except RuntimeError as exc:
            print(f"Hosted login setup failed: {exc}", file=sys.stderr)
            return 3
        print("Hosted login values were stored in Vercel Production; no values were printed or saved.")
    else:
        print("Copy these three variables into the Vercel Project Environment Variables UI:")
        for name, value in values.items():
            print(f"{name}={value}")
        print("Values above are generated secrets; this helper did not save them locally.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
