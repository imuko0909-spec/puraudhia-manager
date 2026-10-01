from __future__ import annotations

import os
import sqlite3
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands


# =========================================================
# ✨ Lumière Guide Bot
# 𝐋𝐮𝐦𝐢𝐞𝐫𝐞 専属案内人Bot
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DATABASE_PATH", "lumiere_guide.db")

BOT_NAME = "Lumière Guide"
GUIDE_NAME = "ルミエール"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("lumiere-guide")


# =========================================================
# 🗃️ データベース
# =========================================================

class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,

                rules_channel_id INTEGER,
                profile_channel_id INTEGER,
                vc_channel_id INTEGER,
                welcome_channel_id INTEGER,
                ticket_category_id INTEGER,

                guide_text TEXT,
                rules_text TEXT,
                profile_text TEXT,
                vc_text TEXT
            );

            CREATE TABLE IF NOT EXISTS tickets (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

        self.conn.commit()

    def ensure_guild(self, guild_id: int):
        self.conn.execute(
            """
            INSERT OR IGNORE INTO guild_settings(guild_id)
            VALUES(?)
            """,
            (guild_id,)
        )
        self.conn.commit()

    def get_settings(self, guild_id: int):
        self.ensure_guild(guild_id)

        return self.conn.execute(
            """
            SELECT *
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,)
        ).fetchone()

    def update_setting(self, guild_id: int, key: str, value):
        allowed = {
            "rules_channel_id",
            "profile_channel_id",
            "vc_channel_id",
            "welcome_channel_id",
            "ticket_category_id",
            "guide_text",
            "rules_text",
            "profile_text",
            "vc_text",
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
            (value, guild_id)
        )

        self.conn.commit()

    def add_ticket(self, guild_id: int, channel_id: int, user_id: int):
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
                datetime.now(timezone.utc).isoformat()
            )
        )
        self.conn.commit()

    def delete_ticket(self, channel_id: int):
        self.conn.execute(
            """
            DELETE FROM tickets
            WHERE channel_id = ?
            """,
            (channel_id,)
        )
        self.conn.commit()

    def get_ticket(self, channel_id: int):
        return self.conn.execute(
            """
            SELECT *
            FROM tickets
            WHERE channel_id = ?
            """,
            (channel_id,)
        ).fetchone()


db = Database(DB_PATH)


# =========================================================
# 🤖 Bot設定
# =========================================================

intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================================================
# 🎨 共通デザイン
# =========================================================

def guide_embed(
    title: str,
    description: str,
    guild: discord.Guild | None = None
) -> discord.Embed:

    embed = discord.Embed(
        title=f"✦ {title}",
        description=description,
        color=discord.Color.from_rgb(245, 220, 160)
    )

    embed.set_author(
        name=f"{GUIDE_NAME}｜{BOT_NAME}"
    )

    if guild and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.set_footer(
        text="𝐋𝐮𝐦𝐢𝐞𝐫𝐞 ─ あなたの居場所を照らす案内人"
    )

    return embed


def default_main_text():
    return (
        "ようこそ、**𝐋𝐮𝐦𝐢𝐞𝐫𝐞**へ。\n\n"
        "私はこの場所の案内人、**ルミエール**です ✨\n\n"
        "分からないことや困ったことがあれば、"
        "下のボタンからいつでも私を呼んでください。\n\n"
        "あなたがこの場所で素敵な時間を過ごせるよう、"
        "お手伝いします。"
    )


def default_rules_text():
    return (
        "📖 **サーバールールについて**\n\n"
        "サーバーを利用する前に、"
        "ルール・利用規約を必ず確認してください。\n\n"
        "お互いが安心して過ごせる場所にするため、"
        "思いやりのある利用をお願いします。"
    )


def default_profile_text():
    return (
        "🪞 **プロフィールについて**\n\n"
        "まずはプロフィールを作成して、"
        "あなたのことをみんなに教えてください。\n\n"
        "趣味や好きなこと、活動時間などを書いておくと、"
        "話しかけてもらいやすくなります。"
    )


def default_vc_text():
    return (
        "🎙️ **VCについて**\n\n"
        "気になるVCがあれば、"
        "遠慮せず参加してみてください。\n\n"
        "最初は聞き専からでも大丈夫です。\n"
        "無理のないペースで交流を楽しんでください。"
    )


# =========================================================
# 🔘 メイン案内パネル
# =========================================================

class GuidePanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # -----------------------------------------------------
    # ルール
    # -----------------------------------------------------

    @discord.ui.button(
        label="ルール",
        emoji="📖",
        style=discord.ButtonStyle.secondary,
        custom_id="lumiere:rules"
    )
    async def rules(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not interaction.guild:
            return

        settings = db.get_settings(interaction.guild.id)

        text = settings["rules_text"] or default_rules_text()

        embed = guide_embed(
            "ルールのご案内",
            text,
            interaction.guild
        )

        channel_id = settings["rules_channel_id"]

        if channel_id:
            channel = interaction.guild.get_channel(channel_id)

            if channel:
                embed.add_field(
                    name="ルールはこちら",
                    value=channel.mention,
                    inline=False
                )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    # -----------------------------------------------------
    # プロフィール
    # -----------------------------------------------------

    @discord.ui.button(
        label="プロフィール",
        emoji="🪞",
        style=discord.ButtonStyle.secondary,
        custom_id="lumiere:profile"
    )
    async def profile(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not interaction.guild:
            return

        settings = db.get_settings(interaction.guild.id)

        text = settings["profile_text"] or default_profile_text()

        embed = guide_embed(
            "プロフィールのご案内",
            text,
            interaction.guild
        )

        channel_id = settings["profile_channel_id"]

        if channel_id:
            channel = interaction.guild.get_channel(channel_id)

            if channel:
                embed.add_field(
                    name="プロフィールはこちら",
                    value=channel.mention,
                    inline=False
                )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    # -----------------------------------------------------
    # VC
    # -----------------------------------------------------

    @discord.ui.button(
        label="VC案内",
        emoji="🎙️",
        style=discord.ButtonStyle.secondary,
        custom_id="lumiere:vc"
    )
    async def vc(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not interaction.guild:
            return

        settings = db.get_settings(interaction.guild.id)

        text = settings["vc_text"] or default_vc_text()

        embed = guide_embed(
            "VCのご案内",
            text,
            interaction.guild
        )

        channel_id = settings["vc_channel_id"]

        if channel_id:
            channel = interaction.guild.get_channel(channel_id)

            if channel:
                embed.add_field(
                    name="VCはこちら",
                    value=channel.mention,
                    inline=False
                )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    # -----------------------------------------------------
    # FAQ
    # -----------------------------------------------------

    @discord.ui.button(
        label="よくある質問",
        emoji="💭",
        style=discord.ButtonStyle.primary,
        custom_id="lumiere:faq"
    )
    async def faq(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        embed = guide_embed(
            "よくある質問",
            (
                "**Q. 何から始めればいい？**\n"
                "→ まずはルール確認とプロフィール作成がおすすめです。\n\n"

                "**Q. VCにいきなり入っても大丈夫？**\n"
                "→ 基本的には大丈夫です。"
                "気になる場合はテキストで一言声をかけてもOKです。\n\n"

                "**Q. 困ったことが起きました。**\n"
                "→ 「管理者に相談」ボタンから相談できます。\n\n"

                "**Q. Botの使い方が分かりません。**\n"
                "→ この案内パネルのボタンを押せば、"
                "必要な場所までルミエールが案内します。"
            ),
            interaction.guild
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    # -----------------------------------------------------
    # 管理者相談
    # -----------------------------------------------------

    @discord.ui.button(
        label="管理者に相談",
        emoji="🔔",
        style=discord.ButtonStyle.danger,
        custom_id="lumiere:ticket"
    )
    async def ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not interaction.guild:
            return

        guild = interaction.guild
        settings = db.get_settings(guild.id)

        category = None

        if settings["ticket_category_id"]:
            category = guild.get_channel(
                settings["ticket_category_id"]
            )

        # すでに本人のチケットがあるか確認
        for channel in guild.text_channels:
            ticket = db.get_ticket(channel.id)

            if ticket and ticket["user_id"] == interaction.user.id:
                await interaction.response.send_message(
                    f"すでに相談チャンネルがあります。\n{channel.mention}",
                    ephemeral=True
                )
                return

        bot_member = guild.me

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                read_message_history=True
            )
        }

        # 管理者に閲覧権限
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )

        safe_name = interaction.user.name.lower()
        safe_name = "".join(
            c for c in safe_name
            if c.isalnum() or c in "-_"
        )

        if not safe_name:
            safe_name = str(interaction.user.id)

        try:
            channel = await guild.create_text_channel(
                name=f"相談-{safe_name}"[:100],
                category=category
                if isinstance(category, discord.CategoryChannel)
                else None,
                overwrites=overwrites,
                reason="Lumière Guide 管理者相談"
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "チャンネルを作成する権限がありません。\n"
                "Botに「チャンネルの管理」権限を付けてください。",
                ephemeral=True
            )
            return

        db.add_ticket(
            guild.id,
            channel.id,
            interaction.user.id
        )

        embed = guide_embed(
            "管理者への相談",
            (
                f"{interaction.user.mention} さん、"
                "こちらで相談内容をお聞かせください。\n\n"
                "管理者が確認でき次第、対応します。\n\n"
                "相談が終了したら下の **「相談を終了」** "
                "ボタンを押してください。"
            ),
            guild
        )

        await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=CloseTicketView()
        )

        await interaction.response.send_message(
            f"相談チャンネルを作成しました ✨\n{channel.mention}",
            ephemeral=True
        )


# =========================================================
# 🔒 チケット終了
# =========================================================

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="相談を終了",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="lumiere:ticket_close"
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not interaction.guild or not interaction.channel:
            return

        ticket = db.get_ticket(interaction.channel.id)

        if not ticket:
            await interaction.response.send_message(
                "このチャンネルは相談チャンネルとして登録されていません。",
                ephemeral=True
            )
            return

        owner_id = ticket["user_id"]

        can_close = (
            interaction.user.id == owner_id
            or interaction.user.guild_permissions.manage_channels
            or interaction.user.guild_permissions.administrator
        )

        if not can_close:
            await interaction.response.send_message(
                "この相談を終了できるのは本人または管理者です。",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "相談を終了します。ご利用ありがとうございました ✨"
        )

        db.delete_ticket(interaction.channel.id)

        try:
            await interaction.channel.delete(
                reason="Lumière Guide 相談終了"
            )
        except discord.HTTPException:
            pass


# =========================================================
# 👋 新規メンバー
# =========================================================

@bot.event
async def on_member_join(member: discord.Member):
    settings = db.get_settings(member.guild.id)

    channel_id = settings["welcome_channel_id"]

    if not channel_id:
        return

    channel = member.guild.get_channel(channel_id)

    if not isinstance(channel, discord.TextChannel):
        return

    embed = guide_embed(
        f"{member.display_name}さん、ようこそ",
        (
            f"{member.mention}\n\n"
            "ようこそ **𝐋𝐮𝐦𝐢𝐞𝐫𝐞** へ ✨\n\n"
            f"私は案内人の **{GUIDE_NAME}** です。\n\n"
            "まずはサーバーのルールやプロフィールを確認して、"
            "ゆっくり過ごしてみてください。\n\n"
            "困ったことがあれば、案内パネルから"
            "いつでも私を呼んでくださいね。"
        ),
        member.guild
    )

    try:
        await channel.send(
            content=member.mention,
            embed=embed
        )
    except discord.Forbidden:
        pass


# =========================================================
# ⚙️ セットアップ
# =========================================================

@bot.tree.command(
    name="lumiere_setup",
    description="Lumière Guideの基本設定をします"
)
@app_commands.describe(
    rules_channel="ルールが置かれているチャンネル",
    profile_channel="プロフィール用チャンネル",
    welcome_channel="新規加入時の案内チャンネル",
    ticket_category="相談チャンネルを作るカテゴリー"
)
@app_commands.checks.has_permissions(administrator=True)
async def lumiere_setup(
    interaction: discord.Interaction,
    rules_channel: discord.TextChannel | None = None,
    profile_channel: discord.TextChannel | None = None,
    welcome_channel: discord.TextChannel | None = None,
    ticket_category: discord.CategoryChannel | None = None
):
    if not interaction.guild:
        return

    guild_id = interaction.guild.id

    if rules_channel:
        db.update_setting(
            guild_id,
            "rules_channel_id",
            rules_channel.id
        )

    if profile_channel:
        db.update_setting(
            guild_id,
            "profile_channel_id",
            profile_channel.id
        )

    if welcome_channel:
        db.update_setting(
            guild_id,
            "welcome_channel_id",
            welcome_channel.id
        )

    if ticket_category:
        db.update_setting(
            guild_id,
            "ticket_category_id",
            ticket_category.id
        )

    embed = guide_embed(
        "セットアップ完了",
        (
            "Lumière Guideの設定を保存しました ✨\n\n"
            "続いて\n"
            "`/lumiere_panel`\n"
            "で案内パネルを設置できます。"
        ),
        interaction.guild
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# 🎙️ VC案内先設定
# =========================================================

@bot.tree.command(
    name="lumiere_vc",
    description="VC案内先を設定します"
)
@app_commands.checks.has_permissions(administrator=True)
async def lumiere_vc(
    interaction: discord.Interaction,
    voice_channel: discord.VoiceChannel
):
    if not interaction.guild:
        return

    db.update_setting(
        interaction.guild.id,
        "vc_channel_id",
        voice_channel.id
    )

    await interaction.response.send_message(
        f"🎙️ VC案内先を {voice_channel.mention} に設定しました。",
        ephemeral=True
    )


# =========================================================
# 🪄 案内文章変更
# =========================================================

@bot.tree.command(
    name="lumiere_text",
    description="Lumière Guideの案内文章を変更します"
)
@app_commands.describe(
    type="変更する案内",
    text="表示する文章"
)
@app_commands.choices(
    type=[
        app_commands.Choice(
            name="メイン案内",
            value="guide"
        ),
        app_commands.Choice(
            name="ルール",
            value="rules"
        ),
        app_commands.Choice(
            name="プロフィール",
            value="profile"
        ),
        app_commands.Choice(
            name="VC",
            value="vc"
        ),
    ]
)
@app_commands.checks.has_permissions(administrator=True)
async def lumiere_text(
    interaction: discord.Interaction,
    type: app_commands.Choice[str],
    text: str
):
    if not interaction.guild:
        return

    mapping = {
        "guide": "guide_text",
        "rules": "rules_text",
        "profile": "profile_text",
        "vc": "vc_text"
    }

    db.update_setting(
        interaction.guild.id,
        mapping[type.value],
        text
    )

    await interaction.response.send_message(
        "✨ 案内文章を変更しました。",
        ephemeral=True
    )


# =========================================================
# ✨ 案内パネル設置
# =========================================================

@bot.tree.command(
    name="lumiere_panel",
    description="Lumière Guideの案内パネルを設置します"
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def lumiere_panel(
    interaction: discord.Interaction
):
    if not interaction.guild:
        return

    settings = db.get_settings(
        interaction.guild.id
    )

    text = (
        settings["guide_text"]
        or default_main_text()
    )

    embed = guide_embed(
        "𝐋𝐮𝐦𝐢𝐞𝐫𝐞へようこそ",
        text,
        interaction.guild
    )

    embed.add_field(
        name="✦ ご案内",
        value=(
            "📖 ルール\n"
            "🪞 プロフィール\n"
            "🎙️ VC案内\n"
            "💭 よくある質問\n"
            "🔔 管理者への相談"
        ),
        inline=False
    )

    await interaction.response.send_message(
        "案内パネルを設置しました ✨",
        ephemeral=True
    )

    if interaction.channel:
        await interaction.channel.send(
            embed=embed,
            view=GuidePanel()
        )


# =========================================================
# 🔍 現在の設定確認
# =========================================================

@bot.tree.command(
    name="lumiere_settings",
    description="現在のLumière Guide設定を確認します"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def lumiere_settings(
    interaction: discord.Interaction
):
    if not interaction.guild:
        return

    settings = db.get_settings(
        interaction.guild.id
    )

    def channel_text(channel_id):
        if not channel_id:
            return "未設定"

        channel = interaction.guild.get_channel(
            channel_id
        )

        return channel.mention if channel else "削除済み"

    embed = guide_embed(
        "現在の設定",
        (
            f"📖 ルール\n"
            f"{channel_text(settings['rules_channel_id'])}\n\n"

            f"🪞 プロフィール\n"
            f"{channel_text(settings['profile_channel_id'])}\n\n"

            f"🎙️ VC\n"
            f"{channel_text(settings['vc_channel_id'])}\n\n"

            f"👋 ウェルカム\n"
            f"{channel_text(settings['welcome_channel_id'])}\n\n"

            f"🔔 相談カテゴリー\n"
            f"{channel_text(settings['ticket_category_id'])}"
        ),
        interaction.guild
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# ❗ コマンドエラー
# =========================================================

@lumiere_setup.error
@lumiere_text.error
@lumiere_panel.error
@lumiere_settings.error
@lumiere_vc.error
async def command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(
        error,
        app_commands.MissingPermissions
    ):
        message = "このコマンドは管理者専用です。"

    else:
        log.exception(
            "Slash command error",
            exc_info=error
        )
        message = (
            "処理中にエラーが発生しました。\n"
            "Botの権限や設定を確認してください。"
        )

    if interaction.response.is_done():
        await interaction.followup.send(
            message,
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            message,
            ephemeral=True
        )


# =========================================================
# 🚀 起動
# =========================================================

@bot.event
async def on_ready():
    bot.add_view(GuidePanel())
    bot.add_view(CloseTicketView())

    try:
        synced = await bot.tree.sync()
        log.info(
            "Synced %s commands.",
            len(synced)
        )
    except Exception:
        log.exception(
            "Command sync failed"
        )

    log.info(
        "Logged in as %s (%s)",
        bot.user,
        bot.user.id if bot.user else "?"
    )


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN が設定されていません。"
        )

    bot.run(TOKEN)
