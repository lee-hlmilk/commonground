"""Durable snapshots and ordered, reconnectable events. No provider credentials."""
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_run ON run_events(run_id,id);
                CREATE TABLE IF NOT EXISTS custom_scenarios (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')
        with self.connect() as con:
            interrupted = [json.loads(row[0]) for row in con.execute('SELECT payload FROM runs')]
        for run in interrupted:
            if run['status'] == 'negotiating':
                run.update(status='interrupted', error='The server restarted. Start a new episode to coordinate again.')
                self.save(run)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.execute('PRAGMA journal_mode=WAL')
            with con:
                yield con
        finally:
            con.close()

    def save(self, run):
        run['updated_at'] = now()
        payload = json.dumps(run)
        with self.connect() as con:
            con.execute('INSERT OR REPLACE INTO runs VALUES (?,?)', (run['id'], payload))
            cur = con.execute('INSERT INTO run_events(run_id,payload,created_at) VALUES (?,?,?)',
                              (run['id'], payload, now()))
            return cur.lastrowid

    def get(self, run_id):
        with self.connect() as con:
            row = con.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self):
        with self.connect() as con:
            rows = con.execute('SELECT payload FROM runs ORDER BY rowid DESC LIMIT 100').fetchall()
        return [json.loads(r[0]) for r in rows]

    def events(self, run_id, after=0):
        with self.connect() as con:
            rows = con.execute('SELECT id,payload FROM run_events WHERE run_id=? AND id>? ORDER BY id',
                               (run_id, after)).fetchall()
        return [(r[0], json.loads(r[1])) for r in rows]

    def save_scenario(self, scenario):
        with self.connect() as con:
            con.execute('INSERT OR REPLACE INTO custom_scenarios VALUES (?,?)',
                        (scenario['id'], json.dumps(scenario)))

    def scenarios(self):
        with self.connect() as con:
            return [json.loads(r[0]) for r in con.execute('SELECT payload FROM custom_scenarios ORDER BY rowid')]
