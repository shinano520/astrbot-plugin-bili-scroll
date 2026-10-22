"""SQLite 仓储。所有表结构在这里定义。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable

import aiosqlite

from ..domain.models import Profile, Subscriber, Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    bvid        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    author      TEXT,
    pubdate     INTEGER,
    verdict     TEXT,
    reason      TEXT,
    keywords    TEXT,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_first ON seen(first_seen);

CREATE TABLE IF NOT EXISTS quota (
    date        TEXT PRIMARY KEY,
    picked      INTEGER DEFAULT 0,
    rounds      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subscribers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    opted_in    INTEGER DEFAULT 1,
    opt_in_at   INTEGER NOT NULL,
    opt_out_at  INTEGER,
    enabled     INTEGER DEFAULT 1,
    UNIQUE(scope, target_id)
);
CREATE INDEX IF NOT EXISTS idx_sub_target ON subscribers(target_id);

CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    weight      REAL DEFAULT 0,
    updated_at  INTEGER NOT NULL,
    UNIQUE(scope, target_id, keyword)
);
CREATE INDEX IF NOT EXISTS idx_profile_target ON profiles(target_id);

CREATE TABLE IF NOT EXISTS push_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid         TEXT NOT NULL,
    scope        TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    probability  REAL,
    sent_at      INTEGER NOT NULL,
    window_end   INTEGER NOT NULL,
    feedback     TEXT,
    feedback_at  INTEGER,
    weight       REAL
);
CREATE INDEX IF NOT EXISTS idx_push_pending ON push_log(feedback);
CREATE INDEX IF NOT EXISTS idx_push_target ON push_log(target_id, sent_at);

CREATE TABLE IF NOT EXISTS push_quota (
    date        TEXT NOT NULL,
    scope       TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    count       INTEGER DEFAULT 0,
    PRIMARY KEY (date, scope, target_id)
);

CREATE TABLE IF NOT EXISTS own_comments (
    rpid         TEXT PRIMARY KEY,
    root_rpid    TEXT,
    bvid         TEXT NOT NULL,
    content      TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    verified     INTEGER DEFAULT 0,
    verified_at  INTEGER,
    replied      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_own_created ON own_comments(created_at);
CREATE INDEX IF NOT EXISTS idx_own_bvid ON own_comments(bvid);

CREATE TABLE IF NOT EXISTS transcriptions (
    bvid        TEXT PRIMARY KEY,
    duration    INTEGER,
    text        TEXT NOT NULL,
    model       TEXT,
    created_at  INTEGER NOT NULL
);
"""


