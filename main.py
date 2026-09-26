from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks


# =========================================================
# ✦ CROSS 管理Bot
#  - VCアクティブ管理
#  - 仮メンバー → 本メンバー復帰
#  - 殿堂メンバー特典申請
#  - 複数サーバー対応 チケット機能
#
# 必要:
#   Python 3.11+
#   discord.py 2.4+
#
# 環境変数:
#   DISCORD_TOKEN=あなたのBotトークン
#   DATABASE_PATH=data/cross_bot.db  ← 省略可
# =========================================================


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

DB_PATH = os.getenv(
    "DATABASE_PATH",
    "data/cross_bot.db",
)

# 最終VC浮上から何日で仮メンバーへ降格するか
DEMOTION_DAYS = 30

# 仮メンバー → 本メンバー復帰に必要なVC時間
RETURN_REQUIRED_SECONDS = 3 * 60 * 60

# 降格チェック間隔
CHECK_INTERVAL_MINUTES = 30


# =========================================================
# 📝 ログ
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("cross-bot")


# =========================================================
# 💾 SQLite
# =========================================================

Path(DB_PATH).parent.mkdir(
    parents=True,
    exist_ok=True,
)


def db_connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db_connect() as con:

        # ---------------------------------------------
        # サーバーごとのCROSS設定
        # ---------------------------------------------
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                temp_member_role_id INTEGER,
                full_member_role_id INTEGER,
                admin_log_channel_id INTEGER,
                hall_of_fame_role_id INTEGER,
                hall_application_channel_id INTEGER,
                hall_application_log_channel_id INTEGER
            )
            """
        )

        # ---------------------------------------------
        # VCアクティブ情報
        # ---------------------------------------------
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS member_activity (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_vc_at TEXT,
                return_seconds INTEGER NOT NULL DEFAULT 0,
                demoted_at TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )

        # ---------------------------------------------
        # 殿堂特典申請
        # ---------------------------------------------
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS hall_applications (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                applied_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by INTEGER,
                log_message_id INTEGER,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )

        # ---------------------------------------------
        # チケット設定
        # ---------------------------------------------
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_settings (
                guild_id INTEGER PRIMARY KEY,
                category_id INTEGER NOT NULL,
                staff_role_id INTEGER NOT NULL,
                log_channel_id INTEGER NOT NULL
            )
            """
        )

        # ---------------------------------------------
        # チケット
        # ---------------------------------------------
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                claimed_by INTEGER,
                created_at TEXT NOT NULL,
                closed_at TEXT,
                closed_by INTEGER
            )
            """
        )

        con.commit()


# =========================================================
# 🕐 日時
# =========================================================

def utcnow():
    return datetime.now(timezone.utc)


def datetime_to_str(
    dt: Optional[datetime],
):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def str_to_datetime(
    value: Optional[str],
):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


# =========================================================
# ⚙️ サーバー設定 DB
# =========================================================

def save_guild_settings(
    guild_id: int,
    temp_member_role_id: int,
    full_member_role_id: int,
    admin_log_channel_id: int,
    hall_of_fame_role_id: int,
    hall_application_channel_id: int,
    hall_application_log_channel_id: int,
):
    with db_connect() as con:
        con.execute(
            """
            INSERT INTO guild_settings (
                guild_id,
                temp_member_role_id,
                full_member_role_id,
                admin_log_channel_id,
                hall_of_fame_role_id,
                hall_application_channel_id,
                hall_application_log_channel_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET
                temp_member_role_id = excluded.temp_member_role_id,
                full_member_role_id = excluded.full_member_role_id,
                admin_log_channel_id = excluded.admin_log_channel_id,
                hall_of_fame_role_id = excluded.hall_of_fame_role_id,
                hall_application_channel_id = excluded.hall_application_channel_id,
                hall_application_log_channel_id = excluded.hall_application_log_channel_id
            """,
            (
                guild_id,
                temp_member_role_id,
                full_member_role_id,
                admin_log_channel_id,
                hall_of_fame_role_id,
                hall_application_channel_id,
                hall_application_log_channel_id,
            ),
        )
        con.commit()


def get_guild_settings(
    guild_id: int,
):
    with db_connect() as con:
        row = con.execute(
            """
            SELECT *
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()

    return dict(row) if row else None


# =========================================================
# 💾 VCアクティブ DB
# =========================================================

def ensure_member(
    guild_id: int,
    user_id: int,
):
    with db_connect() as con:
        con.execute(
            """
            INSERT OR IGNORE INTO member_activity (
                guild_id,
                user_id,
                last_vc_at,
                return_seconds,
                demoted_at
            )
            VALUES (?, ?, NULL, 0, NULL)
            """,
            (
                guild_id,
                user_id,
            ),
        )
        con.commit()


def set_last_vc(
    guild_id: int,
    user_id: int,
    dt: datetime,
):
    ensure_member(
        guild_id,
        user_id,
    )

    with db_connect() as con:
        con.execute(
            """
            UPDATE member_activity
            SET last_vc_at = ?
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                datetime_to_str(dt),
                guild_id,
                user_id,
            ),
        )
        con.commit()


def get_last_vc(
    guild_id: int,
    user_id: int,
):
    ensure_member(
        guild_id,
        user_id,
    )

    with db_connect() as con:
        row = con.execute(
            """
            SELECT last_vc_at
            FROM member_activity
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()

    if not row:
        return None

    return str_to_datetime(
        row["last_vc_at"]
    )


def add_return_seconds(
    guild_id: int,
    user_id: int,
    seconds: int,
):
    ensure_member(
        guild_id,
        user_id,
    )

    seconds = max(
        0,
        int(seconds),
    )

    with db_connect() as con:
        con.execute(
            """
            UPDATE member_activity
            SET return_seconds = return_seconds + ?
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                seconds,
                guild_id,
                user_id,
            ),
        )
        con.commit()


def get_return_seconds(
    guild_id: int,
    user_id: int,
):
    ensure_member(
        guild_id,
        user_id,
    )

    with db_connect() as con:
        row = con.execute(
            """
            SELECT return_seconds
            FROM member_activity
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()

    if not row:
        return 0

    return int(
        row["return_seconds"] or 0
    )


def reset_return_seconds(
    guild_id: int,
    user_id: int,
):
    ensure_member(
        guild_id,
        user_id,
    )

    with db_connect() as con:
        con.execute(
            """
            UPDATE member_activity
            SET return_seconds = 0
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        )
        con.commit()


def set_demoted(
    guild_id: int,
    user_id: int,
    demoted: bool,
):
    ensure_member(
        guild_id,
        user_id,
    )

    value = (
        datetime_to_str(
            utcnow()
        )
        if demoted
        else None
    )

    with db_connect() as con:
        con.execute(
            """
            UPDATE member_activity
            SET demoted_at = ?
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                value,
                guild_id,
                user_id,
            ),
        )
        con.commit()


# =========================================================
# 👑 殿堂特典申請 DB
# =========================================================

def get_hall_application(
    guild_id: int,
    user_id: int,
):
    with db_connect() as con:
        row = con.execute(
            """
            SELECT
                status,
                applied_at,
                decided_at,
                decided_by,
                log_message_id
            FROM hall_applications
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()

    if not row:
        return None

    return {
        "status": row["status"],
        "applied_at": str_to_datetime(
            row["applied_at"]
        ),
        "decided_at": str_to_datetime(
            row["decided_at"]
        ),
        "decided_by": row["decided_by"],
        "log_message_id": row["log_message_id"],
    }


def save_hall_application(
    guild_id: int,
    user_id: int,
):
    now = utcnow()

    with db_connect() as con:
        con.execute(
            """
            INSERT INTO hall_applications (
                guild_id,
                user_id,
                status,
                applied_at,
                decided_at,
                decided_by,
                log_message_id
            )
            VALUES (?, ?, 'pending', ?, NULL, NULL, NULL)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET
                status = 'pending',
                applied_at = excluded.applied_at,
                decided_at = NULL,
                decided_by = NULL,
                log_message_id = NULL
            """,
            (
                guild_id,
                user_id,
                datetime_to_str(now),
            ),
        )
        con.commit()

    return now


def set_hall_log_message_id(
    guild_id: int,
    user_id: int,
    message_id: int,
):
    with db_connect() as con:
        con.execute(
            """
            UPDATE hall_applications
            SET log_message_id = ?
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                message_id,
                guild_id,
                user_id,
            ),
        )
        con.commit()


def decide_hall_application(
    guild_id: int,
    user_id: int,
    status: str,
    decided_by: int,
):
    with db_connect() as con:
        con.execute(
            """
            UPDATE hall_applications
            SET
                status = ?,
                decided_at = ?,
                decided_by = ?
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                status,
                datetime_to_str(
                    utcnow()
                ),
                decided_by,
                guild_id,
                user_id,
            ),
        )
        con.commit()


def reset_hall_application(
    guild_id: int,
    user_id: int,
):
    with db_connect() as con:
        con.execute(
            """
            DELETE FROM hall_applications
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        )
        con.commit()


def get_pending_hall_applications():
    with db_connect() as con:
        rows = con.execute(
            """
            SELECT
                guild_id,
                user_id,
                log_message_id
            FROM hall_applications
            WHERE status = 'pending'
              AND log_message_id IS NOT NULL
            """
        ).fetchall()

    return [
        (
            row["guild_id"],
            row["user_id"],
            row["log_message_id"],
        )
        for row in rows
    ]


# =========================================================
# 🎫 チケット DB
# =========================================================

def save_ticket_settings(
    guild_id: int,
    category_id: int,
    staff_role_id: int,
    log_channel_id: int,
):
    with db_connect() as con:
        con.execute(
            """
            INSERT INTO ticket_settings (
                guild_id,
                category_id,
                staff_role_id,
                log_channel_id
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET
                category_id = excluded.category_id,
                staff_role_id = excluded.staff_role_id,
                log_channel_id = excluded.log_channel_id
            """,
            (
                guild_id,
                category_id,
                staff_role_id,
                log_channel_id,
            ),
        )
        con.commit()


def get_ticket_settings(
    guild_id: int,
):
    with db_connect() as con:
        row = con.execute(
            """
            SELECT *
            FROM ticket_settings
            WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()

    return dict(row) if row else None


def create_ticket_record(
    guild_id: int,
    channel_id: int,
    user_id: int,
):
    with db_connect() as con:
        con.execute(
            """
            INSERT INTO tickets (
                guild_id,
                channel_id,
                user_id,
                status,
                claimed_by,
                created_at,
                closed_at,
                closed_by
            )
            VALUES (?, ?, ?, 'open', NULL, ?, NULL, NULL)
            """,
            (
                guild_id,
                channel_id,
                user_id,
                datetime_to_str(
                    utcnow()
                ),
            ),
        )
        con.commit()


def get_ticket_by_channel(
    channel_id: int,
):
    with db_connect() as con:
        row = con.execute(
            """
            SELECT *
            FROM tickets
            WHERE channel_id = ?
            """,
            (channel_id,),
        ).fetchone()

    return dict(row) if row else None


def get_open_ticket_for_user(
    guild_id: int,
    user_id: int,
):
    with db_connect() as con:
        row = con.execute(
            """
            SELECT *
            FROM tickets
            WHERE guild_id = ?
              AND user_id = ?
              AND status = 'open'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()

    return dict(row) if row else None


def claim_ticket(
    channel_id: int,
    staff_user_id: int,
):
    with db_connect() as con:
        con.execute(
            """
            UPDATE tickets
            SET claimed_by = ?
            WHERE channel_id = ?
              AND status = 'open'
            """,
            (
                staff_user_id,
                channel_id,
            ),
        )
        con.commit()


def close_ticket_record(
    channel_id: int,
    closed_by: int,
):
    with db_connect() as con:
        con.execute(
            """
            UPDATE tickets
            SET
                status = 'closed',
                closed_at = ?,
                closed_by = ?
            WHERE channel_id = ?
            """,
            (
                datetime_to_str(
                    utcnow()
                ),
                closed_by,
                channel_id,
            ),
        )
        con.commit()


def reopen_ticket_record(
    channel_id: int,
):
    with db_connect() as con:
        con.execute(
            """
            UPDATE tickets
            SET
                status = 'open',
                closed_at = NULL,
                closed_by = NULL
            WHERE channel_id = ?
            """,
            (channel_id,),
        )
        con.commit()


def delete_ticket_record(
    channel_id: int,
):
    with db_connect() as con:
        con.execute(
            """
            DELETE FROM tickets
            WHERE channel_id = ?
            """,
            (channel_id,),
        )
        con.commit()


# =========================================================
# 🔧 共通
# =========================================================

def format_seconds(
    seconds: int,
):
    seconds = max(
        0,
        int(seconds),
    )

    hours = seconds // 3600

    minutes = (
        seconds % 3600
    ) // 60

    if hours > 0:
        return (
            f"{hours}時間"
            f"{minutes}分"
        )

    return f"{minutes}分"


def safe_channel_name(
    text: str,
):
    text = text.lower().strip()
    text = re.sub(
        r"[^a-z0-9ぁ-んァ-ヶ一-龠_-]+",
        "-",
        text,
    )
    text = text.strip("-_")

    return (
        text[:40]
        if text
        else "member"
    )


def is_admin_or_manage_guild(
    member: discord.Member,
):
    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
    )


def is_ticket_staff(
    member: discord.Member,
    staff_role: Optional[discord.Role],
):
    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
        or (
            staff_role is not None
            and staff_role in member.roles
        )
    )


