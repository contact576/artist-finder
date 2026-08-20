"""Interactive credential setup. Run it yourself — the secrets never leave your machine.

WHY THIS SCRIPT EXISTS.
Claude cannot accept API keys, tokens or client secrets through chat: anything pasted into a
conversation lands in a transcript. So the values go straight from you into data/config.json,
which is gitignored, and Claude never sees them. This script does the fiddly part — validating
the shape of each value, and telling you exactly where to find it.

    python setup_credentials.py            fill in anything missing
    python setup_credentials.py --show     what is set, WITHOUT revealing values
    python setup_credentials.py --test     check the credentials actually work

Nothing is echoed to the screen while you type it, and nothing is printed back afterwards —
only whether a value is present and whether it has a plausible shape.
"""
import argparse
import getpass
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
CONFIG = os.path.join(BASE, 'data', 'config.json')

FIELDS = [
    ('google_ads', 'developer_token', 'Google Ads DEVELOPER TOKEN', True,
     r'^[A-Za-z0-9_-]{22}$',
     ['Where: ads.google.com/aw/apicenter, signed in to the PPC Guru.ca manager account.',
      'It looks like a long string of letters, numbers and dashes.',
      'If you have not applied yet, apply first — approval takes a few days.']),
    ('google_ads', 'client_id', 'OAuth CLIENT ID', True,
     r'^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$',
     ['Where: console.cloud.google.com -> APIs & Services -> Credentials.',
      'Create an OAuth client of type "Desktop app" if you do not have one.',
      'It ENDS IN .apps.googleusercontent.com — that is how you know it is the right value.']),
    ('google_ads', 'client_secret', 'OAuth CLIENT SECRET', True,
     r'^\S{10,}$',
     ['Same screen as the client ID, shown next to it.',
      'Usually starts with GOCSPX-.']),
    ('google_ads', 'refresh_token', 'OAuth REFRESH TOKEN', True,
     r'^1//\S{20,}$',
     ['This is the one people get stuck on. Run:  python setup_credentials.py --oauth',
      'That prints a link, you approve in the browser, and it gives you the token.',
      'It starts with 1// — if yours does not, it is the wrong value.']),
    ('google_ads', 'login_customer_id', 'MANAGER ACCOUNT ID', False,
     r'^\d{10}$',
     ['The PPC Guru.ca manager account: 1632013729',
      'Ten digits, no dashes. Press Enter to accept the default.']),
    ('google_ads', 'customer_id', 'BILLING ACCOUNT ID', False,
     r'^\d{10}$',
     ['Which account the API request is billed against. The manager account is fine.',
      'Press Enter to accept the default.']),
    (None, 'apify_token', 'APIFY API TOKEN', False,
     r'^apify_api_\S{20,}$',
     ['Where: console.apify.com -> Settings -> Integrations -> API token.',
      'Starts with apify_api_. This is what lets the scout crawl BookMyShow,',
      'which blocks ordinary requests. Everything else works without it.']),
    (None, 'bandsintown_app_id', 'BANDSINTOWN APP ID', False,
     r'^\S{3,}$',
     ['Free. Request at artists.bandsintown.com by accepting their terms.',
      'Optional — everything else works without it. Press Enter to skip.']),
]

DEFAULTS = {'login_customer_id': '1632013729', 'customer_id': '1632013729',
            'api_version': 'v25'}


def load():
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding='utf-8') as f:
            return json.load(f)
    return {'google_ads': dict(DEFAULTS), 'bandsintown_app_id': ''}


def save(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=1)
    try:                      # best effort; Windows ACLs are not POSIX modes
        os.chmod(CONFIG, 0o600)
    except Exception:
        pass
    return CONFIG


def get(cfg, section, key):
    return (cfg.get(section, {}) if section else cfg).get(key) or ''


def put(cfg, section, key, val):
    (cfg.setdefault(section, {}) if section else cfg)[key] = val


def mask(v):
    """Expose only presence; values, lengths, and suffixes are sensitive metadata."""
    return 'SET' if v else 'NOT SET'


def show(cfg):
    print('\nCredential status — values are never displayed\n' + '=' * 52)
    for section, key, label, required, _, _h in FIELDS:
        v = get(cfg, section, key)
        req = 'required' if required else 'optional'
        print(f'  {label:28} {mask(v):28} {req}')
    print(f'\n  file: {CONFIG}')
    print('  this file is gitignored and will never be committed.')
    missing = [lbl for sec, k, lbl, req, _, _h in FIELDS if req and not get(cfg, sec, k)]
    if missing:
        print(f'\n  STILL NEEDED: {", ".join(missing)}')
    else:
        print('\n  All required values present. Next: python setup_credentials.py --test')
    return 0


