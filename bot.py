import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta
import json
import os
from dotenv import load_dotenv

load_dotenv("/home/container/.env")

TOKEN = os.getenv("DISCORD_TOKEN")

# CHANGE THIS to your Discord SERVER ID
GUILD_ID = 1533843796834390116

WARNINGS_FILE = "warnings.json"
WELCOME_FILE = "welcome_settings.json"
RULES_FILE = "rules_settings.json"
RULES_IMAGE_PATH = "rules.png"  # fallback local image, used if no URL is set

WARNING_MUTE_THRESHOLD = 3  # warnings needed before an auto-mute
WARNING_MUTE_HOURS = 48     # length of that auto-mute

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class ModerationBot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)

        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

        print("Slash commands synced.")


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
# BOT STARTUP
# -------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print("Moderation bot is online!")


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
# LOCK CHANNEL
# -------------------------

@bot.tree.command(
    name="lock",
    description="Lock the current channel."
)
@app_commands.checks.has_permissions(administrator=True)
async def lock(interaction: discord.Interaction):

    channel = interaction.channel

    overwrite = channel.overwrites_for(
        interaction.guild.default_role
    )

    overwrite.send_messages = False

    await channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite
    )

    await interaction.response.send_message(
        "🔒 This channel has been locked."
    )


# -------------------------
# UNLOCK CHANNEL
# -------------------------

@bot.tree.command(
    name="unlock",
    description="Unlock the current channel."
)
@app_commands.checks.has_permissions(administrator=True)
async def unlock(interaction: discord.Interaction):

    channel = interaction.channel

    overwrite = channel.overwrites_for(
        interaction.guild.default_role
    )

    overwrite.send_messages = None

    await channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite
    )

    await interaction.response.send_message(
        "🔓 This channel has been unlocked."
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
            "`/clear` — Bulk delete messages\n"
            "`/lock` — Lock the current channel\n"
            "`/unlock` — Unlock the current channel"
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
        name="📜 Rules",
        value=(
            "`/postrules` — Post the rules & guidelines embed\n"
            "`/setrulesimage` — Set the rules embed image via URL"
        ),
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
