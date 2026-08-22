"""Private Vercel handler for Artist Search Intelligence.

This module intentionally uses only the Python standard library.  The deployed
function is a *control plane*: GitHub remains the durable source of the roster
and GitHub Actions remains the durable monthly worker.

Required environment variables (values are never returned or logged):
  HOSTED_AUTH_PASSWORD_HASH  pbkdf2_sha256$<iterations>$<salt-b64url>$<digest-b64url>
  HOSTED_AUTH_SESSION_SECRET high-entropy HMAC signing secret
  HOSTED_AUTH_CSRF_SECRET    separate high-entropy CSRF derivation secret
  GITHUB_OPERATOR_TOKEN    GitHub token limited to this private repository
  GITHUB_REPOSITORY        owner/repository
  GITHUB_DATA_REF          branch or immutable ref used for roster writes

The password digest is ``hashlib.pbkdf2_hmac('sha256', password, salt,
iterations)``.  The digest and salt use unpadded URL-safe base64.  Session
cookies contain a signed JSON payload with an expiry and CSRF token; they are
HttpOnly, Secure, and SameSite=Lax.  They are not encrypted, so the payload
contains no identity or secret.

Vercel's Python runtime discovers the exported ``handler`` class.  Running
``python api/index.py --selftest`` starts an isolated loopback fixture using a
fake GitHub adapter; it makes no network requests and writes no project files.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import uuid
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
SCOUT = BASE / "scout"
DASHBOARD_ROOT = BASE / "out" / "dashboard"
WORKFLOW_FILE = "artist-search-monthly.yml"
MAX_BODY_BYTES = 32_768
SESSION_SECONDS = 8 * 60 * 60
COOKIE_NAME = "artist_dashboard_session"
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/app.css": "app.css",
    "/dashboard-data.json": "dashboard-data.json",
    "/manifest.json": "manifest.json",
}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))
import roster  # noqa: E402


class SafeError(Exception):
    """An error whose message is safe to expose to an authenticated operator."""


class ConfigurationError(SafeError):
    pass


class GitHubError(SafeError):
    pass


class GitHubAuthorizationError(GitHubError):
    pass


class GitHubConflict(GitHubError):
    pass


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigurationError("Service configuration is unavailable.")
    return value


def _verify_password(password: Any) -> bool:
    """Validate a password against the documented PBKDF2 format without logging it."""
    if not isinstance(password, str) or len(password) > 1_024:
        return False
    try:
        algorithm, iterations, salt_text, digest_text = _required_env(
            "HOSTED_AUTH_PASSWORD_HASH"
        ).split("$", 3)
        count = int(iterations)
        if algorithm != "pbkdf2_sha256" or count < 100_000 or count > 10_000_000:
            return False
        expected = _b64_decode(digest_text)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _b64_decode(salt_text), count
        )
        return hmac.compare_digest(actual, expected)
    except (ConfigurationError, ValueError, TypeError, UnicodeError):
        return False


def _csrf_for_sid(sid: str) -> str:
    secret = _required_env("HOSTED_AUTH_CSRF_SECRET").encode("utf-8")
    return _b64_encode(hmac.new(secret, sid.encode("ascii"), hashlib.sha256).digest())


def _make_session() -> tuple[str, dict[str, Any]]:
    secret = _required_env("HOSTED_AUTH_SESSION_SECRET").encode("utf-8")
    sid = secrets.token_urlsafe(18)
    payload = {
        "v": 1,
        "exp": int(time.time()) + SESSION_SECONDS,
        "sid": sid,
        "csrf": _csrf_for_sid(sid),
    }
    encoded = _b64_encode(_json_bytes(payload))
    signature = _b64_encode(hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}", payload


def _read_session(cookie_header: str | None) -> dict[str, Any] | None:
    if not cookie_header:
        return None
    try:
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        raw = cookie.get(COOKIE_NAME)
        if raw is None or "." not in raw.value:
            return None
        encoded, supplied = raw.value.rsplit(".", 1)
        secret = _required_env("HOSTED_AUTH_SESSION_SECRET").encode("utf-8")
        expected = _b64_encode(hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, supplied):
            return None
        payload = json.loads(_b64_decode(encoded).decode("utf-8"))
        if (not isinstance(payload, dict) or payload.get("v") != 1
                or not isinstance(payload.get("sid"), str)
                or not isinstance(payload.get("csrf"), str)
                or int(payload.get("exp", 0)) <= int(time.time())):
            return None
        if not hmac.compare_digest(payload["csrf"], _csrf_for_sid(payload["sid"])):
            return None
        return payload
    except (ConfigurationError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return None


class GitHubAdapter:
    """Minimal GitHub Contents/Actions client; every network error is redacted."""

    api_root = "https://api.github.com"

    def __init__(self) -> None:
        self.token = _required_env("GITHUB_OPERATOR_TOKEN")
        self.repository = _required_env("GITHUB_REPOSITORY")
        self.ref = _required_env("GITHUB_DATA_REF")
        if "/" not in self.repository or any(ch.isspace() for ch in self.repository):
            raise ConfigurationError("Service configuration is unavailable.")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 operation: str = "GitHub request") -> Any:
        data = _json_bytes(payload) if payload is not None else None
        request = Request(
            self.api_root + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "artist-search-intelligence-vercel",
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=20) as response:  # nosec - fixed GitHub API host
                body = response.read()
                return json.loads(body.decode("utf-8")) if body else None
        except HTTPError as exc:
            if exc.code in (409, 422):
                raise GitHubConflict("Roster changed remotely; please retry.") from None
            if exc.code in (401, 403):
                raise GitHubAuthorizationError(f"{operation} is not authorized.") from None
            if exc.code == 404:
                raise GitHubError(f"{operation} was not found.") from None
            raise GitHubError(f"{operation} failed.") from None
        except (URLError, TimeoutError, OSError, ValueError, UnicodeError):
            raise GitHubError(f"{operation} failed.") from None

    def get_roster(self) -> tuple[dict[str, Any], str]:
        path = f"/repos/{self.repository}/contents/data/artist_roster.json?ref={quote(self.ref, safe='')}"
        result = self._request("GET", path, operation="Roster read")
        try:
            sha = str(result["sha"])
            encoding = str(result.get("encoding") or "")
            content = result.get("content")
            # GitHub's Contents API returns encoding="none" and omits content for
            # files larger than 1 MB. The blob endpoint returns the same blob by
            # SHA with base64 content and remains safe for the allowlisted file.
            if encoding != "base64" or not isinstance(content, str) or not content.strip():
                blob_path = f"/repos/{self.repository}/git/blobs/{quote(sha, safe='')}"
                blob = self._request("GET", blob_path, operation="Roster blob read")
                if not isinstance(blob, dict) or blob.get("encoding") != "base64":
                    raise ValueError()
                content = blob.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
            data = json.loads(base64.b64decode("".join(content.split()), validate=True).decode("utf-8"))
            if not isinstance(data, dict) or not sha:
                raise ValueError()
            roster.validate(data)
            return data, sha
        except (KeyError, ValueError, TypeError, UnicodeError, json.JSONDecodeError, binascii.Error):
            raise GitHubError("The remote roster is invalid.") from None

    def commit_roster(self, data: dict[str, Any], sha: str, message: str) -> str:
        roster.validate(data)
        body = {
            "message": message,
            "content": base64.b64encode(
                (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            ).decode("ascii"),
            "sha": sha,
            "branch": self.ref,
        }
        result = self._request(
            "PUT", f"/repos/{self.repository}/contents/data/artist_roster.json", body,
            operation="Roster update"
        )
        try:
            return str(result["content"]["sha"])
        except (KeyError, TypeError):
            raise GitHubError("GitHub did not confirm the roster update.") from None

    def dispatch_monthly(self, job_id: str) -> None:
        self._request(
            "POST",
            f"/repos/{self.repository}/dispatches",
            {"event_type": "artist-search-refresh", "client_payload": {"job_id": job_id}},
            operation="Monthly refresh dispatch",
        )

    def refresh_status(self, job_id: str) -> dict[str, Any]:
        # repository_dispatch uses Contents write, the same permission required
        # for roster edits. Actions read is optional: if it is absent, the UI
        # watches for a newly generated dashboard instead of reporting failure.
        path = (f"/repos/{self.repository}/actions/workflows/{quote(WORKFLOW_FILE, safe='')}/runs"
                f"?event=repository_dispatch&per_page=100&branch={quote(self.ref, safe='')}")
        try:
            result = self._request("GET", path, operation="Monthly refresh status")
        except GitHubAuthorizationError:
            return {"job_id": job_id, "state": "started",
                    "message": "Refresh started. Waiting for the updated dashboard."}
        runs = result.get("workflow_runs", []) if isinstance(result, dict) else []
        run = next((row for row in runs if isinstance(row, dict) and row.get("display_title") == job_id), None)
        if not run:
            return {"job_id": job_id, "state": "queued", "message": "Refresh queued."}
        status = str(run.get("status") or "queued")
        conclusion = run.get("conclusion")
        if status != "completed":
            state = "running" if status == "in_progress" else "queued"
        elif conclusion == "success":
            state = "success"
        else:
            state = "failed"
        return {
            "job_id": job_id,
            "state": state,
            "started_at": run.get("run_started_at"),
            "finished_at": run.get("updated_at") if status == "completed" else None,
            "message": "Monthly refresh completed." if state == "success" else (
                "Monthly refresh failed." if state == "failed" else "Monthly refresh is running."
            ),
        }


class DashboardApp:
    def __init__(self, dashboard_root: Path = DASHBOARD_ROOT, adapter: Any | None = None) -> None:
        self.dashboard_root = dashboard_root.resolve()
        self.adapter = adapter

    def github(self) -> Any:
        return self.adapter if self.adapter is not None else GitHubAdapter()

    def dashboard_bytes(self) -> bytes:
        path = self.dashboard_root / "dashboard-data.json"
        try:
            return path.read_bytes()
        except OSError:
            raise SafeError("Dashboard data is unavailable.") from None

    def static_bytes(self, request_path: str) -> tuple[bytes, str]:
        name = STATIC_FILES.get(request_path)
        if not name:
            raise SafeError("Unknown dashboard path.")
        path = (self.dashboard_root / name).resolve()
        try:
            path.relative_to(self.dashboard_root)
            return path.read_bytes(), CONTENT_TYPES[path.suffix]
        except (OSError, ValueError, KeyError):
            raise SafeError("Dashboard artifact is unavailable.") from None

    def list_artists(self, query: str = "", status: str | None = None) -> list[dict[str, Any]]:
        data, _ = self.github().get_roster()
        value = query.casefold().strip()
        rows = list(data.get("artists", {}).values())
        return [row for row in rows if (not status or row.get("status") == status)
                and (not value or value in str(row.get("name", "")).casefold()
                     or value in str(row.get("slug", "")).casefold())]

    def mutate_roster(self, mutate: Any, message: str) -> dict[str, Any]:
        """Apply a pure roster mutation and retry one SHA conflict without exposing data."""
        last_error: Exception | None = None
        for _ in range(2):
            data, sha = self.github().get_roster()
            try:
                artist = mutate(data)
                roster.validate(data)
                self.github().commit_roster(data, sha, message)
                return artist
            except GitHubConflict as exc:
                last_error = exc
        raise last_error or GitHubConflict("Roster changed remotely; please retry.")


def make_handler(app: DashboardApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ArtistSearchPrivate/1.0"

        def log_message(self, _format: str, *_args: Any) -> None:
            # Request bodies can contain passwords.  Do not use default request logging.
            return

        def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Cache-Control", "private, no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, value: Any, headers: dict[str, str] | None = None) -> None:
            self._send(status, _json_bytes(value), "application/json; charset=utf-8", headers)

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": HTTPStatus(status).phrase.lower().replace(" ", "_"), "message": message})

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

        def _session(self) -> dict[str, Any] | None:
            return _read_session(self.headers.get("Cookie"))

        def _require_session(self) -> dict[str, Any] | None:
            session = self._session()
            if not session:
                self._error(HTTPStatus.UNAUTHORIZED, "Authentication required.")
                return None
            return session

        def _same_origin(self, session: dict[str, Any]) -> bool:
            origin = self.headers.get("Origin")
            host = self.headers.get("Host")
            proto = self.headers.get("X-Forwarded-Proto", "https").split(",", 1)[0].strip()
            expected = f"{proto}://{host}" if host else None
            supplied = self.headers.get("X-CSRF-Token", "")
            if not origin or not expected or origin != expected:
                self._error(HTTPStatus.FORBIDDEN, "Same-origin request required.")
                return False
            if not hmac.compare_digest(supplied, str(session.get("csrf", ""))):
                self._error(HTTPStatus.FORBIDDEN, "Invalid CSRF token.")
                return False
            return True

        def _mutation(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
            session = self._require_session()
            if not session or not self._same_origin(session):
                return None
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "application/json content type required.")
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > MAX_BODY_BYTES:
                    raise ValueError()
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError()
                return session, value
            except (ValueError, UnicodeError, json.JSONDecodeError):
                self._error(HTTPStatus.BAD_REQUEST, "Invalid JSON object.")
                return None

        def _serve_gzip_aware(self, raw: bytes, content_type: str) -> None:
            headers = {"Vary": "Accept-Encoding"}
            if "gzip" in self.headers.get("Accept-Encoding", "").lower():
                headers["Content-Encoding"] = "gzip"
                self._send(200, gzip.compress(raw, mtime=0), content_type, headers)
            elif len(raw) > 3_500_000:
                self._error(HTTPStatus.NOT_ACCEPTABLE, "gzip encoding is required for this response.")
            else:
                self._send(200, raw, content_type, headers)

        def _path(self) -> str:
            return unquote(urlsplit(self.path).path)

        def do_GET(self) -> None:
            path = self._path()
            if path == "/healthz":
                return self._json(200, {"ok": True})
            if path == "/login":
                if self._session():
                    return self._redirect("/")
                return self._send(200, LOGIN_PAGE, "text/html; charset=utf-8")
            session = self._session()
            if not session:
                if path.startswith("/api/"):
                    return self._error(HTTPStatus.UNAUTHORIZED, "Authentication required.")
                return self._redirect("/login")
            try:
                if path == "/api/dashboard":
                    return self._serve_gzip_aware(app.dashboard_bytes(), "application/json; charset=utf-8")
                if path == "/api/dashboard/status":
                    job_id = (parse_qs(urlsplit(self.path).query).get("job_id") or [None])[0]
                    refresh = app.github().refresh_status(job_id) if job_id else {"state": "idle"}
                    return self._json(200, {"refresh": refresh})
                if path == "/api/v1/meta":
                    return self._json(200, {"api_version": 1, "csrf_token": session["csrf"],
                                            "statuses": roster.STATUSES, "niches": roster.load_niches(),
                                            "refresh": {"state": "idle"}})
                if path == "/api/v1/artists":
                    query = parse_qs(urlsplit(self.path).query)
                    return self._json(200, {"artists": app.list_artists(
                        (query.get("q") or [""])[0], (query.get("status") or [None])[0]),
                        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                if path.startswith("/api/refresh/"):
                    job_id = path.rsplit("/", 1)[-1]
                    if not job_id or len(job_id) > 128:
                        return self._error(HTTPStatus.NOT_FOUND, "Unknown refresh job.")
                    return self._json(200, app.github().refresh_status(job_id))
                if path.startswith("/api/"):
                    return self._error(HTTPStatus.NOT_FOUND, "Unknown API endpoint.")
                # PurePosixPath guards the intent explicitly even though serving is allowlisted.
                if any(part == ".." for part in PurePosixPath(path).parts):
                    return self._error(HTTPStatus.FORBIDDEN, "Path outside dashboard directory.")
                raw, content_type = app.static_bytes(path)
                return self._serve_gzip_aware(raw, content_type)
            except SafeError as exc:
                return self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))

        def do_HEAD(self) -> None:
            return self.do_GET()

        def do_POST(self) -> None:
            path = self._path()
            if path == "/api/session":
                return self._login()
            mutation = self._mutation()
            if mutation is None:
                return
            _session, body = mutation
            try:
                if path == "/api/refresh":
                    job_id = uuid.uuid4().hex
                    app.github().dispatch_monthly(job_id)
                    return self._json(HTTPStatus.ACCEPTED, {"job_id": job_id, "state": "queued",
                                                            "message": "Monthly refresh queued."})
                if path == "/api/artists":
                    def add_artist(data: dict[str, Any]) -> dict[str, Any]:
                        artist = roster.operator_add_artist(
                            body.get("name"), body.get("category"), body.get("measurement_keyword"),
                            body.get("evidence_url"), data=data, save_now=False)
                        if body.get("aliases"):
                            artist = roster.operator_edit_artist(
                                artist["slug"], aliases=body["aliases"], data=data, save_now=False)
                        return artist

                    artist = app.mutate_roster(
                        add_artist,
                        "artist roster: operator add",
                    )
                    return self._json(HTTPStatus.CREATED, {"artist": artist,
                                                            "generated_at": _now_utc()})
                parts = path.split("/")
                if len(parts) == 5 and parts[:3] == ["", "api", "artists"]:
                    slug, action = parts[3], parts[4]
                    if action == "category":
                        artist = app.mutate_roster(
                            lambda data: roster.operator_set_category(slug, body.get("category"), data=data, save_now=False),
                            "artist roster: category update")
                    elif action == "status":
                        artist = app.mutate_roster(
                            lambda data: roster.operator_set_status(slug, body.get("active"), data=data, save_now=False),
                            "artist roster: status update")
                    elif action == "keywords":
                        artist = app.mutate_roster(
                            lambda data: roster.operator_update_keyword(
                                slug, body.get("keyword"), body.get("action"), body.get("previous_keyword"),
                                data=data, save_now=False),
                            "artist roster: keyword update")
                    else:
                        return self._error(HTTPStatus.NOT_FOUND, "Unknown API endpoint.")
                    return self._json(200, {"artist": artist, "generated_at": _now_utc()})
                return self._error(HTTPStatus.NOT_FOUND, "Unknown API endpoint.")
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except SafeError as exc:
                return self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))

        def do_PATCH(self) -> None:
            path = self._path()
            mutation = self._mutation()
            if mutation is None:
                return
            _session, body = mutation
            parts = path.split("/")
            if len(parts) != 4 or parts[:3] != ["", "api", "artists"] or not parts[3]:
                return self._error(HTTPStatus.NOT_FOUND, "Unknown API endpoint.")
            slug = parts[3]
            try:
                def update(data: dict[str, Any]) -> dict[str, Any]:
                    artist: dict[str, Any] | None = None
                    edit_fields = {key: body[key] for key in ("name", "aliases", "evidence_url", "evidence_note") if key in body}
                    if edit_fields:
                        artist = roster.operator_edit_artist(slug, data=data, save_now=False, **edit_fields)
                    if "category" in body:
                        artist = roster.operator_set_category(slug, body["category"], data=data, save_now=False)
                    if "active" in body:
                        artist = roster.operator_set_status(slug, body["active"], data=data, save_now=False)
                    if "keyword" in body:
                        artist = roster.operator_update_keyword(
                            slug, body["keyword"], body.get("action", "replace"), body.get("previous_keyword"),
                            data=data, save_now=False)
                    if artist is None:
                        raise ValueError("No supported artist fields were supplied")
                    return artist
                artist = app.mutate_roster(update, "artist roster: operator update")
                return self._json(200, {"artist": artist, "generated_at": _now_utc()})
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except SafeError as exc:
                return self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))

        def _login(self) -> None:
            try:
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "application/json content type required.")
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > MAX_BODY_BYTES:
                    raise ValueError()
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError()
            except (ValueError, UnicodeError, json.JSONDecodeError):
                return self._error(HTTPStatus.BAD_REQUEST, "Invalid JSON object.")
            if not _verify_password(value.get("password")):
                return self._error(HTTPStatus.UNAUTHORIZED, "Invalid credentials.")
            try:
                token, _payload = _make_session()
            except ConfigurationError as exc:
                return self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            cookie = (f"{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_SECONDS}; "
                      "HttpOnly; Secure; SameSite=Lax")
            return self._json(200, {"ok": True}, {"Set-Cookie": cookie})

    return Handler


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


LOGIN_PAGE = b"""<!doctype html><meta charset=utf-8><title>Artist Search Intelligence</title>
<form id=login><input type=text name=username autocomplete=username value=operator hidden><label>Dashboard password <input type=password name=password autocomplete=current-password required></label><button>Sign in</button><p id=error role=alert></p></form>
<script>document.querySelector('#login').addEventListener('submit',async e=>{e.preventDefault();const p=e.currentTarget.password.value,r=await fetch('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p})});if(r.ok)location='/';else document.querySelector('#error').textContent='Sign-in failed.'})</script>"""


# Vercel imports this class.  Construction deliberately does not read configuration/secrets.
class handler(make_handler(DashboardApp())):
    """Named Vercel entry point; the factory remains injectable for offline tests."""



class _FakeGitHub:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = json.loads(json.dumps(data))
        self.sha = "fixture-sha"
        self.commits = 0
        self.dispatched: list[str] = []

    def get_roster(self) -> tuple[dict[str, Any], str]:
        return json.loads(json.dumps(self.data)), self.sha

    def commit_roster(self, data: dict[str, Any], sha: str, _message: str) -> str:
        if sha != self.sha:
            raise GitHubConflict("fixture conflict")
        self.data = json.loads(json.dumps(data))
        self.commits += 1
        self.sha = f"fixture-{self.commits}"
        return self.sha

    def dispatch_monthly(self, job_id: str) -> None:
        self.dispatched.append(job_id)

    def refresh_status(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "state": "success", "message": "Fixture refresh completed."}


class _FakeLargeRosterAdapter(GitHubAdapter):
    """Exercise GitHub's encoding=none response without network or secrets."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.repository = "fixture/repository"
        self.ref = "main"
        self.encoded = base64.b64encode(_json_bytes(data)).decode("ascii")
        self.paths: list[str] = []
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 operation: str = "GitHub request") -> Any:
        self.paths.append(path)
        self.calls.append((method, path, payload))
        if "/contents/data/artist_roster.json" in path:
            return {"sha": "large-fixture-sha", "encoding": "none", "content": ""}
        if path.endswith("/git/blobs/large-fixture-sha"):
            return {"encoding": "base64", "content": self.encoded}
        if path.endswith("/dispatches"):
            return None
        if "/actions/workflows/" in path:
            raise GitHubAuthorizationError(f"{operation} is not authorized")
        raise AssertionError(f"unexpected fixture path for {operation}: {method} {path}")


def _password_hash(password: str) -> str:
    salt = b"artist-dashboard-selftest-salt"
    count = 100_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, count)
    return f"pbkdf2_sha256${count}${_b64_encode(salt)}${_b64_encode(digest)}"


def _selftest() -> int:
    """Offline HTTP checks: auth, CSRF, traversal, GitHub-style mutation, and gzip."""
    import http.client
    env_names = (
        "HOSTED_AUTH_PASSWORD_HASH",
        "HOSTED_AUTH_SESSION_SECRET",
        "HOSTED_AUTH_CSRF_SECRET",
    )
    original = {name: os.environ.get(name) for name in env_names}
    temp = Path(tempfile.mkdtemp(prefix="artist-dashboard-api-"))
    server: ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None
    try:
        os.environ["HOSTED_AUTH_PASSWORD_HASH"] = _password_hash("correct horse battery staple")
        os.environ["HOSTED_AUTH_SESSION_SECRET"] = "selftest-session-secret-with-sufficient-length"
        os.environ["HOSTED_AUTH_CSRF_SECRET"] = "selftest-csrf-secret-with-sufficient-length"
        large_adapter = _FakeLargeRosterAdapter(roster.empty_registry())
        large_adapter.dispatch_monthly("fixture-job")
        fallback_status = large_adapter.refresh_status("fixture-job")
        large_roster, large_sha = large_adapter.get_roster()
        for name, content in {"index.html": b"fixture", "app.js": b"// fixture", "app.css": b"", "dashboard-data.json": b'{"fixture":true}', "manifest.json": b"{}"}.items():
            (temp / name).write_bytes(content)
        fake = _FakeGitHub(roster.empty_registry())
        app = DashboardApp(temp, fake)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port

        def request(method: str, path: str, payload: dict[str, Any] | None = None,
                    cookie: str | None = None, csrf: str | None = None, origin: bool = True,
                    gzip_ok: bool = False) -> tuple[int, dict[str, str], bytes]:
            headers: dict[str, str] = {"Host": f"127.0.0.1:{port}"}
            raw = _json_bytes(payload) if payload is not None else None
            if raw is not None:
                headers["Content-Type"] = "application/json"
            if cookie:
                headers["Cookie"] = cookie
            if csrf:
                headers["X-CSRF-Token"] = csrf
            if origin:
                headers["Origin"] = f"http://127.0.0.1:{port}"
                headers["X-Forwarded-Proto"] = "http"
            if gzip_ok:
                headers["Accept-Encoding"] = "gzip"
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request(method, path, raw, headers)
            response = connection.getresponse()
            body = response.read()
            result = response.status, {key.lower(): value for key, value in response.getheaders()}, body
            connection.close()
            return result

        health, _, _ = request("GET", "/healthz", origin=False)
        denied, denied_headers, _ = request("GET", "/")
        login_bad, _, _ = request("POST", "/api/session", {"password": "wrong"}, origin=False)
        login, headers, _ = request("POST", "/api/session", {"password": "correct horse battery staple"}, origin=False)
        cookie = headers.get("set-cookie", "").split(";", 1)[0]
        meta_status, _, meta_raw = request("GET", "/api/v1/meta", cookie=cookie)
        csrf = json.loads(meta_raw)["csrf_token"] if meta_status == 200 else ""
        traversal, _, _ = request("GET", "/%2e%2e/secret", cookie=cookie)
        csrf_denied, _, _ = request("POST", "/api/artists", {"name": "Fixture Artist", "category": "comedy"}, cookie=cookie)
        added, _, _ = request("POST", "/api/artists", {"name": "Fixture Artist", "aliases": ["Fixture Alias"], "category": "comedy", "measurement_keyword": "Fixture Artist"}, cookie=cookie, csrf=csrf)
        dashboard, dash_headers, dash_body = request("GET", "/api/dashboard", cookie=cookie, gzip_ok=True)
        refresh, _, _ = request("POST", "/api/refresh", {}, cookie=cookie, csrf=csrf)
        checks = [
            ("health reveals no data", health == 200),
            ("dashboard redirects to login", denied == 303 and denied_headers.get("location") == "/login"),
            ("invalid password rejected", login_bad == 401),
            ("password session set", login == 200 and "httponly" in headers.get("set-cookie", "").lower()),
            ("session meta returns CSRF", meta_status == 200 and bool(csrf)),
            ("traversal rejected", traversal == 403),
            ("CSRF required", csrf_denied == 403),
            ("large roster falls back to the immutable blob SHA",
             large_sha == "large-fixture-sha" and large_roster == roster.empty_registry()
             and any(path.endswith("/git/blobs/large-fixture-sha") for path in large_adapter.paths)),
            ("refresh dispatch uses the content-write repository event",
             ("POST", "/repos/fixture/repository/dispatches",
              {"event_type": "artist-search-refresh", "client_payload": {"job_id": "fixture-job"}}) in large_adapter.calls
             and fallback_status["state"] == "started"),
            ("GitHub-backed add commits once with aliases", added == 201 and fake.commits == 1
             and fake.data["artists"]["fixture-artist"]["aliases"] == ["Fixture Alias"]),
            ("dashboard gzip response", dashboard == 200 and dash_headers.get("content-encoding") == "gzip" and gzip.decompress(dash_body) == b'{"fixture":true}'),
            ("refresh dispatches workflow", refresh == 202 and len(fake.dispatched) == 1),
        ]
        ok = all(good for _, good in checks)
        for label, good in checks:
            print(f"  [{'ok ' if good else 'FAIL'}] {label}")
        print(f"\n  {'ALL CHECKS PASS' if ok else 'SELF-TEST FAILED'}")
        return 0 if ok else 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=2)
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        # Temporary fixture is the only filesystem write made by the test.
        import shutil
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(_selftest() if args.selftest else 0)
