import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta
from collections import defaultdict
import json
import os
import re
from dotenv import load_dotenv

load_dotenv("/home/container/.env")

TOKEN = os.getenv("DISCORD_TOKEN")

WARNINGS_FILE = "warnings.json"
WELCOME_FILE = "welcome_settings.json"
LEAVE_FILE = "leave_settings.json"
LOGS_FILE = "logs_settings.json"

WARNING_MUTE_THRESHOLD = 3  # warnings needed before an auto-mute
WARNING_MUTE_HOURS = 48     # length of that auto-mute

# Commands considered "moderation" commands for the 🛡️ tag in the log embed.
MODERATION_COMMANDS = {
    "kick",
    "ban",
    "unban",
    "timeout",
    "untimeout",
    "warn",
    "clearwarnings",
    "clear",
    "giveroleall",
}

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class ModerationBot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents
        )
        self.startup_sync_done = False

    async def setup_hook(self):
        # Global sync makes commands available in every server the bot is
        # in, but Discord can take up to ~1 hour to propagate a global sync
        # everywhere. on_guild_join (below) covers new servers instantly.
        await self.tree.sync()
        print("Slash commands synced globally.")


bot = ModerationBot()


# -------------------------
# WARNINGS
# -------------------------

def load_warnings():
    if not os.path.exists(WARNINGS_FILE):
        return {}

    try:
        with open(WARNINGS_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_warnings(data):
    with open(WARNINGS_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


warnings = load_warnings()


# -------------------------
# WELCOME SETTINGS
# -------------------------

DEFAULT_WELCOME_MESSAGE = (
    "★*. WELCOME *.°\n"
    "*.* ─────⋆⋅☆⋅⋆───── *.*\n\n"
    "Welcome to {server}, {member}!\n\n"
    "• Remember to read {rules}\n"
    "• Check out my socials! {socials}\n\n"
    "Have fun!\n\n"
    "*.* ─────⋆⋅☆⋅⋆───── *.*"
)

DEFAULT_WELCOME_IMAGE = None  # gif/image URL shown at the bottom of the embed
DEFAULT_WELCOME_SOCIALS = None  # your socials link, shown wherever {socials} is used
DEFAULT_RULES_CHANNEL_ID = None  # channel shown wherever {rules} is used


def load_welcome_settings():
    if not os.path.exists(WELCOME_FILE):
        return {}

    try:
        with open(WELCOME_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_welcome_settings(data):
    with open(WELCOME_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


welcome_settings = load_welcome_settings()


# -------------------------
# LEAVE SETTINGS
# -------------------------

DEFAULT_LEAVE_MESSAGE = (
    "☆・GOODBYE・☆\n"
    "*.* ─────⋆⋅☆⋅⋆───── *.*\n\n"
    "**{name}** has left {server}.\n\n"
    "We hope to see you again!\n\n"
    "*.* ─────⋆⋅☆⋅⋆───── *.*"
)

DEFAULT_LEAVE_IMAGE = None  # gif/image URL shown at the bottom of the embed


def load_leave_settings():
    if not os.path.exists(LEAVE_FILE):
        return {}

    try:
        with open(LEAVE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_leave_settings(data):
    with open(LEAVE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


leave_settings = load_leave_settings()


def get_guild_leave_settings(guild_id: str):
    """Return this guild's leave config, creating defaults if missing."""
    if guild_id not in leave_settings:
        leave_settings[guild_id] = {
            "channel_id": None,
            "message": DEFAULT_LEAVE_MESSAGE,
            "image_url": DEFAULT_LEAVE_IMAGE,
            "enabled": True
        }
        save_leave_settings(leave_settings)

    return leave_settings[guild_id]


def build_leave_embed(member: discord.Member, settings: dict) -> discord.Embed:
    text = settings.get("message", DEFAULT_LEAVE_MESSAGE)

    text = (
        text.replace("{member}", member.mention)
            .replace("{mention}", member.mention)
            .replace("{name}", member.display_name)
            .replace("{server}", member.guild.name)
            .replace("{count}", str(member.guild.member_count))
    )

    embed = discord.Embed(
        description=text,
        color=discord.Color.dark_theme()
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    image_url = settings.get("image_url")
    if image_url:
        embed.set_image(url=image_url)

    embed.set_footer(
        text=f"{member.guild.member_count} members remaining."
    )
    embed.timestamp = discord.utils.utcnow()

    return embed


def get_guild_welcome_settings(guild_id: str):
    """Return this guild's welcome config, creating defaults if missing."""
    if guild_id not in welcome_settings:
        welcome_settings[guild_id] = {
            "channel_id": None,
            "message": DEFAULT_WELCOME_MESSAGE,
            "image_url": DEFAULT_WELCOME_IMAGE,
            "socials": DEFAULT_WELCOME_SOCIALS,
            "rules_channel_id": DEFAULT_RULES_CHANNEL_ID,
            "enabled": True
        }
        save_welcome_settings(welcome_settings)

    return welcome_settings[guild_id]


def build_welcome_embed(member: discord.Member, settings: dict) -> discord.Embed:
    text = settings.get("message", DEFAULT_WELCOME_MESSAGE)

    rules_channel_id = settings.get("rules_channel_id")
    rules_channel = (
        member.guild.get_channel(rules_channel_id)
        if rules_channel_id else None
    )
    rules_mention = rules_channel.mention if rules_channel else "the rules"

    text = (
        text.replace("{member}", member.mention)
            .replace("{mention}", member.mention)
            .replace("{name}", member.display_name)
            .replace("{server}", member.guild.name)
            .replace("{count}", str(member.guild.member_count))
            .replace("{socials}", settings.get("socials") or "N/A")
            .replace("{rules}", rules_mention)
    )

    embed = discord.Embed(
        description=text,
        color=discord.Color.dark_theme()
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    image_url = settings.get("image_url")
    if image_url:
        embed.set_image(url=image_url)

    embed.set_footer(
        text=f"You are our {member.guild.member_count}{_ordinal_suffix(member.guild.member_count)} member!"
    )
    embed.timestamp = discord.utils.utcnow()

    return embed


def _ordinal_suffix(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# -------------------------
# LOGS SETTINGS
# -------------------------

def load_logs_settings():
    if not os.path.exists(LOGS_FILE):
        return {}

    try:
        with open(LOGS_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_logs_settings(data):
    with open(LOGS_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


logs_settings = load_logs_settings()


def get_guild_logs_settings(guild_id: str):
    """Return this guild's command-log config, creating defaults if missing."""
    if guild_id not in logs_settings:
        logs_settings[guild_id] = {
            "channel_id": None,
            "enabled": True
        }
        save_logs_settings(logs_settings)

    return logs_settings[guild_id]


def _format_option_value(value) -> str:
    """Turn a resolved slash-command option into readable text for the log embed."""
    if isinstance(value, (discord.Member, discord.User)):
        return f"{value.mention} (`{value.id}`)"
    if isinstance(value, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.Thread)):
        return value.mention
    if isinstance(value, discord.Role):
        return value.mention
    if value is None:
        return "—"
    return str(value)


def build_command_log_embed(
    interaction: discord.Interaction,
    command: app_commands.Command
) -> discord.Embed:
    is_mod_command = command.qualified_name in MODERATION_COMMANDS

    embed = discord.Embed(
        title=f"{'🛡️ Moderation' if is_mod_command else '📖'} Command Used: /{command.qualified_name}",
        color=discord.Color.red() if is_mod_command else discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="User",
        value=f"{interaction.user.mention} (`{interaction.user.id}`)",
        inline=True
    )

    channel = interaction.channel
    embed.add_field(
        name="Channel",
        value=channel.mention if channel else "Unknown",
        inline=True
    )

    # interaction.namespace holds the resolved values the user actually typed/picked.
    options = {}
    try:
        options = dict(interaction.namespace)
    except Exception:
        pass

    if options:
        formatted = "\n".join(
            f"**{name}:** {_format_option_value(value)}"
            for name, value in options.items()
        )
        embed.add_field(
            name="Options",
            value=formatted[:1024],
            inline=False
        )

    embed.set_thumbnail(url=interaction.user.display_avatar.url)

    return embed


# -------------------------
# JOIN ROLE SETTINGS
# -------------------------

JOINROLE_FILE = "joinrole_settings.json"


def load_joinrole_settings():
    if not os.path.exists(JOINROLE_FILE):
        return {}

    try:
        with open(JOINROLE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_joinrole_settings(data):
    with open(JOINROLE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


joinrole_settings = load_joinrole_settings()


def get_guild_joinrole_settings(guild_id: str):
    """Return this guild's join-role config, creating defaults if missing."""
    if guild_id not in joinrole_settings:
        joinrole_settings[guild_id] = {
            "role_id": None,
            "enabled": True
        }
        save_joinrole_settings(joinrole_settings)

    return joinrole_settings[guild_id]


# -------------------------
# AUTOMOD SETTINGS
# -------------------------
#
# Deliberately not configurable beyond on/off - one guild-wide switch
# (/automod toggle) instead of a pile of tuning commands. The rule is
# fixed: spamming links or mentions within a short window gets you
# deleted + timed out.

AUTOMOD_FILE = "automod_settings.json"

URL_REGEX = re.compile(r"https?://\S+", re.IGNORECASE)

AUTOMOD_WINDOW_SECONDS = 10            # rolling window both checks use
AUTOMOD_LINK_THRESHOLD = 4             # linked messages inside the window -> action
AUTOMOD_MENTION_THRESHOLD = 3          # mention-containing messages inside the window -> action
AUTOMOD_MENTION_INSTANT_THRESHOLD = 5  # mentions in a SINGLE message -> action immediately
AUTOMOD_TIMEOUT_HOURS = 48             # length of the timeout automod applies

# In-memory sliding-window trackers: {guild_id: {user_id: [Message, ...]}}
# Not persisted to disk on purpose - this is short-lived spam-window data,
# not something that needs to survive a restart.
link_spam_tracker = defaultdict(lambda: defaultdict(list))
mention_spam_tracker = defaultdict(lambda: defaultdict(list))


def load_automod_settings():
    if not os.path.exists(AUTOMOD_FILE):
        return {}

    try:
        with open(AUTOMOD_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_automod_settings(data):
    with open(AUTOMOD_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


automod_settings = load_automod_settings()


def get_guild_automod_settings(guild_id: str):
    """Return this guild's automod config ({"enabled": bool}), creating
    the default if missing."""
    if guild_id not in automod_settings:
        automod_settings[guild_id] = {"enabled": True}
        save_automod_settings(automod_settings)
    elif "enabled" not in automod_settings[guild_id]:
        # Migrating from the old per-category config format (link_spam /
        # file_spam / mention_spam dicts) - just carry forward whether
        # automod was on at all, since per-category tuning no longer exists.
        old_cfg = automod_settings[guild_id]
        was_enabled = any(
            old_cfg.get(key, {}).get("enabled", True)
            for key in ("link_spam", "file_spam", "mention_spam")
        )
        automod_settings[guild_id] = {"enabled": was_enabled}
        save_automod_settings(automod_settings)

    return automod_settings[guild_id]


async def send_automod_log(
    guild: discord.Guild,
    label: str,
    member: discord.Member,
    removed_count: int,
    action_taken: str
):
    """Reuses the existing /logschannel setup so automod hits and manual
    command usage both land in the same place."""
    settings = get_guild_logs_settings(str(guild.id))

    if not settings.get("enabled", True):
        return

    channel_id = settings.get("channel_id")
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        return

    embed = discord.Embed(
        title=f"🚨 Automod: {label}",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(
        name="User",
        value=f"{member.mention} (`{member.id}`)",
        inline=True
    )
    embed.add_field(
        name="Messages Removed",
        value=str(removed_count),
        inline=True
    )
    embed.add_field(
        name="Action Taken",
        value=action_taken,
        inline=False
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


async def enforce_automod_action(
    offending_messages: list,
    member,
    label: str,
    guild: discord.Guild
):
    """Deletes the given messages and times the member out for
    AUTOMOD_TIMEOUT_HOURS, then reports it to the logs channel."""

    for offending in offending_messages:
        try:
            await offending.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    action_taken = f"Deleted {len(offending_messages)} message(s)"

    if isinstance(member, discord.Member) and member.moderatable:
        try:
            await member.timeout(
                timedelta(hours=AUTOMOD_TIMEOUT_HOURS),
                reason=f"Automod: {label}"
            )
            action_taken += f" + timed out {AUTOMOD_TIMEOUT_HOURS}h"
        except discord.Forbidden:
            pass

    try:
        await member.send(
            f"⚠️ Your recent messages in **{guild.name}** were removed for "
            f"**{label}**, and you've been timed out for "
            f"**{AUTOMOD_TIMEOUT_HOURS} hours**."
        )
    except discord.Forbidden:
        pass

    await send_automod_log(guild, label, member, len(offending_messages), action_taken)


async def check_spam_window(
    tracker: dict,
    guild_id: int,
    message: discord.Message,
    label: str,
    threshold: int
) -> bool:
    """Adds this message to the user's rolling AUTOMOD_WINDOW_SECONDS
    window for this spam type, prunes anything outside the window, and -
    if the count crosses the given threshold - enforces the automod
    action. Returns True if action was taken."""

    now = discord.utils.utcnow()
    window = timedelta(seconds=AUTOMOD_WINDOW_SECONDS)

    bucket = tracker[guild_id][message.author.id]
    bucket.append(message)

    bucket = [m for m in bucket if now - m.created_at <= window]
    tracker[guild_id][message.author.id] = bucket

    if len(bucket) < threshold:
        return False

    offending_messages = bucket
    tracker[guild_id][message.author.id] = []

    await enforce_automod_action(offending_messages, message.author, label, message.guild)
    return True


async def check_mention_spam(message: discord.Message) -> bool:
    """A single message with a large pile of mentions is spam on its own,
    so this checks that first (instant trigger) before falling back to the
    same sliding-window pattern used for link spam."""

    total_mentions = len(message.mentions) + len(message.role_mentions)
    if message.mention_everyone:
        total_mentions += 1

    if total_mentions == 0:
        return False

    if total_mentions >= AUTOMOD_MENTION_INSTANT_THRESHOLD:
        await enforce_automod_action(
            [message],
            message.author,
            "Mention Spam",
            message.guild
        )
        return True

    return await check_spam_window(
        mention_spam_tracker,
        message.guild.id,
        message,
        "Mention Spam",
        AUTOMOD_MENTION_THRESHOLD
    )


# -------------------------
# BOT STARTUP
# -------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print("Moderation bot is online!")

    # A guild that got its commands via on_guild_join has its own frozen
    # command list from that moment - a later global sync alone won't
    # update it. Re-push into every guild we're already in on every
    # startup so newly added commands (like /automod) actually show up
    # without needing to wait on Discord's global propagation delay.
    if not bot.startup_sync_done:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)

        bot.startup_sync_done = True
        print(f"Re-synced commands instantly into {len(bot.guilds)} guild(s).")


@bot.event
async def on_guild_join(guild: discord.Guild):
    # Copies the already-registered global commands into this specific
    # guild's command cache so they show up immediately, instead of
    # waiting for Discord's global command propagation.
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"Joined new guild: {guild.name} ({guild.id}) — commands synced.")


# -------------------------
# COMMAND-USAGE LOGGING
# -------------------------

@bot.event
async def on_app_command_completion(
    interaction: discord.Interaction,
    command: app_commands.Command
):
    """Fires automatically after ANY slash command runs successfully.

    This logs every command (not just moderation ones) to the guild's
    configured logs channel, so nothing needs to be added to individual
    command functions.
    """
    if interaction.guild is None:
        return

    guild_id = str(interaction.guild.id)
    settings = get_guild_logs_settings(guild_id)

    if not settings.get("enabled", True):
        return

    channel_id = settings.get("channel_id")
    if not channel_id:
        return

    log_channel = interaction.guild.get_channel(channel_id)
    if log_channel is None:
        return

    # Don't log the /logschannel-related commands landing in an infinite
    # loop of noise, but do still log everything else, including /logs-settings.
    embed = build_command_log_embed(interaction, command)

    try:
        await log_channel.send(embed=embed)
    except discord.Forbidden:
        pass


# -------------------------
# AUTOMOD: MESSAGE SCANNING
# -------------------------

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    guild_id = str(message.guild.id)
    settings = get_guild_automod_settings(guild_id)

    if not settings.get("enabled", True):
        return

    if isinstance(message.author, discord.Member) and message.author.guild_permissions.administrator:
        return

    if message.content and URL_REGEX.search(message.content):
        handled = await check_spam_window(
            link_spam_tracker,
            message.guild.id,
            message,
            "Link Spam",
            AUTOMOD_LINK_THRESHOLD
        )
        if handled:
            return

    await check_mention_spam(message)


# -------------------------
# WELCOME EVENT
# -------------------------

@bot.event
async def on_member_join(member: discord.Member):
    # Auto-assign the configured join role, independent of welcome messages.
    guild_id = str(member.guild.id)
    joinrole_config = get_guild_joinrole_settings(guild_id)

    if joinrole_config.get("enabled", True):
        role_id = joinrole_config.get("role_id")
        role = member.guild.get_role(role_id) if role_id else None

        if role is not None:
            try:
                await member.add_roles(
                    role,
                    reason="Auto-assigned join role"
                )
            except discord.Forbidden:
                pass

    settings = get_guild_welcome_settings(guild_id)

    if not settings.get("enabled", True):
        return

    channel_id = settings.get("channel_id")
    if not channel_id:
        return

    channel = member.guild.get_channel(channel_id)
    if channel is None:
        return

    embed = build_welcome_embed(member, settings)

    try:
        await channel.send(
            content=f"Welcome, {member.mention}!",
            embed=embed
        )
    except discord.Forbidden:
        pass


# -------------------------
# LEAVE EVENT
# -------------------------

@bot.event
async def on_member_remove(member: discord.Member):
    guild_id = str(member.guild.id)
    settings = get_guild_leave_settings(guild_id)

    if not settings.get("enabled", True):
        return

    channel_id = settings.get("channel_id")
    if not channel_id:
        return

    channel = member.guild.get_channel(channel_id)
    if channel is None:
        return

    embed = build_leave_embed(member, settings)

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


# -------------------------
# WELCOME: INTERACTIVE PANEL
# -------------------------
#
# One command (/welcome) instead of nine. Everything - channel, rules
# channel, message, image, socials, on/off, reset, and a live preview -
# lives in a single panel, the same pattern as /embed.

class WelcomeMessageModal(discord.ui.Modal, title="Welcome Message"):
    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        max_length=4000
    )

    def __init__(self, view: "WelcomePanelView"):
        super().__init__()
        self.view = view
        self.message.default = view.settings.get("message", DEFAULT_WELCOME_MESSAGE)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.settings["message"] = self.message.value.replace("\\n", "\n")
        save_welcome_settings(welcome_settings)
        await interaction.response.edit_message(
            content=self.view.panel_content(),
            embed=self.view.build_preview(interaction.user),
            view=self.view
        )


class WelcomeImageModal(discord.ui.Modal, title="Welcome Image / GIF"):
    image_url = discord.ui.TextInput(
        label="Image/GIF URL (leave blank to remove)",
        required=False
    )

    def __init__(self, view: "WelcomePanelView"):
        super().__init__()
        self.view = view
        current = view.settings.get("image_url")
        if current:
            self.image_url.default = current

    async def on_submit(self, interaction: discord.Interaction):
        self.view.settings["image_url"] = self.image_url.value or None
        save_welcome_settings(welcome_settings)
        await interaction.response.edit_message(
            content=self.view.panel_content(),
            embed=self.view.build_preview(interaction.user),
            view=self.view
        )


class WelcomeSocialsModal(discord.ui.Modal, title="Socials Link"):
    socials = discord.ui.TextInput(
        label="Socials URL (leave blank to remove)",
        required=False
    )

    def __init__(self, view: "WelcomePanelView"):
        super().__init__()
        self.view = view
        current = view.settings.get("socials")
        if current:
            self.socials.default = current

    async def on_submit(self, interaction: discord.Interaction):
        self.view.settings["socials"] = self.socials.value or None
        save_welcome_settings(welcome_settings)
        await interaction.response.edit_message(
            content=self.view.panel_content(),
            embed=self.view.build_preview(interaction.user),
            view=self.view
        )


class WelcomePanelView(discord.ui.View):
    def __init__(self, guild: discord.Guild, settings: dict, author_id: int):
        super().__init__(timeout=600)
        self.guild = guild
        self.settings = settings
        self.author_id = author_id

        channel_id = settings.get("channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        self.channel_select.placeholder = (
            f"Welcome channel: #{channel.name}" if channel else "Welcome channel: not set"
        )

        rules_channel_id = settings.get("rules_channel_id")
        rules_channel = guild.get_channel(rules_channel_id) if rules_channel_id else None
        self.rules_select.placeholder = (
            f"Rules channel: #{rules_channel.name}" if rules_channel else "Rules channel: not set"
        )

        self._sync_toggle_button()

    def _sync_toggle_button(self):
        enabled = self.settings.get("enabled", True)
        self.toggle_button.label = "Disable" if enabled else "Enable"
        self.toggle_button.emoji = "🔕" if enabled else "🔔"
        self.toggle_button.style = (
            discord.ButtonStyle.secondary if enabled else discord.ButtonStyle.success
        )

    def panel_content(self) -> str:
        status = "Enabled ✅" if self.settings.get("enabled", True) else "Disabled ❌"
        return (
            f"**Welcome Panel** — Status: {status}\n"
            f"Pick channels and use the buttons below to edit everything else. "
            f"The preview below updates as you go."
        )

    def build_preview(self, member: discord.Member) -> discord.Embed:
        return build_welcome_embed(member, self.settings)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who opened this panel can use its controls.",
                ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Welcome channel",
        row=0
    )
    async def channel_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect
    ):
        picked = select.values[0]
        channel = picked.resolve() or await picked.fetch()

        self.settings["channel_id"] = channel.id
        save_welcome_settings(welcome_settings)
        select.placeholder = f"Welcome channel: #{channel.name}"

        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_preview(interaction.user),
            view=self
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Rules channel",
        row=1
    )
    async def rules_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect
    ):
        picked = select.values[0]
        channel = picked.resolve() or await picked.fetch()

        self.settings["rules_channel_id"] = channel.id
        save_welcome_settings(welcome_settings)
        select.placeholder = f"Rules channel: #{channel.name}"

        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_preview(interaction.user),
            view=self
        )

    @discord.ui.button(label="Edit Message", emoji="✏️", style=discord.ButtonStyle.primary, row=2)
    async def edit_message_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WelcomeMessageModal(self))

    @discord.ui.button(label="Set Image", emoji="🖼️", style=discord.ButtonStyle.secondary, row=2)
    async def set_image_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WelcomeImageModal(self))

    @discord.ui.button(label="Set Socials", emoji="🔗", style=discord.ButtonStyle.secondary, row=2)
    async def set_socials_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WelcomeSocialsModal(self))

    @discord.ui.button(label="Disable", emoji="🔕", style=discord.ButtonStyle.secondary, row=3)
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["enabled"] = not self.settings.get("enabled", True)
        save_welcome_settings(welcome_settings)
        self._sync_toggle_button()

        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_preview(interaction.user),
            view=self
        )

    @discord.ui.button(label="Reset to Default", emoji="♻️", style=discord.ButtonStyle.danger, row=3)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["message"] = DEFAULT_WELCOME_MESSAGE
        self.settings["image_url"] = DEFAULT_WELCOME_IMAGE
        self.settings["socials"] = DEFAULT_WELCOME_SOCIALS
        save_welcome_settings(welcome_settings)

        await interaction.response.edit_message(
            content=self.panel_content() + "\n♻️ Message, image, and socials reset to default.",
            embed=self.build_preview(interaction.user),
            view=self
        )

    @discord.ui.button(label="Send Test", emoji="📨", style=discord.ButtonStyle.success, row=4)
    async def send_test_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.channel.send(
                content=f"Welcome, {interaction.user.mention}!",
                embed=self.build_preview(interaction.user)
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to send messages in this channel.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "✅ Test welcome message sent to this channel.",
            ephemeral=True
        )

    @discord.ui.button(label="Close", emoji="✅", style=discord.ButtonStyle.secondary, row=4)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="✅ Welcome panel closed. Your settings are saved.",
            view=self
        )
        self.stop()


@bot.tree.command(
    name="welcome",
    description="Open an interactive panel to configure welcome messages."
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_command(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_welcome_settings(guild_id)

    view = WelcomePanelView(
        guild=interaction.guild,
        settings=settings,
        author_id=interaction.user.id
    )

    await interaction.response.send_message(
        content=view.panel_content(),
        embed=view.build_preview(interaction.user),
        view=view,
        ephemeral=True
    )



# -------------------------
# LEAVE: SET CHANNEL
# -------------------------

@bot.tree.command(
    name="leave-setchannel",
    description="Set the channel where leave messages are sent."
)
@app_commands.describe(
    channel="The channel to send leave messages in"
)
@app_commands.checks.has_permissions(administrator=True)
async def leave_setchannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_leave_settings(guild_id)

    settings["channel_id"] = channel.id
    save_leave_settings(leave_settings)

    await interaction.response.send_message(
        f"✅ Leave messages will now be sent in {channel.mention}.",
        ephemeral=True
    )


# -------------------------
# LEAVE: SET MESSAGE
# -------------------------

@bot.tree.command(
    name="leave-setmessage",
    description="Set the leave message text."
)
@app_commands.describe(
    message=(
        "Leave text. Placeholders: {name} {server} {count}. "
        "Use \\n for new lines."
    )
)
@app_commands.checks.has_permissions(administrator=True)
async def leave_setmessage(
    interaction: discord.Interaction,
    message: str
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_leave_settings(guild_id)

    settings["message"] = message.replace("\\n", "\n")
    save_leave_settings(leave_settings)

    await interaction.response.send_message(
        "✅ Leave message updated. Use `/leave-test` to preview it.",
        ephemeral=True
    )


# -------------------------
# LEAVE: SET IMAGE/GIF
# -------------------------

@bot.tree.command(
    name="leave-setimage",
    description="Set the image/gif shown at the bottom of the leave embed."
)
@app_commands.describe(
    url="Direct URL to an image or gif (leave blank to remove it)"
)
@app_commands.checks.has_permissions(administrator=True)
async def leave_setimage(
    interaction: discord.Interaction,
    url: str = None
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_leave_settings(guild_id)

    settings["image_url"] = url
    save_leave_settings(leave_settings)

    if url:
        await interaction.response.send_message(
            "✅ Leave image/gif updated.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "✅ Leave image/gif removed.",
            ephemeral=True
        )


# -------------------------
# LEAVE: TOGGLE ON/OFF
# -------------------------

@bot.tree.command(
    name="leave-toggle",
    description="Enable or disable leave messages."
)
@app_commands.describe(
    enabled="True to enable, False to disable"
)
@app_commands.checks.has_permissions(administrator=True)
async def leave_toggle(
    interaction: discord.Interaction,
    enabled: bool
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_leave_settings(guild_id)

    settings["enabled"] = enabled
    save_leave_settings(leave_settings)

    state = "enabled" if enabled else "disabled"
    await interaction.response.send_message(
        f"✅ Leave messages are now **{state}**.",
        ephemeral=True
    )


# -------------------------
# LEAVE: RESET TO DEFAULT
# -------------------------

@bot.tree.command(
    name="leave-reset",
    description="Reset the leave message and image back to default."
)
@app_commands.checks.has_permissions(administrator=True)
async def leave_reset(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_leave_settings(guild_id)

    settings["message"] = DEFAULT_LEAVE_MESSAGE
    settings["image_url"] = DEFAULT_LEAVE_IMAGE
    save_leave_settings(leave_settings)

    await interaction.response.send_message(
        "✅ Leave message and image/gif have been reset to default. "
        "Your leave channel and enabled/disabled state were left "
        "untouched. Use `/leave-test` to preview.",
        ephemeral=True
    )


# -------------------------
# LEAVE: TEST / PREVIEW
# -------------------------

@bot.tree.command(
    name="leave-test",
    description="Preview the current leave message."
)
@app_commands.checks.has_permissions(administrator=True)
async def leave_test(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_leave_settings(guild_id)

    embed = build_leave_embed(interaction.user, settings)

    await interaction.response.send_message(embed=embed)


# -------------------------
# LEAVE: VIEW SETTINGS
# -------------------------

@bot.tree.command(
    name="leave-settings",
    description="View the current leave message configuration."
)
@app_commands.checks.has_permissions(administrator=True)
async def leave_settings_cmd(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_leave_settings(guild_id)

    channel_id = settings.get("channel_id")
    channel = interaction.guild.get_channel(channel_id) if channel_id else None

    embed = discord.Embed(
        title="Leave Message Settings",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Status",
        value="Enabled ✅" if settings.get("enabled", True) else "Disabled ❌",
        inline=True
    )
    embed.add_field(
        name="Channel",
        value=channel.mention if channel else "Not set",
        inline=True
    )
    embed.add_field(
        name="Image/GIF",
        value=settings.get("image_url") or "Not set",
        inline=False
    )
    embed.add_field(
        name="Message",
        value=f"```{settings.get('message', DEFAULT_LEAVE_MESSAGE)}```",
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# -------------------------
# KICK
# -------------------------

@bot.tree.command(
    name="kick",
    description="Kick a member from the server."
)
@app_commands.describe(
    member="The member to kick",
    reason="Reason for the kick"
)
@app_commands.checks.has_permissions(administrator=True)
async def kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):

    if member == interaction.user:
        await interaction.response.send_message(
            "❌ You cannot kick yourself.",
            ephemeral=True
        )
        return

    if member.top_role >= interaction.user.top_role:
        await interaction.response.send_message(
            "❌ You cannot kick someone with an equal or higher role.",
            ephemeral=True
        )
        return

    if not member.kickable:
        await interaction.response.send_message(
            "❌ I don't have permission to kick that member.",
            ephemeral=True
        )
        return

    try:
        await member.send(
            f"You have been kicked from **{interaction.guild.name}**.\n"
            f"Reason: {reason}"
        )
    except discord.Forbidden:
        pass

    await member.kick(reason=reason)

    await interaction.response.send_message(
        f"👢 **{member}** has been kicked.\n"
        f"Reason: {reason}"
    )


# -------------------------
# BAN
# -------------------------

@bot.tree.command(
    name="ban",
    description="Ban a member from the server."
)
@app_commands.describe(
    member="The member to ban",
    reason="Reason for the ban"
)
@app_commands.checks.has_permissions(administrator=True)
async def ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):

    if member == interaction.user:
        await interaction.response.send_message(
            "❌ You cannot ban yourself.",
            ephemeral=True
        )
        return

    if member.top_role >= interaction.user.top_role:
        await interaction.response.send_message(
            "❌ You cannot ban someone with an equal or higher role.",
            ephemeral=True
        )
        return

    if not member.bannable:
        await interaction.response.send_message(
            "❌ I don't have permission to ban that member.",
            ephemeral=True
        )
        return

    try:
        await member.send(
            f"You have been banned from **{interaction.guild.name}**.\n"
            f"Reason: {reason}"
        )
    except discord.Forbidden:
        pass

    await member.ban(
        reason=reason,
        delete_message_seconds=0
    )

    await interaction.response.send_message(
        f"🔨 **{member}** has been banned.\n"
        f"Reason: {reason}"
    )


# -------------------------
# UNBAN
# -------------------------

@bot.tree.command(
    name="unban",
    description="Unban a user."
)
@app_commands.describe(
    user_id="The Discord ID of the user",
    reason="Reason for the unban"
)
@app_commands.checks.has_permissions(administrator=True)
async def unban(
    interaction: discord.Interaction,
    user_id: str,
    reason: str = "No reason provided"
):

    try:
        user = await bot.fetch_user(int(user_id))
    except (ValueError, discord.NotFound):
        await interaction.response.send_message(
            "❌ Invalid or unknown user ID.",
            ephemeral=True
        )
        return

    try:
        await interaction.guild.unban(
            user,
            reason=reason
        )

        await interaction.response.send_message(
            f"✅ **{user}** has been unbanned.\n"
            f"Reason: {reason}"
        )

    except discord.NotFound:
        await interaction.response.send_message(
            "❌ That user is not currently banned.",
            ephemeral=True
        )


# -------------------------
# TIMEOUT
# -------------------------

@bot.tree.command(
    name="timeout",
    description="Timeout a member."
)
@app_commands.describe(
    member="The member to timeout",
    minutes="Duration in minutes",
    reason="Reason for the timeout"
)
@app_commands.checks.has_permissions(administrator=True)
async def timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: app_commands.Range[int, 1, 40320],
    reason: str = "No reason provided"
):

    if member == interaction.user:
        await interaction.response.send_message(
            "❌ You cannot timeout yourself.",
            ephemeral=True
        )
        return

    if member.top_role >= interaction.user.top_role:
        await interaction.response.send_message(
            "❌ You cannot timeout someone with an equal or higher role.",
            ephemeral=True
        )
        return

    if not member.moderatable:
        await interaction.response.send_message(
            "❌ I cannot timeout that member.",
            ephemeral=True
        )
        return

    try:
        await member.timeout(
            timedelta(minutes=minutes),
            reason=reason
        )

        await interaction.response.send_message(
            f"🔇 **{member}** has been timed out for "
            f"**{minutes} minutes**.\n"
            f"Reason: {reason}"
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I don't have permission to timeout that member.",
            ephemeral=True
        )


# -------------------------
# REMOVE TIMEOUT
# -------------------------

@bot.tree.command(
    name="untimeout",
    description="Remove a timeout from a member."
)
@app_commands.describe(
    member="The member to untimeout"
)
@app_commands.checks.has_permissions(administrator=True)
async def untimeout(
    interaction: discord.Interaction,
    member: discord.Member
):

    if member.top_role >= interaction.user.top_role:
        await interaction.response.send_message(
            "❌ You cannot modify someone with an equal or higher role.",
            ephemeral=True
        )
        return

    try:
        await member.timeout(
            None,
            reason=f"Timeout removed by {interaction.user}"
        )

        await interaction.response.send_message(
            f"🔊 **{member}** is no longer timed out."
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I don't have permission to modify that member.",
            ephemeral=True
        )


# -------------------------
# WARN
# -------------------------

@bot.tree.command(
    name="warn",
    description="Warn a member."
)
@app_commands.describe(
    member="The member to warn",
    reason="Reason for the warning"
)
@app_commands.checks.has_permissions(administrator=True)
async def warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):

    guild_id = str(interaction.guild.id)
    user_id = str(member.id)

    if guild_id not in warnings:
        warnings[guild_id] = {}

    if user_id not in warnings[guild_id]:
        warnings[guild_id][user_id] = []

    warning = {
        "reason": reason,
        "moderator": str(interaction.user),
        "moderator_id": interaction.user.id
    }

    warnings[guild_id][user_id].append(warning)

    save_warnings(warnings)

    total = len(warnings[guild_id][user_id])

    # Auto-mute once the member hits the warning threshold.
    muted = False
    if total >= WARNING_MUTE_THRESHOLD and member.moderatable:
        try:
            await member.timeout(
                timedelta(hours=WARNING_MUTE_HOURS),
                reason=f"Reached {total} warnings"
            )
            muted = True
        except discord.Forbidden:
            pass

    dm_lines = [
        f"⚠️ You have received a warning in **{interaction.guild.name}**.",
        "",
        f"Reason: {reason}",
        f"Warning #{total}"
    ]
    if muted:
        dm_lines.append(
            f"\nYou've reached **{WARNING_MUTE_THRESHOLD} warnings** and "
            f"have been muted for **{WARNING_MUTE_HOURS} hours**."
        )

    try:
        await member.send("\n".join(dm_lines))
    except discord.Forbidden:
        pass

    response_lines = [
        f"⚠️ **{member}** has been warned.",
        f"Reason: {reason}",
        f"Total warnings: **{total}**"
    ]
    if muted:
        response_lines.append(
            f"🔇 **{member}** reached {WARNING_MUTE_THRESHOLD} warnings "
            f"and has been muted for **{WARNING_MUTE_HOURS} hours**."
        )
    elif total >= WARNING_MUTE_THRESHOLD:
        response_lines.append(
            "❌ I couldn't mute this member (missing permissions or "
            "role hierarchy)."
        )

    await interaction.response.send_message("\n".join(response_lines))

    sent_message = await interaction.original_response()
    await sent_message.delete(delay=5)


# -------------------------
# VIEW WARNINGS
# -------------------------

@bot.tree.command(
    name="warnings",
    description="View a member's warnings."
)
@app_commands.describe(
    member="The member to check"
)
@app_commands.checks.has_permissions(administrator=True)
async def view_warnings(
    interaction: discord.Interaction,
    member: discord.Member
):

    guild_id = str(interaction.guild.id)
    user_id = str(member.id)

    user_warnings = warnings.get(
        guild_id,
        {}
    ).get(
        user_id,
        []
    )

    if not user_warnings:
        await interaction.response.send_message(
            f"✅ **{member}** has no warnings.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"Warnings — {member}",
        description=f"Total warnings: **{len(user_warnings)}**",
        color=discord.Color.orange()
    )

    for number, warning in enumerate(
        user_warnings,
        start=1
    ):
        embed.add_field(
            name=f"Warning #{number}",
            value=(
                f"**Reason:** {warning['reason']}\n"
                f"**Moderator:** {warning['moderator']}"
            ),
            inline=False
        )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# -------------------------
# VIEW ALL WARNINGS (SERVER-WIDE)
# -------------------------

@bot.tree.command(
    name="viewwarnings",
    description="View everyone in this server who has at least one warning."
)
@app_commands.checks.has_permissions(administrator=True)
async def view_all_warnings(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    guild_id = str(interaction.guild.id)
    guild_warnings = warnings.get(guild_id, {})

    # Only keep users who still have at least one warning on record.
    active = {
        user_id: user_warnings
        for user_id, user_warnings in guild_warnings.items()
        if user_warnings
    }

    if not active:
        await interaction.followup.send(
            "✅ Nobody in this server currently has any warnings.",
            ephemeral=True
        )
        return

    # Most-warned members first.
    sorted_users = sorted(
        active.items(),
        key=lambda item: len(item[1]),
        reverse=True
    )

    # Discord allows at most 10 embeds per message, and each embed can
    # only show one avatar, so we build one small embed per member.
    shown = sorted_users[:10]
    embeds = []

    for user_id, user_warnings in shown:
        member = interaction.guild.get_member(int(user_id))
        user = member

        if user is None:
            # Not currently in the server (or not cached) - try to
            # fetch them directly so we can still show their @ and pfp.
            try:
                user = await bot.fetch_user(int(user_id))
            except (discord.NotFound, discord.HTTPException):
                user = None

        count = len(user_warnings)
        latest_reason = user_warnings[-1]["reason"]
        flag = " 🔇" if count >= WARNING_MUTE_THRESHOLD else ""

        embed = discord.Embed(color=discord.Color.orange())

        if user:
            embed.description = (
                f"{user.mention}\n"
                f"**{count}** warning(s){flag}\n"
                f"Most recent: {latest_reason}"
            )
            embed.set_thumbnail(url=user.display_avatar.url)
        else:
            embed.description = (
                f"Unknown user (`{user_id}`)\n"
                f"**{count}** warning(s){flag}\n"
                f"Most recent: {latest_reason}"
            )

        embeds.append(embed)

    content = (
        f"⚠️ **{len(sorted_users)}** member(s) have warnings in "
        f"**{interaction.guild.name}**."
    )
    if len(sorted_users) > 10:
        content += " Showing the top 10 by warning count."

    await interaction.followup.send(
        content=content,
        embeds=embeds,
        ephemeral=True
    )

@bot.tree.command(
    name="clearwarnings",
    description="Clear all warnings for a member."
)
@app_commands.describe(
    member="The member whose warnings to clear"
)
@app_commands.checks.has_permissions(administrator=True)
async def clear_warnings(
    interaction: discord.Interaction,
    member: discord.Member
):

    guild_id = str(interaction.guild.id)
    user_id = str(member.id)

    if guild_id in warnings:
        if user_id in warnings[guild_id]:
            warnings[guild_id][user_id] = []

    save_warnings(warnings)

    await interaction.response.send_message(
        f"✅ Warnings for **{member}** have been cleared."
    )

    sent_message = await interaction.original_response()
    await sent_message.delete(delay=5)


# -------------------------
# CLEAR MESSAGES
# -------------------------

@bot.tree.command(
    name="clear",
    description="Delete messages from the channel."
)
@app_commands.describe(
    amount="Number of messages to delete (1-100)"
)
@app_commands.checks.has_permissions(administrator=True)
async def clear(
    interaction: discord.Interaction,
    amount: app_commands.Range[int, 1, 100]
):

    await interaction.response.defer(
        ephemeral=True
    )

    deleted = await interaction.channel.purge(
        limit=amount
    )

    await interaction.followup.send(
        f"🧹 Deleted **{len(deleted)} messages**.",
        ephemeral=True
    )


# -------------------------
# GIVE ROLE TO EVERYONE
# -------------------------

class GiveRoleAllConfirmView(discord.ui.View):
    """Simple Confirm/Cancel gate in front of a mass role-grant, so a
    stray tap/typo can't hand a role to the whole server."""

    def __init__(self, author_id: int, role: discord.Role):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.role = role
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who ran this command can confirm it.",
                ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Confirm", emoji="✅", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"⏳ Giving {self.role.mention} to everyone, this can take a while for large servers...",
            view=self
        )
        self.stop()

    @discord.ui.button(label="Cancel", emoji="🗑️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="❌ Cancelled. No roles were changed.",
            view=self
        )
        self.stop()


@bot.tree.command(
    name="giveroleall",
    description="Give a role to every member currently in the server."
)
@app_commands.describe(
    role="The role to give to everyone",
    include_bots="Also give the role to bots (default: False)"
)
@app_commands.checks.has_permissions(administrator=True)
async def giveroleall(
    interaction: discord.Interaction,
    role: discord.Role,
    include_bots: bool = False
):
    if role.is_default():
        await interaction.response.send_message(
            "❌ You can't mass-assign @everyone.",
            ephemeral=True
        )
        return

    if role.managed:
        await interaction.response.send_message(
            "❌ That role is managed by an integration (e.g. a bot) and "
            "can't be assigned manually.",
            ephemeral=True
        )
        return

    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "❌ I can't assign that role because it's higher than or "
            "equal to my own top role. Move my role above it in "
            "Server Settings → Roles.",
            ephemeral=True
        )
        return

    # Rough headcount for the confirmation prompt, based on the current
    # member cache.
    pending_count = sum(
        1 for member in interaction.guild.members
        if (include_bots or not member.bot) and role not in member.roles
    )

    if pending_count == 0:
        await interaction.response.send_message(
            f"✅ Everyone eligible already has {role.mention}. Nothing to do.",
            ephemeral=True
        )
        return

    view = GiveRoleAllConfirmView(
        author_id=interaction.user.id,
        role=role
    )

    await interaction.response.send_message(
        f"⚠️ This will give {role.mention} to **{pending_count}** "
        f"member(s) who don't already have it"
        f"{' (bots included)' if include_bots else ' (bots excluded)'}. "
        f"This isn't easily reversible in bulk — confirm?",
        view=view,
        ephemeral=True
    )

    timed_out = await view.wait()
    if timed_out or not view.confirmed:
        return

    added = 0
    skipped = 0
    failed = 0

    for member in interaction.guild.members:
        if member.bot and not include_bots:
            skipped += 1
            continue

        if role in member.roles:
            skipped += 1
            continue

        try:
            await member.add_roles(
                role,
                reason=f"/giveroleall run by {interaction.user}"
            )
            added += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1

    summary = (
        f"✅ Gave {role.mention} to **{added}** member(s).\n"
        f"Skipped **{skipped}** (already had it or excluded).\n"
    )
    if failed:
        summary += (
            f"⚠️ Failed on **{failed}** member(s) "
            f"(missing permissions or role hierarchy)."
        )

    await interaction.followup.send(summary, ephemeral=True)


# -------------------------
# JOIN ROLE: SET ROLE
# -------------------------

@bot.tree.command(
    name="joinrole",
    description="Set the role automatically given to everyone who joins the server."
)
@app_commands.describe(
    role="The role to give new members"
)
@app_commands.checks.has_permissions(administrator=True)
async def joinrole(
    interaction: discord.Interaction,
    role: discord.Role
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_joinrole_settings(guild_id)

    if role.is_default():
        await interaction.response.send_message(
            "❌ You can't use @everyone as a join role.",
            ephemeral=True
        )
        return

    if role.managed:
        await interaction.response.send_message(
            "❌ That role is managed by an integration (e.g. a bot) and "
            "can't be assigned manually.",
            ephemeral=True
        )
        return

    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "❌ I can't assign that role because it's higher than or "
            "equal to my own top role. Move my role above it in "
            "Server Settings → Roles.",
            ephemeral=True
        )
        return

    settings["role_id"] = role.id
    settings["enabled"] = True
    save_joinrole_settings(joinrole_settings)

    await interaction.response.send_message(
        f"✅ New members will now automatically receive the {role.mention} role.",
        ephemeral=True
    )


# -------------------------
# JOIN ROLE: TOGGLE ON/OFF
# -------------------------

@bot.tree.command(
    name="joinrole-toggle",
    description="Enable or disable automatically giving new members the join role."
)
@app_commands.describe(
    enabled="True to enable, False to disable"
)
@app_commands.checks.has_permissions(administrator=True)
async def joinrole_toggle(
    interaction: discord.Interaction,
    enabled: bool
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_joinrole_settings(guild_id)

    settings["enabled"] = enabled
    save_joinrole_settings(joinrole_settings)

    state = "enabled" if enabled else "disabled"
    await interaction.response.send_message(
        f"✅ Auto join-role is now **{state}**.",
        ephemeral=True
    )


# -------------------------
# JOIN ROLE: VIEW SETTINGS
# -------------------------

@bot.tree.command(
    name="joinrole-settings",
    description="View the current join-role configuration."
)
@app_commands.checks.has_permissions(administrator=True)
async def joinrole_settings_cmd(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_joinrole_settings(guild_id)

    role_id = settings.get("role_id")
    role = interaction.guild.get_role(role_id) if role_id else None

    embed = discord.Embed(
        title="Join Role Settings",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Status",
        value="Enabled ✅" if settings.get("enabled", True) else "Disabled ❌",
        inline=True
    )
    embed.add_field(
        name="Role",
        value=role.mention if role else "Not set",
        inline=True
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# -------------------------
# EMBED BUILDER
# -------------------------

class AddFieldModal(discord.ui.Modal, title="Add Field"):
    field_name = discord.ui.TextInput(
        label="Field Name",
        max_length=256
    )
    field_value = discord.ui.TextInput(
        label="Field Value",
        style=discord.TextStyle.paragraph,
        max_length=1024
    )
    field_inline = discord.ui.TextInput(
        label="Inline? (yes/no)",
        required=False,
        default="yes",
        max_length=3
    )

    def __init__(self, view: "EmbedBuilderView"):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        if len(self.view.embed.fields) >= 25:
            await interaction.response.send_message(
                "❌ Embeds can only have up to 25 fields.",
                ephemeral=True
            )
            return

        inline = not (
            self.field_inline.value
            and self.field_inline.value.strip().lower().startswith("n")
        )

        self.view.embed.add_field(
            name=self.field_name.value,
            value=self.field_value.value.replace("\\n", "\n"),
            inline=inline
        )

        await interaction.response.edit_message(
            embed=self.view.embed,
            view=self.view
        )


class ThumbnailModal(discord.ui.Modal, title="Set Thumbnail"):
    thumbnail_url = discord.ui.TextInput(
        label="Thumbnail Image URL (leave blank to remove)",
        required=False
    )

    def __init__(self, view: "EmbedBuilderView"):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        if self.thumbnail_url.value:
            self.view.embed.set_thumbnail(url=self.thumbnail_url.value)
        else:
            self.view.embed.set_thumbnail(url=None)

        await interaction.response.edit_message(
            embed=self.view.embed,
            view=self.view
        )


class EmbedTextModal(discord.ui.Modal, title="Embed Details"):
    embed_title = discord.ui.TextInput(
        label="Title",
        required=False,
        max_length=256
    )
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=4000
    )
    color = discord.ui.TextInput(
        label="Color (hex, e.g. 5865F2)",
        required=False,
        max_length=7
    )
    image_url = discord.ui.TextInput(
        label="Image URL",
        required=False
    )
    footer = discord.ui.TextInput(
        label="Footer Text",
        required=False,
        max_length=2048
    )

    def __init__(self, view: "EmbedBuilderView" = None):
        super().__init__()
        self.view = view

        # Pre-fill with the current values when editing an existing embed.
        if view is not None:
            existing = view.embed
            self.embed_title.default = existing.title or None
            self.description.default = existing.description or None
            if existing.color:
                self.color.default = f"{existing.color.value:06X}"
            if existing.image:
                self.image_url.default = existing.image.url
            if existing.footer:
                self.footer.default = existing.footer.text

    async def on_submit(self, interaction: discord.Interaction):
        new_embed = discord.Embed()

        if self.embed_title.value:
            new_embed.title = self.embed_title.value
        if self.description.value:
            new_embed.description = self.description.value.replace("\\n", "\n")

        color_value = self.color.value.strip().lstrip("#") if self.color.value else None
        if color_value:
            try:
                new_embed.color = discord.Color(int(color_value, 16))
            except ValueError:
                new_embed.color = discord.Color.blurple()
        else:
            new_embed.color = discord.Color.blurple()

        if self.image_url.value:
            new_embed.set_image(url=self.image_url.value)

        if self.footer.value:
            new_embed.set_footer(text=self.footer.value)

        # Carry over fields/thumbnail from the embed being edited, if any.
        existing_fields = self.view.embed.fields if self.view else []
        existing_thumbnail = self.view.embed.thumbnail if self.view else None

        if not new_embed.title and not new_embed.description and not existing_fields:
            await interaction.response.send_message(
                "❌ An embed needs at least a title, a description, or a field.",
                ephemeral=True
            )
            return

        for field in existing_fields:
            new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
        if existing_thumbnail:
            new_embed.set_thumbnail(url=existing_thumbnail.url)

        if self.view is None:
            builder_view = EmbedBuilderView(
                embed=new_embed,
                author_id=interaction.user.id,
                default_channel=interaction.channel
            )
            await interaction.response.send_message(
                content=(
                    "**Embed Preview** — pick a channel below and use the "
                    "buttons to keep editing, then hit **Send** when ready."
                ),
                embed=new_embed,
                view=builder_view,
                ephemeral=True
            )
        else:
            self.view.embed = new_embed
            await interaction.response.edit_message(
                embed=new_embed,
                view=self.view
            )


class EmbedBuilderView(discord.ui.View):
    def __init__(
        self,
        embed: discord.Embed,
        author_id: int,
        default_channel: discord.TextChannel
    ):
        super().__init__(timeout=600)
        self.embed = embed
        self.author_id = author_id
        self.target_channel = default_channel

        self.channel_select.placeholder = f"Send to: #{default_channel.name}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person building this embed can use these controls.",
                ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Send to this channel",
        row=0
    )
    async def channel_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect
    ):
        # ChannelSelect gives back a lightweight AppCommandChannel with no
        # .send() - resolve it against the cache, falling back to an API
        # fetch if it isn't cached yet, to get the real channel object.
        picked = select.values[0]
        channel = picked.resolve() or await picked.fetch()

        self.target_channel = channel
        select.placeholder = f"Send to: #{channel.name}"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Edit Text", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def edit_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EmbedTextModal(view=self))

    @discord.ui.button(label="Add Field", emoji="➕", style=discord.ButtonStyle.secondary, row=1)
    async def add_field(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.embed.fields) >= 25:
            await interaction.response.send_message(
                "❌ Embeds can only have up to 25 fields.",
                ephemeral=True
            )
            return
        await interaction.response.send_modal(AddFieldModal(view=self))

    @discord.ui.button(label="Remove Last Field", emoji="➖", style=discord.ButtonStyle.secondary, row=1)
    async def remove_field(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.embed.fields:
            await interaction.response.send_message(
                "❌ There are no fields to remove.",
                ephemeral=True
            )
            return
        self.embed.remove_field(len(self.embed.fields) - 1)
        await interaction.response.edit_message(embed=self.embed, view=self)

    @discord.ui.button(label="Set Thumbnail", emoji="🖼️", style=discord.ButtonStyle.secondary, row=2)
    async def set_thumbnail(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ThumbnailModal(view=self))

    @discord.ui.button(label="Send", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def send_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.target_channel.send(embed=self.embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to send messages in that channel.",
                ephemeral=True
            )
            return

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=f"✅ Embed sent to {self.target_channel.mention}.",
            embed=self.embed,
            view=self
        )
        self.stop()

    @discord.ui.button(label="Cancel", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="❌ Embed builder cancelled.",
            embed=None,
            view=self
        )
        self.stop()


@bot.tree.command(
    name="embed",
    description="Open an easy step-by-step embed builder."
)
@app_commands.checks.has_permissions(administrator=True)
async def embed_command(interaction: discord.Interaction):
    await interaction.response.send_modal(EmbedTextModal())


# -------------------------
# AUTOMOD: TOGGLE (the only automod command)
# -------------------------

automod_group = app_commands.Group(
    name="automod",
    description="Automatic moderation for link and mention spam."
)


@automod_group.command(
    name="toggle",
    description="Enable or disable automod."
)
@app_commands.describe(
    enabled="True to enable, False to disable"
)
@app_commands.checks.has_permissions(administrator=True)
async def automod_toggle(
    interaction: discord.Interaction,
    enabled: bool
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_automod_settings(guild_id)

    settings["enabled"] = enabled
    save_automod_settings(automod_settings)

    state = "enabled" if enabled else "disabled"
    await interaction.response.send_message(
        f"✅ Automod is now **{state}**.\n"
        f"When enabled, anyone other than an administrator who posts "
        f"**{AUTOMOD_LINK_THRESHOLD}+** messages with a link, or "
        f"**{AUTOMOD_MENTION_THRESHOLD}+** messages with a mention "
        f"(or **{AUTOMOD_MENTION_INSTANT_THRESHOLD}+** mentions in one "
        f"message), within **{AUTOMOD_WINDOW_SECONDS} seconds** has those "
        f"messages deleted and is timed out for **{AUTOMOD_TIMEOUT_HOURS} hours**.",
        ephemeral=True
    )


bot.tree.add_command(automod_group)


# -------------------------
# LOGS: SET CHANNEL
# -------------------------

@bot.tree.command(
    name="logschannel",
    description="Set the channel where command usage logs are sent."
)
@app_commands.describe(
    channel="The channel to send command logs in"
)
@app_commands.checks.has_permissions(administrator=True)
async def logschannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_logs_settings(guild_id)

    settings["channel_id"] = channel.id
    settings["enabled"] = True
    save_logs_settings(logs_settings)

    await interaction.response.send_message(
        f"✅ Command logs (moderation actions and every other slash "
        f"command used) will now be sent in {channel.mention}.",
        ephemeral=True
    )


# -------------------------
# LOGS: TOGGLE ON/OFF
# -------------------------

@bot.tree.command(
    name="logs-toggle",
    description="Enable or disable command usage logging."
)
@app_commands.describe(
    enabled="True to enable, False to disable"
)
@app_commands.checks.has_permissions(administrator=True)
async def logs_toggle(
    interaction: discord.Interaction,
    enabled: bool
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_logs_settings(guild_id)

    settings["enabled"] = enabled
    save_logs_settings(logs_settings)

    state = "enabled" if enabled else "disabled"
    await interaction.response.send_message(
        f"✅ Command usage logging is now **{state}**.",
        ephemeral=True
    )


# -------------------------
# LOGS: VIEW SETTINGS
# -------------------------

@bot.tree.command(
    name="logs-settings",
    description="View the current command-logging configuration."
)
@app_commands.checks.has_permissions(administrator=True)
async def logs_settings_cmd(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_logs_settings(guild_id)

    channel_id = settings.get("channel_id")
    channel = interaction.guild.get_channel(channel_id) if channel_id else None

    embed = discord.Embed(
        title="Command Log Settings",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Status",
        value="Enabled ✅" if settings.get("enabled", True) else "Disabled ❌",
        inline=True
    )
    embed.add_field(
        name="Channel",
        value=channel.mention if channel else "Not set",
        inline=True
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# -------------------------
# COMMANDS LIST
# -------------------------

COMMAND_CATEGORIES = [
    ("🛡️ Moderation", [
        "kick", "ban", "unban", "timeout", "untimeout",
        "warn", "warnings", "viewwarnings", "clearwarnings", "clear",
        "giveroleall"
    ], False),
    ("👋 Welcome", [
        "welcome"
    ], True),
    ("🚪 Leave", [
        "leave-setchannel", "leave-setmessage", "leave-setimage",
        "leave-toggle", "leave-test", "leave-settings", "leave-reset"
    ], True),
    ("🎭 Join Role", [
        "joinrole", "joinrole-toggle", "joinrole-settings"
    ], True),
    ("🧾 Logs", [
        "logschannel", "logs-toggle", "logs-settings"
    ], True),
    ("🚨 Automod", [
        "automod toggle"
    ], True),
    ("🧩 Embeds", [
        "embed"
    ], True),
    ("ℹ️ Other", [
        "commands"
    ], True),
]


@bot.tree.command(
    name="commands",
    description="Show a list of all available commands."
)
async def commands_list(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    # Pull the live, synced commands so we can build clickable /command
    # mentions instead of plain code text - much easier to scan and tap.
    try:
        synced = await interaction.client.tree.fetch_commands(
            guild=interaction.guild
        )
        command_ids = {command.name: command.id for command in synced}
    except discord.HTTPException:
        command_ids = {}

    def mention(name: str) -> str:
        # Subcommands (e.g. "automod toggle") share their parent's ID -
        # Discord's command-mention format wants the full qualified name
        # but the top-level command's ID.
        root = name.split(" ", 1)[0]
        command_id = command_ids.get(root)
        return f"</{name}:{command_id}>" if command_id else f"`/{name}`"

    embed = discord.Embed(
        title="📖 Command List",
        description="Tap a command to run it, or type `/` to see its options.",
        color=discord.Color.blurple()
    )

    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)

    for category_name, command_names, inline in COMMAND_CATEGORIES:
        embed.add_field(
            name=category_name,
            value="\n".join(mention(name) for name in command_names),
            inline=inline
        )

    embed.set_footer(
        text=(
            "All commands require the Administrator permission • "
            f"{WARNING_MUTE_THRESHOLD} warnings = auto-mute for "
            f"{WARNING_MUTE_HOURS}h"
        )
    )

    await interaction.followup.send(embed=embed, ephemeral=True)


# -------------------------
# ERROR HANDLER
# -------------------------

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):
        message = (
            "❌ You don't have permission "
            "to use this command."
        )

    elif isinstance(
        error,
        app_commands.BotMissingPermissions
    ):
        message = (
            "❌ I don't have the permissions "
            "required for this command."
        )

    else:
        print(f"Command error: {error}")
        message = (
            "❌ Something went wrong "
            "while executing that command."
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


# -------------------------
# START
# -------------------------

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN environment variable is not set."
    )

bot.run(TOKEN)
