# -*- coding: utf-8 -*-
"""
Puraudhia Management Bot - 運営専用 完成版
discord.py 2.x / Python 3.11+

主な機能
- 指定カテゴリーのHoliday（お休みDAY）一括休止・復元
- 毎週土曜0:00休止 / 月曜0:00再開（日本時間）
- VC・テキストの権限を休止前の状態へ正確に復元
- 自動更新管理ダッシュボード
- VC入退室・滞在時間の記録
- 加入・退出・BAN・Unban・メッセージ削除/編集ログ
- ロール・ニックネーム・タイムアウト変更ログ
- 警告・管理メモ・メンバーカルテ
- Timeout / Kick / BAN
- チャンネルロック・解除
- 設定バックアップ（JSON）
- SQLite保存
- 毎日0時のデイリーミッション自動投稿
- VC滞在・VC参加・チャットミッション自動判定
- 達成一覧・報酬配布待ち・配布済み管理
- 全管理コマンドを管理者または指定管理ロールのみに制限

必要権限
- チャンネル管理
- ロール管理
- メンバーを管理
- メッセージ管理
- 監査ログを表示
- メンバーをタイムアウト
- Kick / BAN（使用する場合）

Developer Portalで有効化
- SERVER MEMBERS INTENT
- MESSAGE CONTENT INTENT
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
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

# 既存Puraudhia管理Botの設定を初期値として使用
GUILD_ID = int(os.getenv("GUILD_ID", "1458016711344263170"))
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "1468161318635962451"))
MANAGEMENT_LOG_CHANNEL_ID = int(
    os.getenv("MANAGEMENT_LOG_CHANNEL_ID", "1523582826623008863")
)
JOIN_LEAVE_LOG_CHANNEL_ID = int(
    os.getenv("JOIN_LEAVE_LOG_CHANNEL_ID", "1472429718144811050")
)
DASHBOARD_CHANNEL_ID = int(
    os.getenv("DASHBOARD_CHANNEL_ID", "1467734039883677856")
)

DB_PATH = Path(os.getenv("DB_PATH", "puraudhia_management.db"))
TZ = ZoneInfo("Asia/Tokyo")

DASHBOARD_UPDATE_MINUTES = 5
HOLIDAY_CHECK_SECONDS = 60
EMBED_COLOR = discord.Color.from_rgb(137, 107, 255)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("puraudhia-management")

# =========================================================
# 時刻・共通
# =========================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    return datetime.now(TZ)


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


def fmt_dt(dt: Optional[datetime], style: str = "f") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>" if dt else "記録なし"


def fmt_duration(seconds: int | float) -> str:
    seconds = max(0, int(seconds))
    days, remain = divmod(seconds, 86400)
    hours, remain = divmod(remain, 3600)
    minutes, _ = divmod(remain, 60)
    if days:
        return f"{days}日{hours}時間{minutes}分"
    if hours:
        return f"{hours}時間{minutes}分"
    return f"{minutes}分"


def truncate(value: Any, limit: int = 1000) -> str:
    text = str(value or "なし")
    return text if len(text) <= limit else text[: limit - 3] + "..."


# =========================================================
# Database
# =========================================================

class Database:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self):
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (guild_id, key)
                );

                CREATE TABLE IF NOT EXISTS holiday_categories (
                    guild_id INTEGER NOT NULL,
                    category_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, category_id)
                );

                CREATE TABLE IF NOT EXISTS holiday_backups (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    target_type TEXT NOT NULL,
                    view_channel INTEGER,
                    connect INTEGER,
                    send_messages INTEGER,
                    speak INTEGER,
                    add_reactions INTEGER,
                    create_public_threads INTEGER,
                    send_messages_in_threads INTEGER,
                    PRIMARY KEY (guild_id, channel_id, target_id)
                );

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
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS voice_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    channel_name TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_seconds INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS member_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id INTEGER,
                    details TEXT,
                    created_at TEXT NOT NULL
                );

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

                CREATE TABLE IF NOT EXISTS notes (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    updated_by INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS missions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    mission_type TEXT NOT NULL,
                    target_value INTEGER NOT NULL DEFAULT 1,
                    reward_points INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mission_days (
                    guild_id INTEGER NOT NULL,
                    mission_date TEXT NOT NULL,
                    message_id INTEGER,
                    channel_id INTEGER,
                    created_at TEXT NOT NULL,
                    closed_at TEXT,
                    PRIMARY KEY (guild_id, mission_date)
                );

                CREATE TABLE IF NOT EXISTS mission_progress (
                    guild_id INTEGER NOT NULL,
                    mission_date TEXT NOT NULL,
                    mission_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    progress_value INTEGER NOT NULL DEFAULT 0,
                    completed_at TEXT,
                    reward_status TEXT NOT NULL DEFAULT 'pending',
                    rewarded_at TEXT,
                    rewarded_by INTEGER,
                    PRIMARY KEY (guild_id, mission_date, mission_id, user_id)
                );
                """
            )

    async def run(self, fn, *args):
        async with self.lock:
            return await asyncio.to_thread(fn, *args)

    def _get_setting(self, guild_id: int, key: str, default: Optional[str] = None):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE guild_id=? AND key=?",
                (guild_id, key),
            ).fetchone()
            return row["value"] if row else default

    async def get_setting(self, guild_id: int, key: str, default: Optional[str] = None):
        return await self.run(self._get_setting, guild_id, key, default)

    def _set_setting(self, guild_id: int, key: str, value: Any):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(guild_id,key,value)
                VALUES(?,?,?)
                ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value
                """,
                (guild_id, key, str(value)),
            )

    async def set_setting(self, guild_id: int, key: str, value: Any):
        await self.run(self._set_setting, guild_id, key, value)

    def _add_holiday_category(self, guild_id: int, category_id: int):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO holiday_categories VALUES(?,?)",
                (guild_id, category_id),
            )

    async def add_holiday_category(self, guild_id: int, category_id: int):
        await self.run(self._add_holiday_category, guild_id, category_id)

    def _remove_holiday_category(self, guild_id: int, category_id: int):
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM holiday_categories WHERE guild_id=? AND category_id=?",
                (guild_id, category_id),
            )

    async def remove_holiday_category(self, guild_id: int, category_id: int):
        await self.run(self._remove_holiday_category, guild_id, category_id)

    def _holiday_categories(self, guild_id: int):
        with self.connect() as conn:
            return [
                int(r["category_id"])
                for r in conn.execute(
                    "SELECT category_id FROM holiday_categories WHERE guild_id=?",
                    (guild_id,),
                )
            ]

    async def holiday_categories(self, guild_id: int):
        return await self.run(self._holiday_categories, guild_id)

    def _save_permission_backup(
        self,
        guild_id: int,
        channel_id: int,
        target_id: int,
        target_type: str,
        overwrite: discord.PermissionOverwrite,
    ):
        def tri(value: Optional[bool]):
            return None if value is None else int(value)

        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO holiday_backups(
                    guild_id, channel_id, target_id, target_type,
                    view_channel, connect, send_messages, speak,
                    add_reactions, create_public_threads, send_messages_in_threads
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    guild_id,
                    channel_id,
                    target_id,
                    target_type,
                    tri(overwrite.view_channel),
                    tri(overwrite.connect),
                    tri(overwrite.send_messages),
                    tri(overwrite.speak),
                    tri(overwrite.add_reactions),
                    tri(overwrite.create_public_threads),
                    tri(overwrite.send_messages_in_threads),
                ),
            )

    async def save_permission_backup(self, *args):
        await self.run(self._save_permission_backup, *args)

    def _permission_backups(self, guild_id: int):
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM holiday_backups WHERE guild_id=?",
                    (guild_id,),
                )
            ]

    async def permission_backups(self, guild_id: int):
        return await self.run(self._permission_backups, guild_id)

    def _clear_permission_backups(self, guild_id: int):
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM holiday_backups WHERE guild_id=?",
                (guild_id,),
            )

    async def clear_permission_backups(self, guild_id: int):
        await self.run(self._clear_permission_backups, guild_id)

    def _upsert_member(self, member: discord.Member):
        now = to_iso()
        joined = to_iso(member.joined_at) if member.joined_at else None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO members(
                    guild_id,user_id,username,display_name,joined_at,
                    first_seen_at,left_at
                ) VALUES(?,?,?,?,?,?,NULL)
                ON CONFLICT(guild_id,user_id) DO UPDATE SET
                    username=excluded.username,
                    display_name=excluded.display_name,
                    joined_at=COALESCE(members.joined_at,excluded.joined_at),
                    left_at=NULL
                """,
                (
                    member.guild.id,
                    member.id,
                    str(member),
                    member.display_name,
                    joined,
                    now,
                ),
            )

    async def upsert_member(self, member: discord.Member):
        await self.run(self._upsert_member, member)

    def _mark_left(self, guild_id: int, user_id: int):
        with self.connect() as conn:
            conn.execute(
                "UPDATE members SET left_at=? WHERE guild_id=? AND user_id=?",
                (to_iso(), guild_id, user_id),
            )

    async def mark_left(self, guild_id: int, user_id: int):
        await self.run(self._mark_left, guild_id, user_id)

    def _start_voice(self, member: discord.Member, channel: discord.abc.GuildChannel):
        now = to_iso()
        with self.connect() as conn:
            open_row = conn.execute(
                """
                SELECT id FROM voice_sessions
                WHERE guild_id=? AND user_id=? AND ended_at IS NULL
                """,
                (member.guild.id, member.id),
            ).fetchone()
            if open_row:
                return
            conn.execute(
                """
                INSERT INTO voice_sessions(
                    guild_id,user_id,channel_id,channel_name,started_at
                ) VALUES(?,?,?,?,?)
                """,
                (member.guild.id, member.id, channel.id, channel.name, now),
            )
            conn.execute(
                """
                UPDATE members
                SET first_vc_at=COALESCE(first_vc_at,?),last_vc_at=?
                WHERE guild_id=? AND user_id=?
                """,
                (now, now, member.guild.id, member.id),
            )

    async def start_voice(self, member: discord.Member, channel):
        await self.run(self._start_voice, member, channel)

    def _end_voice(self, guild_id: int, user_id: int):
        now = utcnow()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id,started_at FROM voice_sessions
                WHERE guild_id=? AND user_id=? AND ended_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (guild_id, user_id),
            ).fetchone()
            if not row:
                return 0
            started = parse_dt(row["started_at"]) or now
            seconds = max(0, int((now - started).total_seconds()))
            conn.execute(
                """
                UPDATE voice_sessions
                SET ended_at=?,duration_seconds=?
                WHERE id=?
                """,
                (to_iso(now), seconds, row["id"]),
            )
            conn.execute(
                "UPDATE members SET last_vc_at=? WHERE guild_id=? AND user_id=?",
                (to_iso(now), guild_id, user_id),
            )
            return seconds

    async def end_voice(self, guild_id: int, user_id: int):
        return await self.run(self._end_voice, guild_id, user_id)

    def _add_event(
        self,
        guild_id: int,
        user_id: int,
        event_type: str,
        actor_id: Optional[int] = None,
        details: Optional[str] = None,
    ):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO member_events(
                    guild_id,user_id,event_type,actor_id,details,created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (guild_id, user_id, event_type, actor_id, details, to_iso()),
            )

    async def add_event(self, *args):
        await self.run(self._add_event, *args)

    def _add_warning(self, guild_id: int, user_id: int, moderator_id: int, reason: str):
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO warnings(
                    guild_id,user_id,moderator_id,reason,created_at
                ) VALUES(?,?,?,?,?)
                """,
                (guild_id, user_id, moderator_id, reason, to_iso()),
            )
            return int(cur.lastrowid)

    async def add_warning(self, *args):
        return await self.run(self._add_warning, *args)

    def _warnings(self, guild_id: int, user_id: int):
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM warnings
                    WHERE guild_id=? AND user_id=?
                    ORDER BY created_at DESC LIMIT 20
                    """,
                    (guild_id, user_id),
                )
            ]

    async def warnings(self, guild_id: int, user_id: int):
        return await self.run(self._warnings, guild_id, user_id)

    def _resolve_warning(self, guild_id: int, warning_id: int, moderator_id: int):
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE warnings SET is_active=0,resolved_at=?,resolved_by=?
                WHERE guild_id=? AND id=? AND is_active=1
                """,
                (to_iso(), moderator_id, guild_id, warning_id),
            )
            return cur.rowcount > 0

    async def resolve_warning(self, *args):
        return await self.run(self._resolve_warning, *args)

    def _set_note(self, guild_id: int, user_id: int, note: str, editor_id: int):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO notes(guild_id,user_id,note,updated_by,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id,user_id) DO UPDATE SET
                    note=excluded.note,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
                """,
                (guild_id, user_id, note, editor_id, to_iso()),
            )

    async def set_note(self, *args):
        await self.run(self._set_note, *args)

    def _member_card(self, guild_id: int, user_id: int):
        with self.connect() as conn:
            member = conn.execute(
                "SELECT * FROM members WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
            vc = conn.execute(
                """
                SELECT COUNT(*) sessions,
                       COALESCE(SUM(duration_seconds),0) seconds,
                       MAX(COALESCE(ended_at,started_at)) last_vc
                FROM voice_sessions WHERE guild_id=? AND user_id=?
                """,
                (guild_id, user_id),
            ).fetchone()
            warnings = conn.execute(
                """
                SELECT COUNT(*) c FROM warnings
                WHERE guild_id=? AND user_id=? AND is_active=1
                """,
                (guild_id, user_id),
            ).fetchone()
            note = conn.execute(
                "SELECT * FROM notes WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
            return {
                "member": dict(member) if member else None,
                "sessions": int(vc["sessions"] or 0),
                "seconds": int(vc["seconds"] or 0),
                "last_vc": vc["last_vc"],
                "warnings": int(warnings["c"] or 0),
                "note": dict(note) if note else None,
            }

    async def member_card(self, guild_id: int, user_id: int):
        return await self.run(self._member_card, guild_id, user_id)

    def _dashboard_stats(self, guild_id: int, since: datetime):
        with self.connect() as conn:
            since_iso = to_iso(since)
            sessions = conn.execute(
                """
                SELECT user_id,duration_seconds,started_at,ended_at
                FROM voice_sessions
                WHERE guild_id=? AND started_at>=?
                """,
                (guild_id, since_iso),
            ).fetchall()
            events = conn.execute(
                """
                SELECT event_type,COUNT(*) c FROM member_events
                WHERE guild_id=? AND created_at>=?
                GROUP BY event_type
                """,
                (guild_id, since_iso),
            ).fetchall()
            return {
                "unique_vc": len({int(r["user_id"]) for r in sessions}),
                "vc_seconds": sum(int(r["duration_seconds"] or 0) for r in sessions),
                "vc_sessions": len(sessions),
                "events": {r["event_type"]: int(r["c"]) for r in events},
            }

    async def dashboard_stats(self, guild_id: int, since: datetime):
        return await self.run(self._dashboard_stats, guild_id, since)

    def _settings_export(self, guild_id: int):
        with self.connect() as conn:
            settings = {
                r["key"]: r["value"]
                for r in conn.execute(
                    "SELECT key,value FROM settings WHERE guild_id=?",
                    (guild_id,),
                )
            }
            categories = [
                int(r["category_id"])
                for r in conn.execute(
                    "SELECT category_id FROM holiday_categories WHERE guild_id=?",
                    (guild_id,),
                )
            ]
            return {"guild_id": guild_id, "settings": settings, "holiday_categories": categories}

    async def settings_export(self, guild_id: int):
        return await self.run(self._settings_export, guild_id)


    def _mission_create(
        self,
        guild_id: int,
        name: str,
        description: str,
        mission_type: str,
        target_value: int,
        reward_points: int,
        created_by: int,
    ):
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO missions(
                    guild_id,name,description,mission_type,target_value,
                    reward_points,is_active,created_by,created_at
                ) VALUES(?,?,?,?,?,?,1,?,?)
                """,
                (
                    guild_id,
                    name,
                    description,
                    mission_type,
                    target_value,
                    reward_points,
                    created_by,
                    to_iso(),
                ),
            )
            return int(cur.lastrowid)

    async def mission_create(self, *args):
        return await self.run(self._mission_create, *args)

    def _mission_list(self, guild_id: int, active_only: bool = False):
        with self.connect() as conn:
            sql = "SELECT * FROM missions WHERE guild_id=?"
            params: list[Any] = [guild_id]
            if active_only:
                sql += " AND is_active=1"
            sql += " ORDER BY id"
            return [dict(r) for r in conn.execute(sql, params)]

    async def mission_list(self, guild_id: int, active_only: bool = False):
        return await self.run(self._mission_list, guild_id, active_only)

    def _mission_set_active(self, guild_id: int, mission_id: int, active: bool):
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE missions SET is_active=? WHERE guild_id=? AND id=?",
                (1 if active else 0, guild_id, mission_id),
            )
            return cur.rowcount > 0

    async def mission_set_active(self, *args):
        return await self.run(self._mission_set_active, *args)

    def _mission_delete(self, guild_id: int, mission_id: int):
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM missions WHERE guild_id=? AND id=?",
                (guild_id, mission_id),
            )
            return cur.rowcount > 0

    async def mission_delete(self, *args):
        return await self.run(self._mission_delete, *args)

    def _mission_ensure_defaults(self, guild_id: int):
        with self.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) c FROM missions WHERE guild_id=?",
                (guild_id,),
            ).fetchone()["c"]
            if count:
                return
            defaults = [
                ("VCに30分滞在", "合計30分VCに参加する", "voice_minutes", 30, 100),
                ("VCに1回参加", "VCへ1回入室する", "voice_join", 1, 30),
                ("チャットを10回", "テキストチャンネルで10回発言する", "messages", 10, 50),
            ]
            for name, desc, mtype, target, reward in defaults:
                conn.execute(
                    """
                    INSERT INTO missions(
                        guild_id,name,description,mission_type,target_value,
                        reward_points,is_active,created_by,created_at
                    ) VALUES(?,?,?,?,?,?,1,NULL,?)
                    """,
                    (guild_id, name, desc, mtype, target, reward, to_iso()),
                )

    async def mission_ensure_defaults(self, guild_id: int):
        await self.run(self._mission_ensure_defaults, guild_id)

    def _mission_day_get(self, guild_id: int, mission_date: str):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM mission_days WHERE guild_id=? AND mission_date=?",
                (guild_id, mission_date),
            ).fetchone()
            return dict(row) if row else None

    async def mission_day_get(self, guild_id: int, mission_date: str):
        return await self.run(self._mission_day_get, guild_id, mission_date)

    def _mission_day_upsert(
        self,
        guild_id: int,
        mission_date: str,
        channel_id: int,
        message_id: int,
    ):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO mission_days(
                    guild_id,mission_date,message_id,channel_id,created_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id,mission_date) DO UPDATE SET
                    message_id=excluded.message_id,
                    channel_id=excluded.channel_id
                """,
                (guild_id, mission_date, message_id, channel_id, to_iso()),
            )

    async def mission_day_upsert(self, *args):
        await self.run(self._mission_day_upsert, *args)

    def _mission_day_close(self, guild_id: int, mission_date: str):
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE mission_days SET closed_at=?
                WHERE guild_id=? AND mission_date=? AND closed_at IS NULL
                """,
                (to_iso(), guild_id, mission_date),
            )

    async def mission_day_close(self, *args):
        await self.run(self._mission_day_close, *args)

    def _mission_add_progress(
        self,
        guild_id: int,
        mission_date: str,
        user_id: int,
        mission_type: str,
        amount: int,
    ):
        with self.connect() as conn:
            missions = conn.execute(
                """
                SELECT * FROM missions
                WHERE guild_id=? AND is_active=1 AND mission_type=?
                """,
                (guild_id, mission_type),
            ).fetchall()
            completed_ids = []
            for mission in missions:
                row = conn.execute(
                    """
                    SELECT progress_value,completed_at
                    FROM mission_progress
                    WHERE guild_id=? AND mission_date=? AND mission_id=? AND user_id=?
                    """,
                    (guild_id, mission_date, mission["id"], user_id),
                ).fetchone()
                old = int(row["progress_value"]) if row else 0
                completed_at = row["completed_at"] if row else None
                new_value = old + amount
                if not completed_at and new_value >= int(mission["target_value"]):
                    completed_at = to_iso()
                    completed_ids.append(int(mission["id"]))
                conn.execute(
                    """
                    INSERT INTO mission_progress(
                        guild_id,mission_date,mission_id,user_id,
                        progress_value,completed_at,reward_status
                    ) VALUES(?,?,?,?,?,?,'pending')
                    ON CONFLICT(guild_id,mission_date,mission_id,user_id)
                    DO UPDATE SET
                        progress_value=excluded.progress_value,
                        completed_at=COALESCE(mission_progress.completed_at,excluded.completed_at)
                    """,
                    (
                        guild_id,
                        mission_date,
                        mission["id"],
                        user_id,
                        new_value,
                        completed_at,
                    ),
                )
            return completed_ids

    async def mission_add_progress(self, *args):
        return await self.run(self._mission_add_progress, *args)

    def _mission_manual_complete(
        self,
        guild_id: int,
        mission_date: str,
        mission_id: int,
        user_id: int,
    ):
        with self.connect() as conn:
            mission = conn.execute(
                "SELECT target_value FROM missions WHERE guild_id=? AND id=?",
                (guild_id, mission_id),
            ).fetchone()
            if not mission:
                return False
            conn.execute(
                """
                INSERT INTO mission_progress(
                    guild_id,mission_date,mission_id,user_id,
                    progress_value,completed_at,reward_status
                ) VALUES(?,?,?,?,?,?,'pending')
                ON CONFLICT(guild_id,mission_date,mission_id,user_id)
                DO UPDATE SET
                    progress_value=excluded.progress_value,
                    completed_at=COALESCE(mission_progress.completed_at,excluded.completed_at)
                """,
                (
                    guild_id,
                    mission_date,
                    mission_id,
                    user_id,
                    int(mission["target_value"]),
                    to_iso(),
                ),
            )
            return True

    async def mission_manual_complete(self, *args):
        return await self.run(self._mission_manual_complete, *args)

    def _mission_completed(self, guild_id: int, mission_date: str):
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT p.*,m.name,m.reward_points,m.target_value
                    FROM mission_progress p
                    JOIN missions m ON m.id=p.mission_id
                    WHERE p.guild_id=? AND p.mission_date=?
                      AND p.completed_at IS NOT NULL
                    ORDER BY p.completed_at
                    """,
                    (guild_id, mission_date),
                )
            ]

    async def mission_completed(self, guild_id: int, mission_date: str):
        return await self.run(self._mission_completed, guild_id, mission_date)

    def _mission_pending_rewards(self, guild_id: int, mission_date: Optional[str] = None):
        with self.connect() as conn:
            sql = """
                SELECT p.*,m.name,m.reward_points
                FROM mission_progress p
                JOIN missions m ON m.id=p.mission_id
                WHERE p.guild_id=? AND p.completed_at IS NOT NULL
                  AND p.reward_status='pending'
            """
            params: list[Any] = [guild_id]
            if mission_date:
                sql += " AND p.mission_date=?"
                params.append(mission_date)
            sql += " ORDER BY p.mission_date,p.user_id,p.mission_id"
            return [dict(r) for r in conn.execute(sql, params)]

    async def mission_pending_rewards(self, guild_id: int, mission_date: Optional[str] = None):
        return await self.run(self._mission_pending_rewards, guild_id, mission_date)

    def _mission_mark_rewarded(
        self,
        guild_id: int,
        mission_date: str,
        mission_id: int,
        user_id: int,
        moderator_id: int,
    ):
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE mission_progress
                SET reward_status='rewarded',rewarded_at=?,rewarded_by=?
                WHERE guild_id=? AND mission_date=? AND mission_id=? AND user_id=?
                  AND completed_at IS NOT NULL
                """,
                (
                    to_iso(),
                    moderator_id,
                    guild_id,
                    mission_date,
                    mission_id,
                    user_id,
                ),
            )
            return cur.rowcount > 0

    async def mission_mark_rewarded(self, *args):
        return await self.run(self._mission_mark_rewarded, *args)

    def _mission_mark_all_rewarded(
        self,
        guild_id: int,
        mission_date: str,
        moderator_id: int,
    ):
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE mission_progress
                SET reward_status='rewarded',rewarded_at=?,rewarded_by=?
                WHERE guild_id=? AND mission_date=?
                  AND completed_at IS NOT NULL AND reward_status='pending'
                """,
                (to_iso(), moderator_id, guild_id, mission_date),
            )
            return cur.rowcount

    async def mission_mark_all_rewarded(self, *args):
        return await self.run(self._mission_mark_all_rewarded, *args)

