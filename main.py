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

# 管理ログ
ADMIN_LOG_CHANNEL_ID = 1542720008499765248


# =========================================================
# ⚙️ アクティブ判定設定
# =========================================================

# 最終VC浮上から30日で降格
DEMOTION_DAYS = 30

# 仮メンバー → 本メンバー復帰に必要なVC時間
RETURN_REQUIRED_SECONDS = 3 * 60 * 60  # 3時間

# 降格チェック間隔
CHECK_INTERVAL_MINUTES = 30

# データ保存先
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
    return sqlite3.connect(DB_PATH)


def init_db():

    with db_connect() as con:

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

        con.commit()


def utcnow():
    return datetime.now(timezone.utc)


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
        return datetime.fromisoformat(value)

    except Exception:
        return None


# =========================================================
# 💾 DB操作
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

    return str_to_datetime(row[0])


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
        datetime_to_str(utcnow())
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
# 🤖 Bot
# =========================================================

intents = discord.Intents.default()

intents.guilds = True
intents.members = True
intents.voice_states = True


class CandyActiveBot(commands.Bot):

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
# 📢 管理ログ
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

        # 復帰した瞬間を
        # 新しい最終VC浮上日時として保存
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

        # 仮メンバー付与
        if temp_role not in member.roles:

            await member.add_roles(
                temp_role,
                reason=(
                    f"{DEMOTION_DAYS}日間"
                    "VC浮上なし"
                ),
            )

        # 本メンバー削除
        if full_role in member.roles:

            await member.remove_roles(
                full_role,
                reason=(
                    f"{DEMOTION_DAYS}日間"
                    "VC浮上なし"
                ),
            )

        # 復帰時間は0から
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

        # 本メンバー・仮メンバーのみ記録
        if (
            full_role in member.roles
            or temp_role in member.roles
        ):

            set_last_vc(
                guild.id,
                member.id,
                now,
            )

        # 仮メンバーなら3時間計測開始
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

        # 仮メンバーのみ
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

            if (
                total
                >= RETURN_REQUIRED_SECONDS
            ):

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

@tasks.loop(minutes=1)
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

        # VCにいない
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

        # 3時間達成
        if (
            total
            >= RETURN_REQUIRED_SECONDS
        ):

            # 現在のセッション分を
            # 一度保存してから復帰
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

        # 本メンバーのみチェック
        if full_role not in member.roles:
            continue

        # 現在VCにいるなら更新
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

        # =================================================
        # 初回登録
        #
        # Bot導入直後に既存メンバーが
        # 一斉降格しないよう
        # 初回は現在日時から30日スタート
        # =================================================

        if last_vc is None:

            set_last_vc(
                guild.id,
                member.id,
                now,
            )

            continue

        # =================================================
        # 💤 30日以上VCなし
        # =================================================

        if last_vc <= cutoff:

            await demote_member(
                member
            )

            # Discord API連打防止
            await asyncio.sleep(1)


@active_check_loop.before_loop
async def before_active_check_loop():

    await bot.wait_until_ready()


# =========================================================
# 🔍 /active_status
# 自分または指定メンバーの状態確認
# =========================================================

@bot.tree.command(
    name="active_status",
    description="VCアクティブ状況を確認します",
    guild=discord.Object(id=GUILD_ID),
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


    # =====================================================
    # 最終VC表示
    # =====================================================

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

        # VCにいる場合は現在セッション分も表示
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
# 管理者専用
# =========================================================

@bot.tree.command(
    name="active_reset",
    description="復帰用VC時間を0に戻します",
    guild=discord.Object(id=GUILD_ID),
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

    # 現在VC中なら
    # 今この瞬間から再計測
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
            "復帰用VC時間を **0分** にリセットしました。"
        ),
        ephemeral=True,
    )


# =========================================================
# 🛠️ /active_set_last
#
# 管理者が最終VC日時を
# 「今」に変更
# =========================================================

@bot.tree.command(
    name="active_set_last",
    description="最終VC浮上日時を現在時刻に更新します",
    guild=discord.Object(id=GUILD_ID),
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
            "最終VC浮上日時を現在時刻に更新しました。"
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
