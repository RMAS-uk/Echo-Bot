import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta
from collections import defaultdict
from typing import Optional
import asyncio
import json
import os
import re
import aiohttp
from dotenv import load_dotenv

load_dotenv("/home/container/.env")

TOKEN = os.getenv("DISCORD_TOKEN")

# This bot is private: it only works in ONE server, and leaves any other
# server it's added to. The ID below is the default; set ALLOWED_GUILD_ID in
# the .env file if you ever want to override it.
DEFAULT_ALLOWED_GUILD_ID = "1533843796834390116"

_allowed_guild_raw = (os.getenv("ALLOWED_GUILD_ID") or DEFAULT_ALLOWED_GUILD_ID).strip()
if not _allowed_guild_raw.isdigit():
    raise RuntimeError(
        "ALLOWED_GUILD_ID must be a numeric server ID."
    )
ALLOWED_GUILD_ID = int(_allowed_guild_raw)

# Supabase is used by the private admin dashboard to show which Discord
# servers Echo is currently installed in. Keep the service-role key ONLY
# on the bot host - never put it in the website JavaScript.
SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "https://roedojtytmltfdcvdxca.supabase.co"
).rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

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
    "automod",
}

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class WrongServer(app_commands.CheckFailure):
    """Raised when a command/button is used outside the allowed server."""


class GuildOnlyTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != ALLOWED_GUILD_ID:
            raise WrongServer()
        return True


class ModerationBot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            tree_cls=GuildOnlyTree
        )

    async def setup_hook(self):
        # Persistent review buttons: re-attach them on every start so the
        # star buttons on already-posted review panels keep working.
        self.add_view(ReviewButtonsView())

        # Commands are registered ONLY in the allowed server (guild sync is
        # instant, no ~1 hour global propagation). The global command list
        # is then emptied so nothing is advertised anywhere else - this also
        # removes any global commands left over from before the bot was
        # made private.
        guild = discord.Object(id=ALLOWED_GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        print(f"Slash commands synced to guild {ALLOWED_GUILD_ID} only.")


bot = ModerationBot()


# -------------------------
# SHARED JSON PERSISTENCE
# -------------------------
#
# Every settings file in this bot (warnings, automod, welcome, leave,
# logs, join role) follows the same "read whole file, tolerate it being
# missing/corrupt, write it back with indent=4" pattern. This helper is
# used by warnings and automod below; the other *_settings.json files
# keep their own load/save wrappers untouched.

def load_json_file(path: str) -> dict:
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_json_file(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


# -------------------------
# WARNINGS
# -------------------------

def load_warnings():
    return load_json_file(WARNINGS_FILE)


def save_warnings(data):
    save_json_file(WARNINGS_FILE, data)


warnings = load_warnings()


# -------------------------
# WELCOME SETTINGS
# -------------------------

DEFAULT_WELCOME_MESSAGE = (
    "★*. WELCOME *.°\n"
    "*.* ─────⋆⋅☆⋅⋆───── *.*\n\n"
    "Welcome to {server}, {member}!\n\n"
    "• Check out my socials! {socials}\n\n"
    "Have fun!\n\n"
    "*.* ─────⋆⋅☆⋅⋆───── *.*"
)

DEFAULT_WELCOME_IMAGE = None  # gif/image URL shown at the bottom of the embed
DEFAULT_WELCOME_SOCIALS = None  # your socials link, shown wherever {socials} is used


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
            "enabled": True
        }
        save_welcome_settings(welcome_settings)

    return welcome_settings[guild_id]


def build_welcome_embed(member: discord.Member, settings: dict) -> discord.Embed:
    text = settings.get("message", DEFAULT_WELCOME_MESSAGE)

    text = (
        text.replace("{member}", member.mention)
            .replace("{mention}", member.mention)
            .replace("{name}", member.display_name)
            .replace("{server}", member.guild.name)
            .replace("{count}", str(member.guild.member_count))
            .replace("{socials}", settings.get("socials") or "N/A")
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
# Fully configurable per guild via the /automod panel: link spam and
# mention spam can be toggled independently, their thresholds and the
# shared rolling window are all tunable, and the punishment escalates
# per member - each automod hit is a "strike", and strikes move a
# member from a timeout, to a kick, to a ban as they rack up.

AUTOMOD_FILE = "automod_settings.json"
AUTOMOD_STRIKES_FILE = "automod_strikes.json"

URL_REGEX = re.compile(r"https?://\S+", re.IGNORECASE)

# Defaults used for brand-new guilds (and to backfill any settings a
# guild's config is missing). Admins can change all of these per guild
# through /automod.
AUTOMOD_DEFAULTS = {
    "enabled": True,
    "window_seconds": 10,        # rolling window both checks use
    "link_spam": {
        "enabled": True,
        "threshold": 4            # linked messages inside the window -> strike
    },
    "mention_spam": {
        "enabled": True,
        "threshold": 3,           # mention-containing messages inside the window -> strike
        "instant_threshold": 5    # mentions in a SINGLE message -> strike immediately
    },
    "timeout_hours": 48,          # duration of the timeout used for early strikes
    "strikes_before_kick": 2,     # strike # at which a member is kicked instead of timed out
    "strikes_before_ban": 3,      # strike # at which a member is banned instead of kicked
}

# In-memory sliding-window trackers: {guild_id: {user_id: [Message, ...]}}
# Not persisted to disk on purpose - this is short-lived spam-window data,
# not something that needs to survive a restart. Strike counts (below)
# ARE persisted, since escalation needs to survive a restart.
link_spam_tracker = defaultdict(lambda: defaultdict(list))
mention_spam_tracker = defaultdict(lambda: defaultdict(list))


def load_automod_settings():
    return load_json_file(AUTOMOD_FILE)


def save_automod_settings(data):
    save_json_file(AUTOMOD_FILE, data)


automod_settings = load_automod_settings()


def get_guild_automod_settings(guild_id: str) -> dict:
    """Return this guild's automod config, filling in any keys that are
    missing - a brand new guild, or one whose settings predate a field
    that's since been added - with the defaults above."""
    settings = automod_settings.setdefault(guild_id, {})

    settings.setdefault("enabled", AUTOMOD_DEFAULTS["enabled"])
    settings.setdefault("window_seconds", AUTOMOD_DEFAULTS["window_seconds"])
    settings.setdefault("timeout_hours", AUTOMOD_DEFAULTS["timeout_hours"])
    settings.setdefault("strikes_before_kick", AUTOMOD_DEFAULTS["strikes_before_kick"])
    settings.setdefault("strikes_before_ban", AUTOMOD_DEFAULTS["strikes_before_ban"])

    link_spam = settings.setdefault("link_spam", {})
    link_spam.setdefault("enabled", AUTOMOD_DEFAULTS["link_spam"]["enabled"])
    link_spam.setdefault("threshold", AUTOMOD_DEFAULTS["link_spam"]["threshold"])

    mention_spam = settings.setdefault("mention_spam", {})
    mention_spam.setdefault("enabled", AUTOMOD_DEFAULTS["mention_spam"]["enabled"])
    mention_spam.setdefault("threshold", AUTOMOD_DEFAULTS["mention_spam"]["threshold"])
    mention_spam.setdefault("instant_threshold", AUTOMOD_DEFAULTS["mention_spam"]["instant_threshold"])

    save_automod_settings(automod_settings)
    return settings


def load_automod_strikes():
    return load_json_file(AUTOMOD_STRIKES_FILE)


def save_automod_strikes(data):
    save_json_file(AUTOMOD_STRIKES_FILE, data)


automod_strikes = load_automod_strikes()


def add_automod_strike(guild_id: str, user_id: str) -> int:
    """Increments and returns this member's automod strike count for
    this guild. Strikes persist across restarts, unlike the sliding
    spam-detection windows above, since escalation depends on them."""
    guild_strikes = automod_strikes.setdefault(guild_id, {})
    guild_strikes[user_id] = guild_strikes.get(user_id, 0) + 1
    save_automod_strikes(automod_strikes)
    return guild_strikes[user_id]



async def send_automod_log(
    guild: discord.Guild,
    label: str,
    member: discord.Member,
    removed_count: int,
    action_taken: str,
    strikes: int
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
        title=f"{MOD_EMOJIS['automod']} Automod: {label}",
        color=MOD_COLORS["automod"],
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
    embed.add_field(name="Strike #", value=str(strikes), inline=True)
    embed.add_field(name="Messages Removed", value=str(removed_count), inline=True)
    embed.add_field(name="Action Taken", value=action_taken, inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


async def enforce_automod_action(
    offending_messages: list,
    member: discord.Member,
    label: str,
    guild: discord.Guild
):
    """Deletes the given messages, then escalates the punishment based
    on how many prior automod strikes this member has in this guild:
    an early strike gets a timeout, a later strike gets kicked, and the
    final tier gets banned - per the guild's configured thresholds."""

    for offending in offending_messages:
        try:
            await offending.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    settings = get_guild_automod_settings(str(guild.id))
    strikes = add_automod_strike(str(guild.id), str(member.id))

    action_taken = f"Deleted {len(offending_messages)} message(s)"
    action_label = "warned"

    if strikes >= settings["strikes_before_ban"] and member.bannable:
        try:
            await member.ban(
                reason=f"Automod: {label} (strike {strikes})",
                delete_message_seconds=0
            )
            action_taken += " + banned"
            action_label = "banned"
        except discord.Forbidden:
            pass
    elif strikes >= settings["strikes_before_kick"] and member.kickable:
        try:
            await member.kick(reason=f"Automod: {label} (strike {strikes})")
            action_taken += " + kicked"
            action_label = "kicked"
        except discord.Forbidden:
            pass
    elif member.moderatable:
        try:
            await member.timeout(
                timedelta(hours=settings["timeout_hours"]),
                reason=f"Automod: {label} (strike {strikes})"
            )
            action_taken += f" + timed out {settings['timeout_hours']}h"
            action_label = f"timed out for **{settings['timeout_hours']} hours**"
        except discord.Forbidden:
            pass

    await dm_user(
        member,
        f"⚠️ Your recent messages in **{guild.name}** were removed for "
        f"**{label}** (strike **{strikes}**), and you've been {action_label}."
    )

    await send_automod_log(guild, label, member, len(offending_messages), action_taken, strikes)


async def check_spam_window(
    tracker: dict,
    guild_id: int,
    message: discord.Message,
    label: str,
    threshold: int,
    window_seconds: int
) -> bool:
    """Adds this message to the user's rolling window for this spam
    type, prunes anything outside the window, and - if the count
    crosses the given threshold - enforces the automod action. Returns
    True if action was taken."""

    now = discord.utils.utcnow()
    window = timedelta(seconds=window_seconds)

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


async def check_mention_spam(message: discord.Message, settings: dict) -> bool:
    """A single message with a large pile of mentions is spam on its own,
    so this checks that first (instant trigger) before falling back to the
    same sliding-window pattern used for link spam."""

    mention_cfg = settings["mention_spam"]
    if not mention_cfg.get("enabled", True):
        return False

    total_mentions = len(message.mentions) + len(message.role_mentions)
    if message.mention_everyone:
        total_mentions += 1

    if total_mentions == 0:
        return False

    if total_mentions >= mention_cfg["instant_threshold"]:
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
        mention_cfg["threshold"],
        settings["window_seconds"]
    )


# -------------------------
# ADMIN DASHBOARD: SERVER SYNC
# -------------------------


def _supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _supabase_headers(prefer=None) -> dict:
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def _supabase_request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    *,
    payload=None,
    prefer=None,
) -> bool:
    """Small REST helper used only for the discord_servers admin table."""
    if not _supabase_enabled():
        return False

    url = f"{SUPABASE_URL}/rest/v1/{path}"

    try:
        async with session.request(
            method,
            url,
            headers=_supabase_headers(prefer),
            json=payload,
        ) as response:
            if 200 <= response.status < 300:
                return True

            body = await response.text()
            print(
                f"Supabase server-sync error {response.status}: "
                f"{body[:500]}"
            )
            return False
    except (aiohttp.ClientError, TimeoutError) as exc:
        print(f"Supabase server-sync request failed: {exc}")
        return False


def _guild_dashboard_payload(guild: discord.Guild) -> dict:
    bot_member = guild.me

    payload = {
        "guild_id": str(guild.id),
        "name": guild.name,
        "member_count": guild.member_count or 0,
        "icon_url": str(guild.icon.url) if guild.icon else None,
        "active": True,
        "last_seen_at": discord.utils.utcnow().isoformat(),
        "removed_at": None,
    }

    if bot_member and bot_member.joined_at:
        payload["joined_at"] = bot_member.joined_at.isoformat()

    return payload


async def sync_guild_to_admin_dashboard(
    guild: discord.Guild,
    session=None,
):
    """Create/update one server row in Supabase for the admin dashboard."""
    if not _supabase_enabled():
        return

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )

    try:
        await _supabase_request(
            session,
            "POST",
            "discord_servers?on_conflict=guild_id",
            payload=_guild_dashboard_payload(guild),
            prefer="resolution=merge-duplicates,return=minimal",
        )
    finally:
        if own_session:
            await session.close()


async def mark_guild_removed_from_admin_dashboard(guild: discord.Guild):
    """Keep the server in history, but mark Echo as no longer installed."""
    if not _supabase_enabled():
        return

    payload = {
        "active": False,
        "removed_at": discord.utils.utcnow().isoformat(),
    }

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10)
    ) as session:
        await _supabase_request(
            session,
            "PATCH",
            f"discord_servers?guild_id=eq.{guild.id}",
            payload=payload,
            prefer="return=minimal",
        )


