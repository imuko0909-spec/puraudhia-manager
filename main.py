from __future__ import annotations

import asyncio

import logging

import os

import sqlite3

from datetime import datetime, timedelta, timezone

from pathlib import Path

from typing import Optional

import discord

from discord import app_commands

from discord.ext import commands, tasks

# =========================================================

# 🍬 Candy VCアクティブ管理Bot

# ＋ 殿堂メンバー特典申請

# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# =========================================================

# 🔧 サーバー設定

# =========================================================

GUILD_ID = 1542420058775494666

# 仮メンバー

TEMP_MEMBER_ROLE_ID = 1542423995184451674

# 本メンバー

FULL_MEMBER_ROLE_ID = 1542424117029109760

# 通常の管理ログ

ADMIN_LOG_CHANNEL_ID = 1542720008499765248

# =========================================================

# 👑 殿堂メンバー特典申請

# =========================================================

# 殿堂メンバー

HALL_OF_FAME_ROLE_ID = 1547465990533677097

# 申請所

HALL_APPLICATION_CHANNEL_ID = 1547464547185463306

# 申請ログ

HALL_APPLICATION_LOG_CHANNEL_ID = 1547464638390861825

# =========================================================

# ⚙️ アクティブ判定設定

# =========================================================

# 最終VC浮上から30日で降格

DEMOTION_DAYS = 30

# 仮メンバー → 本メンバー復帰に必要なVC時間

RETURN_REQUIRED_SECONDS = 3 * 60 * 60

# 降格チェック間隔

CHECK_INTERVAL_MINUTES = 30

# =========================================================

# 💾 データ保存先

# =========================================================

DB_PATH = os.getenv(

    "DATABASE_PATH",

    "data/candy_active.db",

)

# =========================================================

# 📝 ログ設定

# =========================================================

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s | %(levelname)s | %(message)s",

)

log = logging.getLogger("candy-active")

# =========================================================

# 💾 SQLite

# =========================================================

Path(DB_PATH).parent.mkdir(

    parents=True,

    exist_ok=True,

)

def db_connect():

    return sqlite3.connect(

        DB_PATH

    )

def init_db():

    with db_connect() as con:

        # =============================================

        # VCアクティブ情報

        # =============================================

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

        # =============================================

        # 殿堂特典申請

        # =============================================

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

        con.commit()

# =========================================================

# 🕐 日時

# =========================================================

def utcnow():

    return datetime.now(

        timezone.utc

    )

def datetime_to_str(

    dt: Optional[datetime],

):

    if dt is None:

        return None

    return dt.astimezone(

        timezone.utc

    ).isoformat()

def str_to_datetime(

    value: Optional[str],

):

    if not value:

        return None

    try:

        return datetime.fromisoformat(

            value

        )

    except Exception:

        return None

# =========================================================

# 💾 VCアクティブ DB操作

# =========================================================