async def send_admin_log(
    guild: discord.Guild,
    title: str,
    description: str,
):
    settings = get_guild_settings(
        guild.id
    )

    if not settings:
        return

    channel = guild.get_channel(
        settings["admin_log_channel_id"]
    )

    if not isinstance(
        channel,
        discord.TextChannel,
    ):
        return

    embed = discord.Embed(
        title=title,
        description=description,
        timestamp=utcnow(),
    )

    embed.set_footer(
        text="CROSS ✦ 管理ログ"
    )

    try:
        await channel.send(
            embed=embed
        )
    except Exception:
        log.exception(
            "管理ログ送信失敗"
        )


async def send_ticket_log(
    guild: discord.Guild,
    title: str,
    description: str,
):
    settings = get_ticket_settings(
        guild.id
    )

    if not settings:
        return

    channel = guild.get_channel(
        settings["log_channel_id"]
    )

    if not isinstance(
        channel,
        discord.TextChannel,
    ):
        return

    embed = discord.Embed(
        title=title,
        description=description,
        timestamp=utcnow(),
    )

    embed.set_footer(
        text="CROSS ✦ Ticket System"
    )

    try:
        await channel.send(
            embed=embed
        )
    except Exception:
        log.exception(
            "チケットログ送信失敗"
        )


# =========================================================
# 👑 殿堂申請 Embed
# =========================================================