async def sync_all_guilds_to_admin_dashboard():
    """Reconcile Supabase with Discord every time the bot starts."""
    if not _supabase_enabled():
        print(
            "Admin server sync disabled: set SUPABASE_SERVICE_ROLE_KEY "
            "in the bot .env file."
        )
        return

    now = discord.utils.utcnow().isoformat()

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10)
    ) as session:
        # Anything left active from a previous run is temporarily marked
        # inactive. The current bot.guilds list is immediately re-activated
        # below, which also fixes missed removals while the bot was offline.
        await _supabase_request(
            session,
            "PATCH",
            "discord_servers?active=eq.true",
            payload={
                "active": False,
                "removed_at": now,
            },
            prefer="return=minimal",
        )

        for guild in bot.guilds:
            await sync_guild_to_admin_dashboard(guild, session=session)

    print(
        f"Admin dashboard server list synced: {len(bot.guilds)} active server(s)."
    )


# -------------------------
# BOT STARTUP
# -------------------------

@bot.event
async def on_ready():
    # Private bot: leave anything that isn't the allowed server (covers
    # invites that happened while the bot was offline).
    for guild in list(bot.guilds):
        if guild.id != ALLOWED_GUILD_ID:
            print(f"Leaving unauthorised guild: {guild.name} ({guild.id})")
            try:
                await guild.leave()
            except discord.HTTPException as exc:
                print(f"Could not leave {guild.name}: {exc}")

    if bot.get_guild(ALLOWED_GUILD_ID) is None:
        print(
            f"WARNING: the bot is not in the allowed server ({ALLOWED_GUILD_ID}). "
            f"Invite it there, or check ALLOWED_GUILD_ID."
        )

    print(f"Logged in as {bot.user}")
    print("Moderation bot is online!")

    print("")
    print("=" * 60)
    print(f"Echo is currently in {len(bot.guilds)} server(s)")
    print("=" * 60)

    for guild in bot.guilds:
        print(
            f"Server: {guild.name} | "
            f"ID: {guild.id} | "
            f"Members: {guild.member_count}"
        )

    print("=" * 60)
    print("")

    # Push the same server list to Supabase so the private admin dashboard
    # can display it without exposing the Discord bot token.
    await sync_all_guilds_to_admin_dashboard()


@bot.event
async def on_guild_join(guild: discord.Guild):
    if guild.id != ALLOWED_GUILD_ID:
        print(f"Added to unauthorised guild {guild.name} ({guild.id}) - leaving.")
        try:
            await guild.leave()
        except discord.HTTPException as exc:
            print(f"Could not leave {guild.name}: {exc}")
        return

    await sync_guild_to_admin_dashboard(guild)
    print(f"Joined allowed guild: {guild.name} ({guild.id})")