def ensure_member(

    guild_id: int,

    user_id: int,

):

    with db_connect() as con:

        con.execute(

            """

            INSERT OR IGNORE INTO member_activity

            (

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

        row[0]

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

        row[0] or 0

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

        "status": row[0],

        "applied_at": str_to_datetime(

            row[1]

        ),

        "decided_at": str_to_datetime(

            row[2]

        ),

        "decided_by": row[3],

        "log_message_id": row[4],

    }

def save_hall_application(

    guild_id: int,

    user_id: int,

):

    now = utcnow()

    with db_connect() as con:

        con.execute(

            """

            INSERT INTO hall_applications

            (

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

    return rows

# =========================================================

# 🕐 時間表示

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

# =========================================================

# 📢 通常管理ログ

# =========================================================

async def send_admin_log(

    guild: discord.Guild,

    title: str,

    description: str,

):

    channel = guild.get_channel(

        ADMIN_LOG_CHANNEL_ID

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

    try:

        await channel.send(

            embed=embed

        )

    except Exception:

        log.exception(

            "管理ログ送信失敗"

        )

# =========================================================

# 👑 申請ログEmbed

# =========================================================

def create_hall_application_embed(

    member: discord.Member,

):

    embed = discord.Embed(

        title="👑 殿堂メンバー特典申請",

        description=(

            f"{member.mention} さんが\n\n"

            "🏠 **番号式プライベート部屋**\n"

            "を申請しました🍬"

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

        text="Candy 🍬 殿堂メンバー特典"

    )

    return embed

# =========================================================

# ✅❌ 申請ログの状態変更

# =========================================================

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

# 🍬 殿堂申請ボタン

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

        custom_id="candy_hall_private_room_apply",

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

        # =============================================

        # 👑 殿堂メンバー確認

        # =============================================

        hall_role = guild.get_role(

            HALL_OF_FAME_ROLE_ID

        )

        if hall_role is None:

            await interaction.response.send_message(

                (

                    "❌ 殿堂メンバーロールが"

                    "見つかりません。"

                ),

                ephemeral=True,

            )

            return

        if hall_role not in member.roles:

            await interaction.response.send_message(

                (

                    "🔒 この申請は\n"

                    "**殿堂メンバー限定特典**です。\n\n"

                    "殿堂メンバーになってから"

                    "申請してください🍬"

                ),

                ephemeral=True,

            )

            return

        # =============================================

        # 📋 既存申請確認

        # =============================================

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

                        "🍬 すでに申請済みです！\n\n"

                        "現在 **審査待ち** です。\n"

                        "運営からの対応をお待ちください♡"

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

                        "運営までお問い合わせください🍬"

                    ),

                    ephemeral=True,

                )

                return

            # 却下済みなら再申請可能

        # =============================================

        # 📢 申請ログチャンネル

        # =============================================

        log_channel = guild.get_channel(

            HALL_APPLICATION_LOG_CHANNEL_ID

        )

        if not isinstance(

            log_channel,

            discord.TextChannel,

        ):

            await interaction.response.send_message(

                (

                    "❌ 申請ログチャンネルが"

                    "見つかりません。"

                ),

                ephemeral=True,

            )

            return

        await interaction.response.defer(

            ephemeral=True

        )

        # =============================================

        # 💾 DB保存

        # =============================================

        save_hall_application(

            guild.id,

            member.id,

        )

        # =============================================

        # 📩 管理ログへ送信

        # =============================================

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

                (

                    "❌ 申請の送信中に"

                    "エラーが発生しました。"

                ),

                ephemeral=True,

            )

            return

        await interaction.followup.send(

            (

                "🍬 **申請を受け付けました！**\n\n"

                "👑 殿堂メンバー特典\n"

                "🏠 番号式プライベート部屋\n\n"

                "運営の承認をお待ちください♡"

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

    # =====================================================

    # ✅ 承認

    # =====================================================

    @discord.ui.button(

        label="承認",

        emoji="✅",

        style=discord.ButtonStyle.success,

        custom_id="candy_hall_application_approve",

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

        if not (

            interaction.user.guild_permissions.administrator

            or interaction.user.guild_permissions.manage_guild

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

        # =============================================

        # 💾 承認保存

        # =============================================

        decide_hall_application(

            guild.id,

            self.member_id,

            "approved",

            interaction.user.id,

        )

        # =============================================

        # 📋 ログ表示変更

        # =============================================

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

        # =============================================

        # 📩 本人DM

        # =============================================

        if member:

            try:

                await member.send(

                    (

                        "🍬 **Candy 殿堂メンバー特典** 🍬\n\n"

                        "申請していた\n"

                        "🏠 **番号式プライベート部屋** が\n"

                        "**承認されました！** 🎉\n\n"

                        "運営が部屋作成の対応を行います。\n"

                        "しばらくお待ちください♡"

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

        # =============================================

        # ✅ 管理者へ表示

        # =============================================

        if member:

            member_text = member.mention

        else:

            member_text = (

                f"<@{self.member_id}>"

            )

        await interaction.followup.send(

            (

                f"✅ {member_text} の申請を"

                "**承認しました。**\n\n"

                "次に、既存の番号式プライベート部屋Botで"

                "部屋を作成してください🍬"

            ),

            ephemeral=True,

        )

    # =====================================================

    # ❌ 却下

    # =====================================================

    @discord.ui.button(

        label="却下",

        emoji="❌",

        style=discord.ButtonStyle.danger,

        custom_id="candy_hall_application_reject",

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

        if not (

            interaction.user.guild_permissions.administrator

            or interaction.user.guild_permissions.manage_guild

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

        # =============================================

        # 💾 却下保存

        # =============================================

        decide_hall_application(

            guild.id,

            self.member_id,

            "rejected",

            interaction.user.id,

        )

        # =============================================

        # 📋 ログ表示変更

        # =============================================

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

        # =============================================

        # 📩 本人DM

        # =============================================

        if member:

            try:

                await member.send(

                    (

                        "🍬 **Candy 殿堂メンバー特典**\n\n"

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

        # =============================================

        # ❌ 管理者へ表示

        # =============================================

        if member:

            member_text = member.mention

        else:

            member_text = (

                f"<@{self.member_id}>"

            )

        await interaction.followup.send(

            (

                f"❌ {member_text} の申請を"

                "**却下しました。**\n\n"

                "ロール変更・部屋作成は行われません。"

            ),

            ephemeral=True,

        )

# =========================================================

# 🤖 Bot

# =========================================================

intents = discord.Intents.default()

intents.guilds = True

intents.members = True

intents.voice_states = True

class CandyActiveBot(

    commands.Bot

):

    def __init__(self):

        super().__init__(

            command_prefix="!",

            intents=intents,

        )

        # 仮メンバーがVCに入った時間

        self.temp_vc_sessions: dict[

            tuple[int, int],

            datetime

        ] = {}

    async def setup_hook(self):

        init_db()

        # =============================================

        # 🍬 申請パネルを永続化

        # =============================================

        self.add_view(

            HallApplicationView()

        )

        # =============================================

        # 👑 審査待ち承認ボタンを復元

        # =============================================

        try:

            pending_rows = (

                get_pending_hall_applications()

            )

            for (

                guild_id,

                user_id,

                message_id,

            ) in pending_rows:

                if guild_id != GUILD_ID:

                    continue

                if not message_id:

                    continue

                self.add_view(

                    HallApprovalView(

                        user_id

                    ),

                    message_id=message_id,

                )

            log.info(

                "殿堂申請の審査待ちボタンを復元しました: %s件",

                len(pending_rows),

            )

        except Exception:

            log.exception(

                "殿堂申請ボタン復元失敗"

            )

        # =============================================

        # スラッシュコマンド同期

        # =============================================

        guild_obj = discord.Object(

            id=GUILD_ID

        )

        try:

            synced = await self.tree.sync(

                guild=guild_obj

            )

            log.info(

                "スラッシュコマンド同期完了: %s",

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

        guild = self.get_guild(

            GUILD_ID

        )

        if guild is None:

            log.error(

                "指定したサーバーが見つかりません。"

            )

            return

        temp_role = guild.get_role(

            TEMP_MEMBER_ROLE_ID

        )

        now = utcnow()

        # Bot起動時にVCにいる仮メンバーを

        # その時点から計測開始

        if temp_role:

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

            "🍬 Candy VCアクティブ管理Bot 起動完了"

        )

bot = CandyActiveBot()

# =========================================================

# 🍬 /hall_panel

# 殿堂特典申請パネル設置

# =========================================================

@bot.tree.command(

    name="hall_panel",

    description="殿堂メンバー特典申請パネルを設置します",

    guild=discord.Object(

        id=GUILD_ID

    ),

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

    channel = guild.get_channel(

        HALL_APPLICATION_CHANNEL_ID

    )

    if not isinstance(

        channel,

        discord.TextChannel,

    ):

        await interaction.response.send_message(

            (

                "❌ 申請所チャンネルが"

                "見つかりません。"

            ),

            ephemeral=True,

        )

        return

    embed = discord.Embed(

        title="👑 殿堂メンバー特典申請",

        description=(

            "🍬 **Lv.50 殿堂メンバー限定** 🍬\n\n"

            "殿堂メンバーになると、\n"

            "**番号式プライベート部屋** を"

            "申請できます♡\n\n"

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

            "申請後、運営が確認して\n"

            "承認・却下を行います。"

        ),

    )

    embed.set_footer(

        text="Candy 🍬 殿堂メンバー特典"

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

# 自分の申請状況確認

# =========================================================

@bot.tree.command(

    name="hall_status",

    description="殿堂特典の申請状況を確認します",

    guild=discord.Object(

        id=GUILD_ID

    ),

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

            "🍬 まだ殿堂特典を"

            "申請していません。"

        )

    elif application[

        "status"

    ] == "pending":

        text = (

            "🟡 **審査待ち**\n\n"

            "運営の確認をお待ちください♡"

        )

    elif application[

        "status"

    ] == "approved":

        text = (

            "✅ **承認済み**\n\n"

            "番号式プライベート部屋の"

            "作成対象です🍬"

        )

    elif application[

        "status"

    ] == "rejected":

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

# 管理者専用

#

# 承認済み等をリセットして

# 再申請できるようにする

# =========================================================

@bot.tree.command(

    name="hall_reset",

    description="殿堂特典の申請状態をリセットします",

    guild=discord.Object(

        id=GUILD_ID

    ),

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

# ⬆️ 本メンバー復帰

# =========================================================

async def promote_member(

    member: discord.Member,

):

    guild = member.guild

    temp_role = guild.get_role(

        TEMP_MEMBER_ROLE_ID

    )

    full_role = guild.get_role(

        FULL_MEMBER_ROLE_ID

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

            "🍬 本メンバーへ復帰",

            (

                f"{member.mention} が"

                "VC累計 **3時間** を達成しました！\n\n"

                "💤 仮メンバー\n"

                "⬇️\n"

                "🍬 本メンバー\n\n"

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

    temp_role = guild.get_role(

        TEMP_MEMBER_ROLE_ID

    )

    full_role = guild.get_role(

        FULL_MEMBER_ROLE_ID

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

                "🍬 本メンバー\n"

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

    if guild.id != GUILD_ID:

        return

    temp_role = guild.get_role(

        TEMP_MEMBER_ROLE_ID

    )

    full_role = guild.get_role(

        FULL_MEMBER_ROLE_ID

    )

    if (

        temp_role is None

        or full_role is None

    ):

        return

    before_channel = before.channel

    after_channel = after.channel

    now = utcnow()

    # =====================================================

    # 🎙️ VC入室

    # =====================================================

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

    # =====================================================

    # 🚪 VC退出

    # =====================================================

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

    # =====================================================

    # 🔄 VC移動

    # =====================================================

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

# 🍬 仮メンバー復帰チェック

# =========================================================

@tasks.loop(

    minutes=1

)

async def temp_member_return_check():

    guild = bot.get_guild(

        GUILD_ID

    )

    if guild is None:

        return

    temp_role = guild.get_role(

        TEMP_MEMBER_ROLE_ID

    )

    if temp_role is None:

        return

    now = utcnow()

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

    guild = bot.get_guild(

        GUILD_ID

    )

    if guild is None:

        return

    full_role = guild.get_role(

        FULL_MEMBER_ROLE_ID

    )

    if full_role is None:

        log.error(

            "本メンバーロールが見つかりません"

        )

        return

    now = utcnow()

    cutoff = (

        now

        - timedelta(

            days=DEMOTION_DAYS

        )

    )

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

    guild=discord.Object(

        id=GUILD_ID

    ),

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

        TEMP_MEMBER_ROLE_ID

    )

    full_role = guild.get_role(

        FULL_MEMBER_ROLE_ID

    )

    if (

        full_role

        and full_role in target.roles

    ):

        status = "🍬 本メンバー"

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

        title="🍬 Candy アクティブ状況",

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

    # =====================================================

    # 本メンバー

    # =====================================================

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

    # =====================================================

    # 仮メンバー

    # =====================================================

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

            name="🍬 本メンバー復帰まで",

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

    guild=discord.Object(

        id=GUILD_ID

    ),

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

    guild=discord.Object(

        id=GUILD_ID

    ),

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
