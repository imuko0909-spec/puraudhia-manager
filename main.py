from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks


# =========================================================
# ✨ 𝐋𝐮𝐦𝐢𝐞𝐫𝐞
# 専属案内人・運営アシスタントBot
# Chrono / クロノ
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DATABASE_PATH", "chrono.db")

BOT_NAME = "クロノ"
SERVER_NAME = "𝐋𝐮𝐦𝐢𝐞𝐫𝐞"

# VC自動移動対策
VC_PROFILE_DELAY = 4

# 作成したVC
ROOM_JOIN_TIMEOUT = 120
ROOM_EMPTY_DELETE_DELAY = 5

# 新人フォロー
NEWBIE_REMINDER_DAYS = 3
REMINDER_COOLDOWN_DAYS = 3


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("chrono")


# =========================================================
# 🕰️ 時刻
# =========================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


# =========================================================
# 🗄️ DATABASE
# =========================================================

class Database:

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS guild_settings(
                guild_id INTEGER PRIMARY KEY,

                rules_channel_id INTEGER,
                profile_channel_id INTEGER,
                welcome_channel_id INTEGER,
                vc_recruit_channel_id INTEGER,
                event_channel_id INTEGER,
                admin_log_channel_id INTEGER,
                promotion_channel_id INTEGER,

                ticket_category_id INTEGER,
                free_room_category_id INTEGER,
                private_room_category_id INTEGER,

                temp_role_id INTEGER,
                full_role_id INTEGER
            );


            CREATE TABLE IF NOT EXISTS members(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,

                joined_at TEXT,
                last_active_at TEXT,

                rules_done INTEGER DEFAULT 0,
                profile_done INTEGER DEFAULT 0,
                vc_done INTEGER DEFAULT 0,
                greeting_done INTEGER DEFAULT 0,

                review_status TEXT DEFAULT 'pending',

                promoted_at TEXT,
                last_reminder_at TEXT,

                PRIMARY KEY(guild_id, user_id)
            );


            CREATE TABLE IF NOT EXISTS profiles(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,

                age TEXT,
                voice TEXT,
                personality TEXT,
                likes TEXT,
                message TEXT,

                updated_at TEXT,

                PRIMARY KEY(guild_id, user_id)
            );


            CREATE TABLE IF NOT EXISTS self_roles(
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                emoji TEXT,

                PRIMARY KEY(guild_id, role_id)
            );


            CREATE TABLE IF NOT EXISTS tickets(
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS voice_rooms(
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                room_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS vc_profile_messages(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,

                PRIMARY KEY(guild_id, user_id)
            );


            CREATE TABLE IF NOT EXISTS warnings(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS events(
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                closed INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS event_members(
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL,

                PRIMARY KEY(message_id, user_id)
            );
            """
        )

        self.conn.commit()

    # -----------------------------------------------------

    def ensure_guild(self, guild_id: int):
        self.conn.execute(
            """
            INSERT OR IGNORE INTO guild_settings(guild_id)
            VALUES(?)
            """,
            (guild_id,),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def settings(self, guild_id: int):
        self.ensure_guild(guild_id)

        return self.conn.execute(
            """
            SELECT *
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()

    # -----------------------------------------------------

    def set_setting(
        self,
        guild_id: int,
        key: str,
        value,
    ):
        allowed = {
            "rules_channel_id",
            "profile_channel_id",
            "welcome_channel_id",
            "vc_recruit_channel_id",
            "event_channel_id",
            "admin_log_channel_id",
            "promotion_channel_id",

            "ticket_category_id",
            "free_room_category_id",
            "private_room_category_id",

            "temp_role_id",
            "full_role_id",
        }

        if key not in allowed:
            raise ValueError("Invalid setting key")

        self.ensure_guild(guild_id)

        self.conn.execute(
            f"""
            UPDATE guild_settings
            SET {key} = ?
            WHERE guild_id = ?
            """,
            (
                value,
                guild_id,
            ),
        )

        self.conn.commit()

    # =====================================================
    # MEMBER
    # =====================================================

    def ensure_member(
        self,
        guild_id: int,
        user_id: int,
        joined_at: Optional[str] = None,
    ):
        self.conn.execute(
            """
            INSERT OR IGNORE INTO members(
                guild_id,
                user_id,
                joined_at,
                last_active_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                joined_at or now_iso(),
                now_iso(),
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def member(
        self,
        guild_id: int,
        user_id: int,
    ):
        self.ensure_member(
            guild_id,
            user_id,
        )

        return self.conn.execute(
            """
            SELECT *
            FROM members
            WHERE guild_id = ?
            AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()

    # -----------------------------------------------------

    def member_set(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        value,
    ):
        allowed = {
            "last_active_at",
            "rules_done",
            "profile_done",
            "vc_done",
            "greeting_done",
            "review_status",
            "promoted_at",
            "last_reminder_at",
        }

        if key not in allowed:
            raise ValueError("Invalid member field")

        self.ensure_member(
            guild_id,
            user_id,
        )

        self.conn.execute(
            f"""
            UPDATE members
            SET {key} = ?
            WHERE guild_id = ?
            AND user_id = ?
            """,
            (
                value,
                guild_id,
                user_id,
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def all_members(
        self,
        guild_id: int,
    ):
        return self.conn.execute(
            """
            SELECT *
            FROM members
            WHERE guild_id = ?
            ORDER BY joined_at DESC
            """,
            (guild_id,),
        ).fetchall()

    # =====================================================
    # PROFILE
    # =====================================================

    def save_profile(
        self,
        guild_id: int,
        user_id: int,
        age: str,
        voice: str,
        personality: str,
        likes: str,
        message: str,
    ):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO profiles(
                guild_id,
                user_id,
                age,
                voice,
                personality,
                likes,
                message,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                age,
                voice,
                personality,
                likes,
                message,
                now_iso(),
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def profile(
        self,
        guild_id: int,
        user_id: int,
    ):
        return self.conn.execute(
            """
            SELECT *
            FROM profiles
            WHERE guild_id = ?
            AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()

    # =====================================================
    # SELF ROLE
    # =====================================================

    def role_add(
        self,
        guild_id: int,
        role_id: int,
        label: str,
        emoji: Optional[str],
    ):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO self_roles(
                guild_id,
                role_id,
                label,
                emoji
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                guild_id,
                role_id,
                label,
                emoji,
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def role_remove(
        self,
        guild_id: int,
        role_id: int,
    ):
        self.conn.execute(
            """
            DELETE FROM self_roles
            WHERE guild_id = ?
            AND role_id = ?
            """,
            (
                guild_id,
                role_id,
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def roles(
        self,
        guild_id: int,
    ):
        return self.conn.execute(
            """
            SELECT *
            FROM self_roles
            WHERE guild_id = ?
            ORDER BY label
            """,
            (guild_id,),
        ).fetchall()

    # =====================================================
    # TICKET
    # =====================================================

    def add_ticket(
        self,
        guild_id: int,
        channel_id: int,
        user_id: int,
    ):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO tickets(
                channel_id,
                guild_id,
                user_id,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                channel_id,
                guild_id,
                user_id,
                now_iso(),
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def ticket(
        self,
        channel_id: int,
    ):
        return self.conn.execute(
            """
            SELECT *
            FROM tickets
            WHERE channel_id = ?
            """,
            (channel_id,),
        ).fetchone()

    # -----------------------------------------------------

    def user_ticket(
        self,
        guild_id: int,
        user_id: int,
    ):
        return self.conn.execute(
            """
            SELECT *
            FROM tickets
            WHERE guild_id = ?
            AND user_id = ?
            LIMIT 1
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()

    # -----------------------------------------------------

    def delete_ticket(
        self,
        channel_id: int,
    ):
        self.conn.execute(
            """
            DELETE FROM tickets
            WHERE channel_id = ?
            """,
            (channel_id,),
        )

        self.conn.commit()

    # =====================================================
    # VOICE ROOM
    # =====================================================

    def add_room(
        self,
        guild_id: int,
        channel_id: int,
        owner_id: int,
        room_type: str,
    ):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO voice_rooms(
                channel_id,
                guild_id,
                owner_id,
                room_type,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                guild_id,
                owner_id,
                room_type,
                now_iso(),
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def room(
        self,
        channel_id: int,
    ):
        return self.conn.execute(
            """
            SELECT *
            FROM voice_rooms
            WHERE channel_id = ?
            """,
            (channel_id,),
        ).fetchone()

    # -----------------------------------------------------

    def owner_rooms(
        self,
        guild_id: int,
        owner_id: int,
    ):
        return self.conn.execute(
            """
            SELECT *
            FROM voice_rooms
            WHERE guild_id = ?
            AND owner_id = ?
            """,
            (
                guild_id,
                owner_id,
            ),
        ).fetchall()

    # -----------------------------------------------------

    def delete_room(
        self,
        channel_id: int,
    ):
        self.conn.execute(
            """
            DELETE FROM voice_rooms
            WHERE channel_id = ?
            """,
            (channel_id,),
        )

        self.conn.commit()

    # =====================================================
    # VC PROFILE MESSAGE
    # =====================================================

    def set_vc_profile_message(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        message_id: int,
    ):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO vc_profile_messages(
                guild_id,
                user_id,
                channel_id,
                message_id
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                channel_id,
                message_id,
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def vc_profile_message(
        self,
        guild_id: int,
        user_id: int,
    ):
        return self.conn.execute(
            """
            SELECT *
            FROM vc_profile_messages
            WHERE guild_id = ?
            AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()

    # -----------------------------------------------------

    def delete_vc_profile_message(
        self,
        guild_id: int,
        user_id: int,
    ):
        self.conn.execute(
            """
            DELETE FROM vc_profile_messages
            WHERE guild_id = ?
            AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        )

        self.conn.commit()

    # =====================================================
    # WARN
    # =====================================================

    def add_warning(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        reason: str,
    ):
        self.conn.execute(
            """
            INSERT INTO warnings(
                guild_id,
                user_id,
                moderator_id,
                reason,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                moderator_id,
                reason,
                now_iso(),
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def warning_count(
        self,
        guild_id: int,
        user_id: int,
    ) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM warnings
            WHERE guild_id = ?
            AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()

        return int(row["c"])

    # =====================================================
    # EVENTS
    # =====================================================

    def add_event(
        self,
        message_id: int,
        guild_id: int,
        channel_id: int,
        title: str,
        description: str,
    ):
        self.conn.execute(
            """
            INSERT INTO events(
                message_id,
                guild_id,
                channel_id,
                title,
                description,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                guild_id,
                channel_id,
                title,
                description,
                now_iso(),
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def event(
        self,
        message_id: int,
    ):
        return self.conn.execute(
            """
            SELECT *
            FROM events
            WHERE message_id = ?
            """,
            (message_id,),
        ).fetchone()

    # -----------------------------------------------------

    def event_join(
        self,
        message_id: int,
        user_id: int,
    ):
        self.conn.execute(
            """
            INSERT OR IGNORE INTO event_members(
                message_id,
                user_id,
                joined_at
            )
            VALUES (?, ?, ?)
            """,
            (
                message_id,
                user_id,
                now_iso(),
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def event_leave(
        self,
        message_id: int,
        user_id: int,
    ):
        self.conn.execute(
            """
            DELETE FROM event_members
            WHERE message_id = ?
            AND user_id = ?
            """,
            (
                message_id,
                user_id,
            ),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def event_members(
        self,
        message_id: int,
    ):
        return self.conn.execute(
            """
            SELECT *
            FROM event_members
            WHERE message_id = ?
            ORDER BY joined_at
            """,
            (message_id,),
        ).fetchall()

    # -----------------------------------------------------

    def close_event(
        self,
        message_id: int,
    ):
        self.conn.execute(
            """
            UPDATE events
            SET closed = 1
            WHERE message_id = ?
            """,
            (message_id,),
        )

        self.conn.commit()


db = Database(DB_PATH)


# =========================================================
# 🎨 EMBED
# =========================================================

def chrono_embed(
    title: str,
    description: str,
    guild: Optional[discord.Guild] = None,
) -> discord.Embed:

    embed = discord.Embed(
        title=f"✦ {title}",
        description=description,
        color=discord.Color.from_rgb(
            239,
            205,
            145,
        ),
    )

    embed.set_author(
        name=f"{BOT_NAME}｜{SERVER_NAME} 専属案内人"
    )

    if guild and guild.icon:
        embed.set_thumbnail(
            url=guild.icon.url
        )

    embed.set_footer(
        text=f"{SERVER_NAME} ─ クロノがお手伝いします"
    )

    return embed


# =========================================================
# 🧾 PROFILE EMBED
# =========================================================

def make_profile_embed(
    guild: discord.Guild,
    member: discord.Member,
) -> discord.Embed:

    profile = db.profile(
        guild.id,
        member.id,
    )

    if not profile:
        return chrono_embed(
            f"{member.display_name}さんのプロフィール",
            "まだプロフィールが登録されていません。",
            guild,
        )

    embed = discord.Embed(
        title="📖 プロフィール",
        color=discord.Color.from_rgb(
            245,
            174,
            205,
        ),
    )

    embed.set_author(
        name=member.display_name,
        icon_url=member.display_avatar.url,
    )

    embed.add_field(
        name="年齢",
        value=profile["age"] or "未記入",
        inline=True,
    )

    embed.add_field(
        name="声質",
        value=profile["voice"] or "未記入",
        inline=True,
    )

    embed.add_field(
        name="性格",
        value=profile["personality"] or "未記入",
        inline=False,
    )

    embed.add_field(
        name="好きなこと・話題",
        value=profile["likes"] or "未記入",
        inline=False,
    )

    embed.add_field(
        name="ひとこと",
        value=profile["message"] or "よろしくお願いします！",
        inline=False,
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    return embed


# =========================================================
# 🤖 BOT
# =========================================================

class ChronoBot(commands.Bot):

    def __init__(self):

        intents = discord.Intents.default()

        intents.members = True
        intents.guilds = True
        intents.voice_states = True
        intents.messages = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

    async def setup_hook(self):

        self.add_view(MainGuideView())
        self.add_view(AdminPanelView())
        self.add_view(TicketCloseView())
        self.add_view(VCProfileView())
        self.add_view(EventView())

        newbie_reminder_loop.start()

        try:
            synced = await self.tree.sync()

            log.info(
                "Synced %s commands",
                len(synced),
            )

        except Exception:
            log.exception(
                "Slash command sync failed"
            )


bot = ChronoBot()


# =========================================================
# 🔧 VCプロフィール表示用タスク
# =========================================================

vc_profile_tasks: dict[int, asyncio.Task] = {}


async def delete_old_vc_profile(
    guild: discord.Guild,
    user_id: int,
):

    row = db.vc_profile_message(
        guild.id,
        user_id,
    )

    if not row:
        return

    channel = guild.get_channel(
        row["channel_id"]
    )

    if channel:

        try:
            message = await channel.fetch_message(
                row["message_id"]
            )

            await message.delete()

        except Exception:
            pass

    db.delete_vc_profile_message(
        guild.id,
        user_id,
    )


# =========================================================
# 📖 VCプロフィールボタン
# =========================================================

class VCProfileView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="プロフィールを見る",
        emoji="📖",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:view_vc_profile",
    )
    async def view_profile(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        if not interaction.message.embeds:

            await interaction.response.send_message(
                "プロフィール情報を取得できませんでした。",
                ephemeral=True,
            )

            return

        embed = interaction.message.embeds[0]

        footer = embed.footer.text or ""

        match = re.search(
            r"profile_user_id:(\d+)",
            footer,
        )

        if not match:

            await interaction.response.send_message(
                "対象メンバーを確認できませんでした。",
                ephemeral=True,
            )

            return

        user_id = int(
            match.group(1)
        )

        member = interaction.guild.get_member(
            user_id
        )

        if not member:

            await interaction.response.send_message(
                "メンバーが見つかりません。",
                ephemeral=True,
            )

            return

        await interaction.response.send_message(
            embed=make_profile_embed(
                interaction.guild,
                member,
            ),
            ephemeral=True,
        )


# =========================================================
# 👤 VCプロフィール表示
# =========================================================

async def post_vc_profile(
    member: discord.Member,
):

    guild = member.guild

    # 自動移動が落ち着くまで待つ
    await asyncio.sleep(
        VC_PROFILE_DELAY
    )

    # 現在VCにいなければ削除だけ
    if not member.voice or not member.voice.channel:

        await delete_old_vc_profile(
            guild,
            member.id,
        )

        return

    channel = member.voice.channel

    await delete_old_vc_profile(
        guild,
        member.id,
    )

    profile = db.profile(
        guild.id,
        member.id,
    )

    embed = discord.Embed(
        title="🏫 プロフィール",
        description=(
            f"{member.mention} さんがお部屋に参加しました！\n\n"
            "📖 **プロフィール**\n"
            "下のボタンからプロフィールを確認できます。"
        ),
        color=discord.Color.from_rgb(
            245,
            174,
            205,
        ),
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    if not profile:

        embed.add_field(
            name="⚠️",
            value="プロフィールはまだ登録されていません。",
            inline=False,
        )

    embed.set_footer(
        text=f"profile_user_id:{member.id}"
    )

    try:

        message = await channel.send(
            embed=embed,
            view=VCProfileView(),
        )

        db.set_vc_profile_message(
            guild.id,
            member.id,
            channel.id,
            message.id,
        )

    except Exception as e:

        log.warning(
            "VC profile send failed: %s",
            e,
        )


# =========================================================
# 👋 MAIN GUIDE
# =========================================================

class MainGuideView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=None
        )

    # -----------------------------------------------------
    # はじめてガイド
    # -----------------------------------------------------

    @discord.ui.button(
        label="はじめてガイド",
        emoji="✨",
        style=discord.ButtonStyle.primary,
        custom_id="chrono:first_guide",
        row=0,
    )
    async def first_guide(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        data = db.member(
            interaction.guild.id,
            interaction.user.id,
        )

        embed = chrono_embed(
            "クロノのはじめてガイド",
            (
                "**STEP 1｜利用規約を確認**\n"
                "ルールを読んで確認ボタンを押してください。\n\n"

                "**STEP 2｜プロフィール登録**\n"
                "クロノにプロフィールを登録します。\n\n"

                "**STEP 3｜まずは交流**\n"
                "テキストで挨拶してみましょう。\n\n"

                "**STEP 4｜VCに参加**\n"
                "一度VCに参加すると研修進捗に記録されます。\n\n"

                "**STEP 5｜本メンバー審査**\n"
                "準備が整ったら管理者が確認します。"
            ),
            interaction.guild,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    # -----------------------------------------------------
    # ルール
    # -----------------------------------------------------

    @discord.ui.button(
        label="ルール",
        emoji="📖",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:rules",
        row=0,
    )
    async def rules(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        settings = db.settings(
            interaction.guild.id
        )

        channel = interaction.guild.get_channel(
            settings["rules_channel_id"]
        ) if settings[
            "rules_channel_id"
        ] else None

        text = (
            "サーバーをご利用になる前に、"
            "利用規約を必ず確認してください。"
        )

        if channel:
            text += (
                f"\n\n📖 利用規約はこちら\n"
                f"{channel.mention}"
            )

        await interaction.response.send_message(
            embed=chrono_embed(
                "利用規約",
                text,
                interaction.guild,
            ),
            view=RulesDoneView(),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # PROFILE REGISTER
    # -----------------------------------------------------

    @discord.ui.button(
        label="プロフィール登録",
        emoji="🪞",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:profile_register",
        row=0,
    )
    async def profile_register(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_modal(
            ProfileModal()
        )

    # -----------------------------------------------------
    # PROFILE VIEW
    # -----------------------------------------------------

    @discord.ui.button(
        label="自分のプロフィール",
        emoji="👤",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:profile_me",
        row=0,
    )
    async def profile_me(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        await interaction.response.send_message(
            embed=make_profile_embed(
                interaction.guild,
                interaction.user,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    @discord.ui.button(
        label="新人研修進捗",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="chrono:progress",
        row=1,
    )
    async def progress(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        data = db.member(
            interaction.guild.id,
            interaction.user.id,
        )

        def check(value):
            return "✅ 完了" if value else "⬜ 未完了"

        review = {
            "pending": "⏳ 審査待ち",
            "approved": "✅ 承認済み",
            "rejected": "🔄 再確認",
        }.get(
            data["review_status"],
            "⏳ 審査待ち",
        )

        complete = sum(
            [
                bool(data["rules_done"]),
                bool(data["profile_done"]),
                bool(data["greeting_done"]),
                bool(data["vc_done"]),
            ]
        )

        embed = chrono_embed(
            "新人研修進捗",
            (
                f"📖 ルール確認：{check(data['rules_done'])}\n\n"
                f"🪞 プロフィール：{check(data['profile_done'])}\n\n"
                f"💬 初回交流：{check(data['greeting_done'])}\n\n"
                f"🎙️ VC参加：{check(data['vc_done'])}\n\n"
                f"🔎 審査：{review}\n\n"
                f"**進捗：{complete}/4**"
            ),
            interaction.guild,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    # -----------------------------------------------------
    # VC RECRUIT
    # -----------------------------------------------------

    @discord.ui.button(
        label="VC募集",
        emoji="🎙️",
        style=discord.ButtonStyle.success,
        custom_id="chrono:vc_recruit",
        row=1,
    )
    async def vc_recruit(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_modal(
            VCRecruitModal()
        )

    # -----------------------------------------------------
    # ROLES
    # -----------------------------------------------------

    @discord.ui.button(
        label="ロール取得",
        emoji="🏷️",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:self_roles",
        row=1,
    )
    async def self_roles(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        rows = db.roles(
            interaction.guild.id
        )

        if not rows:

            await interaction.response.send_message(
                "現在取得できるロールはありません。",
                ephemeral=True,
            )

            return

        await interaction.response.send_message(
            embed=chrono_embed(
                "ロール取得",
                "欲しいロールを選択してください。",
                interaction.guild,
            ),
            view=SelfRoleView(
                interaction.guild,
                rows,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # ROOMS
    # -----------------------------------------------------

    @discord.ui.button(
        label="お部屋作成",
        emoji="🔑",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:room_create",
        row=1,
    )
    async def room_create(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_message(
            embed=chrono_embed(
                "お部屋作成",
                (
                    "作成するお部屋を選んでください。\n\n"
                    "🔊 **フリールーム**\n"
                    "誰でも参加できます。\n\n"
                    "🔐 **個室**\n"
                    "招待した人だけ参加できます。"
                ),
                interaction.guild,
            ),
            view=RoomCreateView(),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # EVENT
    # -----------------------------------------------------

    @discord.ui.button(
        label="イベント",
        emoji="🎉",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:event_info",
        row=2,
    )
    async def events(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        settings = db.settings(
            interaction.guild.id
        )

        channel = interaction.guild.get_channel(
            settings["event_channel_id"]
        ) if settings[
            "event_channel_id"
        ] else None

        if channel:

            text = (
                "開催中のイベントはこちらから確認できます。\n\n"
                f"{channel.mention}"
            )

        else:

            text = (
                "現在イベントチャンネルが"
                "設定されていません。"
            )

        await interaction.response.send_message(
            embed=chrono_embed(
                "イベント案内",
                text,
                interaction.guild,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # FAQ
    # -----------------------------------------------------

    @discord.ui.button(
        label="よくある質問",
        emoji="💭",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:faq",
        row=2,
    )
    async def faq(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_message(
            embed=chrono_embed(
                "よくある質問",
                (
                    "**Q. 最初に何をすればいい？**\n"
                    "はじめてガイドから順番に進めてください。\n\n"

                    "**Q. プロフィールはどこ？**\n"
                    "クロノのプロフィール登録から登録できます。\n\n"

                    "**Q. VCに入るとどうなる？**\n"
                    "VCのインチャにあなたのプロフィール案内が表示されます。\n\n"

                    "**Q. 管理者に相談したい**\n"
                    "管理者相談ボタンから専用チャンネルを作れます。\n\n"

                    "**Q. 研修の進み具合を見たい**\n"
                    "新人研修進捗を押してください。"
                ),
                interaction.guild,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # TICKET
    # -----------------------------------------------------

    @discord.ui.button(
        label="管理者に相談",
        emoji="🔔",
        style=discord.ButtonStyle.danger,
        custom_id="chrono:ticket",
        row=2,
    )
    async def ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await create_ticket(
            interaction
        )


# =========================================================
# 📖 RULE DONE
# =========================================================

class RulesDoneView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=300
        )

    @discord.ui.button(
        label="ルール確認完了",
        emoji="✅",
        style=discord.ButtonStyle.success,
    )
    async def done(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        db.member_set(
            interaction.guild.id,
            interaction.user.id,
            "rules_done",
            1,
        )

        await interaction.response.edit_message(
            content="✅ ルール確認を記録しました。",
            embed=None,
            view=None,
        )


# =========================================================
# 🪞 PROFILE MODAL
# =========================================================

class ProfileModal(
    discord.ui.Modal,
    title="クロノ｜プロフィール登録",
):

    age = discord.ui.TextInput(
        label="年齢",
        placeholder="例：30代 / 32歳",
        required=False,
        max_length=30,
    )

    voice = discord.ui.TextInput(
        label="声質",
        placeholder="例：低め・落ち着いた声",
        required=False,
        max_length=50,
    )

    personality = discord.ui.TextInput(
        label="性格",
        placeholder="例：人見知りだけど慣れるとよく話します",
        required=False,
        max_length=150,
    )

    likes = discord.ui.TextInput(
        label="好きなこと・話題",
        placeholder="ゲーム、アニメ、雑談、お酒など",
        required=False,
        max_length=150,
    )

    message = discord.ui.TextInput(
        label="ひとこと",
        placeholder="よろしくお願いします！",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=300,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        if not interaction.guild:
            return

        db.save_profile(
            interaction.guild.id,
            interaction.user.id,
            self.age.value,
            self.voice.value,
            self.personality.value,
            self.likes.value,
            self.message.value,
        )

        db.member_set(
            interaction.guild.id,
            interaction.user.id,
            "profile_done",
            1,
        )

        await interaction.response.send_message(
            embed=make_profile_embed(
                interaction.guild,
                interaction.user,
            ),
            ephemeral=True,
        )


# =========================================================
# 🎙️ VC RECRUIT
# =========================================================

class VCRecruitModal(
    discord.ui.Modal,
    title="VC募集",
):

    comment = discord.ui.TextInput(
        label="ひとこと",
        placeholder="誰か話しませんか？",
        required=False,
        max_length=200,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        if not interaction.guild:
            return

        settings = db.settings(
            interaction.guild.id
        )

        channel = interaction.guild.get_channel(
            settings["vc_recruit_channel_id"]
        ) if settings[
            "vc_recruit_channel_id"
        ] else interaction.channel

        if not channel:

            await interaction.response.send_message(
                "VC募集チャンネルが設定されていません。",
                ephemeral=True,
            )

            return

        embed = chrono_embed(
            "VC募集",
            (
                f"{interaction.user.mention} さんが"
                "VCメンバーを募集しています！\n\n"
                f"💬 {self.comment.value or '誰か話しませんか？'}"
            ),
            interaction.guild,
        )

        try:
            await channel.send(
                embed=embed
            )

        except Exception:

            await interaction.response.send_message(
                "VC募集を投稿できませんでした。",
                ephemeral=True,
            )

            return

        await interaction.response.send_message(
            "🎙️ VC募集を投稿しました。",
            ephemeral=True,
        )


# =========================================================
# 🏷️ SELF ROLE
# =========================================================

class SelfRoleSelect(discord.ui.Select):

    def __init__(
        self,
        guild: discord.Guild,
        rows,
    ):

        options = []

        for row in rows[:25]:

            role = guild.get_role(
                row["role_id"]
            )

            if not role:
                continue

            kwargs = {}

            if row["emoji"]:
                kwargs["emoji"] = row["emoji"]

            options.append(
                discord.SelectOption(
                    label=row["label"][:100],
                    value=str(role.id),
                    **kwargs,
                )
            )

        super().__init__(
            placeholder="取得・解除するロールを選択",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        if not interaction.guild:
            return

        role = interaction.guild.get_role(
            int(self.values[0])
        )

        if not role:

            await interaction.response.send_message(
                "ロールが見つかりません。",
                ephemeral=True,
            )

            return

        member = interaction.user

        try:

            if role in member.roles:

                await member.remove_roles(
                    role,
                    reason="クロノ セルフロール解除",
                )

                text = (
                    f"➖ {role.mention} を解除しました。"
                )

            else:

                await member.add_roles(
                    role,
                    reason="クロノ セルフロール付与",
                )

                text = (
                    f"➕ {role.mention} を付与しました。"
                )

        except discord.Forbidden:

            text = (
                "ロールを操作できません。\n"
                "クロノのロールを対象ロールより上にしてください。"
            )

        await interaction.response.send_message(
            text,
            ephemeral=True,
        )


class SelfRoleView(discord.ui.View):

    def __init__(
        self,
        guild,
        rows,
    ):

        super().__init__(
            timeout=300
        )

        self.add_item(
            SelfRoleSelect(
                guild,
                rows,
            )
        )


# =========================================================
# 🔑 ROOM CREATE
# =========================================================

class RoomCreateView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=300
        )

    @discord.ui.button(
        label="フリールーム",
        emoji="🔊",
        style=discord.ButtonStyle.success,
    )
    async def free(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await create_voice_room(
            interaction,
            private=False,
        )

    @discord.ui.button(
        label="個室",
        emoji="🔐",
        style=discord.ButtonStyle.primary,
    )
    async def private(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_modal(
            PrivateRoomModal()
        )


class PrivateRoomModal(
    discord.ui.Modal,
    title="個室作成",
):

    room_name = discord.ui.TextInput(
        label="部屋名",
        placeholder="まったり部屋",
        required=False,
        max_length=40,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        await create_voice_room(
            interaction,
            private=True,
            custom_name=(
                self.room_name.value
                or None
            ),
        )


async def create_voice_room(
    interaction: discord.Interaction,
    private: bool,
    custom_name: Optional[str] = None,
):

    if not interaction.guild:
        return

    guild = interaction.guild
    owner = interaction.user

    # 既存部屋確認
    for row in db.owner_rooms(
        guild.id,
        owner.id,
    ):

        channel = guild.get_channel(
            row["channel_id"]
        )

        if channel:

            await interaction.response.send_message(
                (
                    "すでにあなたのお部屋があります。\n"
                    f"{channel.mention}"
                ),
                ephemeral=True,
            )

            return

        db.delete_room(
            row["channel_id"]
        )

    settings = db.settings(
        guild.id
    )

    category_id = (
        settings["private_room_category_id"]
        if private
        else settings["free_room_category_id"]
    )

    category = guild.get_channel(
        category_id
    ) if category_id else None

    overwrites = None

    if private:

        overwrites = {
            guild.default_role:
                discord.PermissionOverwrite(
                    view_channel=False,
                    connect=False,
                ),

            owner:
                discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    speak=True,
                    manage_channels=True,
                    move_members=True,
                ),

            guild.me:
                discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    manage_channels=True,
                    move_members=True,
                ),
        }

    room_name = (
        custom_name
        or (
            f"{owner.display_name}の個室"
            if private
            else f"{owner.display_name}のフリールーム"
        )
    )

    try:

        channel = await guild.create_voice_channel(
            name=(
                f"🔐 {room_name}"
                if private
                else f"🔊 {room_name}"
            ),
            category=(
                category
                if isinstance(
                    category,
                    discord.CategoryChannel,
                )
                else None
            ),
            overwrites=overwrites,
            reason="クロノ 部屋作成",
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "クロノにチャンネル管理権限が必要です。",
            ephemeral=True,
        )

        return

    db.add_room(
        guild.id,
        channel.id,
        owner.id,
        "private" if private else "free",
    )

    await interaction.response.send_message(
        (
            f"✨ お部屋を作成しました。\n"
            f"{channel.mention}\n\n"
            "2分以内に入室してください。"
        ),
        ephemeral=True,
    )

    async def owner_join_timeout():

        await asyncio.sleep(
            ROOM_JOIN_TIMEOUT
        )

        room = guild.get_channel(
            channel.id
        )

        if not isinstance(
            room,
            discord.VoiceChannel,
        ):
            return

        if owner not in room.members:

            try:
                await room.delete(
                    reason="部屋主未入室"
                )

            except Exception:
                pass

            db.delete_room(
                room.id
            )

    asyncio.create_task(
        owner_join_timeout()
    )


# =========================================================
# 🔔 TICKET
# =========================================================

async def create_ticket(
    interaction: discord.Interaction,
):

    if not interaction.guild:
        return

    guild = interaction.guild

    old = db.user_ticket(
        guild.id,
        interaction.user.id,
    )

    if old:

        old_channel = guild.get_channel(
            old["channel_id"]
        )

        if old_channel:

            await interaction.response.send_message(
                (
                    "すでに相談チャンネルがあります。\n"
                    f"{old_channel.mention}"
                ),
                ephemeral=True,
            )

            return

        db.delete_ticket(
            old["channel_id"]
        )

    settings = db.settings(
        guild.id
    )

    category = guild.get_channel(
        settings["ticket_category_id"]
    ) if settings[
        "ticket_category_id"
    ] else None

    overwrites = {
        guild.default_role:
            discord.PermissionOverwrite(
                view_channel=False
            ),

        interaction.user:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),

        guild.me:
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
            ),
    }

    for role in guild.roles:

        if role.permissions.administrator:

            overwrites[role] = (
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
            )

    safe_name = re.sub(
        r"[^a-zA-Z0-9ぁ-んァ-ン一-龥_-]",
        "",
        interaction.user.display_name,
    )

    safe_name = (
        safe_name
        or str(interaction.user.id)
    )

    try:

        channel = await guild.create_text_channel(
            name=f"相談-{safe_name}"[:100],
            category=(
                category
                if isinstance(
                    category,
                    discord.CategoryChannel,
                )
                else None
            ),
            overwrites=overwrites,
            reason="クロノ 管理者相談",
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "クロノにチャンネル管理権限が必要です。",
            ephemeral=True,
        )

        return

    db.add_ticket(
        guild.id,
        channel.id,
        interaction.user.id,
    )

    await channel.send(
        interaction.user.mention,
        embed=chrono_embed(
            "管理者への相談",
            (
                "こちらに相談内容を書いてください。\n\n"
                "相談が終わったら「相談を終了」を押してください。"
            ),
            guild,
        ),
        view=TicketCloseView(),
    )

    await interaction.response.send_message(
        (
            "相談チャンネルを作成しました。\n"
            f"{channel.mention}"
        ),
        ephemeral=True,
    )


class TicketCloseView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="相談を終了",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="chrono:ticket_close",
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        ticket = db.ticket(
            interaction.channel.id
        )

        if not ticket:

            await interaction.response.send_message(
                "相談チャンネルではありません。",
                ephemeral=True,
            )

            return

        allowed = (
            interaction.user.id
            == ticket["user_id"]
            or interaction.user.guild_permissions.manage_channels
            or interaction.user.guild_permissions.administrator
        )

        if not allowed:

            await interaction.response.send_message(
                "相談を終了する権限がありません。",
                ephemeral=True,
            )

            return

        await interaction.response.send_message(
            "相談を終了します。"
        )

        db.delete_ticket(
            interaction.channel.id
        )

        await asyncio.sleep(2)

        try:
            await interaction.channel.delete()
        except Exception:
            pass


# =========================================================
# 🎉 EVENTS
# =========================================================

class EventView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="参加する",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="chrono:event_join",
    )
    async def join(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        event = db.event(
            interaction.message.id
        )

        if not event or event["closed"]:

            await interaction.response.send_message(
                "イベント受付は終了しています。",
                ephemeral=True,
            )

            return

        db.event_join(
            interaction.message.id,
            interaction.user.id,
        )

        await refresh_event(
            interaction.message
        )

        await interaction.response.send_message(
            "🎉 参加受付しました。",
            ephemeral=True,
        )

    @discord.ui.button(
        label="キャンセル",
        emoji="➖",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:event_leave",
    )
    async def leave(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        db.event_leave(
            interaction.message.id,
            interaction.user.id,
        )

        await refresh_event(
            interaction.message
        )

        await interaction.response.send_message(
            "参加をキャンセルしました。",
            ephemeral=True,
        )


async def refresh_event(
    message: discord.Message,
):

    event = db.event(
        message.id
    )

    if not event:
        return

    members = db.event_members(
        message.id
    )

    participants = (
        "\n".join(
            f"<@{row['user_id']}>"
            for row in members[:30]
        )
        or "まだ参加者はいません。"
    )

    embed = chrono_embed(
        event["title"],
        event["description"],
        message.guild,
    )

    embed.add_field(
        name=f"参加者｜{len(members)}名",
        value=participants[:1024],
        inline=False,
    )

    await message.edit(
        embed=embed,
        view=(
            None
            if event["closed"]
            else EventView()
        ),
    )


# =========================================================
# 👑 ADMIN PANEL
# =========================================================

class AdminPanelView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="新人管理",
        emoji="🔰",
        style=discord.ButtonStyle.primary,
        custom_id="chrono:admin_newbies",
        row=0,
    )
    async def newbies(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.user.guild_permissions.manage_guild:

            await interaction.response.send_message(
                "管理者専用です。",
                ephemeral=True,
            )

            return

        rows = db.all_members(
            interaction.guild.id
        )

        lines = []

        for row in rows:

            if row["review_status"] == "approved":
                continue

            member = interaction.guild.get_member(
                row["user_id"]
            )

            if not member:
                continue

            done = sum(
                [
                    bool(row["rules_done"]),
                    bool(row["profile_done"]),
                    bool(row["greeting_done"]),
                    bool(row["vc_done"]),
                ]
            )

            lines.append(
                (
                    f"{member.mention}\n"
                    f"└ 研修 {done}/4 ｜ "
                    f"審査 {row['review_status']}"
                )
            )

        await interaction.response.send_message(
            embed=chrono_embed(
                "新人管理",
                (
                    "\n\n".join(lines[:30])
                    or "現在、審査待ちの新人はいません。"
                ),
                interaction.guild,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------

    @discord.ui.button(
        label="サーバー状況",
        emoji="📊",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:admin_stats",
        row=0,
    )
    async def stats(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.user.guild_permissions.manage_guild:

            await interaction.response.send_message(
                "管理者専用です。",
                ephemeral=True,
            )

            return

        guild = interaction.guild

        humans = [
            m for m in guild.members
            if not m.bot
        ]

        online = [
            m for m in humans
            if m.status != discord.Status.offline
        ]

        vc = [
            m for m in humans
            if m.voice and m.voice.channel
        ]

        embed = chrono_embed(
            "サーバー状況",
            (
                f"👥 メンバー：**{len(humans)}人**\n"
                f"🟢 オンライン：**{len(online)}人**\n"
                f"🎙️ VC参加中：**{len(vc)}人**"
            ),
            guild,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    # -----------------------------------------------------

    @discord.ui.button(
        label="Bot設定",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:admin_settings",
        row=0,
    )
    async def settings(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.user.guild_permissions.administrator:

            await interaction.response.send_message(
                "管理者専用です。",
                ephemeral=True,
            )

            return

        await interaction.response.send_message(
            embed=chrono_embed(
                "クロノ設定",
                (
                    "設定したい項目を選んでください。\n\n"
                    "スマホでも見やすいように、"
                    "設定は種類ごとに分けています。"
                ),
                interaction.guild,
            ),
            view=SettingsMenuView(),
            ephemeral=True,
        )

    # -----------------------------------------------------

    @discord.ui.button(
        label="イベント作成",
        emoji="🎉",
        style=discord.ButtonStyle.success,
        custom_id="chrono:admin_event",
        row=1,
    )
    async def event_create(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.user.guild_permissions.manage_guild:

            await interaction.response.send_message(
                "管理者専用です。",
                ephemeral=True,
            )

            return

        await interaction.response.send_modal(
            EventCreateModal()
        )


# =========================================================
# ⚙️ SETTINGS MENU
# =========================================================

class SettingsMenuView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=300
        )

    @discord.ui.button(
        label="チャンネル設定",
        emoji="💬",
        style=discord.ButtonStyle.primary,
    )
    async def channels(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.edit_message(
            embed=chrono_embed(
                "チャンネル設定",
                "設定する項目を選んでください。",
                interaction.guild,
            ),
            view=ChannelSettingTypeView(),
        )

    @discord.ui.button(
        label="カテゴリー設定",
        emoji="📁",
        style=discord.ButtonStyle.secondary,
    )
    async def categories(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.edit_message(
            embed=chrono_embed(
                "カテゴリー設定",
                "設定する項目を選んでください。",
                interaction.guild,
            ),
            view=CategorySettingTypeView(),
        )

    @discord.ui.button(
        label="ロール設定",
        emoji="🏷️",
        style=discord.ButtonStyle.secondary,
    )
    async def roles(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.edit_message(
            embed=chrono_embed(
                "ロール設定",
                "設定する項目を選んでください。",
                interaction.guild,
            ),
            view=RoleSettingTypeView(),
        )


# =========================================================
# CHANNEL SETTINGS
# =========================================================

class ChannelSettingTypeView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=300
        )

    async def open_select(
        self,
        interaction,
        key,
        title,
    ):

        await interaction.response.edit_message(
            embed=chrono_embed(
                title,
                "設定するチャンネルを選択してください。",
                interaction.guild,
            ),
            view=SingleChannelSelectView(
                key
            ),
        )

    @discord.ui.button(
        label="利用規約",
        emoji="📖",
    )
    async def rules(
        self,
        interaction,
        button,
    ):
        await self.open_select(
            interaction,
            "rules_channel_id",
            "利用規約チャンネル",
        )

    @discord.ui.button(
        label="プロフィール",
        emoji="🪞",
    )
    async def profile(
        self,
        interaction,
        button,
    ):
        await self.open_select(
            interaction,
            "profile_channel_id",
            "プロフィールチャンネル",
        )

    @discord.ui.button(
        label="ウェルカム",
        emoji="👋",
    )
    async def welcome(
        self,
        interaction,
        button,
    ):
        await self.open_select(
            interaction,
            "welcome_channel_id",
            "ウェルカムチャンネル",
        )

    @discord.ui.button(
        label="VC募集",
        emoji="🎙️",
    )
    async def vc(
        self,
        interaction,
        button,
    ):
        await self.open_select(
            interaction,
            "vc_recruit_channel_id",
            "VC募集チャンネル",
        )

    @discord.ui.button(
        label="イベント",
        emoji="🎉",
    )
    async def event(
        self,
        interaction,
        button,
    ):
        await self.open_select(
            interaction,
            "event_channel_id",
            "イベントチャンネル",
        )

    @discord.ui.button(
        label="管理ログ",
        emoji="📝",
    )
    async def log(
        self,
        interaction,
        button,
    ):
        await self.open_select(
            interaction,
            "admin_log_channel_id",
            "管理ログチャンネル",
        )

    @discord.ui.button(
        label="昇格通知",
        emoji="🌟",
    )
    async def promotion(
        self,
        interaction,
        button,
    ):
        await self.open_select(
            interaction,
            "promotion_channel_id",
            "昇格通知チャンネル",
        )


class SingleChannelSelect(
    discord.ui.ChannelSelect
):

    def __init__(
        self,
        setting_key: str,
    ):

        self.setting_key = setting_key

        super().__init__(
            placeholder="チャンネルを選択",
            min_values=1,
            max_values=1,
            channel_types=[
                discord.ChannelType.text
            ],
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        channel = self.values[0]

        db.set_setting(
            interaction.guild.id,
            self.setting_key,
            channel.id,
        )

        await interaction.response.edit_message(
            content=(
                f"✅ {channel.mention} に設定しました。"
            ),
            embed=None,
            view=None,
        )


class SingleChannelSelectView(
    discord.ui.View
):

    def __init__(
        self,
        setting_key: str,
    ):

        super().__init__(
            timeout=300
        )

        self.add_item(
            SingleChannelSelect(
                setting_key
            )
        )


# =========================================================
# CATEGORY SETTINGS
# =========================================================

class CategorySettingTypeView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=300
        )

    async def open_select(
        self,
        interaction,
        key,
        title,
    ):

        await interaction.response.edit_message(
            embed=chrono_embed(
                title,
                "カテゴリーを選択してください。",
                interaction.guild,
            ),
            view=SingleCategorySelectView(
                key
            ),
        )

    @discord.ui.button(
        label="相談",
        emoji="🔔",
    )
    async def ticket(
        self,
        interaction,
        button,
    ):
        await self.open_select(
            interaction,
            "ticket_category_id",
            "相談カテゴリー",
        )

    @discord.ui.button(
        label="フリールーム",
        emoji="🔊",
    )
    async def free(
        self,
        interaction,
        button,
    ):
        await self.open_select(
            interaction,
            "free_room_category_id",
            "フリールームカテゴリー",
        )

    @discord.ui.button(
        label="個室",
        emoji="🔐",
    )
    async def private(
        self,
        interaction,
        button,
    ):
        await self.open_select(
            interaction,
            "private_room_category_id",
            "個室カテゴリー",
        )


class SingleCategorySelect(
    discord.ui.ChannelSelect
):

    def __init__(
        self,
        setting_key: str,
    ):

        self.setting_key = setting_key

        super().__init__(
            placeholder="カテゴリーを選択",
            min_values=1,
            max_values=1,
            channel_types=[
                discord.ChannelType.category
            ],
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        category = self.values[0]

        db.set_setting(
            interaction.guild.id,
            self.setting_key,
            category.id,
        )

        await interaction.response.edit_message(
            content=(
                f"✅ {category.name} に設定しました。"
            ),
            embed=None,
            view=None,
        )


class SingleCategorySelectView(
    discord.ui.View
):

    def __init__(
        self,
        setting_key: str,
    ):

        super().__init__(
            timeout=300
        )

        self.add_item(
            SingleCategorySelect(
                setting_key
            )
        )


# =========================================================
# ROLE SETTINGS
# =========================================================

class RoleSettingTypeView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=300
        )

    @discord.ui.button(
        label="仮メンバー",
        emoji="🔰",
    )
    async def temp(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            embed=chrono_embed(
                "仮メンバーロール",
                "ロールを選択してください。",
                interaction.guild,
            ),
            view=SingleRoleSelectView(
                "temp_role_id"
            ),
        )

    @discord.ui.button(
        label="本メンバー",
        emoji="✨",
    )
    async def full(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            embed=chrono_embed(
                "本メンバーロール",
                "ロールを選択してください。",
                interaction.guild,
            ),
            view=SingleRoleSelectView(
                "full_role_id"
            ),
        )


class SingleRoleSelect(
    discord.ui.RoleSelect
):

    def __init__(
        self,
        setting_key: str,
    ):

        self.setting_key = setting_key

        super().__init__(
            placeholder="ロールを選択",
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        role = self.values[0]

        db.set_setting(
            interaction.guild.id,
            self.setting_key,
            role.id,
        )

        await interaction.response.edit_message(
            content=(
                f"✅ {role.mention} に設定しました。"
            ),
            embed=None,
            view=None,
        )


class SingleRoleSelectView(
    discord.ui.View
):

    def __init__(
        self,
        setting_key: str,
    ):

        super().__init__(
            timeout=300
        )

        self.add_item(
            SingleRoleSelect(
                setting_key
            )
        )


# =========================================================
# EVENT CREATE
# =========================================================

class EventCreateModal(
    discord.ui.Modal,
    title="イベント作成",
):

    event_title = discord.ui.TextInput(
        label="イベント名",
        max_length=100,
    )

    description = discord.ui.TextInput(
        label="イベント内容",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        settings = db.settings(
            interaction.guild.id
        )

        channel = interaction.guild.get_channel(
            settings["event_channel_id"]
        ) if settings[
            "event_channel_id"
        ] else interaction.channel

        if not channel:

            await interaction.response.send_message(
                "イベントチャンネルが設定されていません。",
                ephemeral=True,
            )

            return

        embed = chrono_embed(
            self.event_title.value,
            self.description.value,
            interaction.guild,
        )

        embed.add_field(
            name="参加者｜0名",
            value="まだ参加者はいません。",
            inline=False,
        )

        message = await channel.send(
            embed=embed,
            view=EventView(),
        )

        db.add_event(
            message.id,
            interaction.guild.id,
            channel.id,
            self.event_title.value,
            self.description.value,
        )

        await interaction.response.send_message(
            "🎉 イベントを作成しました。",
            ephemeral=True,
        )


# =========================================================
# 👋 MEMBER JOIN
# =========================================================

@bot.event
async def on_member_join(
    member: discord.Member,
):

    if member.bot:
        return

    db.ensure_member(
        member.guild.id,
        member.id,
        (
            member.joined_at.isoformat()
            if member.joined_at
            else now_iso()
        ),
    )

    settings = db.settings(
        member.guild.id
    )

    # 仮メンバー付与
    if settings["temp_role_id"]:

        role = member.guild.get_role(
            settings["temp_role_id"]
        )

        if role:

            try:
                await member.add_roles(
                    role,
                    reason="クロノ 新人自動付与",
                )
            except Exception:
                pass

    channel = member.guild.get_channel(
        settings["welcome_channel_id"]
    ) if settings[
        "welcome_channel_id"
    ] else None

    if channel:

        try:

            await channel.send(
                embed=chrono_embed(
                    f"{member.display_name}さん、ようこそ",
                    (
                        f"{member.mention}\n\n"
                        f"ようこそ **{SERVER_NAME}** へ ✨\n\n"
                        "案内人の **クロノ** です。\n\n"
                        "案内パネルから「はじめてガイド」を開いて、"
                        "順番に進めてみてください。"
                    ),
                    member.guild,
                )
            )

        except Exception:
            pass

    await send_admin_log(
        member.guild,
        "メンバー加入",
        f"{member.mention} が参加しました。",
    )


# =========================================================
# 👋 MEMBER REMOVE
# =========================================================

@bot.event
async def on_member_remove(
    member: discord.Member,
):

    await delete_old_vc_profile(
        member.guild,
        member.id,
    )

    await send_admin_log(
        member.guild,
        "メンバー退出",
        (
            f"**{member}** がサーバーから退出しました。\n"
            f"ID：{member.id}"
        ),
    )


# =========================================================
# 💬 MESSAGE
# =========================================================

@bot.event
async def on_message(
    message: discord.Message,
):

    if (
        not message.guild
        or message.author.bot
    ):
        return

    db.ensure_member(
        message.guild.id,
        message.author.id,
    )

    db.member_set(
        message.guild.id,
        message.author.id,
        "last_active_at",
        now_iso(),
    )

    # 最初のテキスト交流
    data = db.member(
        message.guild.id,
        message.author.id,
    )

    if not data["greeting_done"]:

        db.member_set(
            message.guild.id,
            message.author.id,
            "greeting_done",
            1,
        )

    await bot.process_commands(
        message
    )


# =========================================================
# 🎙️ VOICE STATE
# =========================================================

@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):

    if member.bot:
        return

    db.ensure_member(
        member.guild.id,
        member.id,
    )

    db.member_set(
        member.guild.id,
        member.id,
        "last_active_at",
        now_iso(),
    )

    # 一度VC参加したら研修記録
    if after.channel:

        db.member_set(
            member.guild.id,
            member.id,
            "vc_done",
            1,
        )

    # -----------------------------
    # VCプロフィール表示
    # -----------------------------

    old_task = vc_profile_tasks.get(
        member.id
    )

    if old_task and not old_task.done():
        old_task.cancel()

    task = asyncio.create_task(
        post_vc_profile(
            member
        )
    )

    vc_profile_tasks[
        member.id
    ] = task

    # -----------------------------
    # 自動作成VC削除
    # -----------------------------

    if before.channel:

        room_data = db.room(
            before.channel.id
        )

        if room_data:

            async def remove_if_empty(
                channel_id: int,
            ):

                await asyncio.sleep(
                    ROOM_EMPTY_DELETE_DELAY
                )

                channel = member.guild.get_channel(
                    channel_id
                )

                if not isinstance(
                    channel,
                    discord.VoiceChannel,
                ):
                    return

                human_members = [
                    m
                    for m in channel.members
                    if not m.bot
                ]

                if not human_members:

                    try:

                        await channel.delete(
                            reason="クロノ 空室自動削除",
                        )

                    except Exception:
                        pass

                    db.delete_room(
                        channel.id
                    )

            asyncio.create_task(
                remove_if_empty(
                    before.channel.id
                )
            )

    # -----------------------------
    # VCログ
    # -----------------------------

    if before.channel != after.channel:

        if before.channel and after.channel:

            text = (
                f"{member.mention}\n"
                f"🔁 {before.channel.name}"
                f" → {after.channel.name}"
            )

        elif after.channel:

            text = (
                f"{member.mention}\n"
                f"🎙️ {after.channel.name} に参加"
            )

        else:

            text = (
                f"{member.mention}\n"
                f"🚪 VCから退出"
            )

        await send_admin_log(
            member.guild,
            "VCログ",
            text,
        )


# =========================================================
# 📝 ADMIN LOG
# =========================================================

async def send_admin_log(
    guild: discord.Guild,
    title: str,
    description: str,
):

    settings = db.settings(
        guild.id
    )

    channel_id = settings[
        "admin_log_channel_id"
    ]

    if not channel_id:
        return

    channel = guild.get_channel(
        channel_id
    )

    if not channel:
        return

    try:
        await channel.send(
            embed=chrono_embed(
                title,
                description,
                guild,
            )
        )

    except Exception:
        pass


# =========================================================
# 💤 NEWBIE AUTO REMINDER
# =========================================================

@tasks.loop(hours=24)
async def newbie_reminder_loop():

    for guild in bot.guilds:

        for row in db.all_members(
            guild.id
        ):

            if row["review_status"] == "approved":
                continue

            member = guild.get_member(
                row["user_id"]
            )

            if (
                not member
                or member.bot
            ):
                continue

            complete = (
                row["rules_done"]
                and row["profile_done"]
                and row["greeting_done"]
                and row["vc_done"]
            )

            if complete:
                continue

            last_active = parse_iso(
                row["last_active_at"]
            )

            if not last_active:
                continue

            if (
                utcnow() - last_active
                < timedelta(
                    days=NEWBIE_REMINDER_DAYS
                )
            ):
                continue

            last_reminder = parse_iso(
                row["last_reminder_at"]
            )

            if (
                last_reminder
                and utcnow() - last_reminder
                < timedelta(
                    days=REMINDER_COOLDOWN_DAYS
                )
            ):
                continue

            try:

                await member.send(
                    embed=chrono_embed(
                        "お困りではありませんか？",
                        (
                            f"{SERVER_NAME}での"
                            "はじめてガイドがまだ途中のようです。\n\n"
                            "分からないことがあれば、"
                            "案内パネルからクロノを呼んでください ✨"
                        ),
                        guild,
                    )
                )

                db.member_set(
                    guild.id,
                    member.id,
                    "last_reminder_at",
                    now_iso(),
                )

            except Exception:
                pass


@newbie_reminder_loop.before_loop
async def before_reminder():

    await bot.wait_until_ready()


# =========================================================
# 📌 INSTALL
# =========================================================

@bot.tree.command(
    name="chrono_install",
    description="クロノの案内パネルと管理パネルを設置します",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def chrono_install(
    interaction: discord.Interaction,
):

    if not interaction.guild:
        return

    guide_embed = chrono_embed(
        f"{SERVER_NAME}へようこそ",
        (
            f"私は **{SERVER_NAME}** の案内人、"
            f"**{BOT_NAME}** です。\n\n"
            "サーバーで困ったことがあれば、"
            "下のボタンからいつでも呼んでください。"
        ),
        interaction.guild,
    )

    await interaction.channel.send(
        embed=guide_embed,
        view=MainGuideView(),
    )

    admin_embed = chrono_embed(
        "クロノ管理パネル",
        (
            "こちらは管理者専用です。\n\n"
            "新人管理・サーバー状況・設定・"
            "イベント管理などをここから行えます。"
        ),
        interaction.guild,
    )

    await interaction.channel.send(
        embed=admin_embed,
        view=AdminPanelView(),
    )

    await interaction.response.send_message(
        "✅ クロノを設置しました。",
        ephemeral=True,
    )


# =========================================================
# 🔎 REVIEW
# =========================================================

@bot.tree.command(
    name="chrono_review",
    description="新人の審査結果を設定します",
)
@app_commands.describe(
    member="審査するメンバー",
    result="審査結果",
)
@app_commands.choices(
    result=[
        app_commands.Choice(
            name="承認",
            value="approved",
        ),
        app_commands.Choice(
            name="保留",
            value="pending",
        ),
        app_commands.Choice(
            name="再確認",
            value="rejected",
        ),
    ]
)
@app_commands.checks.has_permissions(
    manage_roles=True
)
async def chrono_review(
    interaction: discord.Interaction,
    member: discord.Member,
    result: app_commands.Choice[str],
):

    if not interaction.guild:
        return

    guild = interaction.guild

    db.member_set(
        guild.id,
        member.id,
        "review_status",
        result.value,
    )

    settings = db.settings(
        guild.id
    )

    if result.value == "approved":

        temp_role = guild.get_role(
            settings["temp_role_id"]
        ) if settings[
            "temp_role_id"
        ] else None

        full_role = guild.get_role(
            settings["full_role_id"]
        ) if settings[
            "full_role_id"
        ] else None

        try:

            if temp_role:
                await member.remove_roles(
                    temp_role
                )

            if full_role:
                await member.add_roles(
                    full_role
                )

        except discord.Forbidden:

            await interaction.response.send_message(
                (
                    "審査結果は保存しましたが、"
                    "ロールを変更できませんでした。\n"
                    "クロノのロール位置を確認してください。"
                ),
                ephemeral=True,
            )

            return

        db.member_set(
            guild.id,
            member.id,
            "promoted_at",
            now_iso(),
        )

        channel = guild.get_channel(
            settings["promotion_channel_id"]
        ) if settings[
            "promotion_channel_id"
        ] else None

        if channel:

            try:

                await channel.send(
                    embed=chrono_embed(
                        "本メンバー昇格",
                        (
                            f"🎉 {member.mention}\n\n"
                            f"**{SERVER_NAME} 本メンバーへようこそ！**"
                        ),
                        guild,
                    )
                )

            except Exception:
                pass

        try:

            await member.send(
                embed=chrono_embed(
                    "本メンバー承認",
                    (
                        f"{SERVER_NAME}の"
                        "本メンバーとして承認されました ✨"
                    ),
                    guild,
                )
            )

        except Exception:
            pass

    await send_admin_log(
        guild,
        "新人審査",
        (
            f"{member.mention}\n"
            f"結果：**{result.name}**\n"
            f"担当：{interaction.user.mention}"
        ),
    )

    await interaction.response.send_message(
        (
            f"✅ {member.mention} の審査結果を"
            f"「{result.name}」にしました。"
        ),
        ephemeral=True,
    )


# =========================================================
# 🏷️ SELF ROLE ADD
# =========================================================

@bot.tree.command(
    name="chrono_role_add",
    description="ユーザーが取得できるロールを追加します",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def chrono_role_add(
    interaction: discord.Interaction,
    role: discord.Role,
    表示名: str,
    絵文字: Optional[str] = None,
):

    db.role_add(
        interaction.guild.id,
        role.id,
        表示名,
        絵文字,
    )

    await interaction.response.send_message(
        (
            f"✅ {role.mention} を"
            "セルフロールに追加しました。"
        ),
        ephemeral=True,
    )


# =========================================================
# ROOM INVITE
# =========================================================

@bot.tree.command(
    name="chrono_room_invite",
    description="自分の個室にメンバーを招待します",
)
async def chrono_room_invite(
    interaction: discord.Interaction,
    member: discord.Member,
):

    if not interaction.guild:
        return

    room = None

    for row in db.owner_rooms(
        interaction.guild.id,
        interaction.user.id,
    ):

        if row["room_type"] != "private":
            continue

        channel = interaction.guild.get_channel(
            row["channel_id"]
        )

        if channel:
            room = channel
            break

    if not room:

        await interaction.response.send_message(
            "あなたの個室がありません。",
            ephemeral=True,
        )

        return

    await room.set_permissions(
        member,
        view_channel=True,
        connect=True,
        speak=True,
    )

    await interaction.response.send_message(
        f"✅ {member.mention} を招待しました。",
        ephemeral=True,
    )


# =========================================================
# ⚠️ WARN
# =========================================================

@bot.tree.command(
    name="chrono_warn",
    description="メンバーに警告を記録します",
)
@app_commands.checks.has_permissions(
    moderate_members=True
)
async def chrono_warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
):

    db.add_warning(
        interaction.guild.id,
        member.id,
        interaction.user.id,
        reason,
    )

    count = db.warning_count(
        interaction.guild.id,
        member.id,
    )

    await send_admin_log(
        interaction.guild,
        "警告",
        (
            f"対象：{member.mention}\n"
            f"担当：{interaction.user.mention}\n"
            f"理由：{reason}\n"
            f"警告回数：{count}回"
        ),
    )

    try:

        await member.send(
            embed=chrono_embed(
                "運営からのお知らせ",
                (
                    f"警告が記録されました。\n\n"
                    f"理由：{reason}\n\n"
                    f"現在の警告回数：{count}回"
                ),
                interaction.guild,
            )
        )

    except Exception:
        pass

    await interaction.response.send_message(
        (
            f"⚠️ {member.mention} に警告を記録しました。\n"
            f"現在 {count}回"
        ),
        ephemeral=True,
    )


# =========================================================
# TIMEOUT
# =========================================================

@bot.tree.command(
    name="chrono_timeout",
    description="メンバーをタイムアウトします",
)
@app_commands.checks.has_permissions(
    moderate_members=True
)
async def chrono_timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: app_commands.Range[int, 1, 40320],
    reason: str,
):

    until = utcnow() + timedelta(
        minutes=minutes
    )

    try:

        await member.timeout(
            until,
            reason=reason,
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "タイムアウトできませんでした。",
            ephemeral=True,
        )

        return

    await send_admin_log(
        interaction.guild,
        "タイムアウト",
        (
            f"{member.mention}\n"
            f"{minutes}分\n"
            f"理由：{reason}\n"
            f"担当：{interaction.user.mention}"
        ),
    )

    await interaction.response.send_message(
        f"✅ {member.mention} をタイムアウトしました。",
        ephemeral=True,
    )


# =========================================================
# KICK
# =========================================================

@bot.tree.command(
    name="chrono_kick",
    description="メンバーをKickします",
)
@app_commands.checks.has_permissions(
    kick_members=True
)
async def chrono_kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
):

    try:

        await member.kick(
            reason=reason
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "Kickできませんでした。",
            ephemeral=True,
        )

        return

    await send_admin_log(
        interaction.guild,
        "Kick",
        (
            f"{member} をKickしました。\n"
            f"理由：{reason}\n"
            f"担当：{interaction.user.mention}"
        ),
    )

    await interaction.response.send_message(
        "✅ Kickしました。",
        ephemeral=True,
    )


# =========================================================
# BAN
# =========================================================

@bot.tree.command(
    name="chrono_ban",
    description="メンバーをBANします",
)
@app_commands.checks.has_permissions(
    ban_members=True
)
async def chrono_ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
):

    try:

        await member.ban(
            reason=reason,
            delete_message_seconds=0,
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "BANできませんでした。",
            ephemeral=True,
        )

        return

    await send_admin_log(
        interaction.guild,
        "BAN",
        (
            f"{member} をBANしました。\n"
            f"理由：{reason}\n"
            f"担当：{interaction.user.mention}"
        ),
    )

    await interaction.response.send_message(
        "✅ BANしました。",
        ephemeral=True,
    )


# =========================================================
# ERROR
# =========================================================

@bot.tree.error
async def tree_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):

    if isinstance(
        error,
        app_commands.MissingPermissions,
    ):

        message = (
            "この操作をする権限がありません。"
        )

    else:

        log.error(
            "Slash command error: %s",
            error,
        )

        message = (
            "処理中にエラーが発生しました。\n"
            "Botの権限や設定をご確認ください。"
        )

    if interaction.response.is_done():

        await interaction.followup.send(
            message,
            ephemeral=True,
        )

    else:

        await interaction.response.send_message(
            message,
            ephemeral=True,
        )


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():

    log.info(
        "Logged in as %s (%s)",
        bot.user,
        bot.user.id if bot.user else "?",
    )

    try:

        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{SERVER_NAME}を案内中 ✨",
            )
        )

    except Exception:
        pass


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    if not TOKEN:

        raise RuntimeError(
            "DISCORD_TOKEN が設定されていません。"
        )

    bot.run(TOKEN)