@bot.event
async def on_guild_remove(guild: discord.Guild):
    await mark_guild_removed_from_admin_dashboard(guild)
    print(f"Removed from guild: {guild.name} ({guild.id})")


@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    # Keep server name/icon changes current in the dashboard.
    await sync_guild_to_admin_dashboard(after)


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
    # Keeps the review panel pinned to the bottom of its channel. Runs
    # first (and for bot messages too) so automod's early returns and
    # admin exemptions below can't skip it.
    if message.guild is not None:
        schedule_review_sticky(message)

    if message.author.bot or message.guild is None:
        return

    guild_id = str(message.guild.id)
    settings = get_guild_automod_settings(guild_id)

    if not settings.get("enabled", True):
        return

    if isinstance(message.author, discord.Member) and message.author.guild_permissions.administrator:
        return

    link_cfg = settings["link_spam"]
    if link_cfg.get("enabled", True) and message.content and URL_REGEX.search(message.content):
        handled = await check_spam_window(
            link_spam_tracker,
            message.guild.id,
            message,
            "Link Spam",
            link_cfg["threshold"],
            settings["window_seconds"]
        )
        if handled:
            return

    await check_mention_spam(message, settings)


# -------------------------
# WELCOME EVENT
# -------------------------

@bot.event
async def on_member_join(member: discord.Member):
    await sync_guild_to_admin_dashboard(member.guild)

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
    await sync_guild_to_admin_dashboard(member.guild)

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
# One command (/welcome) instead of nine. Everything - channel,
# message, image, socials, on/off, reset, and a live preview -
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
            f"Pick a channel and use the buttons below to edit everything else. "
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

    @discord.ui.button(label="Edit Message", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def edit_message_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WelcomeMessageModal(self))

    @discord.ui.button(label="Set Image", emoji="🖼️", style=discord.ButtonStyle.secondary, row=1)
    async def set_image_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WelcomeImageModal(self))

    @discord.ui.button(label="Set Socials", emoji="🔗", style=discord.ButtonStyle.secondary, row=1)
    async def set_socials_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WelcomeSocialsModal(self))

    @discord.ui.button(label="Disable", emoji="🔕", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["enabled"] = not self.settings.get("enabled", True)
        save_welcome_settings(welcome_settings)
        self._sync_toggle_button()

        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_preview(interaction.user),
            view=self
        )

    @discord.ui.button(label="Reset to Default", emoji="♻️", style=discord.ButtonStyle.danger, row=2)
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

    @discord.ui.button(label="Send Test", emoji="📨", style=discord.ButtonStyle.success, row=3)
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

    @discord.ui.button(label="Close", emoji="✅", style=discord.ButtonStyle.secondary, row=3)
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
# LEAVE: INTERACTIVE PANEL
# -------------------------
#
# One command (/leave) instead of seven, same pattern as /welcome and
# /embed: channel, message, image, on/off, reset, and a live preview all
# live in a single panel.

class LeaveMessageModal(discord.ui.Modal, title="Leave Message"):
    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        max_length=4000
    )

    def __init__(self, view: "LeavePanelView"):
        super().__init__()
        self.view = view
        self.message.default = view.settings.get("message", DEFAULT_LEAVE_MESSAGE)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.settings["message"] = self.message.value.replace("\\n", "\n")
        save_leave_settings(leave_settings)
        await interaction.response.edit_message(
            content=self.view.panel_content(),
            embed=self.view.build_preview(interaction.user),
            view=self.view
        )


class LeaveImageModal(discord.ui.Modal, title="Leave Image / GIF"):
    image_url = discord.ui.TextInput(
        label="Image/GIF URL (leave blank to remove)",
        required=False
    )

    def __init__(self, view: "LeavePanelView"):
        super().__init__()
        self.view = view
        current = view.settings.get("image_url")
        if current:
            self.image_url.default = current

    async def on_submit(self, interaction: discord.Interaction):
        self.view.settings["image_url"] = self.image_url.value or None
        save_leave_settings(leave_settings)
        await interaction.response.edit_message(
            content=self.view.panel_content(),
            embed=self.view.build_preview(interaction.user),
            view=self.view
        )


class LeavePanelView(discord.ui.View):
    def __init__(self, guild: discord.Guild, settings: dict, author_id: int):
        super().__init__(timeout=600)
        self.guild = guild
        self.settings = settings
        self.author_id = author_id

        channel_id = settings.get("channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        self.channel_select.placeholder = (
            f"Leave channel: #{channel.name}" if channel else "Leave channel: not set"
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
            f"**Leave Panel** — Status: {status}\n"
            f"Pick a channel and use the buttons below to edit everything else. "
            f"The preview below updates as you go."
        )

    def build_preview(self, member: discord.Member) -> discord.Embed:
        return build_leave_embed(member, self.settings)

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
        placeholder="Leave channel",
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
        save_leave_settings(leave_settings)
        select.placeholder = f"Leave channel: #{channel.name}"

        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_preview(interaction.user),
            view=self
        )

    @discord.ui.button(label="Edit Message", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def edit_message_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LeaveMessageModal(self))

    @discord.ui.button(label="Set Image", emoji="🖼️", style=discord.ButtonStyle.secondary, row=1)
    async def set_image_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LeaveImageModal(self))

    @discord.ui.button(label="Disable", emoji="🔕", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["enabled"] = not self.settings.get("enabled", True)
        save_leave_settings(leave_settings)
        self._sync_toggle_button()

        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_preview(interaction.user),
            view=self
        )

    @discord.ui.button(label="Reset to Default", emoji="♻️", style=discord.ButtonStyle.danger, row=2)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["message"] = DEFAULT_LEAVE_MESSAGE
        self.settings["image_url"] = DEFAULT_LEAVE_IMAGE
        save_leave_settings(leave_settings)

        await interaction.response.edit_message(
            content=self.panel_content() + "\n♻️ Message and image reset to default.",
            embed=self.build_preview(interaction.user),
            view=self
        )

    @discord.ui.button(label="Send Test", emoji="📨", style=discord.ButtonStyle.success, row=3)
    async def send_test_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.channel.send(embed=self.build_preview(interaction.user))
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to send messages in this channel.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "✅ Test leave message sent to this channel.",
            ephemeral=True
        )

    @discord.ui.button(label="Close", emoji="✅", style=discord.ButtonStyle.secondary, row=3)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="✅ Leave panel closed. Your settings are saved.",
            view=self
        )
        self.stop()


