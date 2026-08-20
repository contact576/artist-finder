"""Serve a generated Artist Finder dashboard on loopback only.

It intentionally binds to 127.0.0.1, disables directory listings, and rejects
paths that resolve outside the generated dashboard directory.
"""
from __future__ import annotations

import argparse
import functools
import os
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
DEFAULT_DIRECTORY = BASE / 'out' / 'dashboard'


def make_handler(root: Path):
    root = root.resolve()

    class LocalDashboardHandler(SimpleHTTPRequestHandler):
        def _candidate(self) -> Path | None:
            request_path = unquote(urlsplit(self.path).path)
            parts = PurePosixPath(request_path).parts
            if any(part in ('..', '') for part in parts if part not in ('/', '.')):
                return None
            candidate = root.joinpath(*(part for part in parts if part not in ('/', '.'))).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return None
            return candidate

        def do_GET(self):  # noqa: N802 - stdlib handler interface
            if self._candidate() is None:
                self.send_error(HTTPStatus.FORBIDDEN, 'Path outside dashboard directory')
                return
            super().do_GET()

        def do_HEAD(self):  # noqa: N802 - stdlib handler interface
            if self._candidate() is None:
                self.send_error(HTTPStatus.FORBIDDEN, 'Path outside dashboard directory')
                return
            super().do_HEAD()

        def translate_path(self, path: str) -> str:
            candidate = self._candidate()
            return str(candidate if candidate is not None else root / '__forbidden__')

        def list_directory(self, path: str):
            self.send_error(HTTPStatus.FORBIDDEN, 'Directory listing is disabled')
            return None

        def log_message(self, format: str, *args):
            print(f'  {self.address_string()} - {format % args}')

    return LocalDashboardHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Serve Artist Finder dashboard locally.')
    parser.add_argument('--directory', default=str(DEFAULT_DIRECTORY),
                        help='generated dashboard directory (default: out/dashboard)')
    parser.add_argument('--port', type=int, default=8765, help='loopback port (default: 8765)')
    parser.add_argument('--no-browser', action='store_true', help='print the URL without opening a browser')
    args = parser.parse_args(argv)
    root = Path(args.directory).resolve()
    index = root / 'index.html'
    if not index.is_file():
        parser.error(f'No generated dashboard at {root}. Run tools/build_dashboard.py --mode live first.')
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    handler = make_handler(root)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'Artist Finder dashboard: {url}')
    print(f'Serving local files only from: {root}')
    if not args.no_browser:
        threading.Timer(.15, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nDashboard server stopped.')
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