db = Database(DB_PATH)
db.initialize()

# =========================================================
# Bot / 権限
# =========================================================

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True
intents.moderation = True

class ManagementBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )
        self.synced = False

    async def setup_hook(self):
        self.add_view(ManagementPanelView())
        holiday_scheduler.start()
        dashboard_updater.start()
        mission_scheduler.start()

bot = ManagementBot()


def target_guild() -> discord.Object:
    return discord.Object(id=GUILD_ID)


async def is_manager(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.guild_permissions.administrator:
        return True

    configured = await db.get_setting(
        interaction.guild.id,
        "admin_role_id",
        str(ADMIN_ROLE_ID),
    )
    try:
        role_id = int(configured or 0)
    except ValueError:
        role_id = ADMIN_ROLE_ID
    return any(role.id == role_id for role in interaction.user.roles)


def manager_only():
    async def predicate(interaction: discord.Interaction):
        if not await is_manager(interaction):
            raise app_commands.CheckFailure("このコマンドは運営専用です。")
        return True
    return app_commands.check(predicate)


async def private_reply(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    file: Optional[discord.File] = None,
    view: Optional[discord.ui.View] = None,
):
    kwargs: dict[str, Any] = {"ephemeral": True}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if file is not None:
        kwargs["file"] = file
    if view is not None:
        kwargs["view"] = view

    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


async def get_setting_channel(
    guild: discord.Guild,
    key: str,
    fallback_id: int = 0,
) -> Optional[discord.TextChannel]:
    value = await db.get_setting(guild.id, key, str(fallback_id))
    try:
        channel_id = int(value or 0)
    except ValueError:
        return None
    channel = guild.get_channel(channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


async def send_log(
    guild: discord.Guild,
    embed: discord.Embed,
    join_leave: bool = False,
):
    key = "join_leave_log_channel_id" if join_leave else "management_log_channel_id"
    fallback = JOIN_LEAVE_LOG_CHANNEL_ID if join_leave else MANAGEMENT_LOG_CHANNEL_ID
    channel = await get_setting_channel(guild, key, fallback)
    if channel:
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("ログ送信失敗")


def audit_reason(reason: Optional[str], actor: discord.abc.User) -> str:
    return f"{reason or '理由なし'} / 実行者: {actor} ({actor.id})"


# =========================================================
# Holidayシステム
# =========================================================

def bool_to_db(value: Optional[bool]) -> Optional[int]:
    return None if value is None else int(value)


def db_to_bool(value: Optional[int]) -> Optional[bool]:
    return None if value is None else bool(value)


async def selected_holiday_channels(guild: discord.Guild):
    category_ids = await db.holiday_categories(guild.id)
    channels: list[discord.abc.GuildChannel] = []
    for category_id in category_ids:
        category = guild.get_channel(category_id)
        if isinstance(category, discord.CategoryChannel):
            channels.extend(category.channels)
    return channels


async def apply_holiday(guild: discord.Guild, actor: Optional[discord.abc.User] = None):
    active = await db.get_setting(guild.id, "holiday_active", "0")
    if active == "1":
        return False, "すでにお休みモードです。"

    channels = await selected_holiday_channels(guild)
    if not channels:
        return False, "対象カテゴリーが登録されていません。"

    await db.clear_permission_backups(guild.id)

    configured_admin_role_id = await db.get_setting(
        guild.id,
        "admin_role_id",
        str(ADMIN_ROLE_ID),
    )
    try:
        admin_role_id = int(configured_admin_role_id or 0)
    except ValueError:
        admin_role_id = ADMIN_ROLE_ID

    # 管理者・Bot管理用ロールは除外。
    # @everyone と一般ロールすべてへ明示的な拒否を設定する。
    protected_role_ids = {admin_role_id}
    if guild.me:
        protected_role_ids.update(role.id for role in guild.me.roles)

    target_roles = [
        role
        for role in guild.roles
        if not role.is_bot_managed()
        and role.id not in protected_role_ids
    ]

    changed = 0
    failed = 0

    for channel in channels:
        if not isinstance(
            channel,
            (
                discord.VoiceChannel,
                discord.StageChannel,
                discord.TextChannel,
                discord.ForumChannel,
            ),
        ):
            continue

        for role in target_roles:
            original = channel.overwrites_for(role)

            await db.save_permission_backup(
                guild.id,
                channel.id,
                role.id,
                "role",
                original,
            )

            overwrite = channel.overwrites_for(role)

            if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                # お休み中はVC自体を一覧から非表示にする
                overwrite.view_channel = False
                overwrite.connect = False
                overwrite.speak = False

            elif isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                # お休み中はテキスト・フォーラム自体を一覧から非表示にする
                overwrite.view_channel = False
                overwrite.send_messages = False
                overwrite.add_reactions = False
                overwrite.create_public_threads = False
                overwrite.send_messages_in_threads = False

            try:
                await channel.set_permissions(
                    role,
                    overwrite=overwrite,
                    reason="Holidayお休みモード開始",
                )
                changed += 1
            except discord.Forbidden:
                failed += 1
                log.warning("権限変更不可: %s / %s", channel, role)
            except discord.HTTPException:
                failed += 1
                log.exception("権限変更失敗: %s / %s", channel, role)

    await db.set_setting(guild.id, "holiday_active", "1")
    await db.set_setting(guild.id, "holiday_started_at", to_iso())
    await announce_holiday(guild, True)

    embed = discord.Embed(
        title="🌙 Holidayモード開始",
        description=(
            f"対象カテゴリーへ **{changed}件** の休止権限を設定しました。\n"
            f"失敗：**{failed}件**\n\n"
            "管理者ロールとBot管理ロール以外には、対象VC・テキストが表示されません。"
        ),
        color=discord.Color.dark_purple(),
        timestamp=utcnow(),
    )
    if actor:
        embed.set_footer(text=f"実行者: {actor}")
    await send_log(guild, embed)

    return True, (
        f"Holidayモードを開始しました。設定成功：{changed}件、失敗：{failed}件。\n"
        "※対象VC・テキストは一般メンバーから完全に非表示になります。"
    )


async def restore_holiday(guild: discord.Guild, actor: Optional[discord.abc.User] = None):
    active = await db.get_setting(guild.id, "holiday_active", "0")
    backups = await db.permission_backups(guild.id)

    if active != "1" and not backups:
        return False, "現在お休みモードではありません。"

    restored = 0
    for row in backups:
        channel = guild.get_channel(int(row["channel_id"]))
        if not channel:
            continue

        target = guild.get_role(int(row["target_id"]))
        if not target:
            continue

        overwrite = channel.overwrites_for(target)
        overwrite.view_channel = db_to_bool(row["view_channel"])
        overwrite.connect = db_to_bool(row["connect"])
        overwrite.send_messages = db_to_bool(row["send_messages"])
        overwrite.speak = db_to_bool(row["speak"])
        overwrite.add_reactions = db_to_bool(row["add_reactions"])
        overwrite.create_public_threads = db_to_bool(row["create_public_threads"])
        overwrite.send_messages_in_threads = db_to_bool(row["send_messages_in_threads"])

        try:
            if overwrite.is_empty():
                await channel.set_permissions(
                    target,
                    overwrite=None,
                    reason="Holidayモード終了・権限復元",
                )
            else:
                await channel.set_permissions(
                    target,
                    overwrite=overwrite,
                    reason="Holidayモード終了・権限復元",
                )
            restored += 1
        except discord.Forbidden:
            log.warning("復元権限不足: %s", channel)
        except discord.HTTPException:
            log.exception("権限復元失敗: %s", channel)

    await db.clear_permission_backups(guild.id)
    await db.set_setting(guild.id, "holiday_active", "0")
    await announce_holiday(guild, False)

    embed = discord.Embed(
        title="🌸 Holidayモード終了",
        description=f"**{restored}チャンネル** の権限を休止前へ戻しました。",
        color=discord.Color.green(),
        timestamp=utcnow(),
    )
    if actor:
        embed.set_footer(text=f"実行者: {actor}")
    await send_log(guild, embed)
    return True, f"{restored}チャンネルを復元しました。"


async def announce_holiday(guild: discord.Guild, closing: bool):
    channel = await get_setting_channel(guild, "holiday_notice_channel_id")
    if not channel:
        return

    if closing:
        embed = discord.Embed(
            title="🌙 土日はお休みDAYです",
            description=(
                "対象カテゴリーのVC・テキストをお休みにしました。\n"
                "月曜日0:00に自動で再開します。"
            ),
            color=discord.Color.dark_purple(),
            timestamp=utcnow(),
        )
    else:
        embed = discord.Embed(
            title="🌸 お休みDAY終了",
            description="対象カテゴリーを再開しました。皆さんお待たせしました！",
            color=discord.Color.green(),
            timestamp=utcnow(),
        )
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


@tasks.loop(seconds=HOLIDAY_CHECK_SECONDS)
async def holiday_scheduler():
    now = local_now()
    # weekday: 月0〜日6
    for guild in bot.guilds:
        auto = await db.get_setting(guild.id, "holiday_auto", "0")
        if auto != "1":
            continue

        active = await db.get_setting(guild.id, "holiday_active", "0")
        weekend = now.weekday() in (5, 6)

        if weekend and active != "1":
            await apply_holiday(guild)
        elif not weekend and active == "1":
            await restore_holiday(guild)


@holiday_scheduler.before_loop
async def before_holiday_scheduler():
    await bot.wait_until_ready()


# =========================================================
# Daily Mission System
# =========================================================

def mission_date_jst() -> str:
    return local_now().date().isoformat()


def mission_type_label(mission_type: str) -> str:
    return {
        "voice_minutes": "🎙 VC滞在",
        "voice_join": "🚪 VC参加",
        "messages": "💬 チャット",
        "manual": "📝 手動判定",
    }.get(mission_type, mission_type)


async def build_mission_embed(guild: discord.Guild, mission_date: str):
    missions = await db.mission_list(guild.id, active_only=True)
    completed = await db.mission_completed(guild.id, mission_date)

    completed_by_mission: dict[int, list[int]] = {}
    for row in completed:
        completed_by_mission.setdefault(int(row["mission_id"]), []).append(int(row["user_id"]))

    embed = discord.Embed(
        title=f"🎯 デイリーミッション｜{mission_date}",
        description=(
            "毎日0:00に更新されます。\n"
            "報酬ポイントは管理者が確認後、**天真爛漫Bot**で付与します。"
        ),
        color=discord.Color.from_rgb(255, 186, 73),
        timestamp=utcnow(),
    )

    if not missions:
        embed.add_field(name="ミッション未設定", value="管理者がミッションを追加してください。", inline=False)
    else:
        for mission in missions:
            done_ids = completed_by_mission.get(int(mission["id"]), [])
            users = " ".join(f"<@{uid}>" for uid in done_ids[:15])
            if len(done_ids) > 15:
                users += f"\nほか{len(done_ids)-15}人"
            value = (
                f"{mission['description'] or '説明なし'}\n"
                f"目標：**{mission['target_value']}** ／ 報酬：**{mission['reward_points']}pt**\n"
                f"達成：**{len(done_ids)}人**"
            )
            if users:
                value += f"\n{users}"
            embed.add_field(
                name=f"`#{mission['id']}` {mission_type_label(mission['mission_type'])}｜{mission['name']}",
                value=truncate(value),
                inline=False,
            )

    embed.set_footer(text="達成状況は自動更新されます")
    return embed


async def refresh_mission_message(guild: discord.Guild, mission_date: Optional[str] = None):
    date_value = mission_date or mission_date_jst()
    day = await db.mission_day_get(guild.id, date_value)
    channel = await get_setting_channel(guild, "mission_channel_id")
    if not channel:
        return None

    embed = await build_mission_embed(guild, date_value)
    message = None
    if day and day.get("message_id"):
        try:
            message = await channel.fetch_message(int(day["message_id"]))
        except (discord.NotFound, discord.HTTPException, ValueError):
            message = None

    if message:
        await message.edit(embed=embed)
    else:
        message = await channel.send(embed=embed)
        await db.mission_day_upsert(guild.id, date_value, channel.id, message.id)
    return message


async def post_daily_missions(guild: discord.Guild):
    await db.mission_ensure_defaults(guild.id)
    today = mission_date_jst()
    yesterday = (local_now().date() - timedelta(days=1)).isoformat()
    await db.mission_day_close(guild.id, yesterday)
    return await refresh_mission_message(guild, today)


@tasks.loop(minutes=1)
async def mission_scheduler():
    now = local_now()
    for guild in bot.guilds:
        await db.mission_ensure_defaults(guild.id)
        day = await db.mission_day_get(guild.id, now.date().isoformat())
        # Bot再起動後も、その日の投稿がなければ自動投稿
        if not day:
            try:
                await post_daily_missions(guild)
            except discord.HTTPException:
                log.exception("ミッション投稿失敗")
        elif now.hour == 0 and now.minute <= 2:
            try:
                await refresh_mission_message(guild, now.date().isoformat())
            except discord.HTTPException:
                log.exception("ミッション更新失敗")


@mission_scheduler.before_loop
async def before_mission_scheduler():
    await bot.wait_until_ready()


async def update_mission_progress(
    guild: discord.Guild,
    user_id: int,
    mission_type: str,
    amount: int,
):
    completed = await db.mission_add_progress(
        guild.id,
        mission_date_jst(),
        user_id,
        mission_type,
        amount,
    )
    if completed:
        try:
            await refresh_mission_message(guild)
        except discord.HTTPException:
            pass


# =========================================================
# Dashboard
# =========================================================

async def build_dashboard(guild: discord.Guild):
    now = utcnow()
    day_stats = await db.dashboard_stats(guild.id, now - timedelta(days=1))
    week_stats = await db.dashboard_stats(guild.id, now - timedelta(days=7))

    humans = [m for m in guild.members if not m.bot]
    online = [m for m in humans if m.status != discord.Status.offline]
    vc_members = {
        m.id
        for channel in guild.voice_channels
        for m in channel.members
        if not m.bot
    }

    holiday_active = await db.get_setting(guild.id, "holiday_active", "0")
    holiday_auto = await db.get_setting(guild.id, "holiday_auto", "0")
    categories = await db.holiday_categories(guild.id)

    embed = discord.Embed(
        title="🛡️ Puraudhia 管理ダッシュボード",
        color=EMBED_COLOR,
        timestamp=utcnow(),
    )
    embed.add_field(name="👥 メンバー", value=f"{len(humans)}人", inline=True)
    embed.add_field(name="🟢 オンライン", value=f"{len(online)}人", inline=True)
    embed.add_field(name="🎙 現在VC", value=f"{len(vc_members)}人", inline=True)

    embed.add_field(
        name="📊 直近24時間",
        value=(
            f"VC利用者：{day_stats['unique_vc']}人\n"
            f"VC開始：{day_stats['vc_sessions']}回\n"
            f"合計：{fmt_duration(day_stats['vc_seconds'])}"
        ),
        inline=True,
    )
    embed.add_field(
        name="📈 直近7日間",
        value=(
            f"VC利用者：{week_stats['unique_vc']}人\n"
            f"VC開始：{week_stats['vc_sessions']}回\n"
            f"合計：{fmt_duration(week_stats['vc_seconds'])}"
        ),
        inline=True,
    )
    embed.add_field(
        name="🚪 直近7日の出入り",
        value=(
            f"加入：{week_stats['events'].get('join', 0)}人\n"
            f"退出：{week_stats['events'].get('leave', 0)}人\n"
            f"BAN：{week_stats['events'].get('ban', 0)}人"
        ),
        inline=True,
    )
    embed.add_field(
        name="🌙 Holiday",
        value=(
            f"現在：{'🔴 CLOSED' if holiday_active == '1' else '🟢 OPEN'}\n"
            f"土日自動：{'ON' if holiday_auto == '1' else 'OFF'}\n"
            f"対象：{len(categories)}カテゴリー"
        ),
        inline=False,
    )
    embed.set_footer(text="5分ごとに自動更新")
    return embed


@tasks.loop(minutes=DASHBOARD_UPDATE_MINUTES)
async def dashboard_updater():
    for guild in bot.guilds:
        channel = await get_setting_channel(
            guild,
            "dashboard_channel_id",
            DASHBOARD_CHANNEL_ID,
        )
        if not channel:
            continue

        message_id = await db.get_setting(guild.id, "dashboard_message_id")
        embed = await build_dashboard(guild)

        message = None
        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
            except (discord.NotFound, discord.HTTPException, ValueError):
                message = None

        try:
            if message:
                await message.edit(embed=embed, view=ManagementPanelView())
            else:
                message = await channel.send(embed=embed, view=ManagementPanelView())
                await db.set_setting(guild.id, "dashboard_message_id", message.id)
        except discord.HTTPException:
            log.exception("ダッシュボード更新失敗")


@dashboard_updater.before_loop
async def before_dashboard():
    await bot.wait_until_ready()


# =========================================================
# 管理パネル
# =========================================================

class ManagementPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def allowed(self, interaction: discord.Interaction) -> bool:
        if await is_manager(interaction):
            return True
        await private_reply(interaction, "このパネルは運営専用です。")
        return False

    @discord.ui.button(
        label="状態確認",
        emoji="📊",
        style=discord.ButtonStyle.primary,
        custom_id="management:status",
    )
    async def status(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self.allowed(interaction):
            return
        embed = await build_dashboard(interaction.guild)
        await private_reply(interaction, embed=embed)

    @discord.ui.button(
        label="Holiday開始",
        emoji="🌙",
        style=discord.ButtonStyle.danger,
        custom_id="management:holiday_on",
    )
    async def holiday_on(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self.allowed(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        _, message = await apply_holiday(interaction.guild, interaction.user)
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(
        label="Holiday終了",
        emoji="🌸",
        style=discord.ButtonStyle.success,
        custom_id="management:holiday_off",
    )
    async def holiday_off(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self.allowed(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        _, message = await restore_holiday(interaction.guild, interaction.user)
        await interaction.followup.send(message, ephemeral=True)


# =========================================================
# Events / Logs
# =========================================================

@bot.event
async def on_ready():
    if not bot.synced:
        try:
            guild = target_guild()
            synced = await bot.tree.sync(guild=guild)
            bot.synced = True
            log.info("%s に %s件同期", GUILD_ID, len(synced))
        except Exception:
            log.exception("コマンド同期失敗")

    for guild in bot.guilds:
        await db.mission_ensure_defaults(guild.id)
        for member in guild.members:
            if not member.bot:
                await db.upsert_member(member)

        # 再起動時、VCにいる人のセッションを開始
        for channel in guild.voice_channels:
            for member in channel.members:
                if not member.bot:
                    await db.start_voice(member, channel)

    log.info("ログイン完了: %s", bot.user)


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    await db.upsert_member(member)
    await db.add_event(member.guild.id, member.id, "join")

    embed = discord.Embed(
        title="📥 メンバー加入",
        description=f"{member.mention}\n`{member}`\nID: `{member.id}`",
        color=discord.Color.green(),
        timestamp=utcnow(),
    )
    if member.created_at:
        embed.add_field(name="アカウント作成", value=fmt_dt(member.created_at), inline=False)
    await send_log(member.guild, embed, join_leave=True)


@bot.event
async def on_member_remove(member: discord.Member):
    if member.bot:
        return

    actor_id = None
    event_type = "leave"
    details = None

    try:
        async for entry in member.guild.audit_logs(
            limit=5,
            action=discord.AuditLogAction.kick,
        ):
            if entry.target and entry.target.id == member.id:
                if (utcnow() - entry.created_at).total_seconds() < 15:
                    event_type = "kick"
                    actor_id = entry.user.id if entry.user else None
                    details = entry.reason
                    break
    except discord.Forbidden:
        pass

    await db.mark_left(member.guild.id, member.id)
    await db.add_event(member.guild.id, member.id, event_type, actor_id, details)

    embed = discord.Embed(
        title="🥾 Kick" if event_type == "kick" else "📤 メンバー退出",
        description=f"`{member}`\nID: `{member.id}`",
        color=discord.Color.red(),
        timestamp=utcnow(),
    )
    if actor_id:
        embed.add_field(name="実行者", value=f"<@{actor_id}>", inline=False)
    if details:
        embed.add_field(name="理由", value=truncate(details), inline=False)
    await send_log(member.guild, embed, join_leave=True)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    actor = None
    reason = None
    try:
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
            if entry.target and entry.target.id == user.id:
                actor = entry.user
                reason = entry.reason
                break
    except discord.Forbidden:
        pass

    await db.add_event(
        guild.id,
        user.id,
        "ban",
        actor.id if actor else None,
        reason,
    )
    embed = discord.Embed(
        title="🔨 BAN",
        description=f"`{user}`\nID: `{user.id}`",
        color=discord.Color.dark_red(),
        timestamp=utcnow(),
    )
    if actor:
        embed.add_field(name="実行者", value=actor.mention, inline=True)
    if reason:
        embed.add_field(name="理由", value=truncate(reason), inline=False)
    await send_log(guild, embed)


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    await db.add_event(guild.id, user.id, "unban")
    embed = discord.Embed(
        title="🔓 Unban",
        description=f"`{user}`\nID: `{user.id}`",
        color=discord.Color.green(),
        timestamp=utcnow(),
    )
    await send_log(guild, embed)


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    if member.bot or before.channel == after.channel:
        return

    if before.channel:
        seconds = await db.end_voice(member.guild.id, member.id)
        completed_minutes = seconds // 60
        if completed_minutes > 0:
            await update_mission_progress(
                member.guild,
                member.id,
                "voice_minutes",
                completed_minutes,
            )
        embed = discord.Embed(
            title="🔴 VC退出",
            description=(
                f"{member.mention}\n"
                f"退出：{before.channel.mention}\n"
                f"滞在：**{fmt_duration(seconds)}**"
            ),
            color=discord.Color.red(),
            timestamp=utcnow(),
        )
        await send_log(member.guild, embed)

    if after.channel:
        await db.upsert_member(member)
        await db.start_voice(member, after.channel)
        await update_mission_progress(member.guild, member.id, "voice_join", 1)
        embed = discord.Embed(
            title="🟢 VC入室",
            description=f"{member.mention}\n入室：{after.channel.mention}",
            color=discord.Color.green(),
            timestamp=utcnow(),
        )
        await send_log(member.guild, embed)


@bot.event
async def on_message(message: discord.Message):
    if message.guild and not message.author.bot:
        await update_mission_progress(message.guild, message.author.id, "messages", 1)
    await bot.process_commands(message)


@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    embed = discord.Embed(
        title="🗑️ メッセージ削除",
        description=(
            f"投稿者：{message.author.mention}\n"
            f"チャンネル：{message.channel.mention}\n\n"
            f"```{truncate(message.content, 1500)}```"
        ),
        color=discord.Color.orange(),
        timestamp=utcnow(),
    )
    await send_log(message.guild, embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not before.guild or before.author.bot or before.content == after.content:
        return
    embed = discord.Embed(
        title="✏️ メッセージ編集",
        description=f"投稿者：{before.author.mention}\nチャンネル：{before.channel.mention}",
        color=discord.Color.gold(),
        timestamp=utcnow(),
    )
    embed.add_field(name="編集前", value=truncate(before.content), inline=False)
    embed.add_field(name="編集後", value=truncate(after.content), inline=False)
    embed.add_field(name="メッセージ", value=f"[移動]({after.jump_url})", inline=False)
    await send_log(before.guild, embed)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    changes = []

    if before.nick != after.nick:
        changes.append(f"ニックネーム：`{before.nick}` → `{after.nick}`")

    before_roles = {r.id: r for r in before.roles}
    after_roles = {r.id: r for r in after.roles}
    added = [r.mention for rid, r in after_roles.items() if rid not in before_roles]
    removed = [r.mention for rid, r in before_roles.items() if rid not in after_roles]

    if added:
        changes.append("追加ロール：" + " ".join(added))
    if removed:
        changes.append("解除ロール：" + " ".join(removed))

    if before.timed_out_until != after.timed_out_until:
        changes.append(
            f"Timeout：{fmt_dt(before.timed_out_until)} → {fmt_dt(after.timed_out_until)}"
        )

    if not changes:
        return

    embed = discord.Embed(
        title="👤 メンバー情報変更",
        description=f"{after.mention}\n" + "\n".join(changes),
        color=discord.Color.blurple(),
        timestamp=utcnow(),
    )
    await send_log(after.guild, embed)


# =========================================================
# 設定コマンド
# =========================================================

@bot.tree.command(name="管理パネル", description="運営専用管理パネルを表示します")
@app_commands.guilds(target_guild())
@manager_only()
async def management_panel(interaction: discord.Interaction):
    embed = await build_dashboard(interaction.guild)
    await private_reply(interaction, embed=embed, view=ManagementPanelView())


@bot.tree.command(name="管理ロール設定", description="管理コマンドを使えるロールを設定します")
@app_commands.guilds(target_guild())
@manager_only()
async def set_admin_role(interaction: discord.Interaction, role: discord.Role):
    await db.set_setting(interaction.guild.id, "admin_role_id", role.id)
    await private_reply(interaction, f"✅ 管理ロールを {role.mention} に設定しました。")


@bot.tree.command(name="管理ログ設定", description="管理ログの送信先を設定します")
@app_commands.guilds(target_guild())
@manager_only()
async def set_management_log(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    await db.set_setting(interaction.guild.id, "management_log_channel_id", channel.id)
    await private_reply(interaction, f"✅ 管理ログを {channel.mention} に設定しました。")


@bot.tree.command(name="入退室ログ設定", description="加入・退出ログの送信先を設定します")
@app_commands.guilds(target_guild())
@manager_only()
async def set_join_leave_log(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    await db.set_setting(interaction.guild.id, "join_leave_log_channel_id", channel.id)
    await private_reply(interaction, f"✅ 入退室ログを {channel.mention} に設定しました。")


@bot.tree.command(name="ダッシュボード設定", description="自動更新ダッシュボードのチャンネルを設定します")
@app_commands.guilds(target_guild())
@manager_only()
async def set_dashboard_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    await db.set_setting(interaction.guild.id, "dashboard_channel_id", channel.id)
    await db.set_setting(interaction.guild.id, "dashboard_message_id", "0")
    await private_reply(interaction, f"✅ ダッシュボードを {channel.mention} に設定しました。")


@bot.tree.command(name="ダッシュボード設置", description="管理ダッシュボードを今すぐ設置します")
@app_commands.guilds(target_guild())
@manager_only()
async def dashboard_create(interaction: discord.Interaction):
    channel = await get_setting_channel(
        interaction.guild,
        "dashboard_channel_id",
        DASHBOARD_CHANNEL_ID,
    )
    if not channel:
        return await private_reply(interaction, "❌ ダッシュボードチャンネルが見つかりません。")

    embed = await build_dashboard(interaction.guild)
    message = await channel.send(embed=embed, view=ManagementPanelView())
    await db.set_setting(interaction.guild.id, "dashboard_message_id", message.id)
    await private_reply(interaction, f"✅ {channel.mention} に設置しました。")


# =========================================================
# Holiday commands
# =========================================================

holiday_group = app_commands.Group(
    name="holiday",
    description="お休みDAYを管理します",
    guild_ids=[GUILD_ID],
)


@holiday_group.command(name="カテゴリー追加", description="お休み対象カテゴリーを追加します")
@manager_only()
async def holiday_add(
    interaction: discord.Interaction,
    category: discord.CategoryChannel,
):
    await db.add_holiday_category(interaction.guild.id, category.id)
    await private_reply(interaction, f"✅ **{category.name}** を対象に追加しました。")


@holiday_group.command(name="カテゴリー削除", description="お休み対象カテゴリーから削除します")
@manager_only()
async def holiday_remove(
    interaction: discord.Interaction,
    category: discord.CategoryChannel,
):
    active = await db.get_setting(interaction.guild.id, "holiday_active", "0")
    if active == "1":
        return await private_reply(
            interaction,
            "先に `/holiday 終了` でお休みモードを終了してください。",
        )
    await db.remove_holiday_category(interaction.guild.id, category.id)
    await private_reply(interaction, f"✅ **{category.name}** を対象から削除しました。")


@holiday_group.command(name="カテゴリー一覧", description="登録済みカテゴリーを表示します")
@manager_only()
async def holiday_list(interaction: discord.Interaction):
    ids = await db.holiday_categories(interaction.guild.id)
    lines = []
    for cid in ids:
        category = interaction.guild.get_channel(cid)
        lines.append(f"• {category.name if category else '削除済み'} (`{cid}`)")
    await private_reply(
        interaction,
        embed=discord.Embed(
            title="🌙 Holiday対象カテゴリー",
            description="\n".join(lines) if lines else "登録されていません。",
            color=EMBED_COLOR,
        ),
    )


@holiday_group.command(name="開始", description="対象カテゴリーを今すぐ休止します")
@manager_only()
async def holiday_start(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    _, message = await apply_holiday(interaction.guild, interaction.user)
    await interaction.followup.send(message, ephemeral=True)


@holiday_group.command(name="終了", description="対象カテゴリーを今すぐ復元します")
@manager_only()
async def holiday_end(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    _, message = await restore_holiday(interaction.guild, interaction.user)
    await interaction.followup.send(message, ephemeral=True)


@holiday_group.command(name="自動設定", description="毎週土日に自動休止するか設定します")
@app_commands.describe(有効="ONで土日自動休止、OFFで手動管理")
@manager_only()
async def holiday_auto(interaction: discord.Interaction, 有効: bool):
    await db.set_setting(interaction.guild.id, "holiday_auto", "1" if 有効 else "0")
    await private_reply(
        interaction,
        f"✅ 土日自動Holidayを **{'ON' if 有効 else 'OFF'}** にしました。",
    )


@holiday_group.command(name="通知設定", description="開始・終了のお知らせチャンネルを設定します")
@manager_only()
async def holiday_notice(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    await db.set_setting(interaction.guild.id, "holiday_notice_channel_id", channel.id)
    await private_reply(interaction, f"✅ Holiday通知先を {channel.mention} に設定しました。")


@holiday_group.command(name="状態", description="現在のHoliday設定を確認します")
@manager_only()
async def holiday_status(interaction: discord.Interaction):
    active = await db.get_setting(interaction.guild.id, "holiday_active", "0")
    auto = await db.get_setting(interaction.guild.id, "holiday_auto", "0")
    ids = await db.holiday_categories(interaction.guild.id)
    notice = await get_setting_channel(interaction.guild, "holiday_notice_channel_id")

    embed = discord.Embed(title="🌙 Holiday設定", color=EMBED_COLOR)
    embed.add_field(
        name="現在",
        value="🔴 お休み中" if active == "1" else "🟢 通常営業",
        inline=True,
    )
    embed.add_field(name="土日自動", value="ON" if auto == "1" else "OFF", inline=True)
    embed.add_field(name="対象数", value=f"{len(ids)}カテゴリー", inline=True)
    embed.add_field(
        name="通知先",
        value=notice.mention if notice else "未設定",
        inline=False,
    )
    await private_reply(interaction, embed=embed)


bot.tree.add_command(holiday_group)

# =========================================================
# Mission commands
# =========================================================

mission_group = app_commands.Group(
    name="ミッション",
    description="デイリーミッションを管理します",
    guild_ids=[GUILD_ID],
)


@mission_group.command(name="チャンネル設定", description="毎日0時にミッションを投稿するチャンネルを設定します")
@manager_only()
async def mission_channel_set(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    await db.set_setting(interaction.guild.id, "mission_channel_id", channel.id)
    await db.set_setting(interaction.guild.id, "mission_last_channel_id", channel.id)
    await private_reply(interaction, f"✅ ミッション投稿先を {channel.mention} に設定しました。")


@mission_group.command(name="今すぐ投稿", description="本日のミッションを今すぐ投稿・更新します")
@manager_only()
async def mission_post_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    message = await post_daily_missions(interaction.guild)
    if message:
        await interaction.followup.send("✅ 本日のミッションを投稿・更新しました。", ephemeral=True)
    else:
        await interaction.followup.send("❌ 先にミッションチャンネルを設定してください。", ephemeral=True)


@mission_group.command(name="追加", description="デイリーミッションを追加します")
@app_commands.describe(
    種類="VC滞在・VC参加・チャット・手動判定から選択",
    目標値="VC滞在なら分数、チャットなら回数",
    報酬ポイント="天真爛漫Botで後から付与するポイント数",
)
@app_commands.choices(
    種類=[
        app_commands.Choice(name="VC滞在（分）", value="voice_minutes"),
        app_commands.Choice(name="VC参加（回）", value="voice_join"),
        app_commands.Choice(name="チャット（回）", value="messages"),
        app_commands.Choice(name="手動判定", value="manual"),
    ]
)
@manager_only()
async def mission_add(
    interaction: discord.Interaction,
    名前: str,
    説明: str,
    種類: app_commands.Choice[str],
    目標値: app_commands.Range[int, 1, 100000],
    報酬ポイント: app_commands.Range[int, 0, 1000000],
):
    mission_id = await db.mission_create(
        interaction.guild.id,
        名前,
        説明,
        種類.value,
        目標値,
        報酬ポイント,
        interaction.user.id,
    )
    await private_reply(interaction, f"✅ ミッション `#{mission_id}` を追加しました。")
    await refresh_mission_message(interaction.guild)


@mission_group.command(name="一覧", description="登録されているミッションを表示します")
@manager_only()
async def mission_list_command(interaction: discord.Interaction):
    missions = await db.mission_list(interaction.guild.id)
    lines = []
    for m in missions:
        status = "ON" if m["is_active"] else "OFF"
        lines.append(
            f"`#{m['id']}` **{m['name']}** [{status}]\n"
            f"└ {mission_type_label(m['mission_type'])} / 目標 {m['target_value']} / {m['reward_points']}pt"
        )
    embed = discord.Embed(
        title="🎯 ミッション一覧",
        description="\n\n".join(lines) if lines else "ミッションはありません。",
        color=EMBED_COLOR,
    )
    await private_reply(interaction, embed=embed)


@mission_group.command(name="有効切替", description="ミッションを有効・無効にします")
@manager_only()
async def mission_toggle(
    interaction: discord.Interaction,
    mission_id: int,
    有効: bool,
):
    ok = await db.mission_set_active(
        interaction.guild.id,
        mission_id,
        有効,
    )
    if not ok:
        return await private_reply(interaction, "❌ ミッションが見つかりません。")
    await private_reply(interaction, f"✅ ミッション `#{mission_id}` を {'ON' if 有効 else 'OFF'} にしました。")
    await refresh_mission_message(interaction.guild)


@mission_group.command(name="削除", description="ミッションを完全に削除します")
@manager_only()
async def mission_delete_command(
    interaction: discord.Interaction,
    mission_id: int,
):
    ok = await db.mission_delete(interaction.guild.id, mission_id)
    await private_reply(
        interaction,
        "✅ 削除しました。" if ok else "❌ ミッションが見つかりません。",
    )
    if ok:
        await refresh_mission_message(interaction.guild)


@mission_group.command(name="手動達成", description="指定メンバーをミッション達成にします")
@manager_only()
async def mission_manual_complete(
    interaction: discord.Interaction,
    mission_id: int,
    member: discord.Member,
):
    ok = await db.mission_manual_complete(
        interaction.guild.id,
        mission_date_jst(),
        mission_id,
        member.id,
    )
    if not ok:
        return await private_reply(interaction, "❌ ミッションが見つかりません。")
    await refresh_mission_message(interaction.guild)
    await private_reply(interaction, f"✅ {member.mention} をミッション `#{mission_id}` 達成にしました。")


@mission_group.command(name="達成一覧", description="指定日のミッション達成者を表示します")
@manager_only()
async def mission_completed_list(
    interaction: discord.Interaction,
    日付: Optional[str] = None,
):
    date_value = 日付 or mission_date_jst()
    rows = await db.mission_completed(interaction.guild.id, date_value)
    if not rows:
        return await private_reply(interaction, f"ℹ️ {date_value} の達成記録はありません。")

    lines = []
    for row in rows[:50]:
        status = "✅ 配布済" if row["reward_status"] == "rewarded" else "🎁 配布待ち"
        lines.append(
            f"{status} <@{row['user_id']}>｜`#{row['mission_id']}` {row['name']}｜{row['reward_points']}pt"
        )
    embed = discord.Embed(
        title=f"🏆 ミッション達成一覧｜{date_value}",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    await private_reply(interaction, embed=embed)


@mission_group.command(name="配布待ち", description="天真爛漫Botでポイントを入れる対象一覧を表示します")
@manager_only()
async def mission_pending_list(
    interaction: discord.Interaction,
    日付: Optional[str] = None,
):
    rows = await db.mission_pending_rewards(interaction.guild.id, 日付)
    if not rows:
        return await private_reply(interaction, "✅ 配布待ちはありません。")

    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        uid = int(row["user_id"])
        item = grouped.setdefault(uid, {"total": 0, "items": []})
        item["total"] += int(row["reward_points"])
        item["items"].append(f"#{row['mission_id']} {row['name']}")

    lines = []
    for uid, data in list(grouped.items())[:40]:
        lines.append(
            f"<@{uid}> → **{data['total']}pt**\n"
            f"└ {', '.join(data['items'])}"
        )
    embed = discord.Embed(
        title="🎁 ミッション報酬・配布待ち",
        description="\n\n".join(lines),
        color=discord.Color.orange(),
    )
    embed.set_footer(text="ポイントは天真爛漫Botで付与後、配布済みにしてください")
    await private_reply(interaction, embed=embed)


@mission_group.command(name="配布済み", description="1件の報酬を配布済みにします")
@manager_only()
async def mission_rewarded_one(
    interaction: discord.Interaction,
    日付: str,
    mission_id: int,
    member: discord.Member,
):
    ok = await db.mission_mark_rewarded(
        interaction.guild.id,
        日付,
        mission_id,
        member.id,
        interaction.user.id,
    )
    await private_reply(
        interaction,
        "✅ 配布済みにしました。" if ok else "❌ 対象の達成記録が見つかりません。",
    )
    if ok and 日付 == mission_date_jst():
        await refresh_mission_message(interaction.guild)


@mission_group.command(name="全件配布済み", description="指定日の配布待ちをすべて配布済みにします")
@manager_only()
async def mission_rewarded_all(
    interaction: discord.Interaction,
    日付: Optional[str] = None,
):
    date_value = 日付 or mission_date_jst()
    count = await db.mission_mark_all_rewarded(
        interaction.guild.id,
        date_value,
        interaction.user.id,
    )
    await private_reply(interaction, f"✅ {date_value} の **{count}件** を配布済みにしました。")
    if date_value == mission_date_jst():
        await refresh_mission_message(interaction.guild)


bot.tree.add_command(mission_group)

# =========================================================
# Member management
# =========================================================

@bot.tree.command(name="警告追加", description="メンバーに運営警告を記録します")
@app_commands.guilds(target_guild())
@manager_only()
async def warning_add(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
):
    warning_id = await db.add_warning(
        interaction.guild.id,
        member.id,
        interaction.user.id,
        reason,
    )
    await db.add_event(
        interaction.guild.id,
        member.id,
        "warning",
        interaction.user.id,
        reason,
    )
    await private_reply(interaction, f"✅ 警告 `#{warning_id}` を記録しました。")


@bot.tree.command(name="警告一覧", description="メンバーの警告履歴を確認します")
@app_commands.guilds(target_guild())
@manager_only()
async def warning_list(
    interaction: discord.Interaction,
    member: discord.Member,
):
    rows = await db.warnings(interaction.guild.id, member.id)
    lines = []
    for row in rows:
        status = "有効" if row["is_active"] else "解決済み"
        dt = parse_dt(row["created_at"])
        lines.append(
            f"`#{row['id']}` **{status}** {fmt_dt(dt, 'd')}\n"
            f"└ {truncate(row['reason'], 250)}"
        )
    embed = discord.Embed(
        title=f"⚠️ {member.display_name}の警告",
        description="\n\n".join(lines) if lines else "警告はありません。",
        color=discord.Color.orange(),
    )
    await private_reply(interaction, embed=embed)


@bot.tree.command(name="警告解決", description="警告を解決済みにします")
@app_commands.guilds(target_guild())
@manager_only()
async def warning_resolve(interaction: discord.Interaction, warning_id: int):
    ok = await db.resolve_warning(
        interaction.guild.id,
        warning_id,
        interaction.user.id,
    )
    await private_reply(
        interaction,
        "✅ 解決済みにしました。" if ok else "❌ 有効な警告が見つかりません。",
    )


@bot.tree.command(name="管理メモ", description="メンバーの運営メモを保存します")
@app_commands.guilds(target_guild())
@manager_only()
async def member_note(
    interaction: discord.Interaction,
    member: discord.Member,
    note: str,
):
    await db.set_note(
        interaction.guild.id,
        member.id,
        note,
        interaction.user.id,
    )
    await private_reply(interaction, f"✅ {member.mention} の管理メモを保存しました。")


@bot.tree.command(name="メンバーカルテ", description="メンバーの管理情報を確認します")
@app_commands.guilds(target_guild())
@manager_only()
async def member_card(
    interaction: discord.Interaction,
    member: discord.Member,
):
    data = await db.member_card(interaction.guild.id, member.id)
    raw_member = data["member"] or {}
    note = data["note"]

    embed = discord.Embed(
        title=f"📋 {member.display_name}のカルテ",
        description=f"{member.mention}\nID: `{member.id}`",
        color=EMBED_COLOR,
        timestamp=utcnow(),
    )
    embed.add_field(
        name="加入",
        value=fmt_dt(parse_dt(raw_member.get("joined_at"))),
        inline=True,
    )
    embed.add_field(
        name="最終VC",
        value=fmt_dt(parse_dt(data["last_vc"]), "R"),
        inline=True,
    )
    embed.add_field(
        name="VC実績",
        value=f"{data['sessions']}回 / {fmt_duration(data['seconds'])}",
        inline=True,
    )
    embed.add_field(name="有効警告", value=f"{data['warnings']}件", inline=True)
    embed.add_field(
        name="管理メモ",
        value=truncate(note["note"] if note else "なし"),
        inline=False,
    )
    await private_reply(interaction, embed=embed)


@bot.tree.command(name="タイムアウト", description="メンバーをタイムアウトします")
@app_commands.guilds(target_guild())
@manager_only()
async def timeout_member(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: app_commands.Range[int, 1, 40320],
    reason: Optional[str] = None,
):
    until = utcnow() + timedelta(minutes=minutes)
    try:
        await member.timeout(until, reason=audit_reason(reason, interaction.user))
        await db.add_event(
            interaction.guild.id,
            member.id,
            "timeout",
            interaction.user.id,
            reason,
        )
        await private_reply(interaction, f"✅ {member.mention} を{minutes}分タイムアウトしました。")
    except discord.Forbidden:
        await private_reply(interaction, "❌ 権限またはロール順位を確認してください。")


@bot.tree.command(name="タイムアウト解除", description="タイムアウトを解除します")
@app_commands.guilds(target_guild())
@manager_only()
async def timeout_remove(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: Optional[str] = None,
):
    try:
        await member.timeout(None, reason=audit_reason(reason, interaction.user))
        await private_reply(interaction, f"✅ {member.mention} のタイムアウトを解除しました。")
    except discord.Forbidden:
        await private_reply(interaction, "❌ 権限またはロール順位を確認してください。")


@bot.tree.command(name="キック", description="メンバーをサーバーからKickします")
@app_commands.guilds(target_guild())
@manager_only()
async def kick_member(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: Optional[str] = None,
):
    try:
        await member.kick(reason=audit_reason(reason, interaction.user))
        await private_reply(interaction, f"✅ {member} をKickしました。")
    except discord.Forbidden:
        await private_reply(interaction, "❌ Kickできません。権限とロール順位を確認してください。")


@bot.tree.command(name="BAN", description="メンバーをBANします")
@app_commands.guilds(target_guild())
@manager_only()
async def ban_member(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: Optional[str] = None,
):
    try:
        await member.ban(reason=audit_reason(reason, interaction.user))
        await private_reply(interaction, f"✅ {member} をBANしました。")
    except discord.Forbidden:
        await private_reply(interaction, "❌ BANできません。権限とロール順位を確認してください。")


# =========================================================
# Channel management
# =========================================================

@bot.tree.command(name="チャンネルロック", description="指定テキストチャンネルを一般メンバー書込不可にします")
@app_commands.guilds(target_guild())
@manager_only()
async def channel_lock(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite,
        reason=audit_reason("チャンネルロック", interaction.user),
    )
    await private_reply(interaction, f"🔒 {channel.mention} をロックしました。")


@bot.tree.command(name="チャンネル解除", description="指定テキストチャンネルの書込設定を未指定に戻します")
@app_commands.guilds(target_guild())
@manager_only()
async def channel_unlock(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    if overwrite.is_empty():
        overwrite = None
    await channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite,
        reason=audit_reason("チャンネルロック解除", interaction.user),
    )
    await private_reply(interaction, f"🔓 {channel.mention} のロックを解除しました。")


@bot.tree.command(name="一括お知らせ", description="指定チャンネルへ運営告知を送信します")
@app_commands.guilds(target_guild())
@manager_only()
async def announcement(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    message: str,
):
    embed = discord.Embed(
        title=title,
        description=message,
        color=EMBED_COLOR,
        timestamp=utcnow(),
    )
    embed.set_footer(text=f"運営: {interaction.user.display_name}")
    await channel.send(embed=embed)
    await private_reply(interaction, f"✅ {channel.mention} に送信しました。")


# =========================================================
# Backup / status
# =========================================================

@bot.tree.command(name="設定確認", description="現在の管理Bot設定を確認します")
@app_commands.guilds(target_guild())
@manager_only()
async def settings_check(interaction: discord.Interaction):
    admin_role_id = int(await db.get_setting(interaction.guild.id, "admin_role_id", ADMIN_ROLE_ID))
    admin_role = interaction.guild.get_role(admin_role_id)
    management_log = await get_setting_channel(
        interaction.guild,
        "management_log_channel_id",
        MANAGEMENT_LOG_CHANNEL_ID,
    )
    join_log = await get_setting_channel(
        interaction.guild,
        "join_leave_log_channel_id",
        JOIN_LEAVE_LOG_CHANNEL_ID,
    )
    dashboard = await get_setting_channel(
        interaction.guild,
        "dashboard_channel_id",
        DASHBOARD_CHANNEL_ID,
    )
    holiday_active = await db.get_setting(interaction.guild.id, "holiday_active", "0")
    holiday_auto = await db.get_setting(interaction.guild.id, "holiday_auto", "0")

    embed = discord.Embed(title="⚙️ 管理Bot設定", color=EMBED_COLOR)
    embed.add_field(
        name="管理ロール",
        value=admin_role.mention if admin_role else "未設定",
        inline=False,
    )
    embed.add_field(
        name="管理ログ",
        value=management_log.mention if management_log else "未設定",
        inline=True,
    )
    embed.add_field(
        name="入退室ログ",
        value=join_log.mention if join_log else "未設定",
        inline=True,
    )
    embed.add_field(
        name="ダッシュボード",
        value=dashboard.mention if dashboard else "未設定",
        inline=True,
    )
    embed.add_field(
        name="Holiday",
        value=(
            f"状態：{'休止中' if holiday_active == '1' else '通常'}\n"
            f"土日自動：{'ON' if holiday_auto == '1' else 'OFF'}"
        ),
        inline=False,
    )
    await private_reply(interaction, embed=embed)


@bot.tree.command(name="設定バックアップ", description="管理Bot設定をJSONで出力します")
@app_commands.guilds(target_guild())
@manager_only()
async def settings_backup(interaction: discord.Interaction):
    payload = await db.settings_export(interaction.guild.id)
    payload["exported_at"] = to_iso()
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    file = discord.File(
        io.BytesIO(raw),
        filename=f"management_settings_{interaction.guild.id}.json",
    )
    await private_reply(interaction, "✅ 設定バックアップです。", file=file)


# =========================================================
# Error / start
# =========================================================

@bot.tree.error
async def app_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.CheckFailure):
        message = "❌ このコマンドは運営専用です。"
    else:
        original = getattr(error, "original", error)
        log.exception(
            "Slash command error",
            exc_info=(type(original), original, original.__traceback__),
        )
        message = f"❌ エラーが発生しました：`{type(original).__name__}`"

    try:
        await private_reply(interaction, message)
    except discord.HTTPException:
        pass


if not TOKEN:
    raise RuntimeError(
        "環境変数 DISCORD_TOKEN が設定されていません。"
    )

bot.run(TOKEN)