def create_hall_application_embed(
    member: discord.Member,
):
    embed = discord.Embed(
        title="👑 殿堂メンバー特典申請",
        description=(
            f"{member.mention} さんが\n\n"
            "🏠 **番号式プライベート部屋**\n"
            "を申請しました ✦"
        ),
        timestamp=utcnow(),
    )

    embed.add_field(
        name="👤 申請者",
        value=(
            f"{member.mention}\n"
            f"`{member.id}`"
        ),
        inline=False,
    )

    embed.add_field(
        name="👑 資格",
        value="殿堂メンバー",
        inline=False,
    )

    embed.add_field(
        name="📌 状態",
        value="🟡 審査待ち",
        inline=False,
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.set_footer(
        text="CROSS ✦ 殿堂メンバー特典"
    )

    return embed


def update_application_embed_status(
    message: discord.Message,
    status_text: str,
):
    if not message.embeds:
        return None

    embed = discord.Embed.from_dict(
        message.embeds[0].to_dict()
    )

    found = False

    for index, field in enumerate(
        embed.fields
    ):
        if field.name == "📌 状態":
            embed.set_field_at(
                index,
                name="📌 状態",
                value=status_text,
                inline=False,
            )
            found = True
            break

    if not found:
        embed.add_field(
            name="📌 状態",
            value=status_text,
            inline=False,
        )

    return embed


# =========================================================
# 👑 殿堂申請ボタン
# =========================================================

class HallApplicationView(
    discord.ui.View
):
    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="プライベート部屋を申請",
        emoji="🏠",
        style=discord.ButtonStyle.primary,
        custom_id="cross_hall_private_room_apply",
    )
    async def apply_private_room(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "❌ サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        member = interaction.user

        if not isinstance(
            member,
            discord.Member,
        ):
            await interaction.response.send_message(
                "❌ メンバー情報を取得できませんでした。",
                ephemeral=True,
            )
            return

        settings = get_guild_settings(
            guild.id
        )

        if not settings:
            await interaction.response.send_message(
                "❌ このサーバーではCROSS管理設定がまだ完了していません。",
                ephemeral=True,
            )
            return

        hall_role = guild.get_role(
            settings["hall_of_fame_role_id"]
        )

        if hall_role is None:
            await interaction.response.send_message(
                "❌ 殿堂メンバーロールが見つかりません。",
                ephemeral=True,
            )
            return

        if hall_role not in member.roles:
            await interaction.response.send_message(
                (
                    "🔒 この申請は\n"
                    "**殿堂メンバー限定特典**です。\n\n"
                    "殿堂メンバーになってから申請してください ✦"
                ),
                ephemeral=True,
            )
            return

        application = get_hall_application(
            guild.id,
            member.id,
        )

        if application:
            status = application[
                "status"
            ]

            if status == "pending":
                await interaction.response.send_message(
                    (
                        "✦ すでに申請済みです！\n\n"
                        "現在 **審査待ち** です。\n"
                        "運営からの対応をお待ちください。"
                    ),
                    ephemeral=True,
                )
                return

            if status == "approved":
                await interaction.response.send_message(
                    (
                        "✅ この特典はすでに"
                        "**承認されています。**\n\n"
                        "番号式プライベート部屋については"
                        "運営までお問い合わせください。"
                    ),
                    ephemeral=True,
                )
                return

        log_channel = guild.get_channel(
            settings[
                "hall_application_log_channel_id"
            ]
        )

        if not isinstance(
            log_channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "❌ 申請ログチャンネルが見つかりません。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        save_hall_application(
            guild.id,
            member.id,
        )

        embed = create_hall_application_embed(
            member
        )

        view = HallApprovalView(
            member.id
        )

        try:
            message = await log_channel.send(
                content=(
                    f"📩 {member.mention} さんが"
                    "殿堂特典を申請しました。"
                ),
                embed=embed,
                view=view,
            )

            set_hall_log_message_id(
                guild.id,
                member.id,
                message.id,
            )

        except Exception:
            log.exception(
                "殿堂特典申請ログ送信失敗"
            )

            reset_hall_application(
                guild.id,
                member.id,
            )

            await interaction.followup.send(
                "❌ 申請の送信中にエラーが発生しました。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            (
                "✦ **申請を受け付けました！**\n\n"
                "👑 殿堂メンバー特典\n"
                "🏠 番号式プライベート部屋\n\n"
                "運営の承認をお待ちください。"
            ),
            ephemeral=True,
        )


# =========================================================
# 👑 管理者用 承認・却下
# =========================================================

