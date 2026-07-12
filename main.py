# -*- coding: utf-8 -*-
"""
Puraudhia Manager
運営専用Discord管理Bot（discord.py 2.x）

【固定設定済み】
GUILD_ID = 1458016711344263170
ADMIN_ROLE_ID = 1468161318635962451
MANAGEMENT_LOG_CHANNEL_ID = 1523582826623008863
JOIN_LEAVE_LOG_CHANNEL_ID = 1472429718144811050
DASHBOARD_CHANNEL_ID = 1467734039883677856

【主な機能】
- VC入室・退出・移動・滞在時間の自動記録
- 今日 / 7日 / 30日の利用統計
- 最大同時接続・利用者数・総滞在時間・開始回数
- 加入 / 退出 / Kick / BAN / Unban の管理ログ
- ロール・ニックネーム・タイムアウト変更ログ
- メンバーカルテ
- 面接記録
- 警告記録
- 管理メモ
- 新規VC未参加一覧
- VC休眠メンバー一覧
- 自動更新ダッシュボード
- CSV出力
- 全Slashコマンド管理者専用・Ephemeral表示

【重要】
- 天真爛漫Botとは別トークン・別DBで動作します。
- 一般メンバーへDMや公開返信はしません。
- Kick/BAN判定にはBotの「監査ログを表示」権限が必要です。
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# 固定設定
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

GUILD_ID = 1458016711344263170
ADMIN_ROLE_ID = 1468161318635962451
MANAGEMENT_LOG_CHANNEL_ID = 1523582826623008863
JOIN_LEAVE_LOG_CHANNEL_ID = 1472429718144811050
DASHBOARD_CHANNEL_ID = 1467734039883677856

DB_PATH = Path(os.getenv("DB_PATH", "puraudhia_manager.db"))
TIMEZONE_NAME = "Asia/Tokyo"
TZ = ZoneInfo(TIMEZONE_NAME)

DASHBOARD_UPDATE_MINUTES = 5
DAILY_SUMMARY_HOUR = 0
DAILY_SUMMARY_MINUTE = 5

EXCLUDED_VOICE_CATEGORY_IDS: set[int] = set()
EXCLUDED_USER_IDS: set[int] = set()

EMBED_COLOR = discord.Color.from_rgb(137, 107, 255)


# =========================================================
# 放課後ミッション設定
# =========================================================

MISSION_CHECK_SECONDS = 60

MISSION_POOL = [
    {
        "code": "vc15",
        "name": "放課後に顔を出そう",
        "description": "VCで合計15分過ごす",
        "kind": "vc_minutes",
        "target": 15,
        "reward": 50,
        "difficulty": "EASY",
    },
    {
        "code": "vc30",
        "name": "放課後の雑談",
        "description": "VCで合計30分過ごす",
        "kind": "vc_minutes",
        "target": 30,
        "reward": 100,
        "difficulty": "NORMAL",
    },
    {
        "code": "vc60",
        "name": "たっぷり放課後",
        "description": "VCで合計60分過ごす",
        "kind": "vc_minutes",
        "target": 60,
        "reward": 180,
        "difficulty": "HARD",
    },
    {
        "code": "group3_20",
        "name": "三人寄れば放課後",
        "description": "3人以上いる同じVCで20分過ごす",
        "kind": "group_minutes",
        "target": 20,
        "required_people": 3,
        "reward": 150,
        "difficulty": "NORMAL",
    },
    {
        "code": "group4_15",
        "name": "放課後ミニパーティー",
        "description": "4人以上いる同じVCで15分過ごす",
        "kind": "group_minutes",
        "target": 15,
        "required_people": 4,
        "reward": 180,
        "difficulty": "HARD",
    },
    {
        "code": "group5_10",
        "name": "賑やかな教室",
        "description": "5人以上いる同じVCで10分過ごす",
        "kind": "group_minutes",
        "target": 10,
        "required_people": 5,
        "reward": 220,
        "difficulty": "RARE",
    },
    {
        "code": "game20",
        "name": "放課後ゲーム部",
        "description": "ゲーム系VCで20分過ごす",
        "kind": "game_minutes",
        "target": 20,
        "reward": 130,
        "difficulty": "NORMAL",
    },
    {
        "code": "night30",
        "name": "居残り補習",
        "description": "22時〜翌2時の間にVCで30分過ごす",
        "kind": "night_minutes",
        "target": 30,
        "reward": 180,
        "difficulty": "HARD",
    },
]

GAME_CHANNEL_KEYWORDS = (
    "ゲーム", "game", "麻雀", "マイクラ", "原神",
    "valorant", "apex", "モンハン", "スプラ",
)

# =========================================================
# ログ
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("puraudhia-manager")

# =========================================================
# 時刻補助
# =========================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def to_iso(dt: Optional[datetime] = None) -> str:
    return (dt or utcnow()).astimezone(timezone.utc).isoformat()

def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None

def local_now() -> datetime:
    return datetime.now(TZ)

def local_day_bounds(days_ago: int = 0) -> tuple[datetime, datetime]:
    now = local_now()
    target = (now - timedelta(days=days_ago)).date()
    start_local = datetime.combine(target, datetime.min.time(), tzinfo=TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

def range_bounds(days: int) -> tuple[datetime, datetime]:
    now = local_now()
    start_date = (now - timedelta(days=days - 1)).date()
    start_local = datetime.combine(start_date, datetime.min.time(), tzinfo=TZ)
    return start_local.astimezone(timezone.utc), utcnow()

def fmt_dt(dt: Optional[datetime], style: str = "f") -> str:
    if not dt:
        return "記録なし"
    return f"<t:{int(dt.timestamp())}:{style}>"

def fmt_duration(seconds: int | float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}日{hours}時間{minutes}分"
    if hours:
        return f"{hours}時間{minutes}分"
    return f"{minutes}分"

def safe_text(value: Optional[str], limit: int = 1000) -> str:
    text = (value or "").strip()
    return text[:limit] if text else "なし"

# =========================================================
# DB
# =========================================================

class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS members (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    display_name TEXT,
                    joined_at TEXT,
                    first_seen_at TEXT NOT NULL,
                    left_at TEXT,
                    first_vc_at TEXT,
                    last_vc_at TEXT,
                    last_activity_at TEXT,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS voice_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    channel_name TEXT,
                    category_id INTEGER,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_seconds INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_voice_sessions_range
                ON voice_sessions(guild_id, started_at, ended_at);

                CREATE INDEX IF NOT EXISTS idx_voice_sessions_user
                ON voice_sessions(guild_id, user_id, started_at);

                CREATE TABLE IF NOT EXISTS voice_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    before_channel_id INTEGER,
                    after_channel_id INTEGER,
                    before_channel_name TEXT,
                    after_channel_name TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS concurrency_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    concurrent_users INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_concurrency_range
                ON concurrency_samples(guild_id, created_at);

                CREATE TABLE IF NOT EXISTS member_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id INTEGER,
                    details TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_member_events_range
                ON member_events(guild_id, event_type, created_at);

                CREATE TABLE IF NOT EXISTS interviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    interviewer_id INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    source TEXT,
                    ban_history TEXT,
                    same_gender_ok TEXT,
                    memo TEXT,
                    interviewed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_interviews_user
                ON interviews(guild_id, user_id, interviewed_at);

                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    resolved_at TEXT,
                    resolved_by INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_warnings_user
                ON warnings(guild_id, user_id, is_active);

                CREATE TABLE IF NOT EXISTS notes (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    updated_by INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );


                CREATE TABLE IF NOT EXISTS daily_missions (
                    guild_id INTEGER NOT NULL,
                    mission_date TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    mission_code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    target INTEGER NOT NULL,
                    required_people INTEGER NOT NULL DEFAULT 1,
                    reward INTEGER NOT NULL,
                    difficulty TEXT NOT NULL,
                    PRIMARY KEY (guild_id, mission_date, slot)
                );

                CREATE TABLE IF NOT EXISTS mission_progress (
                    guild_id INTEGER NOT NULL,
                    mission_date TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    mission_code TEXT NOT NULL,
                    progress_seconds INTEGER NOT NULL DEFAULT 0,
                    completed_at TEXT,
                    PRIMARY KEY (guild_id, mission_date, user_id, mission_code)
                );

                CREATE TABLE IF NOT EXISTS reward_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    mission_date TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    mission_code TEXT NOT NULL,
                    mission_name TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    completed_by INTEGER,
                    UNIQUE (guild_id, mission_date, user_id, mission_code)
                );

                CREATE INDEX IF NOT EXISTS idx_reward_queue_status
                ON reward_queue(guild_id, status, created_at);

                CREATE TABLE IF NOT EXISTS settings (
                    guild_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (guild_id, key)
                );
                """
            )

    async def run(self, func, *args):
        async with self._lock:
            return await asyncio.to_thread(func, *args)

    def _upsert_member(self, member: discord.Member) -> None:
        now = to_iso()
        joined = to_iso(member.joined_at) if member.joined_at else None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO members (
                    guild_id, user_id, username, display_name, joined_at,
                    first_seen_at, left_at, last_activity_at
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    username=excluded.username,
                    display_name=excluded.display_name,
                    joined_at=COALESCE(members.joined_at, excluded.joined_at),
                    left_at=NULL,
                    last_activity_at=excluded.last_activity_at
                """,
                (
                    member.guild.id,
                    member.id,
                    str(member),
                    member.display_name,
                    joined,
                    now,
                    now,
                ),
            )

    async def upsert_member(self, member: discord.Member) -> None:
        await self.run(self._upsert_member, member)

    def _mark_left(self, guild_id: int, user_id: int) -> None:
        now = to_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE members
                SET left_at=?, last_activity_at=?
                WHERE guild_id=? AND user_id=?
                """,
                (now, now, guild_id, user_id),
            )

    async def mark_left(self, guild_id: int, user_id: int) -> None:
        await self.run(self._mark_left, guild_id, user_id)

    def _start_voice_session(self, member: discord.Member, channel: discord.abc.GuildChannel) -> None:
        now = to_iso()
        category_id = getattr(channel, "category_id", None)
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM voice_sessions
                WHERE guild_id=? AND user_id=? AND ended_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (member.guild.id, member.id),
            ).fetchone()
            if existing:
                return

            conn.execute(
                """
                INSERT INTO voice_sessions (
                    guild_id, user_id, channel_id, channel_name,
                    category_id, started_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    member.guild.id,
                    member.id,
                    channel.id,
                    channel.name,
                    category_id,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE members
                SET first_vc_at=COALESCE(first_vc_at, ?),
                    last_vc_at=?,
                    last_activity_at=?
                WHERE guild_id=? AND user_id=?
                """,
                (now, now, now, member.guild.id, member.id),
            )

    async def start_voice_session(self, member: discord.Member, channel: discord.abc.GuildChannel) -> None:
        await self.run(self._start_voice_session, member, channel)

    def _end_voice_session(self, guild_id: int, user_id: int) -> int:
        now_dt = utcnow()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, started_at FROM voice_sessions
                WHERE guild_id=? AND user_id=? AND ended_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (guild_id, user_id),
            ).fetchone()
            if not row:
                return 0

            started = parse_dt(row["started_at"]) or now_dt
            duration = max(0, int((now_dt - started).total_seconds()))
            conn.execute(
                """
                UPDATE voice_sessions
                SET ended_at=?, duration_seconds=?
                WHERE id=?
                """,
                (to_iso(now_dt), duration, row["id"]),
            )
            conn.execute(
                """
                UPDATE members
                SET last_vc_at=?, last_activity_at=?
                WHERE guild_id=? AND user_id=?
                """,
                (to_iso(now_dt), to_iso(now_dt), guild_id, user_id),
            )
            return duration

    async def end_voice_session(self, guild_id: int, user_id: int) -> int:
        return await self.run(self._end_voice_session, guild_id, user_id)

    def _record_voice_event(
        self,
        guild_id: int,
        user_id: int,
        event_type: str,
        before: Optional[discord.abc.GuildChannel],
        after: Optional[discord.abc.GuildChannel],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO voice_events (
                    guild_id, user_id, event_type,
                    before_channel_id, after_channel_id,
                    before_channel_name, after_channel_name, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    event_type,
                    before.id if before else None,
                    after.id if after else None,
                    before.name if before else None,
                    after.name if after else None,
                    to_iso(),
                ),
            )

    async def record_voice_event(self, *args) -> None:
        await self.run(self._record_voice_event, *args)

    def _record_concurrency(self, guild_id: int, count: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO concurrency_samples (
                    guild_id, concurrent_users, created_at
                ) VALUES (?, ?, ?)
                """,
                (guild_id, count, to_iso()),
            )

    async def record_concurrency(self, guild_id: int, count: int) -> None:
        await self.run(self._record_concurrency, guild_id, count)

    def _add_member_event(
        self,
        guild_id: int,
        user_id: int,
        event_type: str,
        actor_id: Optional[int] = None,
        details: Optional[str] = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO member_events (
                    guild_id, user_id, event_type,
                    actor_id, details, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, event_type, actor_id, details, to_iso()),
            )

    async def add_member_event(self, *args) -> None:
        await self.run(self._add_member_event, *args)

    def _add_interview(
        self,
        guild_id: int,
        user_id: int,
        interviewer_id: int,
        result: str,
        source: str,
        ban_history: str,
        same_gender_ok: str,
        memo: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO interviews (
                    guild_id, user_id, interviewer_id, result, source,
                    ban_history, same_gender_ok, memo, interviewed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    interviewer_id,
                    result,
                    source,
                    ban_history,
                    same_gender_ok,
                    memo,
                    to_iso(),
                ),
            )

    async def add_interview(self, *args) -> None:
        await self.run(self._add_interview, *args)

    def _add_warning(self, guild_id: int, user_id: int, moderator_id: int, reason: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO warnings (
                    guild_id, user_id, moderator_id, reason, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, moderator_id, reason, to_iso()),
            )
            return int(cur.lastrowid)

    async def add_warning(self, *args) -> int:
        return await self.run(self._add_warning, *args)

    def _resolve_warning(self, guild_id: int, warning_id: int, resolver_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE warnings
                SET is_active=0, resolved_at=?, resolved_by=?
                WHERE guild_id=? AND id=? AND is_active=1
                """,
                (to_iso(), resolver_id, guild_id, warning_id),
            )
            return cur.rowcount > 0

    async def resolve_warning(self, *args) -> bool:
        return await self.run(self._resolve_warning, *args)

    def _set_note(self, guild_id: int, user_id: int, note: str, editor_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO notes (
                    guild_id, user_id, note, updated_by, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    note=excluded.note,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
                """,
                (guild_id, user_id, note, editor_id, to_iso()),
            )

    async def set_note(self, *args) -> None:
        await self.run(self._set_note, *args)

    def _get_setting(self, guild_id: int, key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE guild_id=? AND key=?",
                (guild_id, key),
            ).fetchone()
            return row["value"] if row else None

    async def get_setting(self, guild_id: int, key: str) -> Optional[str]:
        return await self.run(self._get_setting, guild_id, key)

    def _set_setting(self, guild_id: int, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (guild_id, key, value)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, key) DO UPDATE SET value=excluded.value
                """,
                (guild_id, key, value),
            )

    async def set_setting(self, guild_id: int, key: str, value: str) -> None:
        await self.run(self._set_setting, guild_id, key, value)

    def _stats(self, guild_id: int, start: datetime, end: datetime) -> dict[str, Any]:
        start_iso, end_iso = to_iso(start), to_iso(end)
        now = utcnow()
        with self.connect() as conn:
            sessions = conn.execute(
                """
                SELECT user_id, started_at, ended_at
                FROM voice_sessions
                WHERE guild_id=?
                  AND started_at < ?
                  AND (ended_at IS NULL OR ended_at > ?)
                """,
                (guild_id, end_iso, start_iso),
            ).fetchall()

            unique_users: set[int] = set()
            total_seconds = 0
            session_starts = 0
            for row in sessions:
                s = parse_dt(row["started_at"]) or start
                e = parse_dt(row["ended_at"]) or min(now, end)
                clipped_start = max(s, start)
                clipped_end = min(e, end)
                if clipped_end > clipped_start:
                    unique_users.add(int(row["user_id"]))
                    total_seconds += int((clipped_end - clipped_start).total_seconds())
                if start <= s < end:
                    session_starts += 1

            max_row = conn.execute(
                """
                SELECT MAX(concurrent_users) AS max_count
                FROM concurrency_samples
                WHERE guild_id=? AND created_at>=? AND created_at<?
                """,
                (guild_id, start_iso, end_iso),
            ).fetchone()

            counts = {}
            for event_type in ("join", "leave", "kick", "ban", "unban"):
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM member_events
                    WHERE guild_id=? AND event_type=?
                      AND created_at>=? AND created_at<?
                    """,
                    (guild_id, event_type, start_iso, end_iso),
                ).fetchone()
                counts[event_type] = int(row["c"] or 0)

            new_members = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM members
                WHERE guild_id=? AND joined_at>=? AND joined_at<?
                """,
                (guild_id, start_iso, end_iso),
            ).fetchone()["c"]

            new_vc = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM members
                WHERE guild_id=? AND joined_at>=? AND joined_at<?
                  AND first_vc_at IS NOT NULL
                  AND first_vc_at<?
                """,
                (guild_id, start_iso, end_iso, end_iso),
            ).fetchone()["c"]

            return {
                "unique_users": len(unique_users),
                "total_seconds": total_seconds,
                "session_starts": session_starts,
                "max_concurrent": int(max_row["max_count"] or 0),
                "joins": counts["join"],
                "leaves": counts["leave"],
                "kicks": counts["kick"],
                "bans": counts["ban"],
                "unbans": counts["unban"],
                "new_members": int(new_members or 0),
                "new_vc_members": int(new_vc or 0),
            }

    async def stats(self, guild_id: int, start: datetime, end: datetime) -> dict[str, Any]:
        return await self.run(self._stats, guild_id, start, end)

    def _member_card(self, guild_id: int, user_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            member = conn.execute(
                "SELECT * FROM members WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()

            vc = conn.execute(
                """
                SELECT
                    COUNT(*) AS sessions,
                    COALESCE(SUM(duration_seconds), 0) AS seconds,
                    MAX(COALESCE(ended_at, started_at)) AS last_vc
                FROM voice_sessions
                WHERE guild_id=? AND user_id=?
                """,
                (guild_id, user_id),
            ).fetchone()

            interview = conn.execute(
                """
                SELECT * FROM interviews
                WHERE guild_id=? AND user_id=?
                ORDER BY interviewed_at DESC LIMIT 1
                """,
                (guild_id, user_id),
            ).fetchone()

            warnings = conn.execute(
                """
                SELECT COUNT(*) AS c FROM warnings
                WHERE guild_id=? AND user_id=? AND is_active=1
                """,
                (guild_id, user_id),
            ).fetchone()["c"]

            note = conn.execute(
                "SELECT * FROM notes WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()

            return {
                "member": dict(member) if member else None,
                "vc_sessions": int(vc["sessions"] or 0),
                "vc_seconds": int(vc["seconds"] or 0),
                "last_vc": vc["last_vc"],
                "interview": dict(interview) if interview else None,
                "warning_count": int(warnings or 0),
                "note": dict(note) if note else None,
            }

    async def member_card(self, guild_id: int, user_id: int) -> dict[str, Any]:
        return await self.run(self._member_card, guild_id, user_id)

    def _warnings(self, guild_id: int, user_id: int, active_only: bool = True) -> list[dict[str, Any]]:
        with self.connect() as conn:
            query = "SELECT * FROM warnings WHERE guild_id=? AND user_id=?"
            params: list[Any] = [guild_id, user_id]
            if active_only:
                query += " AND is_active=1"
            query += " ORDER BY created_at DESC LIMIT 20"
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    async def warnings(self, guild_id: int, user_id: int, active_only: bool = True) -> list[dict[str, Any]]:
        return await self.run(self._warnings, guild_id, user_id, active_only)

    def _top_voice(self, guild_id: int, start: datetime, end: datetime, limit: int = 10):
        start_iso, end_iso = to_iso(start), to_iso(end)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, started_at, ended_at
                FROM voice_sessions
                WHERE guild_id=?
                  AND started_at < ?
                  AND (ended_at IS NULL OR ended_at > ?)
                """,
                (guild_id, end_iso, start_iso),
            ).fetchall()

        agg: dict[int, list[int]] = {}
        now = utcnow()
        for row in rows:
            uid = int(row["user_id"])
            s = max(parse_dt(row["started_at"]) or start, start)
            e = min(parse_dt(row["ended_at"]) or now, end)
            if e <= s:
                continue
            rec = agg.setdefault(uid, [0, 0])
            rec[0] += int((e - s).total_seconds())
            rec[1] += 1

        ranked = sorted(
            ((uid, values[0], values[1]) for uid, values in agg.items()),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:limit]

    async def top_voice(self, guild_id: int, start: datetime, end: datetime, limit: int = 10):
        return await self.run(self._top_voice, guild_id, start, end, limit)

    def _new_without_vc(self, guild_id: int, since: datetime, limit: int = 50):
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM members
                    WHERE guild_id=?
                      AND joined_at>=?
                      AND left_at IS NULL
                      AND first_vc_at IS NULL
                    ORDER BY joined_at DESC
                    LIMIT ?
                    """,
                    (guild_id, to_iso(since), limit),
                ).fetchall()
            ]

    async def new_without_vc(self, guild_id: int, since: datetime, limit: int = 50):
        return await self.run(self._new_without_vc, guild_id, since, limit)

    def _inactive_vc(self, guild_id: int, cutoff: datetime, limit: int = 50):
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM members
                    WHERE guild_id=?
                      AND left_at IS NULL
                      AND (last_vc_at IS NULL OR last_vc_at<?)
                    ORDER BY COALESCE(last_vc_at, joined_at, first_seen_at) ASC
                    LIMIT ?
                    """,
                    (guild_id, to_iso(cutoff), limit),
                ).fetchall()
            ]

    async def inactive_vc(self, guild_id: int, cutoff: datetime, limit: int = 50):
        return await self.run(self._inactive_vc, guild_id, cutoff, limit)

    def _export_rows(self, guild_id: int, kind: str):
        table_map = {
            "vc": """
                SELECT id, user_id, channel_name, started_at,
                       ended_at, duration_seconds
                FROM voice_sessions
                WHERE guild_id=?
                ORDER BY started_at DESC
            """,
            "面接": """
                SELECT id, user_id, interviewer_id, result, source,
                       ban_history, same_gender_ok, memo, interviewed_at
                FROM interviews
                WHERE guild_id=?
                ORDER BY interviewed_at DESC
            """,
            "警告": """
                SELECT id, user_id, moderator_id, reason, created_at,
                       is_active, resolved_at, resolved_by
                FROM warnings
                WHERE guild_id=?
                ORDER BY created_at DESC
            """,
            "メンバー": """
                SELECT user_id, username, display_name, joined_at,
                       left_at, first_vc_at, last_vc_at, last_activity_at
                FROM members
                WHERE guild_id=?
                ORDER BY joined_at DESC
            """,
        }
        if kind not in table_map:
            raise ValueError("不明な出力種別です")
        with self.connect() as conn:
            cur = conn.execute(table_map[kind], (guild_id,))
            return [d[0] for d in cur.description], cur.fetchall()

    async def export_rows(self, guild_id: int, kind: str):
        return await self.run(self._export_rows, guild_id, kind)


    # ---------- 放課後ミッション ----------
    def _ensure_daily_missions(
        self,
        guild_id: int,
        mission_date: str,
        reroll: bool = False,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM daily_missions
                WHERE guild_id=? AND mission_date=?
                ORDER BY slot
                """,
                (guild_id, mission_date),
            ).fetchall()

            if existing and not reroll:
                return [dict(r) for r in existing]

            if reroll:
                conn.execute(
                    "DELETE FROM daily_missions WHERE guild_id=? AND mission_date=?",
                    (guild_id, mission_date),
                )
                conn.execute(
                    "DELETE FROM mission_progress WHERE guild_id=? AND mission_date=?",
                    (guild_id, mission_date),
                )
                conn.execute(
                    """
                    DELETE FROM reward_queue
                    WHERE guild_id=? AND mission_date=? AND status='pending'
                    """,
                    (guild_id, mission_date),
                )

            selected = random.sample(MISSION_POOL, k=3)
            for slot, mission in enumerate(selected, 1):
                conn.execute(
                    """
                    INSERT INTO daily_missions (
                        guild_id, mission_date, slot, mission_code,
                        name, description, kind, target,
                        required_people, reward, difficulty
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        mission_date,
                        slot,
                        mission["code"],
                        mission["name"],
                        mission["description"],
                        mission["kind"],
                        mission["target"],
                        mission.get("required_people", 1),
                        mission["reward"],
                        mission["difficulty"],
                    ),
                )

            rows = conn.execute(
                """
                SELECT * FROM daily_missions
                WHERE guild_id=? AND mission_date=?
                ORDER BY slot
                """,
                (guild_id, mission_date),
            ).fetchall()
            return [dict(r) for r in rows]

    async def ensure_daily_missions(
        self,
        guild_id: int,
        mission_date: str,
        reroll: bool = False,
    ) -> list[dict[str, Any]]:
        return await self.run(
            self._ensure_daily_missions,
            guild_id,
            mission_date,
            reroll,
        )

    def _get_daily_missions(
        self,
        guild_id: int,
        mission_date: str,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM daily_missions
                    WHERE guild_id=? AND mission_date=?
                    ORDER BY slot
                    """,
                    (guild_id, mission_date),
                ).fetchall()
            ]

    async def get_daily_missions(
        self,
        guild_id: int,
        mission_date: str,
    ) -> list[dict[str, Any]]:
        return await self.run(
            self._get_daily_missions,
            guild_id,
            mission_date,
        )

    def _increment_mission_progress(
        self,
        guild_id: int,
        mission_date: str,
        user_id: int,
        mission: dict[str, Any],
        increment_seconds: int,
    ) -> tuple[int, bool]:
        target_seconds = int(mission["target"]) * 60
        now = to_iso()

        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT progress_seconds, completed_at
                FROM mission_progress
                WHERE guild_id=? AND mission_date=?
                  AND user_id=? AND mission_code=?
                """,
                (
                    guild_id,
                    mission_date,
                    user_id,
                    mission["mission_code"],
                ),
            ).fetchone()

            previous = int(row["progress_seconds"] or 0) if row else 0
            already_completed = bool(row and row["completed_at"])

            if already_completed:
                return previous, False

            new_progress = min(target_seconds, previous + increment_seconds)
            completed_now = new_progress >= target_seconds

            conn.execute(
                """
                INSERT INTO mission_progress (
                    guild_id, mission_date, user_id, mission_code,
                    progress_seconds, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    guild_id, mission_date, user_id, mission_code
                ) DO UPDATE SET
                    progress_seconds=excluded.progress_seconds,
                    completed_at=COALESCE(
                        mission_progress.completed_at,
                        excluded.completed_at
                    )
                """,
                (
                    guild_id,
                    mission_date,
                    user_id,
                    mission["mission_code"],
                    new_progress,
                    now if completed_now else None,
                ),
            )

            if completed_now:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reward_queue (
                        guild_id, mission_date, user_id, mission_code,
                        mission_name, points, status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        guild_id,
                        mission_date,
                        user_id,
                        mission["mission_code"],
                        mission["name"],
                        mission["reward"],
                        now,
                    ),
                )

            return new_progress, completed_now

    async def increment_mission_progress(
        self,
        guild_id: int,
        mission_date: str,
        user_id: int,
        mission: dict[str, Any],
        increment_seconds: int,
    ) -> tuple[int, bool]:
        return await self.run(
            self._increment_mission_progress,
            guild_id,
            mission_date,
            user_id,
            mission,
            increment_seconds,
        )

    def _get_user_mission_progress(
        self,
        guild_id: int,
        mission_date: str,
        user_id: int,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    m.*,
                    COALESCE(p.progress_seconds, 0) AS progress_seconds,
                    p.completed_at
                FROM daily_missions m
                LEFT JOIN mission_progress p
                  ON p.guild_id=m.guild_id
                 AND p.mission_date=m.mission_date
                 AND p.mission_code=m.mission_code
                 AND p.user_id=?
                WHERE m.guild_id=? AND m.mission_date=?
                ORDER BY m.slot
                """,
                (user_id, guild_id, mission_date),
            ).fetchall()
            return [dict(r) for r in rows]

    async def get_user_mission_progress(
        self,
        guild_id: int,
        mission_date: str,
        user_id: int,
    ) -> list[dict[str, Any]]:
        return await self.run(
            self._get_user_mission_progress,
            guild_id,
            mission_date,
            user_id,
        )

    def _pending_rewards(
        self,
        guild_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM reward_queue
                    WHERE guild_id=? AND status='pending'
                    ORDER BY created_at ASC
                    LIMIT ?
                    """,
                    (guild_id, limit),
                ).fetchall()
            ]

    async def pending_rewards(
        self,
        guild_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self.run(
            self._pending_rewards,
            guild_id,
            limit,
        )

    def _complete_reward(
        self,
        guild_id: int,
        reward_id: int,
        completed_by: int,
    ) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE reward_queue
                SET status='completed',
                    completed_at=?,
                    completed_by=?
                WHERE guild_id=? AND id=? AND status='pending'
                """,
                (to_iso(), completed_by, guild_id, reward_id),
            )
            return cur.rowcount > 0

    async def complete_reward(
        self,
        guild_id: int,
        reward_id: int,
        completed_by: int,
    ) -> bool:
        return await self.run(
            self._complete_reward,
            guild_id,
            reward_id,
            completed_by,
        )

    def _complete_all_rewards(
        self,
        guild_id: int,
        completed_by: int,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE reward_queue
                SET status='completed',
                    completed_at=?,
                    completed_by=?
                WHERE guild_id=? AND status='pending'
                """,
                (to_iso(), completed_by, guild_id),
            )
            return int(cur.rowcount)

    async def complete_all_rewards(
        self,
        guild_id: int,
        completed_by: int,
    ) -> int:
        return await self.run(
            self._complete_all_rewards,
            guild_id,
            completed_by,
        )