def prompt_all(cfg):
    print('\nArtist Scout — credential setup')
    print('=' * 52)
    print('Nothing you type is shown on screen, and nothing is printed back afterwards.')
    print('Press Enter to keep an existing value or accept a default. Ctrl+C to stop.\n')

    for section, key, label, required, pattern, help_lines in FIELDS:
        cur = get(cfg, section, key)
        default = DEFAULTS.get(key, '')
        print(f'--- {label}' + ('  [required]' if required else '  [optional]'))
        for h in help_lines:
            print(f'    {h}')
        if cur:
            print(f'    Currently: {mask(cur)}')
        elif default:
            print(f'    Default: {default}')

        while True:
            secret = key in ('developer_token', 'client_secret', 'refresh_token')
            raw = (getpass.getpass('    Paste value (hidden): ') if secret
                   else input('    Value: ')).strip()
            if not raw:
                if cur or default:
                    if not cur:
                        put(cfg, section, key, default)
                    break
                if not required:
                    break
                print('    ! required — paste a value or press Ctrl+C to stop.')
                continue
            if pattern and not re.match(pattern, raw):
                print(f'    ! that does not look right for {label}.')
                print(f'      expected shape: {pattern}')
                again = input('      use it anyway? (y/N): ').strip().lower()
                if again != 'y':
                    continue
            put(cfg, section, key, raw)
            break
        print()

    cfg.setdefault('google_ads', {}).setdefault('api_version', DEFAULTS['api_version'])
    save(cfg)
    print(f'Saved to {CONFIG}')
    print('That file is gitignored — it will never be committed or shared.\n')
    return 0


def prompt_developer_token(field):
    section, key, label, required, pattern, help_lines = field
    cfg = load()
    print(f'\n{label}')
    print('=' * 52)
    for line in help_lines:
        print(f'  {line}')

    while True:
        raw = getpass.getpass('\nPaste value (hidden): ').strip()
        if not raw:
            print('Nothing entered; existing configuration was not changed.')
            return 1
        candidates = re.findall(
            r'(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{22}(?![A-Za-z0-9_-])', raw)
        if len(candidates) == 1:
            raw = candidates[0]
        if raw.isdigit() and len(raw) in (10, 12):
            print('That is an account/customer ID, not the Developer token. Please copy the token row.')
            continue
        if pattern and not re.match(pattern, raw):
            print('That is not a Google Ads Developer token.')
            print('Copy only the value beside Developer token, then paste again.')
            continue
        put(cfg, section, key, raw)
        for default_key, default_value in DEFAULTS.items():
            cfg.setdefault('google_ads', {}).setdefault(default_key, default_value)
        save(cfg)
        print('Saved Google Ads DEVELOPER TOKEN locally. The value was not printed or logged.')
        return 0


def prompt_field(field_key):
    field = next((f for f in FIELDS if f[1] == field_key), None)
    if field is None:
        print(f'Unknown credential field: {field_key}')
        return 1
    if field_key == 'developer_token':
        return prompt_developer_token(field)


    section, key, label, required, pattern, help_lines = field
    cfg = load()
    print(f'\n{label}')
    print('=' * 52)
    for line in help_lines:
        print(f'  {line}')
    secret = key in ('developer_token', 'client_secret', 'refresh_token', 'apify_token')
    raw = (getpass.getpass('\nPaste value (hidden): ') if secret else input('\nPaste value: ')).strip()
    if not raw:
        print('Nothing entered; existing configuration was not changed.')
        return 1
    if pattern and not re.match(pattern, raw):
        print(f'That value does not match the expected shape for {label}. Nothing was saved.')
        return 1
    put(cfg, section, key, raw)
    if section == 'google_ads':
        for default_key, default_value in DEFAULTS.items():
            cfg.setdefault('google_ads', {}).setdefault(default_key, default_value)
    save(cfg)
    print(f'Saved {label} locally. The value was not printed or logged.')
    return 0


OAUTH_HELP = """
Getting a REFRESH TOKEN
=======================
One-time setup. This is the step that stops most people, so here it is in full.

1. console.cloud.google.com  ->  pick or create a project (any name will do).
2. APIs & Services -> Library -> search "Google Ads API" -> ENABLE.
3. APIs & Services -> OAuth consent screen -> External -> fill the required fields.
   Under TEST USERS, add your own Google account - the one with access to PPC Guru.ca.
   Skip that and the consent screen will refuse you at step 5.
4. APIs & Services -> Credentials -> Create credentials -> OAuth client ID
   -> Application type: DESKTOP APP.  Copy the client ID and the client secret.
5. Run this script with --oauth. Your browser opens, you approve, and the token is
   captured automatically. Nothing to copy back.

The refresh token starts with 1// and does not expire unless you revoke it.
"""