class HallApprovalView(
    discord.ui.View
):
    def __init__(
        self,
        member_id: int,
    ):
        super().__init__(
            timeout=None
        )

        self.member_id = member_id

    @discord.ui.button(
        label="承認",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="cross_hall_application_approve",
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not isinstance(
            interaction.user,
            discord.Member,
        ):
            return

        if not is_admin_or_manage_guild(
            interaction.user
        ):
            await interaction.response.send_message(
                "❌ 管理者のみ使用できます。",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        if guild is None:
            return

        application = get_hall_application(
            guild.id,
            self.member_id,
        )

        if not application:
            await interaction.response.send_message(
                "❌ 申請データが見つかりません。",
                ephemeral=True,
            )
            return

        if application["status"] != "pending":
            await interaction.response.send_message(
                "⚠️ この申請はすでに処理されています。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        member = guild.get_member(
            self.member_id
        )

        decide_hall_application(
            guild.id,
            self.member_id,
            "approved",
            interaction.user.id,
        )

        message = interaction.message

        if message:
            new_embed = (
                update_application_embed_status(
                    message,
                    (
                        "✅ **承認済み**\n"
                        f"担当：{interaction.user.mention}"
                    ),
                )
            )

            try:
                await message.edit(
                    embed=new_embed,
                    view=None,
                )
            except Exception:
                log.exception(
                    "承認ログ更新失敗"
                )

        if member:
            try:
                await member.send(
                    (
                        "✦ **CROSS 殿堂メンバー特典** ✦\n\n"
                        "申請していた\n"
                        "🏠 **番号式プライベート部屋** が\n"
                        "**承認されました！** 🎉\n\n"
                        "運営が部屋作成の対応を行います。"
                    )
                )
            except discord.Forbidden:
                log.info(
                    "%s にDMを送信できませんでした",
                    member,
                )
            except Exception:
                log.exception(
                    "承認DM送信失敗"
                )

        member_text = (
            member.mention
            if member
            else f"<@{self.member_id}>"
        )

        await interaction.followup.send(
            (
                f"✅ {member_text} の申請を"
                "**承認しました。**"
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="却下",
        emoji="❌",
        style=discord.ButtonStyle.danger,
        custom_id="cross_hall_application_reject",
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not isinstance(
            interaction.user,
            discord.Member,
        ):
            return

        if not is_admin_or_manage_guild(
            interaction.user
        ):
            await interaction.response.send_message(
                "❌ 管理者のみ使用できます。",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        if guild is None:
            return

        application = get_hall_application(
            guild.id,
            self.member_id,
        )

        if not application:
            await interaction.response.send_message(
                "❌ 申請データが見つかりません。",
                ephemeral=True,
            )
            return

        if application["status"] != "pending":
            await interaction.response.send_message(
                "⚠️ この申請はすでに処理されています。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        member = guild.get_member(
            self.member_id
        )

        decide_hall_application(
            guild.id,
            self.member_id,
            "rejected",
            interaction.user.id,
        )

        message = interaction.message

        if message:
            new_embed = (
                update_application_embed_status(
                    message,
                    (
                        "❌ **却下**\n"
                        f"担当：{interaction.user.mention}"
                    ),
                )
            )

            try:
                await message.edit(
                    embed=new_embed,
                    view=None,
                )
            except Exception:
                log.exception(
                    "却下ログ更新失敗"
                )

        if member:
            try:
                await member.send(
                    (
                        "✦ **CROSS 殿堂メンバー特典**\n\n"
                        "申請していた\n"
                        "🏠 番号式プライベート部屋について、\n"
                        "今回は **却下** となりました。\n\n"
                        "※殿堂メンバーロールが"
                        "外れることはありません。"
                    )
                )
            except discord.Forbidden:
                log.info(
                    "%s にDMを送信できませんでした",
                    member,
                )
            except Exception:
                log.exception(
                    "却下DM送信失敗"
                )

        member_text = (
            member.mention
            if member
            else f"<@{self.member_id}>"
        )

        await interaction.followup.send(
            (
                f"❌ {member_text} の申請を"
                "**却下しました。**"
            ),
            ephemeral=True,
        )


# =========================================================
# 🎫 チケット パネル
# =========================================================

class TicketPanelView(
    discord.ui.View
):
    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="チケットを作成",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="cross_ticket_create",
    )
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "❌ サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        member = interaction.user

        if not isinstance(
            member,
            discord.Member,
        ):
            await interaction.response.send_message(
                "❌ メンバー情報を取得できませんでした。",
                ephemeral=True,
            )
            return

        settings = get_ticket_settings(
            guild.id
        )

        if not settings:
            await interaction.response.send_message(
                (
                    "❌ このサーバーでは"
                    "チケット設定がまだ完了していません。\n"
                    "管理者にお問い合わせください。"
                ),
                ephemeral=True,
            )
            return

        existing = get_open_ticket_for_user(
            guild.id,
            member.id,
        )

        if existing:
            existing_channel = guild.get_channel(
                existing["channel_id"]
            )

            if isinstance(
                existing_channel,
                discord.TextChannel,
            ):
                await interaction.response.send_message(
                    (
                        "🎫 すでに開いているチケットがあります。\n\n"
                        f"{existing_channel.mention}"
                    ),
                    ephemeral=True,
                )
                return

            close_ticket_record(
                existing["channel_id"],
                member.id,
            )

        category = guild.get_channel(
            settings["category_id"]
        )

        staff_role = guild.get_role(
            settings["staff_role_id"]
        )

        if not isinstance(
            category,
            discord.CategoryChannel,
        ):
            await interaction.response.send_message(
                "❌ チケットカテゴリが見つかりません。",
                ephemeral=True,
            )
            return

        if staff_role is None:
            await interaction.response.send_message(
                "❌ 運営ロールが見つかりません。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            staff_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            ),
        }

        base_name = safe_channel_name(
            member.display_name
        )

        channel_name = (
            f"ticket-{base_name}"
        )[:95]

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=(
                    f"CROSS Ticket | Owner: {member.id}"
                ),
                reason=(
                    f"チケット作成: {member}"
                ),
            )
        except Exception:
            log.exception(
                "チケットチャンネル作成失敗"
            )

            await interaction.followup.send(
                "❌ チケットの作成に失敗しました。",
                ephemeral=True,
            )
            return

        create_ticket_record(
            guild.id,
            channel.id,
            member.id,
        )

        embed = discord.Embed(
            title="🎫 CROSS SUPPORT",
            description=(
                f"{member.mention} さん、"
                "お問い合わせありがとうございます。\n\n"
                "こちらに内容をご記入ください。\n"
                "運営が確認後、順番に対応します。\n\n"
                "━━━━━━━━━━━━━━\n"
                "🧑‍💼 **担当する**：運営が対応担当になります\n"
                "🔒 **閉じる**：チケットを閉じます\n"
                "━━━━━━━━━━━━━━"
            ),
            timestamp=utcnow(),
        )

        embed.set_footer(
            text="CROSS ✦ Ticket System"
        )

        try:
            await channel.send(
                content=(
                    f"{member.mention} {staff_role.mention}"
                ),
                embed=embed,
                view=TicketControlView(),
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=True,
                ),
            )
        except Exception:
            log.exception(
                "チケット初期メッセージ送信失敗"
            )

        await send_ticket_log(
            guild,
            "🎫 チケット作成",
            (
                f"作成者：{member.mention}\n"
                f"チャンネル：{channel.mention}\n"
                f"ユーザーID：`{member.id}`"
            ),
        )

        await interaction.followup.send(
            (
                "✅ チケットを作成しました！\n\n"
                f"{channel.mention}"
            ),
            ephemeral=True,
        )


# =========================================================
# 🎫 チケット操作
# =========================================================

class TicketControlView(
    discord.ui.View
):
    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="担当する",
        emoji="🧑‍💼",
        style=discord.ButtonStyle.success,
        custom_id="cross_ticket_claim",
    )
    async def claim(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild = interaction.guild
        channel = interaction.channel

        if (
            guild is None
            or not isinstance(
                channel,
                discord.TextChannel,
            )
            or not isinstance(
                interaction.user,
                discord.Member,
            )
        ):
            return

        settings = get_ticket_settings(
            guild.id
        )

        if not settings:
            await interaction.response.send_message(
                "❌ チケット設定がありません。",
                ephemeral=True,
            )
            return

        staff_role = guild.get_role(
            settings["staff_role_id"]
        )

        if not is_ticket_staff(
            interaction.user,
            staff_role,
        ):
            await interaction.response.send_message(
                "❌ 運営のみ使用できます。",
                ephemeral=True,
            )
            return

        ticket = get_ticket_by_channel(
            channel.id
        )

        if not ticket:
            await interaction.response.send_message(
                "❌ チケット情報が見つかりません。",
                ephemeral=True,
            )
            return

        if ticket["status"] != "open":
            await interaction.response.send_message(
                "⚠️ このチケットは閉じられています。",
                ephemeral=True,
            )
            return

        claim_ticket(
            channel.id,
            interaction.user.id,
        )

        await interaction.response.send_message(
            (
                f"🧑‍💼 {interaction.user.mention} が"
                "このチケットを担当します。"
            )
        )

        await send_ticket_log(
            guild,
            "🧑‍💼 チケット担当",
            (
                f"チャンネル：{channel.mention}\n"
                f"担当者：{interaction.user.mention}"
            ),
        )

    @discord.ui.button(
        label="閉じる",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="cross_ticket_close",
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild = interaction.guild
        channel = interaction.channel

        if (
            guild is None
            or not isinstance(
                channel,
                discord.TextChannel,
            )
            or not isinstance(
                interaction.user,
                discord.Member,
            )
        ):
            return

        ticket = get_ticket_by_channel(
            channel.id
        )

        if not ticket:
            await interaction.response.send_message(
                "❌ チケット情報が見つかりません。",
                ephemeral=True,
            )
            return

        if ticket["status"] != "open":
            await interaction.response.send_message(
                "⚠️ このチケットはすでに閉じられています。",
                ephemeral=True,
            )
            return

        settings = get_ticket_settings(
            guild.id
        )

        if not settings:
            await interaction.response.send_message(
                "❌ チケット設定がありません。",
                ephemeral=True,
            )
            return

        staff_role = guild.get_role(
            settings["staff_role_id"]
        )

        is_owner = (
            interaction.user.id
            == ticket["user_id"]
        )

        if not (
            is_owner
            or is_ticket_staff(
                interaction.user,
                staff_role,
            )
        ):
            await interaction.response.send_message(
                "❌ このチケットを閉じる権限がありません。",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        close_ticket_record(
            channel.id,
            interaction.user.id,
        )

        owner = guild.get_member(
            ticket["user_id"]
        )

        if owner:
            try:
                await channel.set_permissions(
                    owner,
                    view_channel=False,
                    reason=(
                        f"チケット閉鎖: {interaction.user}"
                    ),
                )
            except Exception:
                log.exception(
                    "チケット所有者権限変更失敗"
                )

        try:
            if not channel.name.startswith(
                "closed-"
            ):
                await channel.edit(
                    name=(
                        f"closed-{channel.name}"
                    )[:95],
                    reason=(
                        f"チケット閉鎖: {interaction.user}"
                    ),
                )
        except Exception:
            log.exception(
                "チケット名変更失敗"
            )

        embed = discord.Embed(
            title="🔒 チケットを閉じました",
            description=(
                f"閉じた人：{interaction.user.mention}\n\n"
                "運営は下のボタンから"
                "再開または削除できます。"
            ),
            timestamp=utcnow(),
        )

        embed.set_footer(
            text="CROSS ✦ Ticket System"
        )

        await channel.send(
            embed=embed,
            view=TicketClosedView(),
        )

        await send_ticket_log(
            guild,
            "🔒 チケット閉鎖",
            (
                f"チャンネル：{channel.mention}\n"
                f"作成者：<@{ticket['user_id']}>\n"
                f"閉じた人：{interaction.user.mention}"
            ),
        )


# =========================================================
# 🎫 閉じたチケット操作
# =========================================================

class TicketClosedView(
    discord.ui.View
):
    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="再開",
        emoji="🔓",
        style=discord.ButtonStyle.success,
        custom_id="cross_ticket_reopen",
    )
    async def reopen(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild = interaction.guild
        channel = interaction.channel

        if (
            guild is None
            or not isinstance(
                channel,
                discord.TextChannel,
            )
            or not isinstance(
                interaction.user,
                discord.Member,
            )
        ):
            return

        settings = get_ticket_settings(
            guild.id
        )

        if not settings:
            await interaction.response.send_message(
                "❌ チケット設定がありません。",
                ephemeral=True,
            )
            return

        staff_role = guild.get_role(
            settings["staff_role_id"]
        )

        if not is_ticket_staff(
            interaction.user,
            staff_role,
        ):
            await interaction.response.send_message(
                "❌ 運営のみ使用できます。",
                ephemeral=True,
            )
            return

        ticket = get_ticket_by_channel(
            channel.id
        )

        if not ticket:
            await interaction.response.send_message(
                "❌ チケット情報が見つかりません。",
                ephemeral=True,
            )
            return

        if ticket["status"] != "closed":
            await interaction.response.send_message(
                "⚠️ このチケットはすでに開いています。",
                ephemeral=True,
            )
            return

        owner = guild.get_member(
            ticket["user_id"]
        )

        if owner:
            try:
                await channel.set_permissions(
                    owner,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                    reason=(
                        f"チケット再開: {interaction.user}"
                    ),
                )
            except Exception:
                log.exception(
                    "チケット再開権限変更失敗"
                )

        reopen_ticket_record(
            channel.id
        )

        try:
            new_name = channel.name

            if new_name.startswith(
                "closed-"
            ):
                new_name = new_name[
                    len("closed-"):
                ]

            await channel.edit(
                name=new_name[:95],
                reason=(
                    f"チケット再開: {interaction.user}"
                ),
            )
        except Exception:
            log.exception(
                "チケット再開名変更失敗"
            )

        await interaction.response.send_message(
            (
                f"🔓 {interaction.user.mention} が"
                "チケットを再開しました。"
            ),
            view=TicketControlView(),
        )

        await send_ticket_log(
            guild,
            "🔓 チケット再開",
            (
                f"チャンネル：{channel.mention}\n"
                f"再開した人：{interaction.user.mention}"
            ),
        )

    @discord.ui.button(
        label="削除",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="cross_ticket_delete",
    )
    async def delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild = interaction.guild
        channel = interaction.channel

        if (
            guild is None
            or not isinstance(
                channel,
                discord.TextChannel,
            )
            or not isinstance(
                interaction.user,
                discord.Member,
            )
        ):
            return

        settings = get_ticket_settings(
            guild.id
        )

        if not settings:
            await interaction.response.send_message(
                "❌ チケット設定がありません。",
                ephemeral=True,
            )
            return

        staff_role = guild.get_role(
            settings["staff_role_id"]
        )

        if not is_ticket_staff(
            interaction.user,
            staff_role,
        ):
            await interaction.response.send_message(
                "❌ 運営のみ使用できます。",
                ephemeral=True,
            )
            return

        ticket = get_ticket_by_channel(
            channel.id
        )

        if not ticket:
            await interaction.response.send_message(
                "❌ チケット情報が見つかりません。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "🗑️ 3秒後にチケットを削除します。"
        )

        await send_ticket_log(
            guild,
            "🗑️ チケット削除",
            (
                f"チャンネル：#{channel.name}\n"
                f"作成者：<@{ticket['user_id']}>\n"
                f"削除した人：{interaction.user.mention}"
            ),
        )

        delete_ticket_record(
            channel.id
        )

        await asyncio.sleep(3)

        try:
            await channel.delete(
                reason=(
                    f"チケット削除: {interaction.user}"
                )
            )
        except Exception:
            log.exception(
                "チケット削除失敗"
            )


# =========================================================
# 🤖 Bot
# =========================================================

intents = discord.Intents.default()

intents.guilds = True
intents.members = True
intents.voice_states = True


class CrossBot(
    commands.Bot
):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        # 仮メンバーがVCに入った時刻
        self.temp_vc_sessions: dict[
            tuple[int, int],
            datetime
        ] = {}

    async def setup_hook(self):
        init_db()

        # 永続View
        self.add_view(
            HallApplicationView()
        )

        self.add_view(
            TicketPanelView()
        )

        self.add_view(
            TicketControlView()
        )

        self.add_view(
            TicketClosedView()
        )

        # 審査待ちの殿堂申請ボタン復元
        try:
            pending_rows = (
                get_pending_hall_applications()
            )

            restored = 0

            for (
                guild_id,
                user_id,
                message_id,
            ) in pending_rows:
                if not message_id:
                    continue

                self.add_view(
                    HallApprovalView(
                        user_id
                    ),
                    message_id=message_id,
                )

                restored += 1

            log.info(
                "殿堂申請の審査待ちボタン復元: %s件",
                restored,
            )

        except Exception:
            log.exception(
                "殿堂申請ボタン復元失敗"
            )

        # グローバルコマンド同期
        try:
            synced = await self.tree.sync()

            log.info(
                "グローバルスラッシュコマンド同期完了: %s",
                len(synced),
            )
        except Exception:
            log.exception(
                "スラッシュコマンド同期失敗"
            )

        if not active_check_loop.is_running():
            active_check_loop.start()

        if not temp_member_return_check.is_running():
            temp_member_return_check.start()

    async def on_ready(self):
        log.info(
            "ログインしました: %s (%s)",
            self.user,
            self.user.id if self.user else "?",
        )

        now = utcnow()

        # Bot起動時点でVCにいる仮メンバーを計測開始
        for guild in self.guilds:
            settings = get_guild_settings(
                guild.id
            )

            if not settings:
                continue

            temp_role = guild.get_role(
                settings[
                    "temp_member_role_id"
                ]
            )

            if temp_role is None:
                continue

            for member in guild.members:
                if member.bot:
                    continue

                if temp_role not in member.roles:
                    continue

                if (
                    member.voice
                    and member.voice.channel
                ):
                    self.temp_vc_sessions[
                        (
                            guild.id,
                            member.id,
                        )
                    ] = now

        log.info(
            "✦ CROSS 管理Bot 起動完了"
        )


bot = CrossBot()


# =========================================================
# ⚙️ /cross_setup
# =========================================================

@bot.tree.command(
    name="cross_setup",
    description="CROSS管理機能の初期設定を行います",
)
@app_commands.describe(
    temp_member_role="仮メンバーロール",
    full_member_role="本メンバーロール",
    admin_log_channel="通常の管理ログチャンネル",
    hall_of_fame_role="殿堂メンバーロール",
    hall_application_channel="殿堂特典の申請パネル設置先",
    hall_application_log_channel="殿堂特典の申請ログ",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def cross_setup(
    interaction: discord.Interaction,
    temp_member_role: discord.Role,
    full_member_role: discord.Role,
    admin_log_channel: discord.TextChannel,
    hall_of_fame_role: discord.Role,
    hall_application_channel: discord.TextChannel,
    hall_application_log_channel: discord.TextChannel,
):
    guild = interaction.guild

    if guild is None:
        return

    save_guild_settings(
        guild.id,
        temp_member_role.id,
        full_member_role.id,
        admin_log_channel.id,
        hall_of_fame_role.id,
        hall_application_channel.id,
        hall_application_log_channel.id,
    )

    embed = discord.Embed(
        title="✅ CROSS 管理設定完了",
        description=(
            "このサーバーの設定を保存しました。\n"
            "Botを再起動しても設定は残ります。"
        ),
        timestamp=utcnow(),
    )

    embed.add_field(
        name="💤 仮メンバー",
        value=temp_member_role.mention,
        inline=False,
    )

    embed.add_field(
        name="✦ 本メンバー",
        value=full_member_role.mention,
        inline=False,
    )

    embed.add_field(
        name="📋 管理ログ",
        value=admin_log_channel.mention,
        inline=False,
    )

    embed.add_field(
        name="👑 殿堂メンバー",
        value=hall_of_fame_role.mention,
        inline=False,
    )

    embed.add_field(
        name="🏠 殿堂申請所",
        value=hall_application_channel.mention,
        inline=False,
    )

    embed.add_field(
        name="📩 殿堂申請ログ",
        value=hall_application_log_channel.mention,
        inline=False,
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


# =========================================================
# 👑 /hall_panel
# =========================================================

@bot.tree.command(
    name="hall_panel",
    description="殿堂メンバー特典申請パネルを設置します",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def hall_panel(
    interaction: discord.Interaction,
):
    guild = interaction.guild

    if guild is None:
        return

    settings = get_guild_settings(
        guild.id
    )

    if not settings:
        await interaction.response.send_message(
            "❌ 先に `/cross_setup` を実行してください。",
            ephemeral=True,
        )
        return

    channel = guild.get_channel(
        settings[
            "hall_application_channel_id"
        ]
    )

    if not isinstance(
        channel,
        discord.TextChannel,
    ):
        await interaction.response.send_message(
            "❌ 申請所チャンネルが見つかりません。",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="👑 殿堂メンバー特典申請",
        description=(
            "✦ **Lv.50 殿堂メンバー限定** ✦\n\n"
            "殿堂メンバーになると、\n"
            "**番号式プライベート部屋** を"
            "申請できます。\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "🏠 自分専用プライベート部屋\n"
            "🔐 招待制\n"
            "👤 メンバー管理\n"
            "📝 部屋名変更\n"
            "🔒 ロック機能\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "希望する方は、下の\n"
            "**「プライベート部屋を申請」**\n"
            "ボタンを押してください。\n\n"
            "申請後、運営が確認して"
            "承認・却下を行います。"
        ),
    )

    embed.set_footer(
        text="CROSS ✦ 殿堂メンバー特典"
    )

    await channel.send(
        embed=embed,
        view=HallApplicationView(),
    )

    await interaction.response.send_message(
        (
            f"✅ {channel.mention} に"
            "申請パネルを設置しました。"
        ),
        ephemeral=True,
    )


# =========================================================
# 👑 /hall_status
# =========================================================

@bot.tree.command(
    name="hall_status",
    description="殿堂特典の申請状況を確認します",
)
async def hall_status(
    interaction: discord.Interaction,
):
    guild = interaction.guild

    if guild is None:
        return

    member = interaction.user

    if not isinstance(
        member,
        discord.Member,
    ):
        return

    application = get_hall_application(
        guild.id,
        member.id,
    )

    if not application:
        text = (
            "✦ まだ殿堂特典を申請していません。"
        )

    elif application["status"] == "pending":
        text = (
            "🟡 **審査待ち**\n\n"
            "運営の確認をお待ちください。"
        )

    elif application["status"] == "approved":
        text = (
            "✅ **承認済み**\n\n"
            "番号式プライベート部屋の"
            "作成対象です。"
        )

    elif application["status"] == "rejected":
        text = (
            "❌ **却下**\n\n"
            "必要であれば再度申請できます。"
        )

    else:
        text = "❔ 状態不明"

    embed = discord.Embed(
        title="👑 殿堂特典申請状況",
        description=text,
        timestamp=utcnow(),
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


# =========================================================
# 🛠️ /hall_reset
# =========================================================

@bot.tree.command(
    name="hall_reset",
    description="殿堂特典の申請状態をリセットします",
)
@app_commands.describe(
    member="リセットするメンバー"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def hall_reset(
    interaction: discord.Interaction,
    member: discord.Member,
):
    guild = interaction.guild

    if guild is None:
        return

    reset_hall_application(
        guild.id,
        member.id,
    )

    await interaction.response.send_message(
        (
            f"✅ {member.mention} の"
            "殿堂特典申請状態を"
            "リセットしました。\n\n"
            "もう一度申請できます。"
        ),
        ephemeral=True,
    )


# =========================================================
# 🎫 /ticket_setup
# =========================================================

@bot.tree.command(
    name="ticket_setup",
    description="このサーバーのチケット機能を設定します",
)
@app_commands.describe(
    category="チケットを作成するカテゴリ",
    staff_role="チケット対応をする運営ロール",
    log_channel="チケットの作成・担当・閉鎖ログ",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def ticket_setup(
    interaction: discord.Interaction,
    category: discord.CategoryChannel,
    staff_role: discord.Role,
    log_channel: discord.TextChannel,
):
    guild = interaction.guild

    if guild is None:
        return

    save_ticket_settings(
        guild.id,
        category.id,
        staff_role.id,
        log_channel.id,
    )

    embed = discord.Embed(
        title="✅ チケット設定完了",
        description=(
            "このサーバーのチケット設定を保存しました。\n"
            "次に `/ticket_panel` で"
            "チケットパネルを設置してください。"
        ),
        timestamp=utcnow(),
    )

    embed.add_field(
        name="📁 チケットカテゴリ",
        value=category.mention,
        inline=False,
    )

    embed.add_field(
        name="🧑‍💼 運営ロール",
        value=staff_role.mention,
        inline=False,
    )

    embed.add_field(
        name="📋 チケットログ",
        value=log_channel.mention,
        inline=False,
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


# =========================================================
# 🎫 /ticket_panel
# =========================================================

@bot.tree.command(
    name="ticket_panel",
    description="チケット作成パネルを設置します",
)
@app_commands.describe(
    channel="パネルを設置するチャンネル（未指定なら現在のチャンネル）"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def ticket_panel(
    interaction: discord.Interaction,
    channel: Optional[
        discord.TextChannel
    ] = None,
):
    guild = interaction.guild

    if guild is None:
        return

    settings = get_ticket_settings(
        guild.id
    )

    if not settings:
        await interaction.response.send_message(
            "❌ 先に `/ticket_setup` を実行してください。",
            ephemeral=True,
        )
        return

    target_channel = (
        channel
        or interaction.channel
    )

    if not isinstance(
        target_channel,
        discord.TextChannel,
    ):
        await interaction.response.send_message(
            "❌ テキストチャンネルを指定してください。",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🎫 CROSS SUPPORT",
        description=(
            "お問い合わせ・相談・通報などがある方は、\n"
            "下のボタンからチケットを作成してください。\n\n"
            "作成されたチャンネルは、\n"
            "**本人と運営だけが確認できます。**\n\n"
            "━━━━━━━━━━━━━━\n"
            "🎫 **チケットを作成**\n"
            "━━━━━━━━━━━━━━"
        ),
        timestamp=utcnow(),
    )

    embed.set_footer(
        text="CROSS ✦ Ticket System"
    )

    await target_channel.send(
        embed=embed,
        view=TicketPanelView(),
    )

    await interaction.response.send_message(
        (
            f"✅ {target_channel.mention} に"
            "チケットパネルを設置しました。"
        ),
        ephemeral=True,
    )


# =========================================================
# ⬆️ 本メンバー復帰
# =========================================================

async def promote_member(
    member: discord.Member,
):
    guild = member.guild

    settings = get_guild_settings(
        guild.id
    )

    if not settings:
        return

    temp_role = guild.get_role(
        settings[
            "temp_member_role_id"
        ]
    )

    full_role = guild.get_role(
        settings[
            "full_member_role_id"
        ]
    )

    if (
        temp_role is None
        or full_role is None
    ):
        return

    try:
        if full_role not in member.roles:
            await member.add_roles(
                full_role,
                reason=(
                    "VC累計3時間達成による"
                    "本メンバー復帰"
                ),
            )

        if temp_role in member.roles:
            await member.remove_roles(
                temp_role,
                reason=(
                    "VC累計3時間達成による"
                    "本メンバー復帰"
                ),
            )

        reset_return_seconds(
            guild.id,
            member.id,
        )

        set_demoted(
            guild.id,
            member.id,
            False,
        )

        set_last_vc(
            guild.id,
            member.id,
            utcnow(),
        )

        bot.temp_vc_sessions.pop(
            (
                guild.id,
                member.id,
            ),
            None,
        )

        await send_admin_log(
            guild,
            "✦ 本メンバーへ復帰",
            (
                f"{member.mention} が"
                "VC累計 **3時間** を達成しました！\n\n"
                "💤 仮メンバー\n"
                "⬇️\n"
                "✦ 本メンバー\n\n"
                "自動で本メンバーへ復帰しました。"
            ),
        )

        log.info(
            "%s を本メンバーへ復帰しました",
            member,
        )

    except discord.Forbidden:
        log.error(
            "%s のロール変更権限がありません",
            member,
        )

    except Exception:
        log.exception(
            "%s の復帰処理でエラー",
            member,
        )


# =========================================================
# ⬇️ 仮メンバーへ降格
# =========================================================

async def demote_member(
    member: discord.Member,
):
    guild = member.guild

    settings = get_guild_settings(
        guild.id
    )

    if not settings:
        return

    temp_role = guild.get_role(
        settings[
            "temp_member_role_id"
        ]
    )

    full_role = guild.get_role(
        settings[
            "full_member_role_id"
        ]
    )

    if (
        temp_role is None
        or full_role is None
    ):
        return

    try:
        if temp_role not in member.roles:
            await member.add_roles(
                temp_role,
                reason=(
                    f"{DEMOTION_DAYS}日間"
                    "VC浮上なし"
                ),
            )

        if full_role in member.roles:
            await member.remove_roles(
                full_role,
                reason=(
                    f"{DEMOTION_DAYS}日間"
                    "VC浮上なし"
                ),
            )

        reset_return_seconds(
            guild.id,
            member.id,
        )

        set_demoted(
            guild.id,
            member.id,
            True,
        )

        bot.temp_vc_sessions.pop(
            (
                guild.id,
                member.id,
            ),
            None,
        )

        await send_admin_log(
            guild,
            "💤 仮メンバーへ降格",
            (
                f"{member.mention} は"
                f" **{DEMOTION_DAYS}日間VC浮上がありませんでした。**\n\n"
                "✦ 本メンバー\n"
                "⬇️\n"
                "💤 仮メンバー\n\n"
                "🎙️ 今後VC累計 **3時間** で"
                "本メンバーへ自動復帰します。"
            ),
        )

        log.info(
            "%s を仮メンバーへ降格しました",
            member,
        )

    except discord.Forbidden:
        log.error(
            "%s のロール変更権限がありません",
            member,
        )

    except Exception:
        log.exception(
            "%s の降格処理でエラー",
            member,
        )


# =========================================================
# 🎙️ VC入退室監視
# =========================================================

@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    if member.bot:
        return

    guild = member.guild

    settings = get_guild_settings(
        guild.id
    )

    if not settings:
        return

    temp_role = guild.get_role(
        settings[
            "temp_member_role_id"
        ]
    )

    full_role = guild.get_role(
        settings[
            "full_member_role_id"
        ]
    )

    if (
        temp_role is None
        or full_role is None
    ):
        return

    before_channel = before.channel
    after_channel = after.channel
    now = utcnow()

    # VC入室
    if (
        before_channel is None
        and after_channel is not None
    ):
        if (
            full_role in member.roles
            or temp_role in member.roles
        ):
            set_last_vc(
                guild.id,
                member.id,
                now,
            )

        if temp_role in member.roles:
            bot.temp_vc_sessions[
                (
                    guild.id,
                    member.id,
                )
            ] = now

        return

    # VC退出
    if (
        before_channel is not None
        and after_channel is None
    ):
        if (
            full_role in member.roles
            or temp_role in member.roles
        ):
            set_last_vc(
                guild.id,
                member.id,
                now,
            )

        key = (
            guild.id,
            member.id,
        )

        started_at = (
            bot.temp_vc_sessions.pop(
                key,
                None,
            )
        )

        if (
            started_at is not None
            and temp_role in member.roles
        ):
            elapsed = int(
                (
                    now
                    - started_at
                ).total_seconds()
            )

            if elapsed > 0:
                add_return_seconds(
                    guild.id,
                    member.id,
                    elapsed,
                )

            total = get_return_seconds(
                guild.id,
                member.id,
            )

            if total >= RETURN_REQUIRED_SECONDS:
                await promote_member(
                    member
                )

        return

    # VC移動
    if (
        before_channel is not None
        and after_channel is not None
        and before_channel.id
        != after_channel.id
    ):
        if (
            full_role in member.roles
            or temp_role in member.roles
        ):
            set_last_vc(
                guild.id,
                member.id,
                now,
            )


# =========================================================
# ✦ 仮メンバー復帰チェック
# =========================================================

@tasks.loop(
    minutes=1
)
async def temp_member_return_check():
    now = utcnow()

    for guild in bot.guilds:
        settings = get_guild_settings(
            guild.id
        )

        if not settings:
            continue

        temp_role = guild.get_role(
            settings[
                "temp_member_role_id"
            ]
        )

        if temp_role is None:
            continue

        for member in guild.members:
            if member.bot:
                continue

            if temp_role not in member.roles:
                continue

            if (
                member.voice is None
                or member.voice.channel is None
            ):
                continue

            key = (
                guild.id,
                member.id,
            )

            started_at = (
                bot.temp_vc_sessions.get(
                    key
                )
            )

            if started_at is None:
                bot.temp_vc_sessions[
                    key
                ] = now
                continue

            current_session = int(
                (
                    now
                    - started_at
                ).total_seconds()
            )

            saved_seconds = (
                get_return_seconds(
                    guild.id,
                    member.id,
                )
            )

            total = (
                saved_seconds
                + current_session
            )

            if total >= RETURN_REQUIRED_SECONDS:
                if current_session > 0:
                    add_return_seconds(
                        guild.id,
                        member.id,
                        current_session,
                    )

                await promote_member(
                    member
                )


@temp_member_return_check.before_loop
async def before_temp_member_return_check():
    await bot.wait_until_ready()


# =========================================================
# 💤 30日間VCなし自動降格
# =========================================================

@tasks.loop(
    minutes=CHECK_INTERVAL_MINUTES
)
async def active_check_loop():
    now = utcnow()

    cutoff = (
        now
        - timedelta(
            days=DEMOTION_DAYS
        )
    )

    for guild in bot.guilds:
        settings = get_guild_settings(
            guild.id
        )

        if not settings:
            continue

        full_role = guild.get_role(
            settings[
                "full_member_role_id"
            ]
        )

        if full_role is None:
            continue

        for member in guild.members:
            if member.bot:
                continue

            if full_role not in member.roles:
                continue

            if (
                member.voice is not None
                and member.voice.channel is not None
            ):
                set_last_vc(
                    guild.id,
                    member.id,
                    now,
                )
                continue

            last_vc = get_last_vc(
                guild.id,
                member.id,
            )

            # Bot導入直後の一斉降格防止
            if last_vc is None:
                set_last_vc(
                    guild.id,
                    member.id,
                    now,
                )
                continue

            if last_vc <= cutoff:
                await demote_member(
                    member
                )

                await asyncio.sleep(1)


@active_check_loop.before_loop
async def before_active_check_loop():
    await bot.wait_until_ready()


# =========================================================
# 🔍 /active_status
# =========================================================

@bot.tree.command(
    name="active_status",
    description="VCアクティブ状況を確認します",
)
@app_commands.describe(
    member="確認したいメンバー"
)
async def active_status(
    interaction: discord.Interaction,
    member: Optional[
        discord.Member
    ] = None,
):
    target = (
        member
        or interaction.user
    )

    if not isinstance(
        target,
        discord.Member,
    ):
        await interaction.response.send_message(
            "メンバー情報を取得できませんでした。",
            ephemeral=True,
        )
        return

    guild = interaction.guild

    if guild is None:
        return

    settings = get_guild_settings(
        guild.id
    )

    if not settings:
        await interaction.response.send_message(
            "❌ 先に `/cross_setup` を実行してください。",
            ephemeral=True,
        )
        return

    last_vc = get_last_vc(
        guild.id,
        target.id,
    )

    return_seconds = (
        get_return_seconds(
            guild.id,
            target.id,
        )
    )

    temp_role = guild.get_role(
        settings[
            "temp_member_role_id"
        ]
    )

    full_role = guild.get_role(
        settings[
            "full_member_role_id"
        ]
    )

    if (
        full_role
        and full_role in target.roles
    ):
        status = "✦ 本メンバー"

    elif (
        temp_role
        and temp_role in target.roles
    ):
        status = "💤 仮メンバー"

    else:
        status = "❔ 対象外"

    if last_vc:
        unix_time = int(
            last_vc.timestamp()
        )

        last_text = (
            f"<t:{unix_time}:F>\n"
            f"<t:{unix_time}:R>"
        )

    else:
        last_text = (
            "まだ記録されていません"
        )

    embed = discord.Embed(
        title="✦ CROSS アクティブ状況",
        timestamp=utcnow(),
    )

    embed.add_field(
        name="👤 メンバー",
        value=target.mention,
        inline=False,
    )

    embed.add_field(
        name="🏷️ 現在の状態",
        value=status,
        inline=False,
    )

    embed.add_field(
        name="🎙️ 最終VC浮上",
        value=last_text,
        inline=False,
    )

    if (
        full_role
        and full_role in target.roles
        and last_vc
    ):
        demotion_date = (
            last_vc
            + timedelta(
                days=DEMOTION_DAYS
            )
        )

        unix_demotion = int(
            demotion_date.timestamp()
        )

        embed.add_field(
            name="💤 降格判定",
            value=(
                "このままVC浮上がない場合\n"
                f"<t:{unix_demotion}:F>\n"
                f"(<t:{unix_demotion}:R>)"
            ),
            inline=False,
        )

    if (
        temp_role
        and temp_role in target.roles
    ):
        current_total = return_seconds

        key = (
            guild.id,
            target.id,
        )

        started_at = (
            bot.temp_vc_sessions.get(
                key
            )
        )

        if (
            started_at
            and target.voice
            and target.voice.channel
        ):
            current_total += int(
                (
                    utcnow()
                    - started_at
                ).total_seconds()
            )

        progress = min(
            100,
            int(
                current_total
                / RETURN_REQUIRED_SECONDS
                * 100
            ),
        )

        remaining = max(
            0,
            RETURN_REQUIRED_SECONDS
            - current_total,
        )

        filled = min(
            10,
            progress // 10,
        )

        bar = (
            "█" * filled
            + "░" * (
                10 - filled
            )
        )

        embed.add_field(
            name="✦ 本メンバー復帰まで",
            value=(
                f"🎙️ **{format_seconds(current_total)} / 3時間**\n"
                f"`{bar}` **{progress}%**\n"
                f"⏳ あと **{format_seconds(remaining)}**"
            ),
            inline=False,
        )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


# =========================================================
# 🛠️ /active_reset
# =========================================================

@bot.tree.command(
    name="active_reset",
    description="復帰用VC時間を0に戻します",
)
@app_commands.describe(
    member="リセットするメンバー"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def active_reset(
    interaction: discord.Interaction,
    member: discord.Member,
):
    guild = interaction.guild

    if guild is None:
        return

    reset_return_seconds(
        guild.id,
        member.id,
    )

    if (
        member.voice
        and member.voice.channel
    ):
        bot.temp_vc_sessions[
            (
                guild.id,
                member.id,
            )
        ] = utcnow()

    await interaction.response.send_message(
        (
            f"✅ {member.mention} の"
            "復帰用VC時間を **0分** に"
            "リセットしました。"
        ),
        ephemeral=True,
    )


# =========================================================
# 🛠️ /active_set_last
# =========================================================

@bot.tree.command(
    name="active_set_last",
    description="最終VC浮上日時を現在時刻に更新します",
)
@app_commands.describe(
    member="更新するメンバー"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def active_set_last(
    interaction: discord.Interaction,
    member: discord.Member,
):
    guild = interaction.guild

    if guild is None:
        return

    set_last_vc(
        guild.id,
        member.id,
        utcnow(),
    )

    await interaction.response.send_message(
        (
            f"✅ {member.mention} の"
            "最終VC浮上日時を"
            "現在時刻に更新しました。"
        ),
        ephemeral=True,
    )


# =========================================================
# ❌ スラッシュコマンドエラー
# =========================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(
        error,
        app_commands.MissingPermissions,
    ):
        text = (
            "❌ このコマンドは"
            "管理者のみ使用できます。"
        )

        if interaction.response.is_done():
            await interaction.followup.send(
                text,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                text,
                ephemeral=True,
            )

        return

    log.error(
        "スラッシュコマンドエラー: %r",
        error,
    )

    try:
        text = (
            "❌ コマンド実行中に"
            "エラーが発生しました。"
        )

        if interaction.response.is_done():
            await interaction.followup.send(
                text,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                text,
                ephemeral=True,
            )

    except Exception:
        pass


# =========================================================
# 🚀 起動
# =========================================================

async def main():
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN が設定されていません。"
        )

    async with bot:
        await bot.start(
            TOKEN
        )


if __name__ == "__main__":
    try:
        asyncio.run(
            main()
        )
    except KeyboardInterrupt:
        pass