db = Database(DB_PATH)

# =========================================================
# Bot
# =========================================================

intents = discord.Intents.none()
intents.guilds = True
intents.members = True
intents.voice_states = True
intents.moderation = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    intents=intents,
    help_command=None,
)

def target_guild() -> discord.Object:
    return discord.Object(id=GUILD_ID)

def is_excluded_channel(channel: Optional[discord.abc.GuildChannel]) -> bool:
    if channel is None:
        return False
    category_id = getattr(channel, "category_id", None)
    return bool(category_id and category_id in EXCLUDED_VOICE_CATEGORY_IDS)

def should_track_member(member: discord.Member) -> bool:
    return not member.bot and member.id not in EXCLUDED_USER_IDS

def active_voice_members(guild: discord.Guild) -> list[discord.Member]:
    result: list[discord.Member] = []
    for channel in guild.voice_channels:
        if is_excluded_channel(channel):
            continue
        for member in channel.members:
            if should_track_member(member):
                result.append(member)
    for channel in guild.stage_channels:
        if is_excluded_channel(channel):
            continue
        for member in channel.members:
            if should_track_member(member):
                result.append(member)
    return result

async def is_manager(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    return any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles)

def manager_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not await is_manager(interaction):
            raise app_commands.CheckFailure("このコマンドは運営専用です。")
        return True
    return app_commands.check(predicate)

