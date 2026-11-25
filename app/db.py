"""SQLite store. One profile per company, findings as rows, full history kept."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  legal_name TEXT, website TEXT, domain TEXT, parent TEXT, subsidiaries TEXT,
  countries TEXT, industry TEXT, size TEXT,
  relationship TEXT, lead_source TEXT, product_focus TEXT, region TEXT,
  in_radar INTEGER DEFAULT 0, radar_added_at TEXT, radar_removed_at TEXT,
  identity_confirmed INTEGER DEFAULT 0,
  summary_json TEXT, source_list_json TEXT,
  last_research_at TEXT, moved_at TEXT, moved_note TEXT,
  created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_accounts_domain ON accounts(domain) WHERE domain IS NOT NULL AND domain <> '';

CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL,
  name TEXT NOT NULL, title TEXT, linkedin_url TEXT, profile_url TEXT,
  tenure TEXT, background TEXT, priorities TEXT, themes TEXT,
  origin TEXT, rep_status TEXT DEFAULT 'proposed', rep_note TEXT,
  confidence TEXT, sources_json TEXT,
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL,
  section INTEGER NOT NULL,
  statement TEXT NOT NULL,
  type TEXT NOT NULL,
  sources_json TEXT DEFAULT '[]',
  date_published TEXT, date_found TEXT, last_seen TEXT,
  confidence TEXT, reasoning TEXT, so_what TEXT, product TEXT,
  entity TEXT, person_id INTEGER,
  linked_question TEXT,
  status TEXT DEFAULT 'New',
  origin TEXT,
  rep_verdict TEXT, rep_correction TEXT, rep_verdict_by TEXT, rep_verdict_at TEXT,
  supersedes_id INTEGER, superseded_by_id INTEGER, conflicts_with_id INTEGER,
  topic TEXT,
  run_id INTEGER, created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_findings_account ON findings(account_id, section);

CREATE TABLE IF NOT EXISTS finding_events (
  id INTEGER PRIMARY KEY, finding_id INTEGER, account_id INTEGER,
  event TEXT, detail TEXT, by_user TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL,
  text TEXT NOT NULL,
  kind TEXT,
  product TEXT, stage TEXT, theme TEXT,
  person_id INTEGER, person_name TEXT,
  finding_id INTEGER, prep_id INTEGER,
  status TEXT DEFAULT 'open', answer TEXT, answered_at TEXT, answered_by TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS preps (
  id INTEGER PRIMARY KEY,
  account_id INTEGER,
  company_input TEXT,
  products TEXT, stages_json TEXT, meeting_date TEXT,
  attendees_json TEXT, good_result TEXT, known_text TEXT,
  lead_source_json TEXT, partner TEXT, region TEXT, other_stage_text TEXT,
  candidates_json TEXT,
  status TEXT, run_id INTEGER,
  checkback_json TEXT,
  created_by TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS briefs (
  id INTEGER PRIMARY KEY,
  prep_id INTEGER NOT NULL, version INTEGER NOT NULL,
  content_json TEXT, snapshot_json TEXT, note TEXT,
  created_by TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS debriefs (
  id INTEGER PRIMARY KEY, prep_id INTEGER, account_id INTEGER,
  learned TEXT, people_json TEXT, answers_json TEXT,
  next_step TEXT, next_by TEXT, created_by TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, account_id INTEGER, prep_id INTEGER,
  kind TEXT, status TEXT, steps_json TEXT, message TEXT,
  used_apollo INTEGER DEFAULT 0, apollo_note TEXT,
  error TEXT, cost_usd REAL DEFAULT 0, started_at TEXT, finished_at TEXT,
  created_by TEXT
);

CREATE TABLE IF NOT EXISTS cost_log (
  id INTEGER PRIMARY KEY, run_id INTEGER, account_id INTEGER, step TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cache_write INTEGER, cache_read INTEGER,
  searches INTEGER, usd REAL, created_at TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, account_id INTEGER, finding_id INTEGER, run_id INTEGER,
  what TEXT, why TEXT, angle TEXT, priority TEXT, product TEXT,
  source_url TEXT, source_name TEXT, event_date TEXT,
  status TEXT DEFAULT 'open', dismiss_reason TEXT, dismissed_by TEXT, dismissed_at TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY, account_id INTEGER, text TEXT, author TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS timeline (
  id INTEGER PRIMARY KEY, account_id INTEGER, run_id INTEGER, kind TEXT, text TEXT,
  link TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS digests (
  id INTEGER PRIMARY KEY, run_at TEXT, status TEXT, sent_to TEXT, delivery TEXT,
  html_path TEXT, summary TEXT
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx():
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init():
    with tx() as c:
        c.executescript(SCHEMA)


def q(sql, args=()):
    with tx() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    with tx() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def insert(table, row: dict):
    cols = ",".join(row)
    marks = ",".join("?" for _ in row)
    return ex(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


def update(table, id_, fields: dict):
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    ex(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), id_))


def get(table, id_):
    return q1(f"SELECT * FROM {table} WHERE id=?", (id_,))


def jload(s, default=None):
    if s in (None, ""):
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def jdump(o):
    return json.dumps(o, ensure_ascii=False)


def setting(key, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def timeline(account_id, kind, text, run_id=None, link=None):
    insert("timeline", {"account_id": account_id, "run_id": run_id, "kind": kind, "text": text,
                        "link": link, "created_at": now()})


def finding_event(finding_id, account_id, event, detail="", by_user=None):
    insert("finding_events", {"finding_id": finding_id, "account_id": account_id, "event": event,
                              "detail": detail, "by_user": by_user, "created_at": now()})
