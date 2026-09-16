import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta, datetime
import io
import json
import os
from dotenv import load_dotenv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

load_dotenv("/home/container/.env")

TOKEN = os.getenv("DISCORD_TOKEN")

WARNINGS_FILE = "warnings.json"
WELCOME_FILE = "welcome_settings.json"
LEAVE_FILE = "leave_settings.json"
RULES_FILE = "rules_settings.json"
ACTIVITY_FILE = "activity.json"
RULES_IMAGE_PATH = "rules.png"  # fallback local image, used if no URL is set

WARNING_MUTE_THRESHOLD = 3  # warnings needed before an auto-mute
WARNING_MUTE_HOURS = 48     # length of that auto-mute

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True  # needed to track voice activity for /me


class ModerationBot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def setup_hook(self):
        await self.tree.sync()

        print("Global slash commands synced.")


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


# -------------------------
# RULES SETTINGS
# -------------------------

def load_rules_settings():
    if not os.path.exists(RULES_FILE):
        return {}

    try:
        with open(RULES_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_rules_settings(data):
    with open(RULES_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


rules_settings = load_rules_settings()


def get_guild_rules_settings(guild_id: str):
    """Return this guild's rules config, creating defaults if missing."""
    if guild_id not in rules_settings:
        rules_settings[guild_id] = {
            "image_url": None
        }
        save_rules_settings(rules_settings)

    return rules_settings[guild_id]


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
# ACTIVITY TRACKING (for /me)
# -------------------------

ACTIVITY_KEEP_DAYS = 35     # how long daily buckets are kept before pruning
ACTIVITY_WINDOW_DAYS = 14   # the lookback window shown in /me


def load_activity():
    if not os.path.exists(ACTIVITY_FILE):
        return {}

    try:
        with open(ACTIVITY_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_activity(data):
    with open(ACTIVITY_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


activity = load_activity()

# In-memory only: tracks who is currently in a voice channel and when
# they joined it, so we can measure elapsed time when they leave.
# Not persisted - a bot restart just ends any sessions in progress.
voice_sessions = {}


def get_today_key() -> str:
    return discord.utils.utcnow().date().isoformat()


def get_guild_user_activity(guild_id: str, user_id: str) -> dict:
    guild_bucket = activity.setdefault(guild_id, {})
    return guild_bucket.setdefault(user_id, {"daily": {}})


def get_today_bucket(guild_id: str, user_id: str) -> dict:
    user_bucket = get_guild_user_activity(guild_id, user_id)
    today_key = get_today_key()
    return user_bucket["daily"].setdefault(
        today_key,
        {
            "messages": 0,
            "channels": {},
            "voice_seconds": 0,
            "voice_channels": {}
        }
    )


def prune_old_activity(guild_id: str, user_id: str):
    user_bucket = get_guild_user_activity(guild_id, user_id)
    cutoff = discord.utils.utcnow().date() - timedelta(days=ACTIVITY_KEEP_DAYS)
    daily = user_bucket["daily"]

    for key in list(daily.keys()):
        try:
            day = datetime.fromisoformat(key).date()
        except ValueError:
            del daily[key]
            continue

        if day < cutoff:
            del daily[key]


def record_message(guild_id: str, user_id: str, channel_id: str):
    bucket = get_today_bucket(guild_id, user_id)
    bucket["messages"] += 1
    bucket["channels"][channel_id] = bucket["channels"].get(channel_id, 0) + 1
    prune_old_activity(guild_id, user_id)
    save_activity(activity)


def record_voice(guild_id: str, user_id: str, channel_id: str, seconds: float):
    if seconds <= 0:
        return

    bucket = get_today_bucket(guild_id, user_id)
    bucket["voice_seconds"] += seconds
    bucket["voice_channels"][channel_id] = (
        bucket["voice_channels"].get(channel_id, 0) + seconds
    )
    prune_old_activity(guild_id, user_id)
    save_activity(activity)


def get_window_stats(guild_id: str, user_id: str, days: int) -> dict:
    """Totals for this user over the last `days` days, plus a per-channel
    breakdown used to find their top text/voice channels."""
    user_bucket = get_guild_user_activity(guild_id, user_id)
    daily = user_bucket["daily"]
    today = discord.utils.utcnow().date()

    total_messages = 0
    total_voice_seconds = 0.0
    channels = {}
    voice_channels = {}

    for i in range(days):
        key = (today - timedelta(days=i)).isoformat()
        bucket = daily.get(key)
        if not bucket:
            continue

        total_messages += bucket.get("messages", 0)
        total_voice_seconds += bucket.get("voice_seconds", 0)

        for channel_id, count in bucket.get("channels", {}).items():
            channels[channel_id] = channels.get(channel_id, 0) + count

        for channel_id, seconds in bucket.get("voice_channels", {}).items():
            voice_channels[channel_id] = (
                voice_channels.get(channel_id, 0) + seconds
            )

    return {
        "messages": total_messages,
        "voice_seconds": total_voice_seconds,
        "channels": channels,
        "voice_channels": voice_channels
    }


def get_daily_series(guild_id: str, user_id: str, days: int):
    """Ordered (oldest -> newest) per-day message/voice-hours series,
    used to draw the activity chart."""
    user_bucket = get_guild_user_activity(guild_id, user_id)
    daily = user_bucket["daily"]
    today = discord.utils.utcnow().date()

    dates = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    messages = []
    voice_hours = []

    for day in dates:
        bucket = daily.get(day.isoformat(), {})
        messages.append(bucket.get("messages", 0))
        voice_hours.append(round(bucket.get("voice_seconds", 0) / 3600, 2))

    return dates, messages, voice_hours


def get_top_channel(channel_totals: dict):
    if not channel_totals:
        return None, 0

    top_id = max(channel_totals, key=channel_totals.get)
    return top_id, channel_totals[top_id]


def get_activity_rank(guild_id: str, metric: str, user_id: str, days: int):
    """1-based rank of user_id among everyone in this guild with recorded
    activity for the given metric ('messages' or 'voice'). Returns None
    if the user has no recorded activity for that metric."""
    guild_bucket = activity.get(guild_id, {})
    totals = {}

    for uid in guild_bucket:
        stats = get_window_stats(guild_id, uid, days)
        value = stats["messages"] if metric == "messages" else stats["voice_seconds"]
        if value > 0:
            totals[uid] = value

    if user_id not in totals:
        return None

    ranked = sorted(totals, key=totals.get, reverse=True)
    return ranked.index(user_id) + 1


def format_hours(seconds: float) -> str:
    hours = seconds / 3600
    if hours == int(hours):
        return f"{int(hours)} hours"
    return f"{hours:.1f} hours"


def build_activity_chart(guild_id: str, user_id: str, days: int = ACTIVITY_WINDOW_DAYS):
    dates, messages, voice_hours = get_daily_series(guild_id, user_id, days)

    if not any(messages) and not any(voice_hours):
        return None

    fig, ax = plt.subplots(figsize=(6, 2.2), dpi=150)
    fig.patch.set_facecolor("#2b2d31")
    ax.set_facecolor("#2b2d31")

    ax.plot(dates, messages, color="#3ba55d", linewidth=2, label="Messages")
    ax.plot(dates, voice_hours, color="#eb459e", linewidth=2, label="Voice (hrs)")

    ax.tick_params(colors="#b5bac1", labelsize=7)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(color="#3f4147", linewidth=0.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.legend(
        loc="upper left",
        facecolor="#2b2d31",
        edgecolor="none",
        labelcolor="#dbdee1",
        fontsize=7
    )
    fig.autofmt_xdate(rotation=0, ha="center")

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)

    return discord.File(buf, filename="activity_chart.png")


# -------------------------
# BOT STARTUP
# -------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print("Moderation bot is online!")


# -------------------------
# ACTIVITY EVENTS (for /me)
# -------------------------

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        await bot.process_commands(message)
        return

    record_message(
        str(message.guild.id),
        str(message.author.id),
        str(message.channel.id)
    )

    await bot.process_commands(message)


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState
):
    if member.bot:
        return

    guild_id = str(member.guild.id)
    user_id = str(member.id)
    key = (guild_id, user_id)
    now = discord.utils.utcnow()

    # Left a channel (including switching channels) - close out the
    # session that was in progress and log the elapsed time.
    if before.channel is not None and key in voice_sessions:
        session = voice_sessions.pop(key)
        elapsed = (now - session["start"]).total_seconds()
        record_voice(guild_id, user_id, session["channel_id"], elapsed)

    # Joined a channel (including switching channels) - start a new session.
    if after.channel is not None:
        voice_sessions[key] = {
            "channel_id": str(after.channel.id),
            "start": now
        }


# -------------------------
# WELCOME EVENT
# -------------------------

@bot.event
async def on_member_join(member: discord.Member):
    guild_id = str(member.guild.id)
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
# WELCOME: SET CHANNEL
# -------------------------

@bot.tree.command(
    name="welcome-setchannel",
    description="Set the channel where welcome messages are sent."
)
@app_commands.describe(
    channel="The channel to send welcome messages in"
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_setchannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_welcome_settings(guild_id)

    settings["channel_id"] = channel.id
    save_welcome_settings(welcome_settings)

    await interaction.response.send_message(
        f"✅ Welcome messages will now be sent in {channel.mention}.",
        ephemeral=True
    )


# -------------------------
# WELCOME: SET MESSAGE
# -------------------------

@bot.tree.command(
    name="welcome-setmessage",
    description="Set the welcome message text."
)
@app_commands.describe(
    message=(
        "Welcome text. Placeholders: {member} {name} {server} {count} "
        "{rules} {socials}. Use \\n for new lines."
    )
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_setmessage(
    interaction: discord.Interaction,
    message: str
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_welcome_settings(guild_id)

    # allow literal \n typed by the user to become real newlines
    settings["message"] = message.replace("\\n", "\n")
    save_welcome_settings(welcome_settings)

    await interaction.response.send_message(
        "✅ Welcome message updated. Use `/welcome-test` to preview it.",
        ephemeral=True
    )


# -------------------------
# WELCOME: SET IMAGE/GIF
# -------------------------

@bot.tree.command(
    name="welcome-setimage",
    description="Set the image/gif shown at the bottom of the welcome embed."
)
@app_commands.describe(
    url="Direct URL to an image or gif (leave blank to remove it)"
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_setimage(
    interaction: discord.Interaction,
    url: str = None
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_welcome_settings(guild_id)

    settings["image_url"] = url
    save_welcome_settings(welcome_settings)

    if url:
        await interaction.response.send_message(
            "✅ Welcome image/gif updated.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "✅ Welcome image/gif removed.",
            ephemeral=True
        )


# -------------------------
# WELCOME: SET RULES CHANNEL
# -------------------------

@bot.tree.command(
    name="welcome-setruleschannel",
    description="Set the channel linked wherever {rules} is used in the welcome message."
)
@app_commands.describe(
    channel="The channel to link (e.g. your #rules channel)"
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_setruleschannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_welcome_settings(guild_id)

    settings["rules_channel_id"] = channel.id
    save_welcome_settings(welcome_settings)

    await interaction.response.send_message(
        f"✅ {channel.mention} will now show up anywhere your welcome "
        f"message uses `{{rules}}`.",
        ephemeral=True
    )


# -------------------------
# WELCOME: SET SOCIALS
# -------------------------

@bot.tree.command(
    name="welcome-setsocials",
    description="Set your socials link used in the {socials} placeholder."
)
@app_commands.describe(
    url="Link to your socials (leave blank to remove it)"
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_setsocials(
    interaction: discord.Interaction,
    url: str = None
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_welcome_settings(guild_id)

    settings["socials"] = url
    save_welcome_settings(welcome_settings)

    if url:
        await interaction.response.send_message(
            f"✅ Socials link set to {url}. It'll show up anywhere your "
            f"welcome message uses `{{socials}}`.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "✅ Socials link removed.",
            ephemeral=True
        )


# -------------------------
# WELCOME: TOGGLE ON/OFF
# -------------------------

@bot.tree.command(
    name="welcome-toggle",
    description="Enable or disable welcome messages."
)
@app_commands.describe(
    enabled="True to enable, False to disable"
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_toggle(
    interaction: discord.Interaction,
    enabled: bool
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_welcome_settings(guild_id)

    settings["enabled"] = enabled
    save_welcome_settings(welcome_settings)

    state = "enabled" if enabled else "disabled"
    await interaction.response.send_message(
        f"✅ Welcome messages are now **{state}**.",
        ephemeral=True
    )


# -------------------------
# WELCOME: RESET TO DEFAULT
# -------------------------

@bot.tree.command(
    name="welcome-reset",
    description="Reset the welcome message, image, and socials back to default."
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_reset(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_welcome_settings(guild_id)

    settings["message"] = DEFAULT_WELCOME_MESSAGE
    settings["image_url"] = DEFAULT_WELCOME_IMAGE
    settings["socials"] = DEFAULT_WELCOME_SOCIALS
    save_welcome_settings(welcome_settings)

    await interaction.response.send_message(
        "✅ Welcome message, image/gif, and socials have been reset to "
        "default. Your welcome channel and enabled/disabled state were "
        "left untouched. Use `/welcome-test` to preview.",
        ephemeral=True
    )


# -------------------------
# WELCOME: TEST / PREVIEW
# -------------------------

@bot.tree.command(
    name="welcome-test",
    description="Preview the current welcome message."
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_test(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_welcome_settings(guild_id)

    embed = build_welcome_embed(interaction.user, settings)

    await interaction.response.send_message(
        content=f"Welcome, {interaction.user.mention}!",
        embed=embed
    )


# -------------------------
# WELCOME: VIEW SETTINGS
# -------------------------

@bot.tree.command(
    name="welcome-settings",
    description="View the current welcome message configuration."
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_settings_cmd(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    settings = get_guild_welcome_settings(guild_id)

    channel_id = settings.get("channel_id")
    channel = interaction.guild.get_channel(channel_id) if channel_id else None

    rules_channel_id = settings.get("rules_channel_id")
    rules_channel = (
        interaction.guild.get_channel(rules_channel_id)
        if rules_channel_id else None
    )

    embed = discord.Embed(
        title="Welcome Message Settings",
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
        name="Rules Channel",
        value=rules_channel.mention if rules_channel else "Not set",
        inline=True
    )
    embed.add_field(
        name="Image/GIF",
        value=settings.get("image_url") or "Not set",
        inline=False
    )
    embed.add_field(
        name="Socials",
        value=settings.get("socials") or "Not set",
        inline=False
    )
    embed.add_field(
        name="Message",
        value=f"```{settings.get('message', DEFAULT_WELCOME_MESSAGE)}```",
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


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
# RULES
# -------------------------

@bot.tree.command(
    name="setrulesimage",
    description="Set the image shown in the rules embed via a URL."
)
@app_commands.describe(
    url="Direct URL to an image or gif (leave blank to use the local rules.png instead)"
)
@app_commands.checks.has_permissions(administrator=True)
async def setrulesimage(
    interaction: discord.Interaction,
    url: str = None
):
    guild_id = str(interaction.guild.id)
    settings = get_guild_rules_settings(guild_id)

    settings["image_url"] = url
    save_rules_settings(rules_settings)

    if url:
        await interaction.response.send_message(
            "✅ Rules image updated. Use `/postrules` to see it.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "✅ Rules image URL cleared. I'll fall back to the local "
            "`rules.png` file if it exists.",
            ephemeral=True
        )


@bot.tree.command(
    name="postrules",
    description="Post the server rules & guidelines embed."
)
@app_commands.describe(
    channel="Channel to post the rules in (defaults to this channel)"
)
@app_commands.checks.has_permissions(administrator=True)
async def postrules(
    interaction: discord.Interaction,
    channel: discord.TextChannel = None
):
    target_channel = channel or interaction.channel

    guild_id = str(interaction.guild.id)
    settings = get_guild_rules_settings(guild_id)

    embed = discord.Embed(
        color=discord.Color.from_str("#5B1118"),
        title="☆・RULES & GUIDELINES・☆",
        description=(
            "Please be sure to follow all rules and guidelines\n"
            "to avoid being kicked from the server.\n\n"
            "*+*─────☾ ♀ ⚜ ♂ ☽─────*+*\n\n"
            "**1. Be respectful to everyone.** Treat all\n"
            "members with kindness. No harassment,\n"
            "hate speech, racism, or personal attacks.\n\n"
            "**2. No spam or self-promotion.** Don't flood\n"
            "chat, mass-ping, or advertise other servers\n"
            "without permission.\n\n"
            "**3. Use the right channels.** Post content where\n"
            "it belongs and keep conversations on topic.\n\n"
            "**4. No drama or witch-hunting.** Keep\n"
            "disagreements civil and take serious issues\n"
            "to a moderator.\n\n"
            "**5. Listen to the staff team.** Moderators have\n"
            "the final say. Don't argue with decisions in\n"
            "public channels.\n\n"
            "**6. Follow Discord's Terms of Service and\n"
            "Community Guidelines at all times.**\n\n"
            "**8. Be welcoming and have fun.** Help new\n"
            "members feel at home.\n\n"
            "*+*─────☾ ♀ ⚜ ♂ ☽─────*+*\n\n"
            "By staying in this server, you agree to follow\n"
            "these rules."
        )
    )

    embed.set_footer(
        text=(
            "Rules created on "
            f"{discord.utils.utcnow().strftime('%a, %b %d, %Y %I:%M %p')}"
        )
    )
    embed.timestamp = discord.utils.utcnow()

    image_url = settings.get("image_url")
    file = None

    if image_url:
        # A URL was set with /setrulesimage - use it directly, no attachment needed.
        embed.set_image(url=image_url)
    elif os.path.exists(RULES_IMAGE_PATH):
        # Fall back to a local rules.png sitting next to main.py.
        file = discord.File(RULES_IMAGE_PATH, filename="rules.png")
        embed.set_image(url="attachment://rules.png")

    await interaction.response.defer(ephemeral=True)

    try:
        if file:
            await target_channel.send(embed=embed, file=file)
        else:
            await target_channel.send(embed=embed)

        await interaction.followup.send(
            f"✅ Rules posted in {target_channel.mention}.",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I don't have permission to send messages in that channel.",
            ephemeral=True
        )


# -------------------------
# ME (ACTIVITY STATS)
# -------------------------

@bot.tree.command(
    name="me",
    description="View your (or someone else's) server activity stats."
)
@app_commands.describe(
    member="Whose stats to show (defaults to you)"
)
async def me(
    interaction: discord.Interaction,
    member: discord.Member = None
):
    target = member or interaction.user
    guild_id = str(interaction.guild.id)
    user_id = str(target.id)

    await interaction.response.defer()

    stats_1d = get_window_stats(guild_id, user_id, 1)
    stats_7d = get_window_stats(guild_id, user_id, 7)
    stats_14d = get_window_stats(guild_id, user_id, ACTIVITY_WINDOW_DAYS)

    message_rank = get_activity_rank(
        guild_id, "messages", user_id, ACTIVITY_WINDOW_DAYS
    )
    voice_rank = get_activity_rank(
        guild_id, "voice", user_id, ACTIVITY_WINDOW_DAYS
    )

    top_channel_id, top_channel_count = get_top_channel(stats_14d["channels"])
    top_voice_id, top_voice_seconds = get_top_channel(stats_14d["voice_channels"])

    embed = discord.Embed(color=discord.Color.dark_theme())
    embed.set_author(
        name=f"{target.display_name}  ({target})",
        icon_url=target.display_avatar.url
    )
    embed.set_thumbnail(url=target.display_avatar.url)

    embed.add_field(
        name="Created On",
        value=target.created_at.strftime("%d %B %Y"),
        inline=True
    )
    embed.add_field(
        name="Joined On",
        value=(
            target.joined_at.strftime("%d %B %Y")
            if isinstance(target, discord.Member) and target.joined_at
            else "Unknown"
        ),
        inline=True
    )

    embed.add_field(
        name="🏆 Server Ranks",
        value=(
            f"**Message:** {f'#{message_rank}' if message_rank else 'No Data'}\n"
            f"**Voice:** {f'#{voice_rank}' if voice_rank else 'No Data'}"
        ),
        inline=False
    )

    embed.add_field(
        name="# Messages",
        value=(
            f"**1d:** {stats_1d['messages']} messages\n"
            f"**7d:** {stats_7d['messages']} messages\n"
            f"**14d:** {stats_14d['messages']} messages"
        ),
        inline=True
    )

    embed.add_field(
        name="🔊 Voice Activity",
        value=(
            f"**1d:** {format_hours(stats_1d['voice_seconds'])}\n"
            f"**7d:** {format_hours(stats_7d['voice_seconds'])}\n"
            f"**14d:** {format_hours(stats_14d['voice_seconds'])}"
        ),
        inline=True
    )

    top_text_line = "No data"
    if top_channel_id:
        channel_obj = interaction.guild.get_channel(int(top_channel_id))
        name = channel_obj.mention if channel_obj else f"#{top_channel_id}"
        top_text_line = f"{name} — {top_channel_count} messages"

    top_voice_line = "No data"
    if top_voice_id:
        channel_obj = interaction.guild.get_channel(int(top_voice_id))
        name = channel_obj.mention if channel_obj else f"#{top_voice_id}"
        top_voice_line = f"{name} — {format_hours(top_voice_seconds)}"

    embed.add_field(
        name="📈 Top Channels",
        value=f"**Text:** {top_text_line}\n**Voice:** {top_voice_line}",
        inline=False
    )

    chart_file = build_activity_chart(guild_id, user_id)
    if chart_file:
        embed.set_image(url="attachment://activity_chart.png")

    embed.set_footer(
        text=f"Server Lookback: Last {ACTIVITY_WINDOW_DAYS} days"
    )
    embed.timestamp = discord.utils.utcnow()

    if chart_file:
        await interaction.followup.send(embed=embed, file=chart_file)
    else:
        await interaction.followup.send(embed=embed)


# -------------------------
# COMMANDS LIST
# -------------------------

@bot.tree.command(
    name="commands",
    description="Show a list of all available commands."
)
async def commands_list(interaction: discord.Interaction):

    embed = discord.Embed(
        title="📖 Command List",
        description="Here's everything I can do!",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🛡️ Moderation",
        value=(
            "`/kick` — Kick a member\n"
            "`/ban` — Ban a member\n"
            "`/unban` — Unban a user by ID\n"
            "`/timeout` — Timeout a member\n"
            "`/untimeout` — Remove a member's timeout\n"
            "`/warn` — Warn a member\n"
            "`/warnings` — View a member's warnings\n"
            "`/viewwarnings` — View everyone with warnings server-wide\n"
            "`/clearwarnings` — Clear a member's warnings\n"
            "`/clear` — Bulk delete messages"
        ),
        inline=False
    )

    embed.add_field(
        name="👋 Welcome Messages",
        value=(
            "`/welcome-setchannel` — Set the welcome channel\n"
            "`/welcome-setmessage` — Set the welcome text\n"
            "`/welcome-setimage` — Set the welcome gif/image\n"
            "`/welcome-setruleschannel` — Set the {rules} channel link\n"
            "`/welcome-setsocials` — Set your socials link\n"
            "`/welcome-toggle` — Enable/disable welcome messages\n"
            "`/welcome-test` — Preview the welcome message\n"
            "`/welcome-settings` — View current welcome config\n"
            "`/welcome-reset` — Reset welcome settings to default"
        ),
        inline=False
    )

    embed.add_field(
        name="🚪 Leave Messages",
        value=(
            "`/leave-setchannel` — Set the leave channel\n"
            "`/leave-setmessage` — Set the leave text\n"
            "`/leave-setimage` — Set the leave gif/image\n"
            "`/leave-toggle` — Enable/disable leave messages\n"
            "`/leave-test` — Preview the leave message\n"
            "`/leave-settings` — View current leave config\n"
            "`/leave-reset` — Reset leave settings to default"
        ),
        inline=False
    )

    embed.add_field(
        name="📜 Rules",
        value=(
            "`/postrules` — Post the rules & guidelines embed\n"
            "`/setrulesimage` — Set the rules embed image via URL"
        ),
        inline=False
    )

    embed.add_field(
        name="📊 Activity",
        value="`/me` — View your (or someone else's) activity stats",
        inline=False
    )

    embed.add_field(
        name="ℹ️ Other",
        value="`/commands` — Show this list",
        inline=False
    )

    embed.set_footer(
        text=(
            "All commands require the Administrator permission. "
            f"{WARNING_MUTE_THRESHOLD} warnings = auto-mute for "
            f"{WARNING_MUTE_HOURS} hours."
        )
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


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