async def private_reply(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    file: Optional[discord.File] = None,
) -> None:
    kwargs: dict[str, Any] = {"ephemeral": True}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if file is not None:
        kwargs["file"] = file

    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)

async def send_log(
    guild: discord.Guild,
    *,
    title: str,
    description: str,
    color: discord.Color = EMBED_COLOR,
    join_leave: bool = False,
) -> None:
    channel_id = JOIN_LEAVE_LOG_CHANNEL_ID if join_leave else MANAGEMENT_LOG_CHANNEL_ID
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=utcnow(),
    )
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        log.exception("管理ログ送信に失敗しました")

async def ensure_member_record(member: discord.Member) -> None:
    await db.upsert_member(member)

async def get_recent_removal_action(
    guild: discord.Guild,
    user_id: int,
) -> tuple[str, Optional[int], Optional[str]]:
    await asyncio.sleep(1.5)
    now = utcnow()
    checks = (
        (discord.AuditLogAction.kick, "kick"),
        (discord.AuditLogAction.ban, "ban"),
    )
    for action, label in checks:
        try:
            async for entry in guild.audit_logs(limit=8, action=action):
                if (now - entry.created_at).total_seconds() > 15:
                    continue
                if getattr(entry.target, "id", None) == user_id:
                    return label, getattr(entry.user, "id", None), entry.reason
        except discord.Forbidden:
            return "leave", None, "監査ログ権限なし"
        except discord.HTTPException:
            log.exception("監査ログ取得に失敗しました")
            break
    return "leave", None, None


