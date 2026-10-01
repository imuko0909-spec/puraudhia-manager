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
# ✨ 𝐋𝐮𝐦𝐢𝐞𝐫𝐞 専属案内人Bot
# クロノ - Chrono
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DATABASE_PATH", "chrono.db")

BOT_NAME = "クロノ"
SERVER_NAME = "𝐋𝐮𝐦𝐢𝐞𝐫𝐞"

# 自動フォロー
INACTIVE_FOLLOW_DAYS = 3
FOLLOW_COOLDOWN_DAYS = 3

# 個室
OWNER_JOIN_TIMEOUT = 120
EMPTY_ROOM_DELETE_DELAY = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("chrono")


# =========================================================
# ⏰ 時刻
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
# 🗃️ DB
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

                guide_channel_id INTEGER,
                rules_channel_id INTEGER,
                profile_channel_id INTEGER,
                welcome_channel_id INTEGER,
                vc_recruit_channel_id INTEGER,
                event_channel_id INTEGER,
                promotion_channel_id INTEGER,

                ticket_category_id INTEGER,
                free_room_category_id INTEGER,
                private_room_category_id INTEGER,

                temp_role_id INTEGER,
                full_role_id INTEGER,

                guide_text TEXT,
                rules_text TEXT,
                profile_text TEXT,
                vc_text TEXT
            );

            CREATE TABLE IF NOT EXISTS members(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,

                joined_at TEXT,
                last_active_at TEXT,

                rules_done INTEGER DEFAULT 0,
                profile_done INTEGER DEFAULT 0,
                onboarding_done INTEGER DEFAULT 0,

                review_status TEXT DEFAULT 'pending',

                promoted_at TEXT,
                last_reminder_at TEXT,

                PRIMARY KEY(guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS reaction_roles(
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

            CREATE TABLE IF NOT EXISTS events(
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL,
                closed INTEGER DEFAULT 0
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
            "guide_channel_id",
            "rules_channel_id",
            "profile_channel_id",
            "welcome_channel_id",
            "vc_recruit_channel_id",
            "event_channel_id",
            "promotion_channel_id",

            "ticket_category_id",
            "free_room_category_id",
            "private_room_category_id",

            "temp_role_id",
            "full_role_id",

            "guide_text",
            "rules_text",
            "profile_text",
            "vc_text",
        }

        if key not in allowed:
            raise ValueError("Invalid setting")

        self.ensure_guild(guild_id)

        self.conn.execute(
            f"""
            UPDATE guild_settings
            SET {key} = ?
            WHERE guild_id = ?
            """,
            (value, guild_id),
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

        self.ensure_member(guild_id, user_id)

        return self.conn.execute(
            """
            SELECT *
            FROM members
            WHERE guild_id = ?
            AND user_id = ?
            """,
            (guild_id, user_id),
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
            "onboarding_done",
            "review_status",
            "promoted_at",
            "last_reminder_at",
        }

        if key not in allowed:
            raise ValueError("Invalid member field")

        self.ensure_member(guild_id, user_id)

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

    def all_members(self, guild_id: int):

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
    # ROLE PANEL
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
            INSERT OR REPLACE INTO reaction_roles(
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
            DELETE FROM reaction_roles
            WHERE guild_id = ?
            AND role_id = ?
            """,
            (guild_id, role_id),
        )

        self.conn.commit()

    # -----------------------------------------------------

    def roles(self, guild_id: int):

        return self.conn.execute(
            """
            SELECT *
            FROM reaction_roles
            WHERE guild_id = ?
            ORDER BY label
            """,
            (guild_id,),
        ).fetchall()

    # =====================================================
    # TICKET
    # =====================================================

    def ticket_add(
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

    def ticket(self, channel_id: int):

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

    def ticket_delete(self, channel_id: int):

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

    def room_add(
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

    def room(self, channel_id: int):

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

    def room_delete(self, channel_id: int):

        self.conn.execute(
            """
            DELETE FROM voice_rooms
            WHERE channel_id = ?
            """,
            (channel_id,),
        )

        self.conn.commit()

    # =====================================================
    # EVENT
    # =====================================================

    def event_add(
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

    def event(self, message_id: int):

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

    def event_members(self, message_id: int):

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

    def event_close(self, message_id: int):

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
# 🎨 Embed
# =========================================================

def chrono_embed(
    title: str,
    description: str,
    guild: Optional[discord.Guild] = None,
):

    embed = discord.Embed(
        title=f"✦ {title}",
        description=description,
        color=discord.Color.from_rgb(
            235,
            208,
            140,
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
# 📝 DEFAULT TEXT
# =========================================================

def guide_text():

    return (
        f"ようこそ、**{SERVER_NAME}**へ。\n\n"
        f"私はこの場所の案内人、**{BOT_NAME}**です。\n\n"
        "初めての方も、ずっとここにいる方も、"
        "分からないことがあればいつでも呼んでください。\n\n"
        "下のボタンから、必要なご案内を選べます。"
    )


def rules_text():

    return (
        "まずはサーバーの利用規約・ルールをご確認ください。\n\n"
        "みんなが安心して過ごせる場所にするため、"
        "ルールを守ってご利用ください。"
    )


def profile_text():

    return (
        "プロフィールを作成すると、"
        "他のメンバーから話しかけてもらいやすくなります。\n\n"
        "記入後は **「プロフィール提出完了」** を押してください。"
    )


def vc_text():

    return (
        "VCは無理なく、自分のペースで参加してください。\n\n"
        "誰かと話したい時は **VC募集** も使えます。"
    )


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
        self.add_view(TicketCloseView())
        self.add_view(EventView())

        inactive_follow_loop.start()

        try:
            await self.tree.sync()
        except Exception:
            log.exception("Command sync error")


bot = ChronoBot()


# =========================================================
# 🔘 MAIN GUIDE VIEW
# =========================================================

class MainGuideView(discord.ui.View):

    def __init__(self):

        super().__init__(
            timeout=None
        )

    # -----------------------------------------------------
    # ルール
    # -----------------------------------------------------

    @discord.ui.button(
        label="ルール",
        emoji="📖",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:rules",
    )
    async def rules_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        guild = interaction.guild

        if not guild:
            return

        settings = db.settings(guild.id)

        text = (
            settings["rules_text"]
            or rules_text()
        )

        embed = chrono_embed(
            "ルールのご案内",
            text,
            guild,
        )

        channel_id = settings["rules_channel_id"]

        if channel_id:

            channel = guild.get_channel(
                channel_id
            )

            if channel:

                embed.add_field(
                    name="確認はこちら",
                    value=channel.mention,
                    inline=False,
                )

        embed.add_field(
            name="確認後",
            value=(
                "下の「ルール確認完了」ボタンを押してください。"
            ),
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed,
            view=RulesDoneView(),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # PROFILE
    # -----------------------------------------------------

    @discord.ui.button(
        label="プロフィール",
        emoji="🪞",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:profile",
    )
    async def profile_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        guild = interaction.guild

        if not guild:
            return

        settings = db.settings(guild.id)

        text = (
            settings["profile_text"]
            or profile_text()
        )

        embed = chrono_embed(
            "プロフィールのご案内",
            text,
            guild,
        )

        channel_id = settings[
            "profile_channel_id"
        ]

        if channel_id:

            channel = guild.get_channel(
                channel_id
            )

            if channel:

                embed.add_field(
                    name="プロフィールはこちら",
                    value=channel.mention,
                    inline=False,
                )

        await interaction.response.send_message(
            embed=embed,
            view=ProfileDoneView(),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    @discord.ui.button(
        label="進捗確認",
        emoji="✅",
        style=discord.ButtonStyle.primary,
        custom_id="chrono:status",
    )
    async def status_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        guild = interaction.guild

        if not guild:
            return

        data = db.member(
            guild.id,
            interaction.user.id,
        )

        rules_done = (
            "✅ 完了"
            if data["rules_done"]
            else "⬜ 未完了"
        )

        profile_done = (
            "✅ 完了"
            if data["profile_done"]
            else "⬜ 未完了"
        )

        review = {
            "pending": "⏳ 審査待ち",
            "approved": "✅ 承認済み",
            "rejected": "❌ 再確認",
        }.get(
            data["review_status"],
            "⏳ 審査待ち",
        )

        embed = chrono_embed(
            "あなたの進捗",
            (
                f"📖 ルール確認：{rules_done}\n\n"
                f"🪞 プロフィール：{profile_done}\n\n"
                f"🔎 仮メンバー審査：{review}"
            ),
            guild,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    # -----------------------------------------------------
    # VC
    # -----------------------------------------------------

    @discord.ui.button(
        label="VC募集",
        emoji="🎙️",
        style=discord.ButtonStyle.success,
        custom_id="chrono:vcrecruit",
    )
    async def vc_button(
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
        custom_id="chrono:roles",
    )
    async def roles_button(
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
            view=DynamicRoleView(
                interaction.guild,
                rows,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------
    # ROOM
    # -----------------------------------------------------

    @discord.ui.button(
        label="お部屋作成",
        emoji="🔑",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:rooms",
    )
    async def room_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_message(
            embed=chrono_embed(
                "お部屋作成",
                (
                    "作りたいお部屋を選んでください。\n\n"
                    "🔊 フリールーム\n"
                    "誰でも参加できるお部屋\n\n"
                    "🔐 個室\n"
                    "招待した人だけ参加できるお部屋"
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
        custom_id="chrono:events",
    )
    async def event_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_message(
            (
                "🎉 イベント募集がある場合は、"
                "イベントチャンネルをご確認ください。"
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
    )
    async def faq_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        embed = chrono_embed(
            "よくある質問",
            (
                "**Q. 最初に何をすればいい？**\n"
                "ルール確認 → プロフィール作成がおすすめです。\n\n"

                "**Q. VCに勝手に入っていい？**\n"
                "各部屋のルールに問題がなければ大丈夫です。\n\n"

                "**Q. 個室は作れる？**\n"
                "「お部屋作成」から作成できます。\n\n"

                "**Q. 管理者に相談したい**\n"
                "「管理者に相談」から専用チャンネルを作成できます。\n\n"

                "**Q. 自分がどこまで終わったか分からない**\n"
                "「進捗確認」を押してください。"
            ),
            interaction.guild,
        )

        await interaction.response.send_message(
            embed=embed,
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
    )
    async def ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        guild = interaction.guild

        if not guild:
            return

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
                    f"すでに相談チャンネルがあります。\n{old_channel.mention}",
                    ephemeral=True,
                )

                return

            db.ticket_delete(
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
                    read_message_history=True,
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

        name = re.sub(
            r"[^a-zA-Z0-9ぁ-んァ-ン一-龥_-]",
            "",
            interaction.user.display_name,
        )

        if not name:
            name = str(
                interaction.user.id
            )

        try:

            channel = await guild.create_text_channel(
                name=f"相談-{name}"[:100],
                category=(
                    category
                    if isinstance(
                        category,
                        discord.CategoryChannel,
                    )
                    else None
                ),
                overwrites=overwrites,
                reason="クロノ相談チケット",
            )

        except discord.Forbidden:

            await interaction.response.send_message(
                (
                    "チャンネル作成権限がありません。\n"
                    "Botに「チャンネルの管理」を付けてください。"
                ),
                ephemeral=True,
            )

            return

        db.ticket_add(
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
                    "相談が終わったら下のボタンから終了できます。"
                ),
                guild,
            ),
            view=TicketCloseView(),
        )

        await interaction.response.send_message(
            f"相談チャンネルを作成しました。\n{channel.mention}",
            ephemeral=True,
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

        await update_onboarding(
            interaction.guild,
            interaction.user,
        )

        await interaction.response.edit_message(
            content="✅ ルール確認を記録しました。",
            embed=None,
            view=None,
        )


# =========================================================
# 🪞 PROFILE DONE
# =========================================================

class ProfileDoneView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=300
        )

    @discord.ui.button(
        label="プロフィール提出完了",
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
            "profile_done",
            1,
        )

        await update_onboarding(
            interaction.guild,
            interaction.user,
        )

        await interaction.response.edit_message(
            content=(
                "✅ プロフィール提出を記録しました。\n"
                "管理者の確認をお待ちください。"
            ),
            embed=None,
            view=None,
        )


# =========================================================
# 🏷️ ROLE
# =========================================================

class DynamicRoleSelect(
    discord.ui.Select
):

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

            emoji = None

            if row["emoji"]:

                try:
                    emoji = row["emoji"]
                except Exception:
                    pass

            options.append(
                discord.SelectOption(
                    label=row["label"][:100],
                    value=str(role.id),
                    emoji=emoji,
                )
            )

        super().__init__(
            placeholder="ロールを選択",
            min_values=1,
            max_values=max(
                1,
                len(options),
            ),
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        if not interaction.guild:
            return

        member = interaction.user

        changed = []

        for value in self.values:

            role = interaction.guild.get_role(
                int(value)
            )

            if not role:
                continue

            if role in member.roles:

                await member.remove_roles(
                    role,
                    reason="クロノ ロール解除",
                )

                changed.append(
                    f"➖ {role.name}"
                )

            else:

                await member.add_roles(
                    role,
                    reason="クロノ ロール取得",
                )

                changed.append(
                    f"➕ {role.name}"
                )

        await interaction.response.send_message(
            "\n".join(changed)
            or "変更はありませんでした。",
            ephemeral=True,
        )


class DynamicRoleView(
    discord.ui.View
):

    def __init__(
        self,
        guild,
        rows,
    ):

        super().__init__(
            timeout=300
        )

        if rows:
            self.add_item(
                DynamicRoleSelect(
                    guild,
                    rows,
                )
            )


# =========================================================
# 🎙️ VC RECRUIT
# =========================================================

class VCRecruitModal(
    discord.ui.Modal,
    title="VC募集"
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

        guild = interaction.guild

        if not guild:
            return

        settings = db.settings(
            guild.id
        )

        channel = guild.get_channel(
            settings["vc_recruit_channel_id"]
        ) if settings[
            "vc_recruit_channel_id"
        ] else interaction.channel

        if not isinstance(
            channel,
            discord.TextChannel,
        ):

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
            guild,
        )

        await channel.send(
            embed=embed
        )

        await interaction.response.send_message(
            "🎙️ VC募集を投稿しました。",
            ephemeral=True,
        )


# =========================================================
# 🔑 ROOM CREATE
# =========================================================

class RoomCreateView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=300
        )

    @discord.ui.button(
        label="フリールーム",
        emoji="🔊",
        style=discord.ButtonStyle.success,
    )
    async def free_room(
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
    async def private_room(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_modal(
            PrivateRoomModal()
        )


# =========================================================
# 🔐 PRIVATE ROOM MODAL
# =========================================================

class PrivateRoomModal(
    discord.ui.Modal,
    title="個室作成"
):

    room_name = discord.ui.TextInput(
        label="部屋名",
        placeholder="お話部屋",
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


# =========================================================
# CREATE VOICE ROOM
# =========================================================

async def create_voice_room(
    interaction: discord.Interaction,
    private: bool,
    custom_name: Optional[str] = None,
):

    guild = interaction.guild

    if not guild:
        return

    rows = db.owner_rooms(
        guild.id,
        interaction.user.id,
    )

    for row in rows:

        old = guild.get_channel(
            row["channel_id"]
        )

        if old:

            await interaction.response.send_message(
                (
                    "すでにあなたのお部屋があります。\n"
                    f"{old.mention}"
                ),
                ephemeral=True,
            )

            return

        db.room_delete(
            row["channel_id"]
        )

    settings = db.settings(
        guild.id
    )

    if private:

        category_id = settings[
            "private_room_category_id"
        ]

    else:

        category_id = settings[
            "free_room_category_id"
        ]

    category = guild.get_channel(
        category_id
    ) if category_id else None

    owner = interaction.user

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
                    move_members=True,
                    manage_channels=True,
                ),

            guild.me:
                discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    manage_channels=True,
                    move_members=True,
                ),
        }

    name = (
        custom_name
        if custom_name
        else (
            f"{owner.display_name}の個室"
            if private
            else f"{owner.display_name}のフリールーム"
        )
    )

    try:

        channel = await guild.create_voice_channel(
            name=(
                f"🔐 {name}"
                if private
                else f"🔊 {name}"
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
            user_limit=0,
            reason="クロノ お部屋作成",
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "Botに「チャンネルの管理」権限が必要です。",
            ephemeral=True,
        )

        return

    db.room_add(
        guild.id,
        channel.id,
        owner.id,
        (
            "private"
            if private
            else "free"
        ),
    )

    await interaction.response.send_message(
        (
            f"お部屋を作成しました ✨\n{channel.mention}\n\n"
            "2分以内に入室してください。"
        ),
        ephemeral=True,
    )

    async def join_timeout():

        await asyncio.sleep(
            OWNER_JOIN_TIMEOUT
        )

        room = guild.get_channel(
            channel.id
        )

        if not isinstance(
            room,
            discord.VoiceChannel,
        ):
            return

        owner_inside = any(
            m.id == owner.id
            for m in room.members
        )

        if not owner_inside:

            try:
                await room.delete(
                    reason="部屋主未入室"
                )
            except Exception:
                pass

            db.room_delete(
                room.id
            )

    asyncio.create_task(
        join_timeout()
    )


# =========================================================
# 🔒 TICKET CLOSE
# =========================================================

class TicketCloseView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="相談を終了",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="chrono:ticketclose",
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        if not interaction.guild:
            return

        ticket = db.ticket(
            interaction.channel.id
        )

        if not ticket:

            await interaction.response.send_message(
                "相談チャンネルとして登録されていません。",
                ephemeral=True,
            )

            return

        allowed = (
            ticket["user_id"]
            == interaction.user.id
            or interaction.user.guild_permissions.manage_channels
            or interaction.user.guild_permissions.administrator
        )

        if not allowed:

            await interaction.response.send_message(
                "この相談を終了する権限がありません。",
                ephemeral=True,
            )

            return

        await interaction.response.send_message(
            "相談を終了します。"
        )

        db.ticket_delete(
            interaction.channel.id
        )

        await asyncio.sleep(2)

        try:
            await interaction.channel.delete()
        except Exception:
            pass


# =========================================================
# 🎉 EVENT VIEW
# =========================================================

class EventView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="参加する",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="chrono:eventjoin",
    )
    async def join_event(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        event = db.event(
            interaction.message.id
        )

        if not event:

            await interaction.response.send_message(
                "イベント情報が見つかりません。",
                ephemeral=True,
            )

            return

        if event["closed"]:

            await interaction.response.send_message(
                "このイベントの受付は終了しています。",
                ephemeral=True,
            )

            return

        db.event_join(
            interaction.message.id,
            interaction.user.id,
        )

        await refresh_event_message(
            interaction.message
        )

        await interaction.response.send_message(
            "🎉 イベント参加を受け付けました。",
            ephemeral=True,
        )

    @discord.ui.button(
        label="キャンセル",
        emoji="➖",
        style=discord.ButtonStyle.secondary,
        custom_id="chrono:eventleave",
    )
    async def leave_event(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        db.event_leave(
            interaction.message.id,
            interaction.user.id,
        )

        await refresh_event_message(
            interaction.message
        )

        await interaction.response.send_message(
            "イベント参加をキャンセルしました。",
            ephemeral=True,
        )


# =========================================================
# EVENT REFRESH
# =========================================================

async def refresh_event_message(
    message: discord.Message,
):

    event = db.event(
        message.id
    )

    if not event:
        return

    rows = db.event_members(
        message.id
    )

    mentions = []

    for row in rows[:30]:

        mentions.append(
            f"<@{row['user_id']}>"
        )

    participants = (
        "\n".join(mentions)
        if mentions
        else "まだ参加者はいません。"
    )

    embed = chrono_embed(
        event["title"],
        event["description"]
        or "イベント参加者を募集しています。",
        message.guild,
    )

    embed.add_field(
        name=f"参加者｜{len(rows)}名",
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
# 🧭 ONBOARDING CHECK
# =========================================================

async def update_onboarding(
    guild: discord.Guild,
    member: discord.Member,
):

    data = db.member(
        guild.id,
        member.id,
    )

    if (
        data["rules_done"]
        and data["profile_done"]
        and not data["onboarding_done"]
    ):

        db.member_set(
            guild.id,
            member.id,
            "onboarding_done",
            1,
        )

        try:

            await member.send(
                embed=chrono_embed(
                    "初期案内完了",
                    (
                        f"{SERVER_NAME}での"
                        "最初の準備が完了しました ✨\n\n"
                        "あとは管理者による確認をお待ちください。"
                    ),
                    guild,
                )
            )

        except discord.Forbidden:
            pass


# =========================================================
# 👋 JOIN
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
        member.joined_at.isoformat()
        if member.joined_at
        else now_iso(),
    )

    settings = db.settings(
        member.guild.id
    )

    # 仮メンバー付与
    role_id = settings[
        "temp_role_id"
    ]

    if role_id:

        role = member.guild.get_role(
            role_id
        )

        if role:

            try:
                await member.add_roles(
                    role,
                    reason="新人加入"
                )
            except discord.Forbidden:
                pass

    # ウェルカム
    channel_id = settings[
        "welcome_channel_id"
    ]

    channel = member.guild.get_channel(
        channel_id
    ) if channel_id else None

    if isinstance(
        channel,
        discord.TextChannel,
    ):

        embed = chrono_embed(
            f"{member.display_name}さん、ようこそ",
            (
                f"{member.mention}\n\n"
                f"ようこそ **{SERVER_NAME}** へ ✨\n\n"
                f"案内人の **{BOT_NAME}** です。\n\n"
                "まずは案内パネルから\n"
                "① ルール確認\n"
                "② プロフィール作成\n"
                "を進めてください。"
            ),
            member.guild,
        )

        try:
            await channel.send(
                embed=embed
            )
        except discord.Forbidden:
            pass


# =========================================================
# ACTIVITY
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

    db.member_set(
        message.guild.id,
        message.author.id,
        "last_active_at",
        now_iso(),
    )

    await bot.process_commands(
        message
    )


# =========================================================
# VOICE STATE
# =========================================================

@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):

    if member.bot:
        return

    db.member_set(
        member.guild.id,
        member.id,
        "last_active_at",
        now_iso(),
    )

    # 退出した部屋の削除判定
    if before.channel:

        room_data = db.room(
            before.channel.id
        )

        if room_data:

            async def delete_if_empty(
                channel_id: int,
            ):

                await asyncio.sleep(
                    EMPTY_ROOM_DELETE_DELAY
                )

                channel = member.guild.get_channel(
                    channel_id
                )

                if not isinstance(
                    channel,
                    discord.VoiceChannel,
                ):
                    return

                if len(
                    [
                        m for m in channel.members
                        if not m.bot
                    ]
                ) == 0:

                    try:

                        await channel.delete(
                            reason="空室自動削除"
                        )

                    except Exception:
                        pass

                    db.room_delete(
                        channel.id
                    )

            asyncio.create_task(
                delete_if_empty(
                    before.channel.id
                )
            )


# =========================================================
# 🕰️ INACTIVE FOLLOW
# =========================================================

@tasks.loop(
    hours=24
)
async def inactive_follow_loop():

    for guild in bot.guilds:

        rows = db.all_members(
            guild.id
        )

        for row in rows:

            member = guild.get_member(
                row["user_id"]
            )

            if (
                not member
                or member.bot
                or row["onboarding_done"]
            ):
                continue

            last_active = parse_iso(
                row["last_active_at"]
            )

            if not last_active:
                continue

            if utcnow() - last_active < timedelta(
                days=INACTIVE_FOLLOW_DAYS
            ):
                continue

            last_reminder = parse_iso(
                row["last_reminder_at"]
            )

            if (
                last_reminder
                and utcnow() - last_reminder
                < timedelta(
                    days=FOLLOW_COOLDOWN_DAYS
                )
            ):
                continue

            try:

                await member.send(
                    embed=chrono_embed(
                        "お困りではありませんか？",
                        (
                            f"{SERVER_NAME}での"
                            "初期案内がまだ途中のようです。\n\n"
                            "分からないことがあれば、"
                            "案内パネルからいつでもクロノを呼んでください ✨"
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

            except discord.Forbidden:
                pass


@inactive_follow_loop.before_loop
async def before_inactive():

    await bot.wait_until_ready()


# =========================================================
# ⚙️ SETUP
# =========================================================

@bot.tree.command(
    name="chrono_setup",
    description="クロノの基本設定をします",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def chrono_setup(
    interaction: discord.Interaction,
    rules_channel: Optional[
        discord.TextChannel
    ] = None,
    profile_channel: Optional[
        discord.TextChannel
    ] = None,
    welcome_channel: Optional[
        discord.TextChannel
    ] = None,
    vc_recruit_channel: Optional[
        discord.TextChannel
    ] = None,
    event_channel: Optional[
        discord.TextChannel
    ] = None,
    promotion_channel: Optional[
        discord.TextChannel
    ] = None,
    ticket_category: Optional[
        discord.CategoryChannel
    ] = None,
    free_room_category: Optional[
        discord.CategoryChannel
    ] = None,
    private_room_category: Optional[
        discord.CategoryChannel
    ] = None,
    temp_role: Optional[
        discord.Role
    ] = None,
    full_role: Optional[
        discord.Role
    ] = None,
):

    if not interaction.guild:
        return

    gid = interaction.guild.id

    mapping = {
        "rules_channel_id":
            rules_channel,
        "profile_channel_id":
            profile_channel,
        "welcome_channel_id":
            welcome_channel,
        "vc_recruit_channel_id":
            vc_recruit_channel,
        "event_channel_id":
            event_channel,
        "promotion_channel_id":
            promotion_channel,
        "ticket_category_id":
            ticket_category,
        "free_room_category_id":
            free_room_category,
        "private_room_category_id":
            private_room_category,
        "temp_role_id":
            temp_role,
        "full_role_id":
            full_role,
    }

    for key, obj in mapping.items():

        if obj:

            db.set_setting(
                gid,
                key,
                obj.id,
            )

    await interaction.response.send_message(
        embed=chrono_embed(
            "セットアップ完了",
            (
                "設定を保存しました ✨\n\n"
                "次に `/chrono_panel` で"
                "案内パネルを設置してください。"
            ),
            interaction.guild,
        ),
        ephemeral=True,
    )


# =========================================================
# PANEL
# =========================================================

@bot.tree.command(
    name="chrono_panel",
    description="クロノ案内パネルを設置します",
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def chrono_panel(
    interaction: discord.Interaction,
):

    if not interaction.guild:
        return

    embed = chrono_embed(
        f"{SERVER_NAME}へようこそ",
        guide_text(),
        interaction.guild,
    )

    embed.add_field(
        name="クロノにできること",
        value=(
            "📖 ルール案内\n"
            "🪞 プロフィール案内\n"
            "✅ 進捗確認\n"
            "🎙️ VC募集\n"
            "🏷️ ロール取得\n"
            "🔑 お部屋作成\n"
            "🎉 イベント案内\n"
            "💭 よくある質問\n"
            "🔔 管理者相談"
        ),
        inline=False,
    )

    await interaction.response.send_message(
        "案内パネルを設置しました。",
        ephemeral=True,
    )

    await interaction.channel.send(
        embed=embed,
        view=MainGuideView(),
    )


# =========================================================
# 🏷️ ROLE ADD
# =========================================================

@bot.tree.command(
    name="chrono_role_add",
    description="取得できるロールを追加します",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def chrono_role_add(
    interaction: discord.Interaction,
    role: discord.Role,
    label: str,
    emoji: Optional[str] = None,
):

    if not interaction.guild:
        return

    db.role_add(
        interaction.guild.id,
        role.id,
        label,
        emoji,
    )

    await interaction.response.send_message(
        f"✅ {role.mention} をロール取得に追加しました。",
        ephemeral=True,
    )


# =========================================================
# ROLE REMOVE
# =========================================================

@bot.tree.command(
    name="chrono_role_remove",
    description="取得ロールから削除します",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def chrono_role_remove(
    interaction: discord.Interaction,
    role: discord.Role,
):

    if not interaction.guild:
        return

    db.role_remove(
        interaction.guild.id,
        role.id,
    )

    await interaction.response.send_message(
        f"✅ {role.name} を削除しました。",
        ephemeral=True,
    )


# =========================================================
# 🔎 REVIEW
# =========================================================

@bot.tree.command(
    name="chrono_review",
    description="仮メンバー審査を行います",
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

        temp = guild.get_role(
            settings["temp_role_id"]
        ) if settings[
            "temp_role_id"
        ] else None

        full = guild.get_role(
            settings["full_role_id"]
        ) if settings[
            "full_role_id"
        ] else None

        try:

            if temp:
                await member.remove_roles(
                    temp,
                    reason="クロノ審査承認",
                )

            if full:
                await member.add_roles(
                    full,
                    reason="クロノ審査承認",
                )

        except discord.Forbidden:

            await interaction.response.send_message(
                (
                    "審査結果は保存しましたが、"
                    "ロール変更権限がありません。"
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

        # お祝い
        promotion_channel_id = settings[
            "promotion_channel_id"
        ]

        channel = guild.get_channel(
            promotion_channel_id
        ) if promotion_channel_id else None

        if isinstance(
            channel,
            discord.TextChannel,
        ):

            await channel.send(
                embed=chrono_embed(
                    "本メンバー昇格",
                    (
                        f"🎉 {member.mention}\n\n"
                        f"**{SERVER_NAME} 本メンバーへようこそ！**\n\n"
                        "これからも素敵な時間をお過ごしください ✨"
                    ),
                    guild,
                )
            )

        try:

            await member.send(
                embed=chrono_embed(
                    "審査完了",
                    (
                        f"**{SERVER_NAME}** の"
                        "本メンバーとして承認されました ✨\n\n"
                        "これからよろしくお願いします。"
                    ),
                    guild,
                )
            )

        except discord.Forbidden:
            pass

    await interaction.response.send_message(
        f"{member.mention} の審査結果を **{result.name}** にしました。",
        ephemeral=True,
    )


# =========================================================
# 👥 NEW MEMBER LIST
# =========================================================

@bot.tree.command(
    name="chrono_newbies",
    description="新人・審査待ち一覧を表示します",
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def chrono_newbies(
    interaction: discord.Interaction,
):

    if not interaction.guild:
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

        rule_icon = (
            "✅"
            if row["rules_done"]
            else "⬜"
        )

        profile_icon = (
            "✅"
            if row["profile_done"]
            else "⬜"
        )

        lines.append(
            (
                f"{member.mention}\n"
                f"└ ルール {rule_icon} / "
                f"プロフィール {profile_icon} / "
                f"審査 {row['review_status']}"
            )
        )

    text = (
        "\n\n".join(
            lines[:30]
        )
        if lines
        else "現在、審査待ちはいません。"
    )

    await interaction.response.send_message(
        embed=chrono_embed(
            "新人・審査待ち一覧",
            text,
            interaction.guild,
        ),
        ephemeral=True,
    )


# =========================================================
# 🎉 EVENT CREATE
# =========================================================

@bot.tree.command(
    name="chrono_event",
    description="イベント参加募集を作成します",
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def chrono_event(
    interaction: discord.Interaction,
    title: str,
    description: str,
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
    ] else interaction.channel

    if not isinstance(
        channel,
        discord.TextChannel,
    ):

        await interaction.response.send_message(
            "イベントチャンネルが設定されていません。",
            ephemeral=True,
        )

        return

    embed = chrono_embed(
        title,
        description,
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

    db.event_add(
        message.id,
        interaction.guild.id,
        channel.id,
        title,
        description,
    )

    await interaction.response.send_message(
        f"🎉 イベント募集を作成しました。\n{message.jump_url}",
        ephemeral=True,
    )


# =========================================================
# EVENT CLOSE
# =========================================================

@bot.tree.command(
    name="chrono_event_close",
    description="イベント受付を終了します",
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def chrono_event_close(
    interaction: discord.Interaction,
    message_id: str,
):

    if not interaction.guild:
        return

    try:
        mid = int(message_id)
    except ValueError:

        await interaction.response.send_message(
            "メッセージIDが正しくありません。",
            ephemeral=True,
        )
        return

    event = db.event(
        mid
    )

    if not event:

        await interaction.response.send_message(
            "イベントが見つかりません。",
            ephemeral=True,
        )
        return

    channel = interaction.guild.get_channel(
        event["channel_id"]
    )

    if not isinstance(
        channel,
        discord.TextChannel,
    ):
        return

    try:
        message = await channel.fetch_message(
            mid
        )
    except Exception:

        await interaction.response.send_message(
            "イベントメッセージが見つかりません。",
            ephemeral=True,
        )
        return

    db.event_close(
        mid
    )

    await refresh_event_message(
        message
    )

    await interaction.response.send_message(
        "イベント受付を終了しました。",
        ephemeral=True,
    )


# =========================================================
# 🔐 PRIVATE ROOM INVITE
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

    rows = db.owner_rooms(
        interaction.guild.id,
        interaction.user.id,
    )

    private_channel = None

    for row in rows:

        if row["room_type"] != "private":
            continue

        channel = interaction.guild.get_channel(
            row["channel_id"]
        )

        if isinstance(
            channel,
            discord.VoiceChannel,
        ):

            private_channel = channel
            break

    if not private_channel:

        await interaction.response.send_message(
            "あなたの個室がありません。",
            ephemeral=True,
        )
        return

    await private_channel.set_permissions(
        member,
        view_channel=True,
        connect=True,
        speak=True,
    )

    await interaction.response.send_message(
        f"{member.mention} を個室に招待しました。",
        ephemeral=True,
    )


# =========================================================
# ROOM KICK PERMISSION
# =========================================================

@bot.tree.command(
    name="chrono_room_remove",
    description="個室の招待を解除します",
)
async def chrono_room_remove(
    interaction: discord.Interaction,
    member: discord.Member,
):

    if not interaction.guild:
        return

    rows = db.owner_rooms(
        interaction.guild.id,
        interaction.user.id,
    )

    private_channel = None

    for row in rows:

        if row["room_type"] != "private":
            continue

        channel = interaction.guild.get_channel(
            row["channel_id"]
        )

        if isinstance(
            channel,
            discord.VoiceChannel,
        ):

            private_channel = channel
            break

    if not private_channel:

        await interaction.response.send_message(
            "あなたの個室がありません。",
            ephemeral=True,
        )
        return

    await private_channel.set_permissions(
        member,
        overwrite=None,
    )

    if (
        member.voice
        and member.voice.channel
        and member.voice.channel.id
        == private_channel.id
    ):

        try:
            await member.move_to(None)
        except discord.Forbidden:
            pass

    await interaction.response.send_message(
        f"{member.mention} の招待を解除しました。",
        ephemeral=True,
    )


# =========================================================
# CURRENT SETTINGS
# =========================================================

@bot.tree.command(
    name="chrono_settings",
    description="クロノの現在の設定を確認します",
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def chrono_settings(
    interaction: discord.Interaction,
):

    if not interaction.guild:
        return

    guild = interaction.guild
    settings = db.settings(
        guild.id
    )

    def channel_name(
        channel_id,
    ):

        if not channel_id:
            return "未設定"

        channel = guild.get_channel(
            channel_id
        )

        if not channel:
            return "削除済み"

        return channel.mention

    def role_name(
        role_id,
    ):

        if not role_id:
            return "未設定"

        role = guild.get_role(
            role_id
        )

        return (
            role.mention
            if role
            else "削除済み"
        )

    text = (
        f"📖 ルール：{channel_name(settings['rules_channel_id'])}\n"
        f"🪞 プロフィール：{channel_name(settings['profile_channel_id'])}\n"
        f"👋 ウェルカム：{channel_name(settings['welcome_channel_id'])}\n"
        f"🎙️ VC募集：{channel_name(settings['vc_recruit_channel_id'])}\n"
        f"🎉 イベント：{channel_name(settings['event_channel_id'])}\n"
        f"🌟 昇格通知：{channel_name(settings['promotion_channel_id'])}\n\n"

        f"🔔 相談カテゴリ：{channel_name(settings['ticket_category_id'])}\n"
        f"🔊 フリーカテゴリ：{channel_name(settings['free_room_category_id'])}\n"
        f"🔐 個室カテゴリ：{channel_name(settings['private_room_category_id'])}\n\n"

        f"🔰 仮メンバー：{role_name(settings['temp_role_id'])}\n"
        f"✨ 本メンバー：{role_name(settings['full_role_id'])}"
    )

    await interaction.response.send_message(
        embed=chrono_embed(
            "現在の設定",
            text,
            guild,
        ),
        ephemeral=True,
    )


# =========================================================
# ERRORS
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
            "このコマンドを使用する権限がありません。"
        )

    else:

        log.error(
            "Command error: %s",
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
