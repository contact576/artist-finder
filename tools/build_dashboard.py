"""Build the private monthly Artist Search Intelligence dashboard.

This is a scheduled-task-safe, stdlib-only projection step.  It reads derived
scout data, writes only the chosen dashboard output directory, and never reads
``data/config.json`` or credentials.

Examples:
    python tools/build_dashboard.py --mode live
    python tools/build_dashboard.py --mode fixture
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
SCOUT = BASE / 'scout'
TEMPLATE = BASE / 'search_dashboard'
DEFAULT_LIVE = BASE / 'out' / 'dashboard'
DEFAULT_FIXTURE = BASE / 'out' / 'dashboard-fixture'

if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

import search_dashboard as dashboard  # noqa: E402


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _copy_asset(source: Path, destination: Path) -> None:
    with open(source, 'r', encoding='utf-8') as handle:
        _atomic_write(destination, handle.read())


def write_artifacts(payload: dict, output: Path) -> list[Path]:
    """Publish a complete generation with a restore-on-failure directory swap."""
    if not TEMPLATE.is_dir():
        raise FileNotFoundError(f'Dashboard template directory is missing: {TEMPLATE}')
    output = output.resolve()
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{output.name}.generation-', dir=parent))
    backup = parent / f'.{output.name}.previous'
    artifacts = []
    try:
        for name in ('index.html', 'app.css', 'app.js'):
            source = TEMPLATE / name
            if not source.is_file():
                raise FileNotFoundError(f'Dashboard template asset is missing: {source}')
            destination = staging / name
            _copy_asset(source, destination)
            artifacts.append(destination)
        data_path = staging / 'dashboard-data.json'
        _atomic_write(data_path, json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n')
        artifacts.append(data_path)
        manifest = dict(schema_version=1, generated_at=payload.get('generated_at'), mode=payload.get('mode'),
                        files=[path.name for path in artifacts])
        manifest_path = staging / 'manifest.json'
        _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        artifacts.append(manifest_path)
        expected = set(manifest['files']) | {'manifest.json'}
        if {item.name for item in artifacts} != expected or any(not item.is_file() for item in artifacts):
            raise OSError('Dashboard generation validation failed before publication')
        if backup.exists():
            shutil.rmtree(backup)
        had_output = output.exists()
        if had_output:
            os.replace(output, backup)
        try:
            os.replace(staging, output)
        except Exception:
            if had_output and backup.exists() and not output.exists():
                os.replace(backup, output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return [output / name for name in manifest['files']] + [output / 'manifest.json']
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists() and output.exists():
            shutil.rmtree(backup)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Generate the private Artist Finder static dashboard.')
    parser.add_argument('--mode', choices=('live', 'fixture'), default='live',
                        help='live reads the retained project evidence; fixture is explicitly demo-only')
    parser.add_argument('--fixture', action='store_true',
                        help='compatibility alias for --mode fixture')
    parser.add_argument('--output', help='optional local output directory')
    parser.add_argument('--selftest', action='store_true', help='run dashboard payload invariants only')
    args = parser.parse_args(argv)
    if args.fixture:
        args.mode = 'fixture'
    if args.selftest:
        return dashboard._selftest()
    output = Path(args.output).resolve() if args.output else (
        DEFAULT_FIXTURE if args.mode == 'fixture' else DEFAULT_LIVE)
    payload = dashboard.fixture_payload() if args.mode == 'fixture' else dashboard.build_payload()
    artifacts = write_artifacts(payload, output)
    print(f'  dashboard ({args.mode}) -> {output}')
    print(f'  {len(payload.get("artists") or [])} artist records · {len(artifacts)} generated artifacts')
    print('  local/private only — search estimates are not ticket sales; no credentials included')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