# =========================================================
# 放課後ミッション補助
# =========================================================

def mission_date_key() -> str:
    return local_now().strftime("%Y-%m-%d")

def difficulty_emoji(value: str) -> str:
    return {
        "EASY": "🟢",
        "NORMAL": "🔵",
        "HARD": "🟣",
        "RARE": "🌈",
    }.get(value, "🎯")

async def get_mission_channel(
    guild: discord.Guild,
) -> Optional[discord.TextChannel]:
    channel_id = await db.get_setting(guild.id, "mission_channel_id")
    if not channel_id:
        return None
    channel = guild.get_channel(int(channel_id))
    return channel if isinstance(channel, discord.TextChannel) else None

def mission_board_embed(
    missions: list[dict[str, Any]],
    date_key: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="🏫 今日の放課後ミッション",
        description=(
            f"**{date_key}**\n"
            "VCで遊びながら達成しよう！\n"
            "報酬は管理者確認後、天真爛漫Botで付与されます。"
        ),
        color=EMBED_COLOR,
        timestamp=utcnow(),
    )
    for mission in missions:
        embed.add_field(
            name=(
                f"{difficulty_emoji(mission['difficulty'])} "
                f"{mission['difficulty']}｜{mission['name']}"
            ),
            value=(
                f"{mission['description']}\n"
                f"報酬予定：**{mission['reward']}pt**"
            ),
            inline=False,
        )
    embed.set_footer(
        text="進捗確認：/ミッション進捗"
    )
    return embed

async def post_daily_missions(
    guild: discord.Guild,
    *,
    reroll: bool = False,
) -> bool:
    date_key = mission_date_key()
    missions = await db.ensure_daily_missions(
        guild.id,
        date_key,
        reroll,
    )
    channel = await get_mission_channel(guild)
    if not channel:
        return False

    try:
        await channel.send(
            embed=mission_board_embed(missions, date_key)
        )
        return True
    except discord.HTTPException:
        log.exception("ミッション掲示に失敗しました")
        return False