@bot.tree.command(
    name="leave",
    description="Open an interactive panel to configure leave messages."
)
@app_commands.checks.has_permissions(administrator=True)
async def leave_command(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_leave_settings(guild_id)

    view = LeavePanelView(
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
# MODERATION: SHARED HELPERS
# -------------------------
#
# kick/ban/timeout/warn/etc all repeated the same three things: a
# self-target + role-hierarchy check, a best-effort DM, and a hand-rolled
# text response. Centralising them here means every mod command checks
# permissions the same way and reports back in the same "case card"
# embed style instead of several slightly different wordings.

MOD_COLORS = {
    "kick": discord.Color.orange(),
    "ban": discord.Color.red(),
    "unban": discord.Color.green(),
    "timeout": discord.Color.dark_gold(),
    "untimeout": discord.Color.green(),
    "warn": discord.Color.gold(),
    "clearwarnings": discord.Color.green(),
    "clear": discord.Color.blurple(),
    "automod": discord.Color.red(),
}

MOD_EMOJIS = {
    "kick": "👢",
    "ban": "🔨",
    "unban": "✅",
    "timeout": "🔇",
    "untimeout": "🔊",
    "warn": "⚠️",
    "clearwarnings": "🗑️",
    "clear": "🧹",
    "automod": "🚨",
}


def check_hierarchy(
    interaction: discord.Interaction,
    member: discord.Member,
    action: str,
    check_self: bool = True
) -> Optional[str]:
    """Returns an error message if `action` shouldn't proceed (targeting
    yourself, or targeting someone with an equal/higher role), otherwise
    None. Shared by every command that takes a target member."""
    if check_self and member == interaction.user:
        return f"❌ You cannot {action} yourself."
    if member.top_role >= interaction.user.top_role:
        return f"❌ You cannot {action} someone with an equal or higher role."
    return None


async def dm_user(member: discord.abc.User, content: str) -> bool:
    """Best-effort DM. Most mod actions still go ahead even if the
    target has DMs closed, so callers can ignore the return value unless
    they want to flag that the DM didn't land."""
    try:
        await member.send(content)
        return True
    except discord.Forbidden:
        return False


def build_mod_embed(
    action: str,
    title: str,
    member,
    moderator: discord.Member,
    reason: Optional[str] = None,
    extra: Optional[dict] = None
) -> discord.Embed:
    """The shared "case card" embed every moderation command replies
    with, so kick/ban/timeout/warn/etc all look and read the same way."""
    embed = discord.Embed(
        title=f"{MOD_EMOJIS.get(action, '🛡️')} {title}",
        color=MOD_COLORS.get(action, discord.Color.blurple()),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
    embed.add_field(name="Moderator", value=moderator.mention, inline=True)

    if extra:
        for name, value in extra.items():
            embed.add_field(name=name, value=value, inline=True)

    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)

    return embed


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

    error = check_hierarchy(interaction, member, "kick")
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    if not member.kickable:
        await interaction.response.send_message(
            "❌ I don't have permission to kick that member.",
            ephemeral=True
        )
        return

    await dm_user(
        member,
        f"You have been kicked from **{interaction.guild.name}**.\nReason: {reason}"
    )

    await member.kick(reason=reason)

    await interaction.response.send_message(
        embed=build_mod_embed("kick", "Member Kicked", member, interaction.user, reason)
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

    error = check_hierarchy(interaction, member, "ban")
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    if not member.bannable:
        await interaction.response.send_message(
            "❌ I don't have permission to ban that member.",
            ephemeral=True
        )
        return

    await dm_user(
        member,
        f"You have been banned from **{interaction.guild.name}**.\nReason: {reason}"
    )

    await member.ban(reason=reason, delete_message_seconds=0)

    await interaction.response.send_message(
        embed=build_mod_embed("ban", "Member Banned", member, interaction.user, reason)
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
        await interaction.guild.unban(user, reason=reason)

        await interaction.response.send_message(
            embed=build_mod_embed("unban", "Member Unbanned", user, interaction.user, reason)
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

    error = check_hierarchy(interaction, member, "timeout")
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    if not member.moderatable:
        await interaction.response.send_message(
            "❌ I cannot timeout that member.",
            ephemeral=True
        )
        return

    try:
        await member.timeout(timedelta(minutes=minutes), reason=reason)

        await interaction.response.send_message(
            embed=build_mod_embed(
                "timeout", "Member Timed Out", member, interaction.user, reason,
                extra={"Duration": f"{minutes} minute(s)"}
            )
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

    error = check_hierarchy(interaction, member, "untimeout", check_self=False)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    try:
        await member.timeout(None, reason=f"Timeout removed by {interaction.user}")

        await interaction.response.send_message(
            embed=build_mod_embed("untimeout", "Timeout Removed", member, interaction.user)
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

    # The original version had no hierarchy check here, unlike every
    # other targeted mod command - added for consistency, so you can't
    # warn a co-admin or someone above you.
    error = check_hierarchy(interaction, member, "warn")
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    user_id = str(member.id)

    warnings.setdefault(guild_id, {}).setdefault(user_id, []).append({
        "reason": reason,
        "moderator": str(interaction.user),
        "moderator_id": interaction.user.id
    })
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

    dm_content = (
        f"⚠️ You have received a warning in **{interaction.guild.name}**.\n\n"
        f"Reason: {reason}\n"
        f"Warning #{total}"
    )
    if muted:
        dm_content += (
            f"\n\nYou've reached **{WARNING_MUTE_THRESHOLD} warnings** and "
            f"have been muted for **{WARNING_MUTE_HOURS} hours**."
        )
    await dm_user(member, dm_content)

    extra = {"Total Warnings": str(total)}
    if muted:
        extra["Auto-Mute"] = f"🔇 {WARNING_MUTE_HOURS}h timeout applied"
    elif total >= WARNING_MUTE_THRESHOLD:
        extra["Auto-Mute"] = "❌ Couldn't mute (permissions/role hierarchy)"

    await interaction.response.send_message(
        embed=build_mod_embed("warn", "Member Warned", member, interaction.user, reason, extra=extra)
    )


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
        title=f"⚠️ Warnings — {member}",
        description=f"Total warnings: **{len(user_warnings)}**",
        color=MOD_COLORS["warn"],
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    for number, warning in enumerate(user_warnings, start=1):
        embed.add_field(
            name=f"Warning #{number}",
            value=(
                f"**Reason:** {warning['reason']}\n"
                f"**Moderator:** {warning['moderator']}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


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

        embed = discord.Embed(color=MOD_COLORS["warn"])

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

    cleared = len(warnings.get(guild_id, {}).get(user_id, []))
    warnings.setdefault(guild_id, {})[user_id] = []
    save_warnings(warnings)

    await interaction.response.send_message(
        embed=build_mod_embed(
            "clearwarnings", "Warnings Cleared", member, interaction.user,
            extra={"Warnings Removed": str(cleared)}
        )
    )


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

    deleted = await interaction.channel.purge(limit=amount)

    embed = discord.Embed(
        title=f"{MOD_EMOJIS['clear']} Messages Cleared",
        color=MOD_COLORS["clear"],
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Channel", value=interaction.channel.mention, inline=True)
    embed.add_field(name="Deleted", value=str(len(deleted)), inline=True)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)

    await interaction.followup.send(embed=embed, ephemeral=True)


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
# AUTOMOD: INTERACTIVE PANEL
# -------------------------
#
# Same pattern as /welcome and /leave: one command opens a live panel
# instead of a pile of separate commands. Link spam and mention spam can
# be toggled independently, their thresholds and the shared window are
# editable via modals, and the escalation ladder (timeout -> kick -> ban)
# is configurable too.

class AutomodThresholdsModal(discord.ui.Modal, title="Automod Thresholds"):
    link_threshold = discord.ui.TextInput(
        label="Link spam: messages -> strike",
        max_length=3
    )
    mention_threshold = discord.ui.TextInput(
        label="Mention spam: messages -> strike",
        max_length=3
    )
    mention_instant = discord.ui.TextInput(
        label="Mentions in ONE message -> strike",
        max_length=3
    )
    window_seconds = discord.ui.TextInput(
        label="Rolling window (seconds)",
        max_length=4
    )

    def __init__(self, view: "AutomodPanelView"):
        super().__init__()
        self.view = view
        settings = view.settings
        self.link_threshold.default = str(settings["link_spam"]["threshold"])
        self.mention_threshold.default = str(settings["mention_spam"]["threshold"])
        self.mention_instant.default = str(settings["mention_spam"]["instant_threshold"])
        self.window_seconds.default = str(settings["window_seconds"])

    async def on_submit(self, interaction: discord.Interaction):
        try:
            link_t = int(self.link_threshold.value)
            mention_t = int(self.mention_threshold.value)
            mention_i = int(self.mention_instant.value)
            window_s = int(self.window_seconds.value)
        except ValueError:
            await interaction.response.send_message(
                "❌ All four fields must be whole numbers.",
                ephemeral=True
            )
            return

        if min(link_t, mention_t, mention_i, window_s) < 1:
            await interaction.response.send_message(
                "❌ All values must be at least 1.",
                ephemeral=True
            )
            return

        settings = self.view.settings
        settings["link_spam"]["threshold"] = link_t
        settings["mention_spam"]["threshold"] = mention_t
        settings["mention_spam"]["instant_threshold"] = mention_i
        settings["window_seconds"] = window_s
        save_automod_settings(automod_settings)

        await interaction.response.edit_message(
            content=self.view.panel_content(),
            embed=self.view.build_summary(),
            view=self.view
        )


class AutomodEscalationModal(discord.ui.Modal, title="Automod Escalation"):
    timeout_hours = discord.ui.TextInput(
        label="Timeout duration (hours)",
        max_length=4
    )
    strikes_before_kick = discord.ui.TextInput(
        label="Strike # that gets kicked instead",
        max_length=2
    )
    strikes_before_ban = discord.ui.TextInput(
        label="Strike # that gets banned instead",
        max_length=2
    )

    def __init__(self, view: "AutomodPanelView"):
        super().__init__()
        self.view = view
        settings = view.settings
        self.timeout_hours.default = str(settings["timeout_hours"])
        self.strikes_before_kick.default = str(settings["strikes_before_kick"])
        self.strikes_before_ban.default = str(settings["strikes_before_ban"])

    async def on_submit(self, interaction: discord.Interaction):
        try:
            timeout_h = int(self.timeout_hours.value)
            kick_at = int(self.strikes_before_kick.value)
            ban_at = int(self.strikes_before_ban.value)
        except ValueError:
            await interaction.response.send_message(
                "❌ All three fields must be whole numbers.",
                ephemeral=True
            )
            return

        if min(timeout_h, kick_at, ban_at) < 1:
            await interaction.response.send_message(
                "❌ All values must be at least 1.",
                ephemeral=True
            )
            return

        if ban_at < kick_at:
            await interaction.response.send_message(
                "❌ The ban strike # can't be lower than the kick strike #.",
                ephemeral=True
            )
            return

        settings = self.view.settings
        settings["timeout_hours"] = timeout_h
        settings["strikes_before_kick"] = kick_at
        settings["strikes_before_ban"] = ban_at
        save_automod_settings(automod_settings)

        await interaction.response.edit_message(
            content=self.view.panel_content(),
            embed=self.view.build_summary(),
            view=self.view
        )


class AutomodPanelView(discord.ui.View):
    def __init__(self, settings: dict, author_id: int):
        super().__init__(timeout=600)
        self.settings = settings
        self.author_id = author_id
        self._sync_buttons()

    def _sync_buttons(self):
        enabled = self.settings.get("enabled", True)
        self.toggle_automod.label = "Disable Automod" if enabled else "Enable Automod"
        self.toggle_automod.emoji = "🔕" if enabled else "🔔"
        self.toggle_automod.style = (
            discord.ButtonStyle.secondary if enabled else discord.ButtonStyle.success
        )

        link_on = self.settings["link_spam"].get("enabled", True)
        self.toggle_link.label = "Disable Link Check" if link_on else "Enable Link Check"
        self.toggle_link.style = (
            discord.ButtonStyle.secondary if link_on else discord.ButtonStyle.success
        )

        mention_on = self.settings["mention_spam"].get("enabled", True)
        self.toggle_mention.label = "Disable Mention Check" if mention_on else "Enable Mention Check"
        self.toggle_mention.style = (
            discord.ButtonStyle.secondary if mention_on else discord.ButtonStyle.success
        )

    def panel_content(self) -> str:
        status = "Enabled ✅" if self.settings.get("enabled", True) else "Disabled ❌"
        return (
            f"**Automod Panel** — Status: {status}\n"
            f"Use the buttons below to toggle detection and edit thresholds "
            f"and escalation. Administrators are always exempt."
        )

    def build_summary(self) -> discord.Embed:
        settings = self.settings
        window = settings["window_seconds"]

        embed = discord.Embed(
            title=f"{MOD_EMOJIS['automod']} Automod Configuration",
            color=MOD_COLORS["automod"],
            timestamp=discord.utils.utcnow()
        )

        link_status = "✅ On" if settings["link_spam"].get("enabled", True) else "❌ Off"
        embed.add_field(
            name="Link Spam",
            value=f"{link_status} — {settings['link_spam']['threshold']}+ linked messages / {window}s",
            inline=False
        )

        mention_status = "✅ On" if settings["mention_spam"].get("enabled", True) else "❌ Off"
        embed.add_field(
            name="Mention Spam",
            value=(
                f"{mention_status} — {settings['mention_spam']['threshold']}+ messages "
                f"with a mention / {window}s, or "
                f"{settings['mention_spam']['instant_threshold']}+ mentions in one message"
            ),
            inline=False
        )

        kick_at = settings["strikes_before_kick"]
        ban_at = settings["strikes_before_ban"]

        escalation_lines = []
        if kick_at > 1:
            escalation_lines.append(
                f"Strike 1–{kick_at - 1}: 🔇 Timeout ({settings['timeout_hours']}h)"
            )
        if ban_at > kick_at:
            escalation_lines.append(f"Strike {kick_at}–{ban_at - 1}: 👢 Kick")
        escalation_lines.append(f"Strike {ban_at}+: 🔨 Ban")

        embed.add_field(
            name="Escalation (per member, persists across restarts)",
            value="\n".join(escalation_lines),
            inline=False
        )

        return embed

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

    @discord.ui.button(label="Disable Automod", emoji="🔕", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_automod(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["enabled"] = not self.settings.get("enabled", True)
        save_automod_settings(automod_settings)
        self._sync_buttons()
        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_summary(),
            view=self
        )

    @discord.ui.button(label="Disable Link Check", emoji="🔗", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["link_spam"]["enabled"] = not self.settings["link_spam"].get("enabled", True)
        save_automod_settings(automod_settings)
        self._sync_buttons()
        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_summary(),
            view=self
        )

    @discord.ui.button(label="Disable Mention Check", emoji="📣", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_mention(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["mention_spam"]["enabled"] = not self.settings["mention_spam"].get("enabled", True)
        save_automod_settings(automod_settings)
        self._sync_buttons()
        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_summary(),
            view=self
        )

    @discord.ui.button(label="Edit Thresholds", emoji="🎚️", style=discord.ButtonStyle.primary, row=1)
    async def edit_thresholds(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AutomodThresholdsModal(self))

    @discord.ui.button(label="Edit Escalation", emoji="⚖️", style=discord.ButtonStyle.primary, row=1)
    async def edit_escalation(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AutomodEscalationModal(self))

    @discord.ui.button(label="Reset to Default", emoji="♻️", style=discord.ButtonStyle.danger, row=2)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings.clear()
        self.settings.update(json.loads(json.dumps(AUTOMOD_DEFAULTS)))
        save_automod_settings(automod_settings)
        self._sync_buttons()

        await interaction.response.edit_message(
            content=self.panel_content() + "\n♻️ Reset to default settings.",
            embed=self.build_summary(),
            view=self
        )

    @discord.ui.button(label="Close", emoji="✅", style=discord.ButtonStyle.secondary, row=2)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="✅ Automod panel closed. Your settings are saved.",
            view=self
        )
        self.stop()


@bot.tree.command(
    name="automod",
    description="Open an interactive panel to configure automod."
)
@app_commands.checks.has_permissions(administrator=True)
async def automod_command(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_automod_settings(guild_id)

    view = AutomodPanelView(settings=settings, author_id=interaction.user.id)

    await interaction.response.send_message(
        content=view.panel_content(),
        embed=view.build_summary(),
        view=view,
        ephemeral=True
    )



# -------------------------
# REVIEWS
# -------------------------
#
# /reviews opens an admin panel (same pattern as /welcome, /leave and
# /automod) to pick the channel reviews are posted in, restyle the panel
# embed, and post it. The posted panel has five persistent star buttons;
# clicking one opens a short form (who helped + written feedback), and the
# submitted review is posted to the reviews channel as a "New Review" card.
#
# The star buttons use fixed custom_ids and are registered in setup_hook,
# so they keep working after the bot restarts.

REVIEWS_FILE = "reviews_settings.json"

REVIEW_COOLDOWN_SECONDS = 60   # per member, per server, between reviews
REVIEW_DEFAULT_COLOR = "A020F0"

DEFAULT_REVIEW_TITLE = "⭐ Leave a Review"
DEFAULT_REVIEW_DESCRIPTION = (
    "How was your experience with **{brand}**?\n"
    "Pick your star rating (1–5) below, tell us who helped you, "
    "and drop a short review.\n\n"
    "Thanks for your feedback! 💜"
)

# {(guild_id, user_id): datetime of last submitted review}
review_cooldowns = {}


def load_reviews_settings():
    return load_json_file(REVIEWS_FILE)


def save_reviews_settings(data):
    save_json_file(REVIEWS_FILE, data)


reviews_settings = load_reviews_settings()


def get_guild_reviews_settings(guild_id: str) -> dict:
    """Return this guild's reviews config, filling in any missing keys."""
    settings = reviews_settings.setdefault(guild_id, {})

    settings.setdefault("channel_id", None)       # where reviews get posted
    settings.setdefault("enabled", True)
    settings.setdefault("title", DEFAULT_REVIEW_TITLE)
    settings.setdefault("description", DEFAULT_REVIEW_DESCRIPTION)
    settings.setdefault("color", REVIEW_DEFAULT_COLOR)
    settings.setdefault("image_url", None)        # banner under the panel text
    settings.setdefault("brand", None)            # falls back to the server name
    settings.setdefault("sticky", True)           # keep the panel at the bottom of its channel
    settings.setdefault("panel_channel_id", None) # where the live panel currently is
    settings.setdefault("panel_message_id", None)

    save_reviews_settings(reviews_settings)
    return settings


def _review_brand(guild: discord.Guild, settings: dict) -> str:
    return settings.get("brand") or guild.name


def _review_color(settings: dict) -> discord.Color:
    raw = str(settings.get("color") or REVIEW_DEFAULT_COLOR).strip().lstrip("#")
    try:
        return discord.Color(int(raw, 16))
    except ValueError:
        return discord.Color(int(REVIEW_DEFAULT_COLOR, 16))


def build_review_panel_embed(guild: discord.Guild, settings: dict) -> discord.Embed:
    """The public 'Leave a Review' embed that carries the star buttons."""
    brand = _review_brand(guild, settings)

    text = (
        settings.get("description", DEFAULT_REVIEW_DESCRIPTION)
        .replace("{brand}", brand)
        .replace("{server}", guild.name)
    )

    embed = discord.Embed(
        title=settings.get("title", DEFAULT_REVIEW_TITLE),
        description=text,
        color=_review_color(settings)
    )

    image_url = settings.get("image_url")
    if image_url:
        embed.set_image(url=image_url)

    embed.set_footer(
        text=f"{brand} • Verified Review",
        icon_url=guild.icon.url if guild.icon else None
    )

    return embed


def resolve_review_staff(guild: discord.Guild, raw: str) -> str:
    """Turn whatever the reviewer typed into the 'Helped Staff Member'
    field into a mention when we can find the person (mention, user ID,
    username or display name), otherwise show their text as-is."""
    raw = (raw or "").strip()
    if not raw:
        return "Not specified"

    member = None

    match = re.fullmatch(r"<@!?(\d+)>", raw) or re.fullmatch(r"(\d{15,20})", raw)
    if match:
        member = guild.get_member(int(match.group(1)))
    else:
        name = raw.lstrip("@").strip()
        member = guild.get_member_named(name)
        if member is None:
            lowered = name.lower()
            member = next(
                (
                    m for m in guild.members
                    if not m.bot and lowered in (m.name.lower(), m.display_name.lower())
                ),
                None
            )

    if member is not None:
        return member.mention

    return discord.utils.escape_mentions(discord.utils.escape_markdown(raw))[:100]


def build_review_embed(
    guild: discord.Guild,
    settings: dict,
    reviewer: discord.abc.User,
    stars: int,
    staff_text: str,
    feedback: str
) -> discord.Embed:
    """The 'New Review' card posted to the reviews channel."""
    brand = _review_brand(guild, settings)

    embed = discord.Embed(
        title="New Review",
        description=f"{'⭐' * stars} **{stars}/5**",
        color=_review_color(settings),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(name="User", value=reviewer.mention, inline=False)
    embed.add_field(name="Helped Staff Member", value=staff_text, inline=False)

    quoted = "\n".join(f"> {line}" for line in feedback.strip().splitlines() if line.strip())
    embed.add_field(name="Feedback", value=quoted[:1024] or "> —", inline=False)

    embed.set_thumbnail(url=reviewer.display_avatar.url)
    embed.set_footer(
        text=f"{brand} • Verified Review",
        icon_url=guild.icon.url if guild.icon else None
    )

    return embed


class ReviewModal(discord.ui.Modal):
    staff = discord.ui.TextInput(
        label="Who helped you?",
        placeholder="@staff member, username or ID",
        required=False,
        max_length=100
    )
    feedback = discord.ui.TextInput(
        label="Your review",
        style=discord.TextStyle.paragraph,
        placeholder="Tell us how it went...",
        min_length=3,
        max_length=800
    )

    def __init__(self, stars: int):
        super().__init__(title=f"Leave a Review - {stars} Star{'s' if stars != 1 else ''}")
        self.stars = stars

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        settings = get_guild_reviews_settings(str(guild.id))

        channel_id = settings.get("channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None

        if not settings.get("enabled", True) or channel is None:
            await interaction.response.send_message(
                "❌ Reviews aren't set up in this server right now.",
                ephemeral=True
            )
            return

        embed = build_review_embed(
            guild,
            settings,
            interaction.user,
            self.stars,
            resolve_review_staff(guild, self.staff.value),
            self.feedback.value
        )

        try:
            await channel.send(
                content=f"{interaction.user.mention} 💯 thanks for your review!",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=[interaction.user],
                    roles=False,
                    everyone=False
                )
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "❌ I couldn't post your review. Please let a server admin know.",
                ephemeral=True
            )
            return

        review_cooldowns[(guild.id, interaction.user.id)] = discord.utils.utcnow()

        await interaction.response.send_message(
            "✅ Thanks! Your review has been submitted.",
            ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"Review modal error: {error}")
        message = "❌ Something went wrong submitting your review."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class ReviewStarButton(discord.ui.Button):
    def __init__(self, stars: int):
        super().__init__(
            label=f"{stars}⭐",
            style=discord.ButtonStyle.primary,
            custom_id=f"echo_review:{stars}"
        )
        self.stars = stars

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return

        settings = get_guild_reviews_settings(str(interaction.guild.id))

        if not settings.get("enabled", True) or not settings.get("channel_id"):
            await interaction.response.send_message(
                "❌ Reviews aren't set up in this server right now.",
                ephemeral=True
            )
            return

        last = review_cooldowns.get((interaction.guild.id, interaction.user.id))
        if last is not None:
            remaining = REVIEW_COOLDOWN_SECONDS - (discord.utils.utcnow() - last).total_seconds()
            if remaining > 0:
                await interaction.response.send_message(
                    f"⏳ Please wait **{int(remaining) + 1}s** before leaving another review.",
                    ephemeral=True
                )
                return

        await interaction.response.send_modal(ReviewModal(self.stars))


class ReviewButtonsView(discord.ui.View):
    """Persistent view: timeout=None plus fixed custom_ids, registered once
    in setup_hook, so the buttons on already-posted panels keep working
    across restarts and across every server."""

    def __init__(self):
        super().__init__(timeout=None)
        for stars in range(1, 6):
            self.add_item(ReviewStarButton(stars))


# --- sticky panel -----------------------------------------------------
#
# The most recently sent panel is tracked per server. While "sticky" is on,
# any new message in that channel makes the bot re-post the panel at the
# bottom and delete the old copy. Bursts of chat are coalesced: a single
# repost happens REVIEW_STICKY_DELAY seconds after the first new message.

REVIEW_STICKY_DELAY = 2

review_sticky_tasks = {}   # {channel_id: asyncio.Task}


async def post_review_panel(guild: discord.Guild, channel, settings: dict):
    """Sends a fresh panel to `channel`, removes the previously tracked
    panel when sticky is on, and remembers the new one."""
    old_channel_id = settings.get("panel_channel_id")
    old_message_id = settings.get("panel_message_id")

    sent = await channel.send(
        embed=build_review_panel_embed(guild, settings),
        view=ReviewButtonsView()
    )

    if settings.get("sticky", True) and old_message_id:
        old_channel = guild.get_channel(old_channel_id) if old_channel_id else None
        if old_channel is not None:
            try:
                await old_channel.get_partial_message(old_message_id).delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    settings["panel_channel_id"] = channel.id
    settings["panel_message_id"] = sent.id
    save_reviews_settings(reviews_settings)

    return sent


def _is_review_panel_message(message: discord.Message) -> bool:
    for row in message.components:
        for child in getattr(row, "children", []):
            if str(getattr(child, "custom_id", "") or "").startswith("echo_review:"):
                return True
    return False


async def _repost_review_panel(guild: discord.Guild, channel_id: int):
    try:
        await asyncio.sleep(REVIEW_STICKY_DELAY)

        settings = reviews_settings.get(str(guild.id))
        if not settings or not settings.get("sticky", True) or not settings.get("enabled", True):
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        await post_review_panel(guild, channel, settings)
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"Review sticky repost failed in {guild.name}: {exc}")
    finally:
        review_sticky_tasks.pop(channel_id, None)


def schedule_review_sticky(message: discord.Message):
    """Called for every guild message; cheap no-op unless the message is in
    a channel that currently holds a sticky review panel."""
    settings = reviews_settings.get(str(message.guild.id))
    if not settings or not settings.get("sticky", True) or not settings.get("enabled", True):
        return

    if message.channel.id != settings.get("panel_channel_id"):
        return

    # Never react to the panel itself (that would loop forever).
    if message.id == settings.get("panel_message_id") or _is_review_panel_message(message):
        return

    existing = review_sticky_tasks.get(message.channel.id)
    if existing is not None and not existing.done():
        return

    review_sticky_tasks[message.channel.id] = asyncio.create_task(
        _repost_review_panel(message.guild, message.channel.id)
    )


class ReviewsPanelTextModal(discord.ui.Modal, title="Review Panel Text"):
    panel_title = discord.ui.TextInput(
        label="Title",
        max_length=256
    )
    description = discord.ui.TextInput(
        label="Description ({brand} = your name)",
        style=discord.TextStyle.paragraph,
        max_length=2000
    )
    color = discord.ui.TextInput(
        label="Color (hex, e.g. A020F0)",
        required=False,
        max_length=7
    )
    image_url = discord.ui.TextInput(
        label="Banner image URL (leave blank to remove)",
        required=False
    )
    brand = discord.ui.TextInput(
        label="Brand name (blank = server name)",
        required=False,
        max_length=100
    )

    def __init__(self, view: "ReviewsAdminView"):
        super().__init__()
        self.view = view
        settings = view.settings
        self.panel_title.default = settings.get("title", DEFAULT_REVIEW_TITLE)
        self.description.default = settings.get("description", DEFAULT_REVIEW_DESCRIPTION)
        self.color.default = str(settings.get("color") or REVIEW_DEFAULT_COLOR)
        if settings.get("image_url"):
            self.image_url.default = settings["image_url"]
        if settings.get("brand"):
            self.brand.default = settings["brand"]

    async def on_submit(self, interaction: discord.Interaction):
        color_value = (self.color.value or "").strip().lstrip("#")
        if color_value:
            try:
                int(color_value, 16)
            except ValueError:
                await interaction.response.send_message(
                    "❌ That color isn't valid hex. Use something like `A020F0`.",
                    ephemeral=True
                )
                return

        settings = self.view.settings
        settings["title"] = self.panel_title.value
        settings["description"] = self.description.value.replace("\\n", "\n")
        settings["color"] = color_value or REVIEW_DEFAULT_COLOR
        settings["image_url"] = self.image_url.value.strip() or None
        settings["brand"] = self.brand.value.strip() or None
        save_reviews_settings(reviews_settings)

        await interaction.response.edit_message(
            content=self.view.panel_content(),
            embed=self.view.build_preview(),
            view=self.view
        )


class ReviewsAdminView(discord.ui.View):
    def __init__(self, guild: discord.Guild, settings: dict, author_id: int, default_channel):
        super().__init__(timeout=600)
        self.guild = guild
        self.settings = settings
        self.author_id = author_id
        self.target_channel = default_channel

        channel_id = settings.get("channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        self.reviews_channel_select.placeholder = (
            f"Reviews are posted in: #{channel.name}" if channel else "Reviews channel: not set"
        )
        self.panel_channel_select.placeholder = f"Send panel to: #{default_channel.name}"

        self._sync_toggle_button()
        self._sync_sticky_button()

    def _sync_toggle_button(self):
        enabled = self.settings.get("enabled", True)
        self.toggle_button.label = "Disable" if enabled else "Enable"
        self.toggle_button.emoji = "🔕" if enabled else "🔔"
        self.toggle_button.style = (
            discord.ButtonStyle.secondary if enabled else discord.ButtonStyle.success
        )

    def _sync_sticky_button(self):
        sticky = self.settings.get("sticky", True)
        self.sticky_button.label = "Sticky: On" if sticky else "Sticky: Off"
        self.sticky_button.style = (
            discord.ButtonStyle.success if sticky else discord.ButtonStyle.secondary
        )

    def panel_content(self) -> str:
        status = "Enabled ✅" if self.settings.get("enabled", True) else "Disabled ❌"
        sticky = "📌 Sticky (stays at the bottom)" if self.settings.get("sticky", True) else "Not sticky"
        return (
            f"**Reviews Panel** — Status: {status} • {sticky}\n"
            f"1) Pick where finished reviews are posted. "
            f"2) Pick where to send the review panel. "
            f"3) Hit **Send Panel**. The preview below is what members will see."
        )

    def build_preview(self) -> discord.Embed:
        return build_review_panel_embed(self.guild, self.settings)

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
        placeholder="Reviews channel",
        row=0
    )
    async def reviews_channel_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect
    ):
        picked = select.values[0]
        channel = picked.resolve() or await picked.fetch()

        self.settings["channel_id"] = channel.id
        save_reviews_settings(reviews_settings)
        select.placeholder = f"Reviews are posted in: #{channel.name}"

        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_preview(),
            view=self
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Send panel to this channel",
        row=1
    )
    async def panel_channel_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect
    ):
        picked = select.values[0]
        channel = picked.resolve() or await picked.fetch()

        self.target_channel = channel
        select.placeholder = f"Send panel to: #{channel.name}"

        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_preview(),
            view=self
        )

    @discord.ui.button(label="Edit Panel", emoji="✏️", style=discord.ButtonStyle.primary, row=2)
    async def edit_panel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReviewsPanelTextModal(self))

    @discord.ui.button(label="Disable", emoji="🔕", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["enabled"] = not self.settings.get("enabled", True)
        save_reviews_settings(reviews_settings)
        self._sync_toggle_button()

        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_preview(),
            view=self
        )

    @discord.ui.button(label="Reset to Default", emoji="♻️", style=discord.ButtonStyle.danger, row=2)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["title"] = DEFAULT_REVIEW_TITLE
        self.settings["description"] = DEFAULT_REVIEW_DESCRIPTION
        self.settings["color"] = REVIEW_DEFAULT_COLOR
        self.settings["image_url"] = None
        self.settings["brand"] = None
        save_reviews_settings(reviews_settings)

        await interaction.response.edit_message(
            content=self.panel_content() + "\n♻️ Panel text, color, banner and brand reset to default.",
            embed=self.build_preview(),
            view=self
        )

    @discord.ui.button(label="Send Panel", emoji="📨", style=discord.ButtonStyle.success, row=3)
    async def send_panel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.settings.get("channel_id"):
            await interaction.response.send_message(
                "❌ Pick a reviews channel first, so there's somewhere for reviews to go.",
                ephemeral=True
            )
            return

        try:
            await post_review_panel(self.guild, self.target_channel, self.settings)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ I don't have permission to send messages in {self.target_channel.mention}.",
                ephemeral=True
            )
            return

        note = (
            " It will stay at the bottom of the channel."
            if self.settings.get("sticky", True) else ""
        )
        await interaction.response.send_message(
            f"✅ Review panel sent to {self.target_channel.mention}.{note}",
            ephemeral=True
        )

    @discord.ui.button(label="Sticky: On", emoji="📌", style=discord.ButtonStyle.success, row=3)
    async def sticky_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["sticky"] = not self.settings.get("sticky", True)
        save_reviews_settings(reviews_settings)
        self._sync_sticky_button()

        await interaction.response.edit_message(
            content=self.panel_content(),
            embed=self.build_preview(),
            view=self
        )

    @discord.ui.button(label="Close", emoji="✅", style=discord.ButtonStyle.secondary, row=3)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="✅ Reviews panel closed. Your settings are saved.",
            view=self
        )
        self.stop()


@bot.tree.command(
    name="reviews",
    description="Open an interactive panel to set up the review system."
)
@app_commands.checks.has_permissions(administrator=True)
async def reviews_command(interaction: discord.Interaction):
    settings = get_guild_reviews_settings(str(interaction.guild.id))

    view = ReviewsAdminView(
        guild=interaction.guild,
        settings=settings,
        author_id=interaction.user.id,
        default_channel=interaction.channel
    )

    await interaction.response.send_message(
        content=view.panel_content(),
        embed=view.build_preview(),
        view=view,
        ephemeral=True
    )


# -------------------------
# FREE TRIALS
# -------------------------
#
# /givefreetrial records that a member has been given their free trial
# (once per member - it never overwrites the original record), and
# /freetrial checks whether a member has been given one.

FREETRIALS_FILE = "freetrials.json"


def load_freetrials():
    return load_json_file(FREETRIALS_FILE)


def save_freetrials(data):
    save_json_file(FREETRIALS_FILE, data)


freetrials = load_freetrials()


@bot.tree.command(
    name="givefreetrial",
    description="Record that a member has been given their free trial."
)
@app_commands.describe(
    user="The member who is getting the free trial",
    note="Optional note (e.g. which product/plan)"
)
@app_commands.checks.has_permissions(administrator=True)
async def givefreetrial_command(
    interaction: discord.Interaction,
    user: discord.Member,
    note: str = None
):
    if user.bot:
        await interaction.response.send_message(
            "❌ Bots can't be given a free trial.",
            ephemeral=True
        )
        return

    guild_trials = freetrials.setdefault(str(interaction.guild.id), {})
    existing = guild_trials.get(str(user.id))

    if existing:
        await interaction.response.send_message(
            f"⚠️ {user.mention} has **already** been given a free trial by "
            f"<@{existing['given_by']}> on <t:{existing['given_at']}:F> "
            f"(<t:{existing['given_at']}:R>). Nothing was changed.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none()
        )
        return

    guild_trials[str(user.id)] = {
        "given_by": interaction.user.id,
        "given_at": int(discord.utils.utcnow().timestamp()),
        "note": (note or "").strip()[:200] or None
    }
    save_freetrials(freetrials)

    embed = discord.Embed(
        title="🎁 Free Trial Given",
        description=f"{user.mention} has been given a free trial.",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Given by", value=interaction.user.mention, inline=True)
    if guild_trials[str(user.id)]["note"]:
        embed.add_field(name="Note", value=guild_trials[str(user.id)]["note"], inline=True)
    embed.set_thumbnail(url=user.display_avatar.url)

    await interaction.response.send_message(
        embed=embed,
        allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(
    name="freetrial",
    description="Check whether a member has been given a free trial."
)
@app_commands.describe(user="The member to check")
@app_commands.checks.has_permissions(administrator=True)
async def freetrial_command(
    interaction: discord.Interaction,
    user: discord.Member
):
    record = freetrials.get(str(interaction.guild.id), {}).get(str(user.id))

    if record:
        embed = discord.Embed(
            title="✅ Free Trial Already Given",
            description=f"{user.mention} **has** been given a free trial.",
            color=discord.Color.green()
        )
        embed.add_field(name="Given by", value=f"<@{record['given_by']}>", inline=True)
        embed.add_field(
            name="Given on",
            value=f"<t:{record['given_at']}:F>\n(<t:{record['given_at']}:R>)",
            inline=True
        )
        if record.get("note"):
            embed.add_field(name="Note", value=record["note"], inline=False)
    else:
        embed = discord.Embed(
            title="❌ No Free Trial Yet",
            description=f"{user.mention} has **not** been given a free trial.",
            color=discord.Color.red()
        )

    embed.set_thumbnail(url=user.display_avatar.url)

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(
    name="removefreetrial",
    description="Remove a member's free trial record (e.g. if it was given by mistake)."
)
@app_commands.describe(user="The member whose free trial record should be removed")
@app_commands.checks.has_permissions(administrator=True)
async def removefreetrial_command(
    interaction: discord.Interaction,
    user: discord.Member
):
    guild_trials = freetrials.get(str(interaction.guild.id), {})
    record = guild_trials.pop(str(user.id), None)

    if record is None:
        await interaction.response.send_message(
            f"⚠️ {user.mention} doesn't have a free trial on record, so there's nothing to remove.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none()
        )
        return

    save_freetrials(freetrials)

    embed = discord.Embed(
        title="🗑️ Free Trial Removed",
        description=f"{user.mention} no longer has a free trial on record.",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Removed by", value=interaction.user.mention, inline=True)
    embed.add_field(
        name="Originally given",
        value=f"by <@{record['given_by']}> on <t:{record['given_at']}:F>",
        inline=True
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    await interaction.response.send_message(
        embed=embed,
        allowed_mentions=discord.AllowedMentions.none()
    )


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
# USER INFO
# -------------------------

@bot.tree.command(
    name="userinfo",
    description="View detailed info about a member."
)
@app_commands.describe(
    member="The member to look up (defaults to yourself)"
)
@app_commands.checks.has_permissions(administrator=True)
async def userinfo(
    interaction: discord.Interaction,
    member: discord.Member = None
):
    member = member or interaction.user

    guild_id = str(interaction.guild.id)
    user_id = str(member.id)

    embed = discord.Embed(
        title=f"👤 User Info — {member}",
        color=member.color if member.color.value else discord.Color.blurple()
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(
        name="ID",
        value=f"`{member.id}`",
        inline=True
    )

    # Timeout status
    if member.is_timed_out():
        timeout_value = (
            f"Yes, until {discord.utils.format_dt(member.timed_out_until, style='F')} "
            f"({discord.utils.format_dt(member.timed_out_until, style='R')})"
        )
    else:
        timeout_value = "No"

    embed.add_field(
        name="Timed Out",
        value=timeout_value,
        inline=True
    )

    # Warnings on record for this member in this guild
    user_warnings = warnings.get(guild_id, {}).get(user_id, [])
    warning_flag = " 🔇" if len(user_warnings) >= WARNING_MUTE_THRESHOLD else ""
    embed.add_field(
        name="Warnings",
        value=f"{len(user_warnings)}{warning_flag}",
        inline=True
    )

    embed.add_field(
        name="Account Created",
        value=(
            f"{discord.utils.format_dt(member.created_at, style='F')}\n"
            f"({discord.utils.format_dt(member.created_at, style='R')})"
        ),
        inline=False
    )

    if member.joined_at:
        joined_value = (
            f"{discord.utils.format_dt(member.joined_at, style='F')}\n"
            f"({discord.utils.format_dt(member.joined_at, style='R')})"
        )
    else:
        joined_value = "Unknown"

    embed.add_field(
        name="Joined Server",
        value=joined_value,
        inline=False
    )

    # Roles, highest first, excluding @everyone.
    roles = [role.mention for role in reversed(member.roles) if not role.is_default()]
    if roles:
        roles_value = ", ".join(roles)
        if len(roles_value) > 1024:
            roles_value = roles_value[:1000] + "…"
    else:
        roles_value = "None"

    embed.add_field(
        name=f"Roles ({len(roles)})",
        value=roles_value,
        inline=False
    )

    embed.set_footer(text=f"Requested by {interaction.user}")
    embed.timestamp = discord.utils.utcnow()

    await interaction.response.send_message(embed=embed, ephemeral=True)


# -------------------------
# COMMANDS LIST
# -------------------------

@bot.tree.command(
    name="commands",
    description="Get a link to the full list of commands."
)
async def commands_list(interaction: discord.Interaction):
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="View Commands",
        emoji="📖",
        style=discord.ButtonStyle.link,
        url="https://www.echobot.co.uk/commands"
    ))

    await interaction.response.send_message(
        content="📖 Full command list: https://www.echobot.co.uk/commands",
        view=view,
        ephemeral=True
    )


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

    elif isinstance(error, WrongServer):
        message = "❌ This bot is private and only works in its home server."

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
