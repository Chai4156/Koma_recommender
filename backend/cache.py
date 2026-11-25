"""Disk-backed SQLite intent cache, session state, quota tracking, feedback, and rate limiting."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent


def default_state_db_path() -> str:
    return (
        os.environ.get("CACHE_DB_PATH")
        or os.environ.get("STATE_DB_PATH")
        or str(BASE_DIR / "state_v1.sqlite")
    )


class StateStore:
    """Thread-safe SQLite store for intent cache, sessions, Gemini daily quota, and feedback."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or default_state_db_path()
        self._lock = threading.RLock()
        self._initialized_paths: set[str] = set()

    @property
    def db_path(self) -> str:
        env_path = os.environ.get("CACHE_DB_PATH") or os.environ.get("STATE_DB_PATH")
        return env_path or self._db_path

    def set_db_path(self, db_path: str) -> None:
        with self._lock:
            self._db_path = db_path
            self._initialized_paths.discard(db_path)

    def _connect(self) -> sqlite3.Connection:
        path = self.db_path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        con = sqlite3.connect(path, timeout=15.0, isolation_level=None)
        if path not in self._initialized_paths:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS intent_cache (
                    cache_key TEXT PRIMARY KEY,
                    norm_text TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    intent_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS intent_sessions (
                    intent_id TEXT PRIMARY KEY,
                    norm_text TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    base_intent_json TEXT NOT NULL,
                    seed_titles_json TEXT NOT NULL,
                    degraded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_quota (
                    day_utc TEXT PRIMARY KEY,
                    calls INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent_id TEXT NOT NULL,
                    series_id INTEGER NOT NULL,
                    vote INTEGER NOT NULL CHECK (vote IN (1, -1)),
                    note TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._initialized_paths.add(path)
        return con

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _today_utc() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def make_cache_key(norm_text: str, prompt_version: str) -> str:
        return f"{prompt_version}:{norm_text}"

    @staticmethod
    def make_intent_id(norm_text: str, prompt_version: str, seed_titles: list[str]) -> str:
        payload = json.dumps(
            {
                "v": prompt_version,
                "t": norm_text,
                "s": [s.strip() for s in seed_titles if s and s.strip()],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def get_cached_intent(self, norm_text: str, prompt_version: str) -> dict[str, Any] | None:
        key = self.make_cache_key(norm_text, prompt_version)
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT intent_json FROM intent_cache WHERE cache_key = ?",
                    (key,),
                ).fetchone()
                if not row:
                    return None
                return json.loads(row[0])
            finally:
                con.close()

    def save_cached_intent(
        self,
        norm_text: str,
        prompt_version: str,
        intent: dict[str, Any],
    ) -> None:
        """Persist successful LLM intent extraction. Never call on degraded fallback."""
        key = self.make_cache_key(norm_text, prompt_version)
        encoded = json.dumps(intent, ensure_ascii=False)
        now = self._now_iso()
        with self._lock:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    """
                    INSERT INTO intent_cache(cache_key, norm_text, prompt_version, intent_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        intent_json = excluded.intent_json,
                        created_at = excluded.created_at
                    """,
                    (key, norm_text, prompt_version, encoded, now),
                )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            finally:
                con.close()

    def save_session(
        self,
        norm_text: str,
        prompt_version: str,
        base_intent: dict[str, Any],
        seed_titles: list[str],
        degraded: bool,
    ) -> str:
        intent_id = self.make_intent_id(norm_text, prompt_version, seed_titles)
        encoded_intent = json.dumps(base_intent, ensure_ascii=False)
        encoded_seeds = json.dumps(seed_titles, ensure_ascii=False)
        now = self._now_iso()
        with self._lock:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    """
                    INSERT INTO intent_sessions(
                        intent_id, norm_text, prompt_version, base_intent_json,
                        seed_titles_json, degraded, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(intent_id) DO UPDATE SET
                        base_intent_json = excluded.base_intent_json,
                        seed_titles_json = excluded.seed_titles_json,
                        degraded = excluded.degraded,
                        created_at = excluded.created_at
                    """,
                    (
                        intent_id,
                        norm_text,
                        prompt_version,
                        encoded_intent,
                        encoded_seeds,
                        1 if degraded else 0,
                        now,
                    ),
                )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            finally:
                con.close()
        return intent_id

    def get_session(self, intent_id: str) -> dict[str, Any] | None:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    """
                    SELECT intent_id, norm_text, prompt_version, base_intent_json,
                           seed_titles_json, degraded
                    FROM intent_sessions WHERE intent_id = ?
                    """,
                    (intent_id,),
                ).fetchone()
                if not row:
                    return None
                return {
                    "intent_id": row[0],
                    "norm_text": row[1],
                    "prompt_version": row[2],
                    "base_intent": json.loads(row[3]),
                    "seed_titles": json.loads(row[4]),
                    "degraded": bool(row[5]),
                }
            finally:
                con.close()

    def get_cache_size(self) -> int:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute("SELECT COUNT(*) FROM intent_cache").fetchone()
                return int(row[0]) if row else 0
            finally:
                con.close()

    def try_consume_gemini_quota(self, daily_cap: int) -> bool:
        """Atomically check and increment today's UTC Gemini call counter."""
        if daily_cap <= 0:
            return False
        day = self._today_utc()
        with self._lock:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute(
                    "SELECT calls FROM daily_quota WHERE day_utc = ?",
                    (day,),
                ).fetchone()
                current = int(row[0]) if row else 0
                if current >= daily_cap:
                    con.execute("ROLLBACK")
                    return False
                con.execute(
                    """
                    INSERT INTO daily_quota(day_utc, calls) VALUES (?, 1)
                    ON CONFLICT(day_utc) DO UPDATE SET calls = calls + 1
                    """,
                    (day,),
                )
                con.execute("COMMIT")
                return True
            except Exception:
                con.execute("ROLLBACK")
                raise
            finally:
                con.close()

    def get_daily_gemini_calls(self) -> int:
        day = self._today_utc()
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT calls FROM daily_quota WHERE day_utc = ?",
                    (day,),
                ).fetchone()
                return int(row[0]) if row else 0
            finally:
                con.close()

    def append_feedback(
        self,
        intent_id: str,
        series_id: int,
        vote: int,
        note: str | None = None,
    ) -> int:
        now = self._now_iso()
        with self._lock:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                cur = con.execute(
                    """
                    INSERT INTO feedback(intent_id, series_id, vote, note, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (intent_id, series_id, vote, note, now),
                )
                row_id = int(cur.lastrowid)
                con.execute("COMMIT")
                return row_id
            except Exception:
                con.execute("ROLLBACK")
                raise
            finally:
                con.close()

    def list_feedback(self, intent_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            con = self._connect()
            try:
                if intent_id is not None:
                    rows = con.execute(
                        "SELECT id, intent_id, series_id, vote, note, created_at FROM feedback WHERE intent_id = ? ORDER BY id ASC",
                        (intent_id,),
                    ).fetchall()
                else:
                    rows = con.execute(
                        "SELECT id, intent_id, series_id, vote, note, created_at FROM feedback ORDER BY id ASC"
                    ).fetchall()
                return [
                    {
                        "id": r[0],
                        "intent_id": r[1],
                        "series_id": r[2],
                        "vote": r[3],
                        "note": r[4],
                        "created_at": r[5],
                    }
                    for r in rows
                ]
            finally:
                con.close()


class RateLimiter:
    """Thread-safe sliding-window per-IP rate limiter."""

    def __init__(self, default_per_min: int = 20) -> None:
        self._default_per_min = default_per_min
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def max_per_min(self) -> int:
        raw = os.environ.get("RATE_LIMIT_PER_MIN")
        if raw is not None:
            try:
                return max(1, int(raw))
            except ValueError:
                pass
        return self._default_per_min

    def allow(self, key: str) -> bool:
        limit = self.max_per_min()
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


state_store = StateStore()
rate_limiter = RateLimiter()
