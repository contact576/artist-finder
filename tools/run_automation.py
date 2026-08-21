"""Scheduled-safe weekly/monthly entrypoint with non-secret dashboard status."""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
SCOUT = os.path.join(BASE, 'scout')
OUT = os.path.join(BASE, 'out', 'automation')
LOCK = os.path.join(OUT, 'automation.lock')
STATUS = os.path.join(OUT, 'status.json')
CONFIG = os.path.join(BASE, 'data', 'config.json')
STALE_SECONDS = 12 * 60 * 60
STEP_TIMEOUT_SECONDS = 30 * 60
REDACTIONS = ((re.compile(r'(?i)(\b(?:authorization|bearer)\s+)[^\s,;]+'), r'\1[REDACTED]'),
              (re.compile(r'\b(?:apify_api_[A-Za-z0-9_-]+|1//[^\s,;]{12,}|GOCSPX-[A-Za-z0-9_-]+|AIza[A-Za-z0-9_-]{20,})\b'), '[REDACTED]'),
              (re.compile(r'(?i)(\b(?:developer[_ -]?token|refresh[_ -]?token|client[_ -]?secret|api[_ -]?key)\b\s*[:=]\s*)[^\s,;]+'), r'\1[REDACTED]'))


def now(): return dt.datetime.now().astimezone().isoformat(timespec='seconds')
def relative(path): return os.path.relpath(path, BASE).replace('\\', '/')
def redact(value):
    for pattern, replacement in REDACTIONS: value = pattern.sub(replacement, str(value))
    return value

def command(args): return subprocess.list2cmdline([str(x) for x in args])
def read_status():
    try:
        with open(STATUS, encoding='utf-8') as f: value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError): return {}
def write_status(value):
    os.makedirs(OUT, exist_ok=True); temporary = STATUS + '.tmp'
    with open(temporary, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, indent=2, sort_keys=True); f.write('\n')
    os.replace(temporary, STATUS)

def credential_summary():
    """Only configuration presence; no values, lengths, suffixes, or config data."""
    try:
        with open(CONFIG, encoding='utf-8') as f: config = json.load(f)
    except FileNotFoundError: return 'No local credential configuration recorded.'
    except (OSError, ValueError): return 'Local credential configuration is unreadable.'
    ads = config.get('google_ads') if isinstance(config, dict) else {}; ads = ads if isinstance(ads, dict) else {}
    google = 'configured' if all(ads.get(k) for k in ('developer_token', 'client_id', 'client_secret', 'refresh_token')) else 'incomplete'
    bms = 'configured' if config.get('apify_token') else 'not configured'
    return f'Google Ads credentials {google}; BookMyShow route {bms}.'

def next_run(mode, current=None):
    current = (current or dt.datetime.now()).replace(second=0, microsecond=0)
    if mode == 'weekly':
        result = (current + dt.timedelta((0 - current.weekday()) % 7)).replace(hour=9, minute=30)
        return (result if result > current else result + dt.timedelta(days=7)).isoformat(timespec='minutes')
    year, month = current.year, current.month; result = current.replace(day=3, hour=9, minute=40)
    if result <= current:
        month += 1
        if month == 13: year, month = year + 1, 1
        result = dt.datetime(year, month, 3, 9, 40)
    return result.isoformat(timespec='minutes')

def result_for(mode, steps):
    failed = [x for x in steps if isinstance(x.get('exit_code'), int) and x['exit_code']]
    if not failed: return 'success', 'Success', 0
    code = failed[0]['exit_code']; later_ok = all(x.get('exit_code') in (0, None) for x in steps[1:])
    if mode == 'weekly' and steps[0].get('name') == 'fetch' and later_ok: return 'partial_failure', f'Partial failure (fetch exit {code})', code
    return 'failed', f'Failed (exit {code})', code

class Tee:
    def __init__(self, path=None):
        self.file = None
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.file = open(path, 'a', encoding='utf-8')
    def write(self, line):
        safe = redact(line); print(safe, end='')
        if self.file:
            self.file.write(safe); self.file.flush()
    def close(self):
        if self.file:
            self.file.close()

def acquire_lock(mode):
    os.makedirs(OUT, exist_ok=True)
    try: fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - os.path.getmtime(LOCK) <= STALE_SECONDS: raise SystemExit(f'another automation run holds {LOCK}')
        os.remove(LOCK); fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(dict(pid=os.getpid(), mode=mode, started_at=now()), f)
