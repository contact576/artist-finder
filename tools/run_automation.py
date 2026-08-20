"""Unattended entrypoint for the scout's two cadences.

Use this file from Windows Task Scheduler, cron, or any other scheduler. It deliberately uses
the current Python interpreter and absolute paths derived from this file, so the task does not
depend on a working directory or a global `python` command.

    python run_automation.py weekly
    python run_automation.py monthly
    python run_automation.py weekly --dry-run

Weekly fetch failures do not erase history or prevent the compute pass. They are logged and the
run exits non-zero, while the scout still rebuilds from whatever sources succeeded. A lock stops
overlapping scheduled runs from corrupting a same-day retry.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
SCOUT = os.path.join(BASE, 'scout')
OUT = os.path.join(BASE, 'out', 'automation')
LOCK = os.path.join(OUT, 'automation.lock')
STALE_SECONDS = 12 * 60 * 60


class Tee:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.file = open(path, 'a', encoding='utf-8')

    def write(self, line):
        print(line, end='')
        self.file.write(line)
        self.file.flush()

    def close(self):
        self.file.close()


def acquire_lock(mode):
    os.makedirs(OUT, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = time.time() - os.path.getmtime(LOCK)
        if age <= STALE_SECONDS:
            raise SystemExit(f'another automation run holds {LOCK}')
        os.remove(LOCK)
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(dict(pid=os.getpid(), mode=mode, started_at=dt.datetime.now().isoformat()), f)


def release_lock():
    try:
        os.remove(LOCK)
    except FileNotFoundError:
        pass


def run_step(args, tee, env):
    tee.write('\n$ ' + ' '.join(args) + '\n')
    proc = subprocess.Popen(args, cwd=BASE, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                            errors='replace')
    for line in proc.stdout:
        tee.write(line)
    return proc.wait()


def weekly(date, dry_run, tee, env):
    fetch = [sys.executable, os.path.join(HERE, 'fetch_listings.py'), '--date', date]
    scout = [sys.executable, os.path.join(SCOUT, 'run_weekly.py'), '--date', date,
             '--international', '--write-dossiers']
    if dry_run:
        fetch.append('--dry-run')
        scout.append('--no-save')
        scout.append('--recompute')
    fetch_code = run_step(fetch, tee, env)
    scout_code = run_step(scout, tee, env)
    return fetch_code or scout_code


def monthly(history_months, tee, env):
    cmd = [sys.executable, os.path.join(HERE, 'fetch_search_volume.py'), '--all',
           '--history-months', str(history_months)]
    return run_step(cmd, tee, env)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=('weekly', 'monthly'))
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--dry-run', action='store_true', help='weekly only; writes no project data')
    ap.add_argument('--history-months', type=int, default=48)
    a = ap.parse_args(argv)
    if a.dry_run and a.mode != 'weekly':
        ap.error('--dry-run is available only for weekly')

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    log = os.path.join(OUT, f'{a.mode}_{stamp}.log')
    tee = Tee(log)
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    acquire_lock(a.mode)
    try:
        tee.write(f'Artist Scout automation: {a.mode} at {dt.datetime.now().isoformat()}\n')
        code = (weekly(a.date, a.dry_run, tee, env) if a.mode == 'weekly'
                else monthly(a.history_months, tee, env))
        tee.write(f'\ncompleted with exit code {code}; log: {log}\n')
        return code
    finally:
        release_lock()
        tee.close()


if __name__ == '__main__':
    raise SystemExit(main())
