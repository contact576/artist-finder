"""Local-only Artist Finder dashboard and operator API; no public binding or credential access."""
from __future__ import annotations
import argparse, hashlib, http.client, json, os, secrets, subprocess, sys, tempfile, threading, time, uuid, webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, parse_qs

HERE = Path(__file__).resolve().parent; BASE = HERE.parent; SCOUT = BASE / 'scout'; DEFAULT_DIRECTORY = BASE / 'out' / 'dashboard'; AUDIT_PATH = BASE / 'out' / 'operator_audit.json'; FAVORITES_PATH = BASE / 'out' / 'favorites.json'
MAX_BODY_BYTES = 262_144
sys.path.insert(0, str(SCOUT)); import roster  # noqa: E402

class OperatorApp:
    def __init__(self, root=DEFAULT_DIRECTORY, roster_path=None, audit_path=AUDIT_PATH, favorites_path=FAVORITES_PATH, test_mode=False):
        self.root, self.roster_path, self.audit_path, self.favorites_path, self.test_mode = Path(root).resolve(), roster_path, Path(audit_path), Path(favorites_path), test_mode
        self.csrf = secrets.token_urlsafe(24); self.lock = threading.RLock(); self.refresh = None
    def status(self):
        job = self.refresh or {}; return dict(state=job.get('state', 'idle'), job_id=job.get('job_id'), started_at=job.get('started_at'), finished_at=job.get('finished_at'), exit_code=job.get('exit_code'), message=job.get('message'))
    def artists(self, query='', status=None):
        rows = list(roster.load(self.roster_path).get('artists', {}).values()); query = query.casefold().strip()
        return [r for r in rows if (not status or r.get('status') == status) and (not query or query in r.get('name','').casefold() or query in r.get('slug',''))]
    def favorites(self):
        """Return durable favorites whose slugs still exist in the artist registry."""
        with self.lock:
            try: data = json.loads(self.favorites_path.read_text(encoding='utf-8')) if self.favorites_path.exists() else {}
            except (OSError, ValueError): data = {}
            values = data.get('favorites', []) if isinstance(data, dict) else []
            favorites = {item for item in values if isinstance(item, str)}
            known = set(roster.load(self.roster_path).get('artists', {}))
            return sorted(favorites & known)
    def set_favorite(self, slug, favorite):
        """Atomically persist one validated favorite without touching the artist registry."""
        if not isinstance(favorite, bool): raise ValueError('favorite must be true or false')
        registry = roster.load(self.roster_path)
        if slug not in registry.get('artists', {}): raise ValueError('unknown artist slug')
        with self.lock:
            try: data = json.loads(self.favorites_path.read_text(encoding='utf-8')) if self.favorites_path.exists() else {}
            except (OSError, ValueError): data = {}
            values = data.get('favorites', []) if isinstance(data, dict) else []
            favorites = {item for item in values if isinstance(item, str)}
            prior = slug in favorites
            if favorite: favorites.add(slug)
            else: favorites.discard(slug)
            changed = prior != favorite
            if changed:
                payload = dict(schema_version=1, favorites=sorted(favorites))
                self.favorites_path.parent.mkdir(parents=True, exist_ok=True); fd, tmp = tempfile.mkstemp(prefix='.favorites.', suffix='.tmp', dir=self.favorites_path.parent)
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as h: json.dump(payload, h, ensure_ascii=False, indent=2); h.write('\n'); h.flush(); os.fsync(h.fileno())
                    os.replace(tmp, self.favorites_path)
                finally:
                    if os.path.exists(tmp): os.unlink(tmp)
            return dict(slug=slug, favorite=favorite, previous_value=prior, changed=changed, count=len(favorites))
    def set_favorites(self, slugs, favorite):
        """Persist a validated artist set with one atomic favorites write."""
        if not isinstance(favorite, bool): raise ValueError('favorite must be true or false')
        registry = roster.load(self.roster_path); targets = roster.operator_validate_slugs(slugs, registry)
        with self.lock:
            try: data = json.loads(self.favorites_path.read_text(encoding='utf-8')) if self.favorites_path.exists() else {}
            except (OSError, ValueError): data = {}
            values = data.get('favorites', []) if isinstance(data, dict) else []
            favorites = {item for item in values if isinstance(item, str)}
            changed = [slug for slug in targets if (slug in favorites) != favorite]
            if favorite: favorites.update(targets)
            else: favorites.difference_update(targets)
            if changed:
                payload = dict(schema_version=1, favorites=sorted(favorites))
                self.favorites_path.parent.mkdir(parents=True, exist_ok=True); fd, tmp = tempfile.mkstemp(prefix='.favorites.', suffix='.tmp', dir=self.favorites_path.parent)
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as h: json.dump(payload, h, ensure_ascii=False, indent=2); h.write('\n'); h.flush(); os.fsync(h.fileno())
                    os.replace(tmp, self.favorites_path)
                finally:
                    if os.path.exists(tmp): os.unlink(tmp)
            return dict(favorite=favorite, requested_count=len(targets), changed_count=len(changed),
                        changed_slugs=changed, favorites=sorted(favorites), count=len(favorites))
    def audit(self, action, slug=None, detail=None):
        row = dict(at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), action=action, slug=slug, detail=detail or {})
        with self.lock:
            try: data = json.loads(self.audit_path.read_text(encoding='utf-8')) if self.audit_path.exists() else dict(entries=[])
            except (OSError, ValueError): data = dict(entries=[])
            data.setdefault('entries', []).append(row); data['entries'] = data['entries'][-1000:]
            self.audit_path.parent.mkdir(parents=True, exist_ok=True); fd, tmp = tempfile.mkstemp(prefix='.operator_audit.', suffix='.tmp', dir=self.audit_path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as h: json.dump(data, h, ensure_ascii=False, indent=2); h.write('\n'); h.flush(); os.fsync(h.fileno())
                os.replace(tmp, self.audit_path)
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
        return row
    def rebuild_dashboard(self):
        """Rebuild after a successful roster mutation; fixture mode is deliberately no-write."""
        if self.test_mode: return 0
        env = os.environ.copy(); env.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        code = subprocess.call([sys.executable, str(HERE / 'build_dashboard.py'), '--mode', 'live'], cwd=BASE, env=env)
        if code != 0: raise OSError('Artist change was saved, but the dashboard rebuild failed. Run tools/build_dashboard.py --mode live.')
        return code
    def begin_refresh(self):
        with self.lock:
            if self.refresh and self.refresh.get('state') == 'running': raise RuntimeError('a monthly refresh is already running')
            job = dict(job_id=uuid.uuid4().hex, state='running', started_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), finished_at=None, exit_code=None, message='Monthly refresh started locally.')
            self.refresh = job
        if self.test_mode:
            job.update(state='success', finished_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), exit_code=0, message='Fixture refresh completed; no project data was changed.')
        else: threading.Thread(target=self._run_refresh, args=(job,), daemon=True).start()
        return dict(job)
    def _run_refresh(self, job):
        env = os.environ.copy(); env.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        try:
            code = subprocess.call([sys.executable, str(HERE / 'run_automation.py'), 'monthly', '--history-months', '48'], cwd=BASE, env=env)
            job.update(state=('success' if code == 0 else 'failed'), exit_code=code, message=('Monthly refresh and dashboard rebuild completed.' if code == 0 else 'Monthly refresh failed; dashboard rebuild was skipped or failed.'))
        except OSError:
            job.update(state='failed', exit_code=127, message='Could not start the local monthly automation.')
        finally: job['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def make_handler(app):
    root = app.root
    class Handler(SimpleHTTPRequestHandler):
        def end_headers(self):
            if not urlsplit(self.path).path.startswith('/api/'):
                self.send_header('Cache-Control','no-store')
            return super().end_headers()
        def _json(self, status, value):
            text=json.dumps(value, ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(text))); self.end_headers(); self.wfile.write(text)
        def _error(self, status, message, field_errors=None): self._json(status, dict(error=HTTPStatus(status).phrase.lower().replace(' ','_'), message=message, **(dict(field_errors=field_errors) if field_errors else {})))
        def _allowed_host(self):
            host=(self.headers.get('Host') or '').split(':',1)[0].strip('[]'); return host in ('127.0.0.1','localhost')
        def _mutation(self):
            if not self._allowed_host(): self._error(HTTPStatus.FORBIDDEN,'loopback Host required'); return None
            origin=self.headers.get('Origin'); expected=f'http://127.0.0.1:{self.server.server_port}'
            if origin != expected: self._error(HTTPStatus.FORBIDDEN,'same-origin request required'); return None
            if self.headers.get('X-CSRF-Token') != app.csrf: self._error(HTTPStatus.FORBIDDEN,'invalid CSRF token'); return None
            if self.headers.get('Content-Type','').split(';',1)[0].lower() != 'application/json': self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,'application/json content type required'); return None
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length < 2 or length > MAX_BODY_BYTES: raise ValueError()
                value=json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(value,dict): raise ValueError()
                return value
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError): self._error(HTTPStatus.BAD_REQUEST,'invalid JSON object'); return None
        def _candidate(self):
            path=unquote(urlsplit(self.path).path); parts=PurePosixPath(path).parts
            if any(part=='..' for part in parts): return None
            value=root.joinpath(*(p for p in parts if p not in ('/','.'))).resolve()
            try: value.relative_to(root); return value
            except ValueError: return None
        def do_GET(self):
            path=urlsplit(self.path).path
            if path == '/api/dashboard/status': return self._json(200, app.status())
            if path == '/api/dashboard':
                data=root/'dashboard-data.json'
                if not data.is_file(): return self._error(404,'dashboard data is unavailable')
                try: return self._json(200,json.loads(data.read_text(encoding='utf-8')))
                except ValueError: return self._error(503,'dashboard data is invalid')
            if path == '/api/v1/meta': return self._json(200,dict(api_version=1, csrf_token=app.csrf, statuses=roster.STATUSES, niches=roster.load_niches(), refresh=app.status()))
            if path == '/api/v1/favorites':
                favorites = app.favorites(); return self._json(200, dict(favorites=favorites, count=len(favorites)))
            if path == '/api/v1/artists':
                q=parse_qs(urlsplit(self.path).query); return self._json(200,dict(artists=app.artists((q.get('q') or [''])[0],(q.get('status') or [None])[0]),generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
            if path == '/api/v1/audit':
                try: return self._json(200,json.loads(app.audit_path.read_text(encoding='utf-8')) if app.audit_path.exists() else dict(entries=[]))
                except ValueError: return self._error(503,'audit log unreadable')
            if path.startswith('/api/refresh/'):
                return self._json(200,app.status()) if app.status().get('job_id') == path.rsplit('/',1)[-1] else self._error(404,'unknown refresh job')
            if self._candidate() is None: return self._error(403,'path outside dashboard directory')
            return super().do_GET()
        def do_HEAD(self):
            if self._candidate() is None: return self._error(403,'path outside dashboard directory')
            return super().do_HEAD()
        def do_POST(self):
            body=self._mutation()
            if body is None: return
            path=urlsplit(self.path).path
            try:
                if path == '/api/refresh': value=app.begin_refresh(); app.audit('refresh_started',detail=dict(job_id=value['job_id'])); return self._json(202,value)
                if path == '/api/artists':
                    with app.lock:
                        artist=roster.operator_add_artist(body.get('name'),body.get('category'),body.get('measurement_keyword'),body.get('evidence_url'),path=app.roster_path); app.rebuild_dashboard(); app.audit('artist_added',artist['slug'])
                    return self._json(201,dict(artist=artist,generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
                if path == '/api/artists/bulk/favorite':
                    value=app.set_favorites(body.get('slugs'), body.get('favorite'))
                    targets=body.get('slugs') or []; digest=hashlib.sha256('\n'.join(targets).encode('utf-8')).hexdigest()
                    app.audit('artists_bulk_favorited' if value['favorite'] else 'artists_bulk_unfavorited', detail=dict(requested_count=value['requested_count'], changed_count=value['changed_count'], target_slugs=targets[:100], target_list_truncated=len(targets)>100, target_digest=digest))
                    return self._json(200,dict(**value,generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
                if path == '/api/artists/bulk/status':
                    with app.lock:
                        value=roster.operator_set_status_bulk(body.get('slugs'),body.get('active'),path=app.roster_path); app.rebuild_dashboard()
                        targets=body.get('slugs') or []; digest=hashlib.sha256('\n'.join(targets).encode('utf-8')).hexdigest()
                        app.audit('artists_bulk_status_changed',detail=dict(active=value['active'],requested_count=value['requested_count'],changed_count=value['changed_count'],target_slugs=targets[:100],target_list_truncated=len(targets)>100,target_digest=digest))
                    return self._json(200,dict(**value,generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
                bits=path.split('/'); slug=bits[3] if len(bits)==5 else None
                if slug and bits[-1]=='favorite':
                    value=app.set_favorite(slug, body.get('favorite'))
                    app.audit('artist_favorited' if value['favorite'] else 'artist_unfavorited', slug, dict(prior_value=value['previous_value'], new_value=value['favorite'], changed=value['changed']))
                    return self._json(200, dict(**value, favorites=app.favorites(), generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
                if slug and bits[-1]=='category':
                    with app.lock:
                        artist=roster.operator_set_category(slug,body.get('category'),path=app.roster_path); app.rebuild_dashboard(); app.audit('category_changed',slug,dict(category=artist['primary_genre']))
                    return self._json(200,dict(artist=artist,generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
                if slug and bits[-1]=='status':
                    with app.lock:
                        artist=roster.operator_set_status(slug,body.get('active'),path=app.roster_path); app.rebuild_dashboard(); app.audit('status_changed',slug,dict(status=artist['status']))
                    return self._json(200,dict(artist=artist,generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
                if slug and bits[-1]=='keywords':
                    with app.lock:
                        artist=roster.operator_update_keyword(slug,body.get('keyword'),body.get('action'),body.get('previous_keyword'),path=app.roster_path); app.rebuild_dashboard(); app.audit('keyword_changed',slug,dict(action=body.get('action')))
                    return self._json(200,dict(artist=artist,generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
                return self._error(404,'unknown API endpoint')
            except RuntimeError as exc: return self._error(409,str(exc))
            except OSError as exc: return self._error(500,str(exc))
            except ValueError as exc: return self._error(400,str(exc),dict(input=str(exc)))
        def do_PATCH(self):
            body=self._mutation()
            if body is None: return
            bits=urlsplit(self.path).path.split('/')
            if len(bits)!=4 or bits[:3]!=['','api','artists']: return self._error(404,'unknown API endpoint')
            try:
                with app.lock:
                    registry=roster.load(app.roster_path); artist=registry['artists'].get(bits[3])
                    if not artist: raise ValueError('unknown artist slug')
                    edit_fields = {key: body[key] for key in ('name','aliases','evidence_url','evidence_note') if key in body}
                    if edit_fields: artist=roster.operator_edit_artist(bits[3], data=registry, save_now=False, **edit_fields)
                    if 'category' in body: artist=roster.operator_set_category(bits[3],body['category'],data=registry,save_now=False)
                    if 'active' in body: artist=roster.operator_set_status(bits[3],body['active'],data=registry,save_now=False)
                    if 'keyword' in body: artist=roster.operator_update_keyword(bits[3],body['keyword'],body.get('action','replace'),body.get('previous_keyword'),data=registry,save_now=False)
                    roster.save(registry,app.roster_path)
                    app.rebuild_dashboard(); app.audit('artist_updated',bits[3])
                return self._json(200,dict(artist=artist,generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
            except OSError as exc: return self._error(500,str(exc))
            except ValueError as exc: return self._error(400,str(exc),dict(input=str(exc)))
        def translate_path(self,path): return str(self._candidate() or root/'__forbidden__')
        def list_directory(self,path): self.send_error(403,'Directory listing is disabled'); return None
        def log_message(self,format,*args): print(f'  {self.address_string()} - {format % args}')
    return Handler

def _selftest():
    import shutil
    temp=Path(tempfile.mkdtemp()); reg=temp/'artist_roster.json'; audit=temp/'audit.json'; favorites=temp/'favorites.json'; root=temp/'dashboard'; root.mkdir(); (root/'index.html').write_text('fixture',encoding='utf-8')
    roster.save(roster.empty_registry(),str(reg)); app=OperatorApp(root, str(reg), audit, favorites, True)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(app)); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start(); port=server.server_port
    def request(method,path,body=None,csrf=None,origin=True,ctype=True):
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5); headers={'Host':f'127.0.0.1:{port}'}
        if origin: headers['Origin']=f'http://127.0.0.1:{port}'
        if ctype: headers['Content-Type']='application/json'
        if csrf: headers['X-CSRF-Token']=csrf
        raw=json.dumps(body).encode() if body is not None else None
        conn.request(method,path,body=raw,headers=headers); res=conn.getresponse(); text=res.read().decode(); conn.close(); return res.status,(json.loads(text) if text else {})
    try:
        denied,_=request('POST','/api/artists',dict(name='Fixture Artist',category='comedy'))
        meta_status,meta=request('GET','/api/v1/meta'); token=meta.get('csrf_token')
        created_status,created=request('POST','/api/artists',dict(name='Fixture Artist',category='comedy',measurement_keyword='Fixture Artist'),token)
        slug=created.get('artist',{}).get('slug')
        second_status,second=request('POST','/api/artists',dict(name='Second Fixture',category='comedy',measurement_keyword='Second Fixture'),token)
        second_slug=second.get('artist',{}).get('slug')
        patch_status,patched=request('PATCH',f'/api/artists/{slug}',dict(name='Renamed Fixture',aliases=['Fixture Alias'],evidence_url='https://example.com/evidence',evidence_note='Public identity page.',keyword='Renamed Fixture Live',action='replace',category='devotional'),token)
        registry_before_favorite=json.dumps(roster.load(str(reg)), sort_keys=True)
        favorite_status,favorited=request('POST',f'/api/artists/{slug}/favorite',dict(favorite=True),token)
        duplicate_status,duplicate=request('POST',f'/api/artists/{slug}/favorite',dict(favorite=True),token)
        invalid_status,_=request('POST','/api/artists/not-a-real-artist/favorite',dict(favorite=True),token)
        unfavorite_status,unfavorited=request('POST',f'/api/artists/{slug}/favorite',dict(favorite=False),token)
        refavorite_status,refavorited=request('POST',f'/api/artists/{slug}/favorite',dict(favorite=True),token)
        favorite_list_status,favorite_list=request('GET','/api/v1/favorites')
        restarted=OperatorApp(root, str(reg), audit, favorites, True)
        restart_favorites=restarted.favorites()
        bulk_favorite_status,bulk_favorite=request('POST','/api/artists/bulk/favorite',dict(slugs=[slug,second_slug],favorite=True),token)
        favorite_before_invalid=app.favorites()
        invalid_bulk_favorite_status,_=request('POST','/api/artists/bulk/favorite',dict(slugs=[slug,'not-a-real-artist'],favorite=False),token)
        invalid_bulk_favorite_atomic=app.favorites()==favorite_before_invalid
        bulk_unfavorite_status,bulk_unfavorite=request('POST','/api/artists/bulk/favorite',dict(slugs=[slug,second_slug],favorite=False),token)
        roster_unchanged=registry_before_favorite==json.dumps(roster.load(str(reg)), sort_keys=True)
        status_before_invalid=json.dumps(roster.load(str(reg)),sort_keys=True)
        invalid_bulk_status,_=request('POST','/api/artists/bulk/status',dict(slugs=[slug,'not-a-real-artist'],active=False),token)
        invalid_bulk_status_atomic=json.dumps(roster.load(str(reg)),sort_keys=True)==status_before_invalid
        bulk_archive_status,bulk_archive=request('POST','/api/artists/bulk/status',dict(slugs=[slug,second_slug],active=False),token)
        bulk_restore_status,bulk_restore=request('POST','/api/artists/bulk/status',dict(slugs=[slug,second_slug],active=True),token)
        state_status,state=request('GET','/api/dashboard/status'); traversal,_=request('GET','/%2e%2e/secret')
        saved=roster.load(str(reg))['artists'].get(slug) or {}; audit_rows=json.loads(audit.read_text(encoding='utf-8')).get('entries', []); job=app.begin_refresh()
        checks=[('CSRF rejection',denied==403),('metadata exposes CSRF',meta_status==200 and bool(token)),('valid mutation succeeds',created_status==201 and second_status==201),('PATCH edits and preserves slug',patch_status==200 and patched['artist']['slug']==slug and patched['artist']['name']=='Renamed Fixture' and 'Fixture Artist' in patched['artist']['aliases']),('PATCH persists candidate keyword review',saved.get('measurement_keyword')=='Renamed Fixture Live' and saved.get('keyword_review_state')=='pending' and saved.get('primary_genre')=='devotional' and saved.get('category_history')),('favorite validates and persists',favorite_status==200 and favorited['favorite'] and favorited['changed'] and favorites.is_file()),('duplicate favorite is idempotent',duplicate_status==200 and duplicate['favorite'] and not duplicate['changed']),('unknown favorite slug is rejected',invalid_status==400),('unfavorite persists',unfavorite_status==200 and not unfavorited['favorite'] and unfavorited['changed']),('favorite survives app reload',refavorite_status==200 and refavorited['favorite'] and favorite_list_status==200 and favorite_list['favorites']==[slug] and restart_favorites==[slug]),('bulk favorite uses one validated atomic response',bulk_favorite_status==200 and bulk_favorite['requested_count']==2 and bulk_favorite['changed_count']==1 and set(bulk_favorite['favorites'])=={slug,second_slug}),('invalid bulk favorite is all-or-nothing',invalid_bulk_favorite_status==400 and invalid_bulk_favorite_atomic),('bulk unfavorite persists both artists',bulk_unfavorite_status==200 and bulk_unfavorite['changed_count']==2 and bulk_unfavorite['favorites']==[]),('favorite leaves analytics registry unchanged',roster_unchanged),('bulk status rejects the whole invalid target set',invalid_bulk_status==400 and invalid_bulk_status_atomic),('bulk archive and restore are reversible',bulk_archive_status==200 and bulk_restore_status==200 and bulk_archive['changed_count']==2 and bulk_restore['changed_count']==2 and all(row['status']=='candidate' for row in bulk_restore['artists'])),('favorite audit records prior and new values',len([row for row in audit_rows if row['action'] in ('artist_favorited','artist_unfavorited')])==4 and all('prior_value' in row['detail'] and 'new_value' in row['detail'] for row in audit_rows if row['action'] in ('artist_favorited','artist_unfavorited'))),('bulk actions have bounded audit records',len([row for row in audit_rows if row['action'].startswith('artists_bulk_')])==4 and all('target_digest' in row['detail'] for row in audit_rows if row['action'].startswith('artists_bulk_'))),('sanitized status',state_status==200 and 'state' in state),('traversal rejected',traversal==403),('fixture refresh is no-write',job['state']=='success' and not (root/'dashboard-data.json').exists()),('atomic audit exists',audit.is_file())]
        ok=all(good for _,good in checks)
        for label,good in checks: print(f'  [{"ok " if good else "FAIL"}] {label}')
        print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}'); return 0 if ok else 1
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2); shutil.rmtree(temp)

def main(argv=None):
    parser=argparse.ArgumentParser(description='Serve Artist Finder dashboard and local operator API.'); parser.add_argument('--directory',default=str(DEFAULT_DIRECTORY)); parser.add_argument('--port',type=int,default=8765); parser.add_argument('--no-browser',action='store_true'); parser.add_argument('--selftest',action='store_true'); args=parser.parse_args(argv)
    if args.selftest: return _selftest()
    root=Path(args.directory).resolve();
    if not (root/'index.html').is_file(): parser.error(f'No generated dashboard at {root}. Run tools/build_dashboard.py --mode live first.')
    if not 1<=args.port<=65535: parser.error('--port must be between 1 and 65535')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(OperatorApp(root))); url=f'http://127.0.0.1:{server.server_port}/'; print(f'Artist Finder dashboard: {url}'); print('Local API: /api/v1/meta (loopback only)')
    if not args.no_browser: threading.Timer(.15,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print('\nDashboard server stopped.')
    finally: server.server_close()
    return 0
if __name__=='__main__': raise SystemExit(main())