async def announce_mission_complete(
    guild: discord.Guild,
    member: discord.Member,
    mission: dict[str, Any],
) -> None:
    channel = await get_mission_channel(guild)
    if not channel:
        channel = guild.get_channel(MANAGEMENT_LOG_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return

    embed = discord.Embed(
        title="🎉 Mission Complete!",
        description=(
            f"{member.mention} がミッションを達成しました！\n\n"
            f"**{mission['name']}**\n"
            f"{mission['description']}\n\n"
            f"💰 報酬予定：**{mission['reward']}pt**\n"
            "※管理者確認後にポイントが付与されます。"
        ),
        color=discord.Color.green(),
        timestamp=utcnow(),
    )
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        log.exception("ミッション達成通知に失敗しました")

# =========================================================
# Events
# =========================================================

@bot.event
async def setup_hook() -> None:
    db.initialize()
    synced = await bot.tree.sync(guild=target_guild())
    log.info("Synced %d guild commands", len(synced))

@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "?")
    guild = bot.get_guild(GUILD_ID)
    if guild:
        for member in guild.members:
            if not member.bot:
                await ensure_member_record(member)
        for member in active_voice_members(guild):
            if member.voice and member.voice.channel:
                await db.start_voice_session(member, member.voice.channel)
        await db.record_concurrency(guild.id, len(active_voice_members(guild)))

    if not dashboard_loop.is_running():
        dashboard_loop.start()
    if not daily_summary_loop.is_running():
        daily_summary_loop.start()
    if not mission_progress_loop.is_running():
        mission_progress_loop.start()
    if not mission_daily_post_loop.is_running():
        mission_daily_post_loop.start()

@bot.event
async def on_member_join(member: discord.Member) -> None:
    if member.guild.id != GUILD_ID or member.bot:
        return
    await db.upsert_member(member)
    await db.add_member_event(member.guild.id, member.id, "join")
    await send_log(
        member.guild,
        title="📥 メンバー加入",
        description=(
            f"{member.mention}（`{member.id}`）\n"
            f"アカウント作成: {fmt_dt(member.created_at, 'R')}\n"
            f"加入: {fmt_dt(utcnow())}"
        ),
        color=discord.Color.green(),
        join_leave=True,
    )

@bot.event
async def on_raw_member_remove(payload: discord.RawMemberRemoveEvent) -> None:
    if payload.guild_id != GUILD_ID or payload.user.bot:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    await db.mark_left(guild.id, payload.user.id)
    action, actor_id, reason = await get_recent_removal_action(guild, payload.user.id)
    await db.add_member_event(guild.id, payload.user.id, action, actor_id, reason)

    labels = {
        "leave": ("📤 メンバー退出", discord.Color.orange()),
        "kick": ("🥾 メンバーKick", discord.Color.red()),
        "ban": ("🔨 メンバーBAN", discord.Color.dark_red()),
    }
    title, color = labels[action]
    actor = f"<@{actor_id}>" if actor_id else "不明 / 自主退出"
    await send_log(
        guild,
        title=title,
        description=(
            f"{payload.user.mention}（`{payload.user.id}`）\n"
            f"種別: **{action.upper()}**\n"
            f"実行者: {actor}\n"
            f"理由: {safe_text(reason)}"
        ),
        color=color,
        join_leave=True,
    )

@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User) -> None:
    if guild.id != GUILD_ID:
        return
    actor_id = None
    reason = None
    await asyncio.sleep(1)
    try:
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.unban):
            if getattr(entry.target, "id", None) == user.id:
                actor_id = getattr(entry.user, "id", None)
                reason = entry.reason
                break
    except discord.HTTPException:
        pass

    await db.add_member_event(guild.id, user.id, "unban", actor_id, reason)
    await send_log(
        guild,
        title="🔓 BAN解除",
        description=(
            f"{user.mention}（`{user.id}`）\n"
            f"実行者: {f'<@{actor_id}>' if actor_id else '不明'}\n"
            f"理由: {safe_text(reason)}"
        ),
        color=discord.Color.green(),
        join_leave=True,
    )

@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    if member.guild.id != GUILD_ID or not should_track_member(member):
        return
    if before.channel == after.channel:
        return

    await ensure_member_record(member)

    before_ch = before.channel
    after_ch = after.channel
    before_trackable = before_ch is not None and not is_excluded_channel(before_ch)
    after_trackable = after_ch is not None and not is_excluded_channel(after_ch)

    if before_trackable and not after_trackable:
        duration = await db.end_voice_session(member.guild.id, member.id)
        await db.record_voice_event(member.guild.id, member.id, "leave", before_ch, after_ch)
        await send_log(
            member.guild,
            title="🎤 VC退出",
            description=(
                f"{member.mention}\n"
                f"退出: **{before_ch.name}**\n"
                f"滞在: **{fmt_duration(duration)}**"
            ),
            color=discord.Color.orange(),
        )

    elif not before_trackable and after_trackable:
        await db.start_voice_session(member, after_ch)
        await db.record_voice_event(member.guild.id, member.id, "join", before_ch, after_ch)
        await send_log(
            member.guild,
            title="🎤 VC入室",
            description=f"{member.mention}\n入室: **{after_ch.name}**",
            color=discord.Color.green(),
        )

    elif before_trackable and after_trackable:
        duration = await db.end_voice_session(member.guild.id, member.id)
        await db.start_voice_session(member, after_ch)
        await db.record_voice_event(member.guild.id, member.id, "move", before_ch, after_ch)
        await send_log(
            member.guild,
            title="🔁 VC移動",
            description=(
                f"{member.mention}\n"
                f"**{before_ch.name}** → **{after_ch.name}**\n"
                f"移動前滞在: **{fmt_duration(duration)}**"
            ),
            color=discord.Color.blue(),
        )

    await db.record_concurrency(member.guild.id, len(active_voice_members(member.guild)))

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    if after.guild.id != GUILD_ID or after.bot:
        return

    changes: list[str] = []

    if before.nick != after.nick:
        changes.append(f"ニックネーム: `{before.display_name}` → `{after.display_name}`")

    before_roles = {r.id: r for r in before.roles if not r.is_default()}
    after_roles = {r.id: r for r in after.roles if not r.is_default()}
    added = [r.mention for rid, r in after_roles.items() if rid not in before_roles]
    removed = [r.mention for rid, r in before_roles.items() if rid not in after_roles]
    if added:
        changes.append("追加ロール: " + ", ".join(added))
    if removed:
        changes.append("削除ロール: " + ", ".join(removed))

    if before.timed_out_until != after.timed_out_until:
        if after.timed_out_until:
            changes.append(f"タイムアウト: {fmt_dt(after.timed_out_until)} まで")
            event_type = "timeout_add"
        else:
            changes.append("タイムアウト解除")
            event_type = "timeout_remove"
        await db.add_member_event(
            after.guild.id,
            after.id,
            event_type,
            details="\n".join(changes),
        )

    if changes:
        await db.upsert_member(after)
        await send_log(
            after.guild,
            title="🛠️ メンバー情報変更",
            description=f"{after.mention}\n" + "\n".join(changes),
            color=discord.Color.gold(),
        )

# =========================================================
# Embed生成
# =========================================================

def stats_embed(
    title: str,
    stats: dict[str, Any],
    *,
    previous: Optional[dict[str, Any]] = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=EMBED_COLOR, timestamp=utcnow())
    new_count = stats["new_members"]
    participated = stats["new_vc_members"]
    rate = (participated / new_count * 100) if new_count else 0

    embed.add_field(
        name="🎤 VC",
        value=(
            f"利用者: **{stats['unique_users']}人**\n"
            f"開始回数: **{stats['session_starts']}回**\n"
            f"合計滞在: **{fmt_duration(stats['total_seconds'])}**\n"
            f"最大同時接続: **{stats['max_concurrent']}人**"
        ),
        inline=False,
    )
    embed.add_field(
        name="👥 メンバー",
        value=(
            f"加入: **{stats['joins']}人**\n"
            f"退出: **{stats['leaves']}人**\n"
            f"Kick: **{stats['kicks']}人**\n"
            f"BAN: **{stats['bans']}人**\n"
            f"純増減: **{stats['joins'] - stats['leaves'] - stats['kicks'] - stats['bans']:+d}人**"
        ),
        inline=True,
    )
    embed.add_field(
        name="🌱 新規VC参加",
        value=(
            f"対象: **{new_count}人**\n"
            f"VC参加済み: **{participated}人**\n"
            f"参加率: **{rate:.1f}%**"
        ),
        inline=True,
    )

    if previous:
        def pct(current: int, old: int) -> str:
            if old == 0:
                return "比較不能" if current else "±0%"
            return f"{((current - old) / old * 100):+.1f}%"

        embed.add_field(
            name="📈 前期間との比較",
            value=(
                f"VC利用者: **{pct(stats['unique_users'], previous['unique_users'])}**\n"
                f"合計滞在: **{pct(stats['total_seconds'], previous['total_seconds'])}**\n"
                f"新規加入: **{pct(stats['joins'], previous['joins'])}**"
            ),
            inline=False,
        )
    return embed

