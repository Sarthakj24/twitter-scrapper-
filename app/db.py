"""SQLite storage. No ORM — plain stdlib sqlite3.

Two tables:
  seen  — every tweet we've processed (dedup key = tweet id).
  kv    — small key/value store for the cached user id and the *rotating*
          OAuth refresh token (X rotates refresh tokens on every use, so we
          persist the latest one rather than trusting a static env value).
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .config import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    id          TEXT PRIMARY KEY,
    created_at  TEXT,
    is_hiring   INTEGER,
    emailed     INTEGER DEFAULT 0,
    data        TEXT
);
CREATE INDEX IF NOT EXISTS idx_seen_pending
    ON seen (is_hiring, emailed);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(config.db_path)) or ".", exist_ok=True)
    conn = sqlite3.connect(config.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


# --- key/value helpers -----------------------------------------------------

def kv_get(key: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def kv_set(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# --- seen tweets -----------------------------------------------------------

def latest_seen_id() -> Optional[str]:
    """Highest tweet id we've stored. Tweet ids are monotonic snowflakes, so
    string ordering by length-then-value == chronological order. We compare as
    integers to be safe."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM seen ORDER BY CAST(id AS INTEGER) DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None


def has_seen(tweet_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM seen WHERE id = ?", (tweet_id,)).fetchone()
        return row is not None


def upsert_tweet(tweet_id: str, created_at: str, is_hiring: bool, data: dict[str, Any]) -> None:
    """Insert (or ignore if already present). Dedup on id."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen (id, created_at, is_hiring, emailed, data) "
            "VALUES (?, ?, ?, 0, ?)",
            (tweet_id, created_at, 1 if is_hiring else 0, json.dumps(data)),
        )


def pending_digest() -> list[dict[str, Any]]:
    """Hiring tweets not yet emailed, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, data FROM seen "
            "WHERE is_hiring = 1 AND emailed = 0 "
            "ORDER BY CAST(id AS INTEGER) DESC"
        ).fetchall()
    out = []
    for r in rows:
        rec = json.loads(r["data"])
        rec["id"] = r["id"]
        rec["created_at"] = r["created_at"]
        out.append(rec)
    return out


def mark_emailed(ids: list[str]) -> None:
    if not ids:
        return
    with get_conn() as conn:
        conn.executemany("UPDATE seen SET emailed = 1 WHERE id = ?", [(i,) for i in ids])