def release_lock():
    try: os.remove(LOCK)
    except FileNotFoundError: pass

def run_step(name, args, tee, env, timeout_seconds):
    """Run one child with a finite scheduler-safe timeout and redacted output."""
    rendered = command(args); tee.write('\n$ ' + rendered + '\n'); started = now()
    try:
        proc = subprocess.Popen(args, cwd=BASE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        try:
            output, _ = proc.communicate(timeout=timeout_seconds)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate()
            tee.write(f'! {name} timed out after {timeout_seconds}s; child terminated.\n')
            code = 124
        if output: tee.write(output)
    except OSError as exc: tee.write(f'! could not start {name}: {exc}\n'); code = 127
    return dict(name=name, command=rendered, started_at=started, finished_at=now(), exit_code=code, timeout_seconds=timeout_seconds)
def planned_step(name, args, tee):
    rendered = command(args); tee.write('\n$ ' + rendered + '\n  dry-run: dashboard build skipped because it would write a generated artifact.\n')
    return dict(name=name, command=rendered, started_at=now(), finished_at=now(), exit_code=None, skipped=True)

def weekly(date, dry_run, tee, env, timeout_seconds):
    fetch = [sys.executable, os.path.join(HERE, 'fetch_listings.py'), '--date', date]
    scout = [sys.executable, os.path.join(SCOUT, 'run_weekly.py'), '--date', date, '--international', '--write-dossiers']
    if dry_run: fetch.append('--dry-run'); scout.extend(('--no-save', '--recompute'))
    dashboard = [sys.executable, os.path.join(HERE, 'build_dashboard.py'), '--mode', 'live']
    steps = [run_step('fetch', fetch, tee, env, timeout_seconds), run_step('scout', scout, tee, env, timeout_seconds)]
    steps.append(planned_step('dashboard', dashboard, tee) if dry_run else run_step('dashboard', dashboard, tee, env, timeout_seconds))
    return steps

def _skipped_step(name, args, reason):
    return dict(name=name, command=command(args), started_at=now(), finished_at=now(),
                exit_code=None, skipped=True, reason=reason)


def _fail_fast(specs, runner):
    """Run ordered monthly stages; explicitly skip every downstream stage after failure."""
    steps = []
    for index, (name, args) in enumerate(specs):
        step = runner(name, args)
        steps.append(step)
        if step.get('exit_code') not in (0, None):
            reason = f'skipped because upstream {name} failed (exit {step["exit_code"]})'
            steps.extend(_skipped_step(later_name, later_args, reason)
                         for later_name, later_args in specs[index + 1:])
            break
    return steps


def monthly(history_months, tee, env, timeout_seconds, dry_run=False):
    if dry_run:
        # Pure checks only: no roster/demand/dashboard artifact/status/log/lock write and no API call.
        specs = [('roster_selftest', [sys.executable, os.path.join(HERE, 'refresh_artist_roster.py'), '--self-test']),
                 ('demand_selftest', [sys.executable, os.path.join(HERE, 'fetch_search_volume.py'), '--self-test']),
                 ('dashboard_selftest', [sys.executable, os.path.join(HERE, 'build_dashboard.py'), '--selftest'])]
    else:
        specs = [('roster_discovery', [sys.executable, os.path.join(HERE, 'refresh_artist_roster.py'),
                                        '--seed', '--discover', '--limit-ideas', '200']),
                 ('demand', [sys.executable, os.path.join(HERE, 'fetch_search_volume.py'), '--all',
                              '--include-candidates', '--history-months', str(history_months)]),
                 ('dashboard', [sys.executable, os.path.join(HERE, 'build_dashboard.py'), '--mode', 'live'])]
    return _fail_fast(specs, lambda name, args: run_step(name, args, tee, env, timeout_seconds))

def selftest():
    checks = [('secrets are redacted', '[REDACTED]' in redact('Authorization Bearer apify_api_abcdefghijklmnopqrstuvwxyz') and 'abcdefghijklmnopqrstuvwxyz' not in redact('refresh_token=1//abcdefghijklmnopqrstuvwxyz')),
              ('weekly cadence is Monday', next_run('weekly', dt.datetime(2026, 8, 21, 10)).startswith('2026-08-24T09:30')),
              ('monthly cadence is day three', next_run('monthly', dt.datetime(2026, 8, 21, 10)).startswith('2026-09-03T09:40')),
              ('fetch-only failure is partial', result_for('weekly', [dict(name='fetch', exit_code=2), dict(name='scout', exit_code=0), dict(name='dashboard', exit_code=0)])[0] == 'partial_failure'),
              ('compute failure is failed', result_for('weekly', [dict(name='fetch', exit_code=0), dict(name='scout', exit_code=1), dict(name='dashboard', exit_code=0)])[0] == 'failed'),
              ('monthly failure explicitly skips downstream', all(step.get('skipped') for step in _fail_fast([('a', ['a']), ('b', ['b']), ('c', ['c'])], lambda name, args: dict(name=name, exit_code=(9 if name == 'a' else 0)))[1:]))]
    ok = True
    for label, good in checks: print(f'  [{"ok " if good else "FAIL"}] {label}'); ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}'); return 0 if ok else 1

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument('mode', choices=('weekly', 'monthly')); ap.add_argument('--date', default=dt.date.today().isoformat()); ap.add_argument('--dry-run', action='store_true', help='weekly preserves its existing dry-run; monthly runs pure self-checks and writes nothing at all'); ap.add_argument('--history-months', type=int, default=48); ap.add_argument('--step-timeout-seconds', type=int, default=STEP_TIMEOUT_SECONDS, help='per child command timeout; default 1800'); ap.add_argument('--self-test', action='store_true', help='run pure automation checks; writes nothing')
    a = ap.parse_args(argv)
    if a.self_test: return selftest()
    if a.step_timeout_seconds < 1: ap.error('--step-timeout-seconds must be positive')
    env = os.environ.copy(); env['PYTHONIOENCODING'] = 'utf-8'; env['PYTHONUTF8'] = '1'; env['PYTHONDONTWRITEBYTECODE'] = '1'
    if a.mode == 'monthly' and a.dry_run:
        tee = Tee()
        try:
            tee.write('Artist Scout monthly dry run: pure self-checks only; no status/log/lock/artifact write.\n')
            steps = monthly(a.history_months, tee, env, a.step_timeout_seconds, dry_run=True)
            state, result, code = result_for('monthly', steps)
            tee.write(f'completed {state} with exit code {code}; no persistent status was written.\n')
            return code
        finally:
            tee.close()
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S'); log = os.path.join(OUT, f'{a.mode}_{stamp}.log')
    acquire_lock(a.mode); tee = Tee(log); status = read_status(); job = dict(state='running', result='Running', started_at=now(), finished_at=None, exit_code=None, artifact_date=a.date, log_path=relative(log), next_run=next_run(a.mode), credentials_available=credential_summary(), dry_run=bool(a.dry_run), steps=[], note='Running from a local scheduled-compatible command path.')
    status[a.mode] = job; status['updated_at'] = now(); write_status(status)
    try:
        tee.write(f'Artist Scout automation: {a.mode} at {now()}\n'); steps = weekly(a.date, a.dry_run, tee, env, a.step_timeout_seconds) if a.mode == 'weekly' else monthly(a.history_months, tee, env, a.step_timeout_seconds); state, result, code = result_for(a.mode, steps)
        job.update(state=state, result=result, exit_code=code, finished_at=now(), steps=steps, dashboard_artifact=('out/dashboard/index.html' if not a.dry_run else None), note=('Dry run completed: isolated/no-save core commands; dashboard build skipped.' if a.dry_run else 'Nonzero means source/API/downstream failure; retained data was not erased.'))
        status[a.mode] = job; status['updated_at'] = now(); write_status(status); tee.write(f'\ncompleted with exit code {code}; log: {relative(log)}\n'); return code
    except BaseException as exc:
        job.update(state='failed', result='Failed (runner exception)', exit_code=1, finished_at=now(), note=f'Runner exception: {redact(type(exc).__name__)}'); status[a.mode] = job; status['updated_at'] = now(); write_status(status); tee.write(f'\nrunner exception: {redact(type(exc).__name__)}\n'); raise
    finally: release_lock(); tee.close()

if __name__ == '__main__': raise SystemExit(main())