async def build_dashboard_embed(guild: discord.Guild) -> discord.Embed:
    start, end = range_bounds(7)
    stats = await db.stats(guild.id, start, end)
    active = active_voice_members(guild)

    embed = discord.Embed(
        title="👑 Puraudhia 管理ダッシュボード",
        description="運営専用・自動更新",
        color=EMBED_COLOR,
        timestamp=utcnow(),
    )
    embed.add_field(
        name="🏫 現在",
        value=(
            f"総メンバー: **{guild.member_count or len(guild.members)}人**\n"
            f"VC利用者: **{len(active)}人**\n"
            f"稼働VC: **{len({m.voice.channel.id for m in active if m.voice and m.voice.channel})}部屋**"
        ),
        inline=False,
    )

    if active:
        lines = []
        for m in active[:20]:
            channel_name = m.voice.channel.name if m.voice and m.voice.channel else "不明"
            lines.append(f"• {m.mention} — **{channel_name}**")
        embed.add_field(name="🎙️ 現在VC中", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="🎙️ 現在VC中", value="現在、VC利用者はいません。", inline=False)

    embed.add_field(
        name="📊 直近7日",
        value=(
            f"利用者: **{stats['unique_users']}人**\n"
            f"合計滞在: **{fmt_duration(stats['total_seconds'])}**\n"
            f"最大同時接続: **{stats['max_concurrent']}人**\n"
            f"加入 / 退出: **{stats['joins']} / {stats['leaves'] + stats['kicks'] + stats['bans']}**"
        ),
        inline=False,
    )
    embed.set_footer(text=f"{DASHBOARD_UPDATE_MINUTES}分ごとに自動更新")
    return embed

# =========================================================
# Slash Commands
# =========================================================