def today_str() -> str:
    return time.strftime("%Y-%m-%d")


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage 未初始化")
        return self._db

    async def is_seen(self, bvid: str) -> bool:
        async with self.db.execute("SELECT 1 FROM seen WHERE bvid=?", (bvid,)) as cur:
            return await cur.fetchone() is not None

    async def mark_seen(self, v: Verdict, video_meta: dict | None = None) -> None:
        now = int(time.time())
        meta = video_meta or {}
        await self.db.execute(
            """INSERT INTO seen (bvid,title,author,pubdate,verdict,reason,keywords,first_seen,last_seen)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(bvid) DO UPDATE SET verdict=excluded.verdict,
                 reason=excluded.reason, last_seen=excluded.last_seen""",
            (
                v.bvid,
                meta.get("title", ""),
                meta.get("author", ""),
                meta.get("pubdate", 0),
                "worth" if v.worth else "skip",
                v.reason,
                json.dumps(meta.get("keywords", []), ensure_ascii=False),
                now,
                now,
            ),
        )
        await self.db.commit()

    async def quota_today(self) -> tuple[int, int]:
        async with self.db.execute(
            "SELECT picked, rounds FROM quota WHERE date=?", (today_str(),)
        ) as cur:
            row = await cur.fetchone()
        return (row["picked"], row["rounds"]) if row else (0, 0)

    async def incr_quota(self, picked: int = 0, rounds: int = 1) -> None:
        await self.db.execute(
            """INSERT INTO quota (date,picked,rounds) VALUES (?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                 picked=quota.picked+excluded.picked,
                 rounds=quota.rounds+excluded.rounds""",
            (today_str(), picked, rounds),
        )
        await self.db.commit()

    async def upsert_subscriber(self, scope: str, target_id: str, opted_in: bool) -> None:
        now = int(time.time())
        if opted_in:
            await self.db.execute(
                """INSERT INTO subscribers (scope,target_id,opted_in,opt_in_at,enabled)
                   VALUES (?,?,1,?,1)
                   ON CONFLICT(scope,target_id) DO UPDATE SET
                     opted_in=1, opt_in_at=excluded.opt_in_at, opt_out_at=NULL, enabled=1""",
                (scope, target_id, now),
            )
        else:
            await self.db.execute(
                "UPDATE subscribers SET opted_in=0, opt_out_at=? WHERE scope=? AND target_id=?",
                (now, scope, target_id),
            )
        await self.db.commit()

    async def list_subscribers(self) -> list[Subscriber]:
        async with self.db.execute(
            "SELECT scope,target_id,opted_in,enabled FROM subscribers WHERE opted_in=1 AND enabled=1"
        ) as cur:
            rows = await cur.fetchall()
        return [
            Subscriber(
                scope=r["scope"],
                target_id=r["target_id"],
                opted_in=bool(r["opted_in"]),
                enabled=bool(r["enabled"]),
            )
            for r in rows
        ]

    async def get_subscriber(self, scope: str, target_id: str) -> Subscriber | None:
        async with self.db.execute(
            "SELECT scope,target_id,opted_in,enabled FROM subscribers WHERE scope=? AND target_id=?",
            (scope, target_id),
        ) as cur:
            r = await cur.fetchone()
        if not r:
            return None
        return Subscriber(
            scope=r["scope"],
            target_id=r["target_id"],
            opted_in=bool(r["opted_in"]),
            enabled=bool(r["enabled"]),
        )

    async def get_profile(self, scope: str, target_id: str) -> Profile:
        async with self.db.execute(
            "SELECT keyword,weight FROM profiles WHERE scope=? AND target_id=?",
            (scope, target_id),
        ) as cur:
            rows = await cur.fetchall()
        return Profile(
            scope=scope,
            target_id=target_id,
            entries={r["keyword"]: r["weight"] for r in rows},
        )

    async def add_weight(
        self, scope: str, target_id: str, keywords: Iterable[str], delta: float
    ) -> None:
        now = int(time.time())
        for kw in keywords:
            kw = (kw or "").strip()
            if not kw:
                continue
            await self.db.execute(
                """INSERT INTO profiles (scope,target_id,keyword,weight,updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(scope,target_id,keyword) DO UPDATE SET
                     weight=profiles.weight+excluded.weight, updated_at=excluded.updated_at""",
                (scope, target_id, kw, delta, now),
            )
        await self.db.commit()

    async def set_weight(self, scope: str, target_id: str, keyword: str, weight: float) -> None:
        now = int(time.time())
        await self.db.execute(
            """INSERT INTO profiles (scope,target_id,keyword,weight,updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(scope,target_id,keyword) DO UPDATE SET
                 weight=excluded.weight, updated_at=excluded.updated_at""",
            (scope, target_id, keyword, weight, now),
        )
        await self.db.commit()

    async def decay_profiles(self, half_life_days: int) -> int:
        cutoff = int(time.time()) - half_life_days * 86400
        cur = await self.db.execute(
            "UPDATE profiles SET weight=weight*0.5, updated_at=? WHERE updated_at < ? AND weight != 0",
            (int(time.time()), cutoff),
        )
        await self.db.commit()
        return cur.rowcount or 0

    async def trim_profile(self, scope: str, target_id: str, max_keywords: int) -> None:
        async with self.db.execute(
            "SELECT COUNT(*) AS n FROM profiles WHERE scope=? AND target_id=?",
            (scope, target_id),
        ) as cur:
            row = await cur.fetchone()
        total = row["n"] if row else 0
        if total <= max_keywords:
            return
        await self.db.execute(
            """DELETE FROM profiles WHERE id IN (
                 SELECT id FROM profiles WHERE scope=? AND target_id=?
                 ORDER BY ABS(weight) ASC, updated_at ASC LIMIT ?
               )""",
            (scope, target_id, total - max_keywords),
        )
        await self.db.commit()

    async def log_push(
        self, bvid: str, scope: str, target_id: str, probability: float, window_seconds: int
    ) -> int:
        now = int(time.time())
        cur = await self.db.execute(
            """INSERT INTO push_log (bvid,scope,target_id,probability,sent_at,window_end)
               VALUES (?,?,?,?,?,?)""",
            (bvid, scope, target_id, probability, now, now + window_seconds),
        )
        await self.db.commit()
        return cur.lastrowid or 0

    async def sent_today(self, scope: str, target_id: str) -> int:
        async with self.db.execute(
            "SELECT count FROM push_quota WHERE date=? AND scope=? AND target_id=?",
            (today_str(), scope, target_id),
        ) as cur:
            row = await cur.fetchone()
        return row["count"] if row else 0

    async def incr_push_quota(self, scope: str, target_id: str) -> None:
        await self.db.execute(
            """INSERT INTO push_quota (date,scope,target_id,count) VALUES (?,?,?,1)
               ON CONFLICT(date,scope,target_id) DO UPDATE SET count=push_quota.count+1""",
            (today_str(), scope, target_id),
        )
        await self.db.commit()

    async def last_sent_at(self, scope: str, target_id: str) -> int | None:
        async with self.db.execute(
            "SELECT sent_at FROM push_log WHERE scope=? AND target_id=? ORDER BY sent_at DESC LIMIT 1",
            (scope, target_id),
        ) as cur:
            row = await cur.fetchone()
        return row["sent_at"] if row else None

    async def pending_window(self, scope: str, target_id: str, now: int) -> dict | None:
        async with self.db.execute(
            """SELECT id,bvid,sent_at,window_end FROM push_log
               WHERE scope=? AND target_id=? AND feedback IS NULL AND window_end >= ?
               ORDER BY sent_at DESC LIMIT 1""",
            (scope, target_id, now),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def close_window(self, push_id: int, feedback: str, weight: float) -> None:
        await self.db.execute(
            "UPDATE push_log SET feedback=?, feedback_at=?, weight=? WHERE id=?",
            (feedback, int(time.time()), weight, push_id),
        )
        await self.db.commit()

    async def expire_windows(self, now: int) -> list[dict]:
        async with self.db.execute(
            "SELECT id,scope,target_id,bvid FROM push_log WHERE feedback IS NULL AND window_end < ?",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def save_own_comment(
        self, rpid: str, root_rpid: str | None, bvid: str, content: str
    ) -> None:
        await self.db.execute(
            """INSERT INTO own_comments (rpid,root_rpid,bvid,content,created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(rpid) DO NOTHING""",
            (rpid, root_rpid, bvid, content, int(time.time())),
        )
        await self.db.commit()

    async def commented_bvid(self, bvid: str) -> bool:
        async with self.db.execute(
            "SELECT 1 FROM own_comments WHERE bvid=? LIMIT 1", (bvid,)
        ) as cur:
            return await cur.fetchone() is not None

    async def list_own_comments(self, since_days: int = 7) -> list[dict]:
        cutoff = int(time.time()) - since_days * 86400
        async with self.db.execute(
            "SELECT * FROM own_comments WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def mark_replied(self, rpid: str) -> None:
        await self.db.execute("UPDATE own_comments SET replied=1 WHERE rpid=?", (rpid,))
        await self.db.commit()

    async def list_unverified(self) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM own_comments WHERE verified=0 AND rpid != 'unknown'"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def set_verified(self, rpid: str, status: int) -> None:
        await self.db.execute(
            "UPDATE own_comments SET verified=?, verified_at=? WHERE rpid=?",
            (status, int(time.time()), rpid),
        )
        await self.db.commit()

    async def get_transcription(self, bvid: str) -> str | None:
        async with self.db.execute("SELECT text FROM transcriptions WHERE bvid=?", (bvid,)) as cur:
            row = await cur.fetchone()
        return row["text"] if row else None

    async def save_transcription(self, bvid: str, duration: int, text: str, model: str) -> None:
        await self.db.execute(
            """INSERT INTO transcriptions (bvid,duration,text,model,created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(bvid) DO UPDATE SET text=excluded.text, model=excluded.model""",
            (bvid, duration, text, model, int(time.time())),
        )
        await self.db.commit()
