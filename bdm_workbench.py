"""Local-only editable BDM working view. Binds loopback; no external publication."""
import argparse
import json
import secrets
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import bdm_monitor as m
import bdm_workflow as w
from project_paths import PROJECT_DIR, database_directory


def save_card(db, data_dir, payload, moment):
    key = payload['action_id']
    old = db.execute('SELECT * FROM results WHERE action_id=?', (key,)).fetchone()
    if not old or old['updated_at'] != payload.get('updated_at'):
        raise ValueError('This record changed. Reload before saving.')
    human = payload.get('human', {})
    if any(k not in m.HUMAN for k in human):
        raise ValueError('Unknown editable field')
    if any(len(str(v)) > 8000 for v in human.values()):
        raise ValueError('Field is too long')
    with db:
        w.save_qualification(db, key, payload.get('qualification', {}), payload['qualification_version'], moment)
        if key.startswith('LEGACY:'):
            import action_tracker as tracker
            # Same SQLite transaction across both workflow databases.
            db.execute('ATTACH DATABASE ? AS legacy', (str((data_dir/'actions.db').resolve()),))
            legacy = db.execute('SELECT * FROM legacy.actions WHERE action_id=?', (old['source_id'],)).fetchone()
            if not legacy:
                raise ValueError('Legacy action missing')
            for field, raw in human.items():
                value = str(raw or '').strip()
                if field == 'stage' and value not in tracker.STAGES:
                    raise ValueError('Invalid legacy stage')
                if field == 'owner' and value not in ('Lane', 'Sirish', legacy['owner']):
                    raise ValueError('Invalid owner')
                if field == 'follow_up' and value:
                    datetime.strptime(value, '%Y-%m-%d')
                if value != (legacy[field] or ''):
                    db.execute(f'UPDATE legacy.actions SET {field}=?,updated_at=? WHERE action_id=?', (value, moment.isoformat(), old['source_id']))
                    db.execute('INSERT INTO legacy.action_history VALUES(?,?,?,?,?)', (old['source_id'],field,legacy[field],value,moment.isoformat()))
                    db.execute(f'UPDATE results SET {field}=?,updated_at=? WHERE action_id=?', (value,moment.isoformat(),key))
                    db.execute('INSERT INTO edit_history VALUES(?,?,?,?,?)', (key,field,old[field],value,moment.isoformat()))
        else:
            m.update_edits(db, [dict(action_id=key, **human)], moment)


def build_server(state, data_dir, out, port=8767):
    token = secrets.token_urlsafe(32)
    guard = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body, mime='application/json'):
            body = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.valid_host():
                self.respond(403, '{}'); return
            path = urlsplit(self.path).path
            if path == '/api/identity':
                self.respond(200,json.dumps({'service':'illoura-bdm'}))
            elif path == '/':
                page = (PROJECT_DIR/'bdm_workbench.html').read_text(encoding='utf-8').replace('__CSRF__', token)
                self.respond(200, page, 'text/html')
            elif path == '/api/results':
                try:
                    with guard, m.lock(state.with_suffix('.lock')), closing(m.connect(state)) as db:
                        now = m.now_utc()
                        w.legacy_actions(db, data_dir, now)
                        r = w.rows(db, now)
                        health = json.loads((out/'local_status.json').read_text(encoding='utf-8')) if (out/'local_status.json').exists() else {}
                        self.respond(200,json.dumps({'rows':r,'health':health,'generated_at':now.isoformat()},ensure_ascii=False))
                except RuntimeError:
                    self.respond(409,json.dumps({'error':'Monitor is updating. Retry in a moment.'}))
            elif path in ('/results.csv','/active_results.csv','/archived_results.csv'):
                self.respond(200,(out/path[1:]).read_text(encoding='utf-8-sig'),'text/csv')
            else:
                self.respond(404,'{}')

        def do_POST(self):
            origin = self.headers.get('Origin')
            valid_origin = origin in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
            if not self.valid_host() or not valid_origin or self.headers.get('X-BDM-CSRF') != token:
                self.respond(403,'{}'); return
            if self.path != '/api/save':
                self.respond(404,'{}'); return
            try:
                size = int(self.headers.get('Content-Length','0'))
                if size < 1 or size > 64000:
                    raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(size))
                with guard, m.lock(state.with_suffix('.lock')), closing(m.connect(state)) as db:
                    save_card(db,data_dir,payload,m.now_utc())
                    m.export_results(db,out,m.now_utc())
                self.respond(200,json.dumps({'saved':True}))
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                self.respond(400,json.dumps({'error':str(exc)}))
            except (sqlite3.Error, RuntimeError):
                self.respond(409,json.dumps({'error':'Monitor is updating. Retry after reloading.'}))

    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8767)
    parser.add_argument('--state',type=Path,default=database_directory()/'bdm_monitor.db')
    parser.add_argument('--data-dir',type=Path,default=database_directory())
    parser.add_argument('--out',type=Path,default=PROJECT_DIR/'exports/bdm')
    args=parser.parse_args()
    server=build_server(args.state,args.data_dir,args.out,args.port)
    print('Illoura BDM working view: http://127.0.0.1:'+str(args.port),flush=True)
    server.serve_forever()


if __name__=='__main__':
    main()