def oauth_flow():
    """Loopback OAuth: a one-shot local server catches the code, so nothing is pasted by hand.

    WHY LOOPBACK AND NOT COPY-PASTE. The old out-of-band flow - where Google displayed a code
    for you to paste back - was blocked for new clients in February 2022 and fully deprecated
    in January 2023 as a phishing risk. An earlier version of this function used it and would
    have failed at the final step. The supported route for a Desktop app client is a loopback
    redirect to 127.0.0.1, which is also less work: the browser hands the code straight back
    and there is nothing to transcribe.
    """
    import http.server
    import socket
    import threading
    import urllib.parse
    import urllib.request
    import webbrowser

    print(OAUTH_HELP)
    cfg = load()
    google_ads = cfg.get('google_ads') or {}
    cid = google_ads.get('client_id', '').strip()
    csec = google_ads.get('client_secret', '').strip()
    if cid and csec:
        print('Using the OAuth Client ID and Client Secret already saved locally.')
    else:
        cid = input('Paste your OAuth CLIENT ID: ').strip()
        csec = getpass.getpass('Paste your OAuth CLIENT SECRET (hidden): ').strip()
        if not cid or not csec:
            print('Both are needed. Stopping.')
            return 1
        if not re.match(r'^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$', cid):
            print('That Client ID does not have the expected Desktop OAuth format.')
            print('It must end in .apps.googleusercontent.com. Nothing was saved.')
            return 1
        if not re.match(r'^\S{10,}$', csec):
            print('That Client Secret is too short. Nothing was saved.')
            return 1
        put(cfg, 'google_ads', 'client_id', cid)
        put(cfg, 'google_ads', 'client_secret', csec)
        for k, v in DEFAULTS.items():
            cfg['google_ads'].setdefault(k, v)
        save(cfg)
        print('Client ID and Client Secret saved locally. Future retries will reuse them.')

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    redirect = 'http://127.0.0.1:%d' % port
    caught = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            caught.update({k: v[0] for k, v in q.items()})
            ok = 'code' in caught
            page = ('<html><body style="font-family:system-ui;padding:3rem;text-align:center">'
                    + ('<h2>Done.</h2><p>Close this tab and go back to the terminal.</p>'
                       if ok else
                       '<h2>Something went wrong.</h2><p>%s</p>'
                       % caught.get('error', 'no code returned'))
                    + '</body></html>')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(page.encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(('127.0.0.1', port), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()

    auth = ('https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({
        'client_id': cid, 'redirect_uri': redirect, 'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/adwords',
        'access_type': 'offline', 'prompt': 'consent'}))

    print()
    print('Opening your browser. If nothing happens, paste this in yourself:')
    print()
    print(auth)
    print()
    try:
        webbrowser.open(auth)
    except Exception:
        pass
    print('Waiting for you to approve...  (Ctrl+C to cancel)')

    waited = 0
    while 'code' not in caught and 'error' not in caught and waited < 300:
        time.sleep(1)
        waited += 1
    srv.server_close()

    if 'code' not in caught:
        print()
        print('No authorisation received (%s).' % caught.get('error', 'timed out'))
        print('The usual cause: your account is not listed under TEST USERS on the OAuth')
        print('consent screen. Add it there and run --oauth again.')
        return 1

    body = urllib.parse.urlencode(dict(
        code=caught['code'], client_id=cid, client_secret=csec,
        redirect_uri=redirect, grant_type='authorization_code')).encode()
    req = urllib.request.Request('https://oauth2.googleapis.com/token', data=body)
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            tok = json.loads(r.read().decode())
    except Exception as e:                                        # noqa: BLE001
        print()
        print('Token exchange failed: %s' % e)
        print('Codes are single-use - just run --oauth again for a fresh one.')
        return 1

    rt = tok.get('refresh_token')
    if not rt:
        print()
        print('No refresh_token came back. That happens when this account has already')
        print('approved this client. Revoke it at myaccount.google.com/permissions,')
        print('then run --oauth again.')
        return 1

    cfg = load()
    put(cfg, 'google_ads', 'client_id', cid)
    put(cfg, 'google_ads', 'client_secret', csec)
    put(cfg, 'google_ads', 'refresh_token', rt)
    for k, v in DEFAULTS.items():
        cfg['google_ads'].setdefault(k, v)
    save(cfg)
    print()
    print('Refresh token captured and saved to %s' % CONFIG)
    print('Not printing it - it is a long-lived key and does not belong on screen.')
    print()
    print('Still needed: the DEVELOPER TOKEN. Run this script with no arguments.')
    return 0


def test():
    cfg = load().get('google_ads') or {}
    missing = [k for k in ('developer_token', 'client_id', 'client_secret', 'refresh_token')
               if not cfg.get(k)]
    if missing:
        print(f'Cannot test — still missing: {", ".join(missing)}')
        print('Run this script with no arguments to fill them in.')
        return 1
    print('Testing against the live API...\n')
    sys.path.insert(0, HERE)
    import fetch_search_volume as f                                # noqa: E402
    return f.main(['--check'])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--show', action='store_true', help='what is set, without revealing values')
    ap.add_argument('--test', action='store_true', help='check the credentials actually work')
    ap.add_argument('--oauth', action='store_true', help='walk through getting a refresh token')
    ap.add_argument('--field', choices=[f[1] for f in FIELDS],
                    help='prompt for exactly one credential and save it locally')
    a = ap.parse_args(argv)
    if a.field:
        return prompt_field(a.field)
    if a.oauth:
        return oauth_flow()
    if a.show:
        return show(load())
    if a.test:
        return test()
    return prompt_all(load())


if __name__ == '__main__':
    raise SystemExit(main())