@bot.tree.command(name="今日の統計", description="本日のVC・加入・退出状況を表示します。")
@app_commands.guilds(target_guild())
@manager_only()
async def today_stats(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    start, _ = local_day_bounds(0)
    stats = await db.stats(interaction.guild_id, start, utcnow())
    await private_reply(interaction, embed=stats_embed("📊 今日の統計", stats))

@bot.tree.command(name="週間統計", description="直近7日間と、その前7日間を比較します。")
@app_commands.guilds(target_guild())
@manager_only()
async def weekly_stats(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    end = utcnow()
    start = range_bounds(7)[0]
    previous_start = start - timedelta(days=7)
    current = await db.stats(interaction.guild_id, start, end)
    previous = await db.stats(interaction.guild_id, previous_start, start)
    await private_reply(interaction, embed=stats_embed("📈 週間統計（直近7日）", current, previous=previous))

@bot.tree.command(name="月間統計", description="直近30日間と、その前30日間を比較します。")
@app_commands.guilds(target_guild())
@manager_only()
async def monthly_stats(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    end = utcnow()
    start = range_bounds(30)[0]
    previous_start = start - timedelta(days=30)
    current = await db.stats(interaction.guild_id, start, end)
    previous = await db.stats(interaction.guild_id, previous_start, start)
    await private_reply(interaction, embed=stats_embed("📆 月間統計（直近30日）", current, previous=previous))

@bot.tree.command(name="vcランキング", description="指定期間のVC滞在時間ランキングを表示します。")
@app_commands.describe(日数="1〜90日")
@app_commands.guilds(target_guild())
@manager_only()
async def vc_ranking(
    interaction: discord.Interaction,
    日数: app_commands.Range[int, 1, 90] = 7,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    start, end = range_bounds(日数)
    rows = await db.top_voice(interaction.guild_id, start, end, 15)

    embed = discord.Embed(
        title=f"🏆 VCランキング（直近{日数}日）",
        color=EMBED_COLOR,
        timestamp=utcnow(),
    )
    if not rows:
        embed.description = "該当データはありません。"
    else:
        embed.description = "\n".join(
            f"**{i}.** <@{uid}> — **{fmt_duration(seconds)}** / {sessions}回"
            for i, (uid, seconds, sessions) in enumerate(rows, 1)
        )
    await private_reply(interaction, embed=embed)

@bot.tree.command(name="メンバー確認", description="メンバーカルテを表示します。")
@app_commands.guilds(target_guild())
@manager_only()
async def member_check(interaction: discord.Interaction, メンバー: discord.Member) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    await ensure_member_record(メンバー)
    data = await db.member_card(interaction.guild_id, メンバー.id)
    record = data["member"] or {}
    interview = data["interview"]
    note = data["note"]

    embed = discord.Embed(
        title=f"👤 メンバーカルテ｜{メンバー.display_name}",
        color=EMBED_COLOR,
        timestamp=utcnow(),
    )
    embed.set_thumbnail(url=メンバー.display_avatar.url)
    embed.add_field(
        name="基本情報",
        value=(
            f"ユーザー: {メンバー.mention}\n"
            f"ID: `{メンバー.id}`\n"
            f"加入: {fmt_dt(parse_dt(record.get('joined_at')))}\n"
            f"初VC: {fmt_dt(parse_dt(record.get('first_vc_at')))}\n"
            f"最終VC: {fmt_dt(parse_dt(record.get('last_vc_at')), 'R')}"
        ),
        inline=False,
    )
    embed.add_field(
        name="VC記録",
        value=(
            f"参加回数: **{data['vc_sessions']}回**\n"
            f"合計滞在: **{fmt_duration(data['vc_seconds'])}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="管理情報",
        value=(
            f"有効警告: **{data['warning_count']}件**\n"
            f"面接: **{'登録済み' if interview else '未登録'}**"
        ),
        inline=True,
    )

    if interview:
        embed.add_field(
            name="最新の面接記録",
            value=(
                f"担当: <@{interview['interviewer_id']}>\n"
                f"結果: **{interview['result']}**\n"
                f"知った場所: {safe_text(interview['source'], 200)}\n"
                f"BAN履歴: **{interview['ban_history']}**\n"
                f"同性会話: **{interview['same_gender_ok']}**\n"
                f"メモ: {safe_text(interview['memo'], 500)}"
            ),
            inline=False,
        )

    embed.add_field(
        name="運営メモ",
        value=safe_text(note["note"] if note else None, 1000),
        inline=False,
    )
    await private_reply(interaction, embed=embed)

RESULT_CHOICES = [
    app_commands.Choice(name="合格", value="合格"),
    app_commands.Choice(name="保留", value="保留"),
    app_commands.Choice(name="不合格", value="不合格"),
]

YES_NO_CHOICES = [
    app_commands.Choice(name="はい", value="はい"),
    app_commands.Choice(name="いいえ", value="いいえ"),
    app_commands.Choice(name="不明", value="不明"),
]

@bot.tree.command(name="面接登録", description="面接内容をメンバーカルテへ保存します。")
@app_commands.describe(
    メンバー="面接対象",
    結果="面接結果",
    知った場所="DISBOARD、ディス速、紹介など",
    ban履歴="BAN履歴の申告",
    同性会話="同性との会話が可能か",
    メモ="補足事項",
)
@app_commands.choices(結果=RESULT_CHOICES, ban履歴=YES_NO_CHOICES, 同性会話=YES_NO_CHOICES)
@app_commands.guilds(target_guild())
@manager_only()
async def interview_register(
    interaction: discord.Interaction,
    メンバー: discord.Member,
    結果: app_commands.Choice[str],
    知った場所: str,
    ban履歴: app_commands.Choice[str],
    同性会話: app_commands.Choice[str],
    メモ: str = "",
) -> None:
    await ensure_member_record(メンバー)
    await db.add_interview(
        interaction.guild_id,
        メンバー.id,
        interaction.user.id,
        結果.value,
        知った場所,
        ban履歴.value,
        同性会話.value,
        メモ,
    )
    await db.add_member_event(
        interaction.guild_id,
        メンバー.id,
        "interview",
        interaction.user.id,
        f"結果={結果.value}; source={知った場所}",
    )
    await send_log(
        interaction.guild,
        title="📝 面接登録",
        description=(
            f"対象: {メンバー.mention}\n"
            f"担当: {interaction.user.mention}\n"
            f"結果: **{結果.value}**\n"
            f"知った場所: {知った場所}\n"
            f"BAN履歴: **{ban履歴.value}**\n"
            f"同性会話: **{同性会話.value}**\n"
            f"メモ: {safe_text(メモ)}"
        ),
        color=discord.Color.blue(),
    )
    await private_reply(interaction, content=f"✅ {メンバー.mention} の面接記録を保存しました。")

@bot.tree.command(name="警告追加", description="メンバーへ運営上の警告記録を追加します。")
@app_commands.guilds(target_guild())
@manager_only()
async def warning_add(
    interaction: discord.Interaction,
    メンバー: discord.Member,
    理由: str,
) -> None:
    warning_id = await db.add_warning(
        interaction.guild_id,
        メンバー.id,
        interaction.user.id,
        理由,
    )
    await db.add_member_event(
        interaction.guild_id,
        メンバー.id,
        "warning",
        interaction.user.id,
        f"警告ID={warning_id}; {理由}",
    )
    await send_log(
        interaction.guild,
        title="⚠️ 警告追加",
        description=(
            f"警告ID: **#{warning_id}**\n"
            f"対象: {メンバー.mention}\n"
            f"登録者: {interaction.user.mention}\n"
            f"理由: {理由}"
        ),
        color=discord.Color.red(),
    )
    await private_reply(interaction, content=f"✅ 警告 #{warning_id} を登録しました。")

@bot.tree.command(name="警告一覧", description="メンバーの警告履歴を表示します。")
@app_commands.guilds(target_guild())
@manager_only()
async def warning_list(
    interaction: discord.Interaction,
    メンバー: discord.Member,
    解決済みも表示: bool = False,
) -> None:
    rows = await db.warnings(
        interaction.guild_id,
        メンバー.id,
        active_only=not 解決済みも表示,
    )
    embed = discord.Embed(
        title=f"⚠️ 警告一覧｜{メンバー.display_name}",
        color=discord.Color.red(),
        timestamp=utcnow(),
    )
    if not rows:
        embed.description = "該当する警告はありません。"
    else:
        lines = []
        for row in rows:
            state = "有効" if row["is_active"] else "解決済み"
            lines.append(
                f"**#{row['id']} [{state}]** {fmt_dt(parse_dt(row['created_at']), 'd')}\n"
                f"登録: <@{row['moderator_id']}>\n"
                f"{safe_text(row['reason'], 300)}"
            )
        embed.description = "\n\n".join(lines)[:4000]
    await private_reply(interaction, embed=embed)

@bot.tree.command(name="警告解決", description="警告を解決済みに変更します。")
@app_commands.guilds(target_guild())
@manager_only()
async def warning_resolve(interaction: discord.Interaction, 警告id: int) -> None:
    ok = await db.resolve_warning(interaction.guild_id, 警告id, interaction.user.id)
    if not ok:
        await private_reply(interaction, content="❌ 有効な警告IDが見つかりません。")
        return
    await private_reply(interaction, content=f"✅ 警告 #{警告id} を解決済みにしました。")

@bot.tree.command(name="メモ設定", description="メンバーの運営メモを保存・上書きします。")
@app_commands.guilds(target_guild())
@manager_only()
async def note_set(
    interaction: discord.Interaction,
    メンバー: discord.Member,
    メモ: str,
) -> None:
    await db.set_note(interaction.guild_id, メンバー.id, メモ, interaction.user.id)
    await send_log(
        interaction.guild,
        title="🗒️ 運営メモ更新",
        description=(
            f"対象: {メンバー.mention}\n"
            f"更新者: {interaction.user.mention}\n"
            f"内容: {safe_text(メモ)}"
        ),
        color=discord.Color.gold(),
    )
    await private_reply(interaction, content=f"✅ {メンバー.mention} のメモを保存しました。")

@bot.tree.command(name="新規vc未参加", description="指定期間内に加入し、まだVCへ参加していない人を表示します。")
@app_commands.guilds(target_guild())
@manager_only()
async def new_no_vc(
    interaction: discord.Interaction,
    日数: app_commands.Range[int, 1, 90] = 30,
) -> None:
    since = utcnow() - timedelta(days=日数)
    rows = await db.new_without_vc(interaction.guild_id, since, 50)

    embed = discord.Embed(
        title=f"🌱 新規VC未参加（直近{日数}日）",
        color=EMBED_COLOR,
        timestamp=utcnow(),
    )
    if not rows:
        embed.description = "対象者はいません。"
    else:
        embed.description = "\n".join(
            f"• <@{row['user_id']}> — 加入 {fmt_dt(parse_dt(row['joined_at']), 'R')}"
            for row in rows
        )[:4000]
        embed.set_footer(text=f"{len(rows)}人を表示")
    await private_reply(interaction, embed=embed)

@bot.tree.command(name="vc休眠一覧", description="指定日数以上VCへ参加していない人を表示します。")
@app_commands.guilds(target_guild())
@manager_only()
async def inactive_list(
    interaction: discord.Interaction,
    日数: app_commands.Range[int, 1, 365] = 7,
) -> None:
    cutoff = utcnow() - timedelta(days=日数)
    rows = await db.inactive_vc(interaction.guild_id, cutoff, 50)

    embed = discord.Embed(
        title=f"🌙 VC休眠一覧（{日数}日以上）",
        description="※このBotが記録したVC最終参加日時を基準にしています。",
        color=discord.Color.dark_purple(),
        timestamp=utcnow(),
    )
    if not rows:
        embed.add_field(name="結果", value="対象者はいません。", inline=False)
    else:
        lines = []
        for row in rows:
            last = parse_dt(row["last_vc_at"])
            label = fmt_dt(last, "R") if last else "VC記録なし"
            lines.append(f"• <@{row['user_id']}> — {label}")
        embed.add_field(name="対象者（最大50人）", value="\n".join(lines)[:4000], inline=False)
    await private_reply(interaction, embed=embed)

@bot.tree.command(name="管理ダッシュボード", description="管理チャンネルに自動更新ダッシュボードを設置します。")
@app_commands.guilds(target_guild())
@manager_only()
async def dashboard_create(interaction: discord.Interaction) -> None:
    channel = interaction.guild.get_channel(DASHBOARD_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        await private_reply(interaction, content="❌ ダッシュボード用チャンネルが見つかりません。")
        return

    embed = await build_dashboard_embed(interaction.guild)
    message = await channel.send(embed=embed)
    await db.set_setting(interaction.guild_id, "dashboard_message_id", str(message.id))
    await private_reply(interaction, content=f"✅ ダッシュボードを {channel.mention} に設置しました。")

EXPORT_CHOICES = [
    app_commands.Choice(name="VC履歴", value="vc"),
    app_commands.Choice(name="面接記録", value="面接"),
    app_commands.Choice(name="警告記録", value="警告"),
    app_commands.Choice(name="メンバー一覧", value="メンバー"),
]

@bot.tree.command(name="データ出力", description="管理データをCSVで出力します。")
@app_commands.choices(種類=EXPORT_CHOICES)
@app_commands.guilds(target_guild())
@manager_only()
async def data_export(
    interaction: discord.Interaction,
    種類: app_commands.Choice[str],
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    headers, rows = await db.export_rows(interaction.guild_id, 種類.value)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row[h] for h in headers])

    raw = buffer.getvalue().encode("utf-8-sig")
    filename = f"puraudhia_{種類.value}_{local_now().strftime('%Y%m%d_%H%M')}.csv"
    file = discord.File(io.BytesIO(raw), filename=filename)
    await private_reply(interaction, content=f"✅ {len(rows)}件を出力しました。", file=file)


# =========================================================
# 放課後ミッション Slash Commands
# =========================================================

@bot.tree.command(
    name="今日のミッション",
    description="本日の放課後ミッションを表示します。",
)
@app_commands.guilds(target_guild())
async def today_missions(
    interaction: discord.Interaction,
) -> None:
    date_key = mission_date_key()
    missions = await db.ensure_daily_missions(
        interaction.guild_id,
        date_key,
    )
    await private_reply(
        interaction,
        embed=mission_board_embed(missions, date_key),
    )

@bot.tree.command(
    name="ミッション進捗",
    description="自分の今日のミッション進捗を確認します。",
)
@app_commands.guilds(target_guild())
async def mission_progress(
    interaction: discord.Interaction,
) -> None:
    date_key = mission_date_key()
    await db.ensure_daily_missions(
        interaction.guild_id,
        date_key,
    )
    rows = await db.get_user_mission_progress(
        interaction.guild_id,
        date_key,
        interaction.user.id,
    )

    embed = discord.Embed(
        title="🎮 今日のミッション進捗",
        description=f"{interaction.user.mention} の進捗",
        color=EMBED_COLOR,
        timestamp=utcnow(),
    )

    for row in rows:
        progress_minutes = int(row["progress_seconds"]) // 60
        target = int(row["target"])
        complete = bool(row["completed_at"])
        status = "✅ COMPLETE" if complete else f"{progress_minutes}/{target}分"
        embed.add_field(
            name=f"{difficulty_emoji(row['difficulty'])} {row['name']}",
            value=(
                f"{row['description']}\n"
                f"進捗：**{status}**\n"
                f"報酬予定：**{row['reward']}pt**"
            ),
            inline=False,
        )

    await private_reply(interaction, embed=embed)

@bot.tree.command(
    name="ミッションチャンネル設定",
    description="現在のチャンネルをミッション掲示先に設定します。",
)
@app_commands.guilds(target_guild())
@manager_only()
async def mission_channel_set(
    interaction: discord.Interaction,
) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await private_reply(
            interaction,
            content="❌ テキストチャンネルで実行してください。",
        )
        return

    await db.set_setting(
        interaction.guild_id,
        "mission_channel_id",
        str(interaction.channel.id),
    )
    await private_reply(
        interaction,
        content=(
            f"✅ {interaction.channel.mention} を"
            "ミッション掲示チャンネルに設定しました。"
        ),
    )

@bot.tree.command(
    name="ミッション掲示",
    description="今日のミッションを設定済みチャンネルへ掲示します。",
)
@app_commands.guilds(target_guild())
@manager_only()
async def mission_post(
    interaction: discord.Interaction,
) -> None:
    ok = await post_daily_missions(interaction.guild)
    await private_reply(
        interaction,
        content=(
            "✅ 今日のミッションを掲示しました。"
            if ok
            else "❌ 先に /ミッションチャンネル設定 を実行してください。"
        ),
    )

@bot.tree.command(
    name="ミッション再抽選",
    description="本日のミッションを3つ再抽選します。",
)
@app_commands.guilds(target_guild())
@manager_only()
async def mission_reroll(
    interaction: discord.Interaction,
) -> None:
    ok = await post_daily_missions(
        interaction.guild,
        reroll=True,
    )
    await private_reply(
        interaction,
        content=(
            "✅ 本日のミッションを再抽選して掲示しました。"
            if ok
            else (
                "✅ 再抽選しました。"
                "掲示する場合は先にチャンネル設定をしてください。"
            )
        ),
    )

@bot.tree.command(
    name="報酬一覧",
    description="未付与のミッション報酬一覧を表示します。",
)
@app_commands.guilds(target_guild())
@manager_only()
async def rewards_list(
    interaction: discord.Interaction,
) -> None:
    rows = await db.pending_rewards(interaction.guild_id, 100)
    embed = discord.Embed(
        title="💰 ミッション報酬待ち一覧",
        color=discord.Color.gold(),
        timestamp=utcnow(),
    )

    if not rows:
        embed.description = "現在、未付与の報酬はありません。"
    else:
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            item = grouped.setdefault(
                int(row["user_id"]),
                {"points": 0, "rewards": []},
            )
            item["points"] += int(row["points"])
            item["rewards"].append(
                f"#{row['id']} {row['mission_name']}（{row['points']}pt）"
            )

        lines = []
        for user_id, item in grouped.items():
            lines.append(
                f"<@{user_id}>　合計 **{item['points']}pt**\n"
                + "\n".join(f"└ {x}" for x in item["rewards"])
            )
        embed.description = "\n\n".join(lines)[:4000]
        embed.set_footer(
            text="付与後：/報酬完了 または /報酬一括完了"
        )

    await private_reply(interaction, embed=embed)

@bot.tree.command(
    name="報酬完了",
    description="指定した報酬IDを付与済みにします。",
)
@app_commands.guilds(target_guild())
@manager_only()
async def reward_complete(
    interaction: discord.Interaction,
    報酬id: int,
) -> None:
    ok = await db.complete_reward(
        interaction.guild_id,
        報酬id,
        interaction.user.id,
    )
    await private_reply(
        interaction,
        content=(
            f"✅ 報酬 #{報酬id} を付与済みにしました。"
            if ok
            else "❌ 未付与の報酬IDが見つかりません。"
        ),
    )

@bot.tree.command(
    name="報酬一括完了",
    description="未付与の報酬をすべて付与済みにします。",
)
@app_commands.guilds(target_guild())
@manager_only()
async def rewards_complete_all(
    interaction: discord.Interaction,
) -> None:
    count = await db.complete_all_rewards(
        interaction.guild_id,
        interaction.user.id,
    )
    await private_reply(
        interaction,
        content=f"✅ {count}件の報酬を付与済みにしました。",
    )

# =========================================================
# 定期処理
# =========================================================


@tasks.loop(seconds=MISSION_CHECK_SECONDS)
async def mission_progress_loop() -> None:
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    date_key = mission_date_key()
    missions = await db.ensure_daily_missions(
        guild.id,
        date_key,
    )

    for channel in list(guild.voice_channels) + list(guild.stage_channels):
        if is_excluded_channel(channel):
            continue

        members = [
            m for m in channel.members
            if should_track_member(m)
        ]
        if not members:
            continue

        people_count = len(members)
        channel_name_lower = channel.name.lower()
        is_game_channel = any(
            keyword.lower() in channel_name_lower
            for keyword in GAME_CHANNEL_KEYWORDS
        )

        hour = local_now().hour
        is_night = hour >= 22 or hour < 2

        for member in members:
            for mission in missions:
                kind = mission["kind"]
                qualifies = False

                if kind == "vc_minutes":
                    qualifies = True
                elif kind == "group_minutes":
                    qualifies = people_count >= int(
                        mission["required_people"]
                    )
                elif kind == "game_minutes":
                    qualifies = is_game_channel
                elif kind == "night_minutes":
                    qualifies = is_night

                if not qualifies:
                    continue

                _, completed_now = await db.increment_mission_progress(
                    guild.id,
                    date_key,
                    member.id,
                    mission,
                    MISSION_CHECK_SECONDS,
                )

                if completed_now:
                    await announce_mission_complete(
                        guild,
                        member,
                        mission,
                    )
                    await send_log(
                        guild,
                        title="🎯 ミッション達成・報酬待ち",
                        description=(
                            f"対象: {member.mention}\n"
                            f"ミッション: **{mission['name']}**\n"
                            f"付与予定: **{mission['reward']}pt**\n"
                            "管理者は /報酬一覧 を確認してください。"
                        ),
                        color=discord.Color.green(),
                    )

@mission_progress_loop.before_loop
async def before_mission_progress_loop() -> None:
    await bot.wait_until_ready()

@tasks.loop(minutes=1)
async def mission_daily_post_loop() -> None:
    now = local_now()
    if now.hour != 0 or now.minute != 1:
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    today = mission_date_key()
    last_posted = await db.get_setting(
        guild.id,
        "last_mission_post_date",
    )
    if last_posted == today:
        return

    await db.ensure_daily_missions(guild.id, today)
    posted = await post_daily_missions(guild)
    if posted:
        await db.set_setting(
            guild.id,
            "last_mission_post_date",
            today,
        )

@mission_daily_post_loop.before_loop
async def before_mission_daily_post_loop() -> None:
    await bot.wait_until_ready()

@tasks.loop(minutes=DASHBOARD_UPDATE_MINUTES)
async def dashboard_loop() -> None:
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    message_id = await db.get_setting(guild.id, "dashboard_message_id")
    if not message_id:
        return

    channel = guild.get_channel(DASHBOARD_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(embed=await build_dashboard_embed(guild))
    except discord.NotFound:
        await db.set_setting(guild.id, "dashboard_message_id", "")
    except discord.HTTPException:
        log.exception("ダッシュボード更新に失敗しました")

@dashboard_loop.before_loop
async def before_dashboard_loop() -> None:
    await bot.wait_until_ready()

@tasks.loop(minutes=1)
async def daily_summary_loop() -> None:
    now = local_now()
    if now.hour != DAILY_SUMMARY_HOUR or now.minute != DAILY_SUMMARY_MINUTE:
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    today_key = now.strftime("%Y-%m-%d")
    sent_key = await db.get_setting(guild.id, "last_daily_summary")
    if sent_key == today_key:
        return

    start, end = local_day_bounds(1)
    stats = await db.stats(guild.id, start, end)
    channel = guild.get_channel(MANAGEMENT_LOG_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        embed = stats_embed(f"📅 日次レポート｜{start.astimezone(TZ).date()}", stats)
        try:
            await channel.send(embed=embed)
            await db.set_setting(guild.id, "last_daily_summary", today_key)
        except discord.HTTPException:
            log.exception("日次レポート送信に失敗しました")

@daily_summary_loop.before_loop
async def before_daily_summary_loop() -> None:
    await bot.wait_until_ready()

# =========================================================
# エラー処理
# =========================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if isinstance(error, app_commands.CheckFailure):
        await private_reply(interaction, content="❌ このコマンドは運営専用です。")
        return

    log.exception("Slash command error", exc_info=error)
    await private_reply(
        interaction,
        content="❌ コマンド実行中にエラーが発生しました。コンソールログを確認してください。",
    )

# =========================================================
# 起動
# =========================================================

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN が設定されていません。")

bot.run(TOKEN, log_handler=None)
