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
GUILD_ID = 1538122777628778616

WARNINGS_FILE = "warnings.json"

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
# BOT STARTUP
# -------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print("Moderation bot is online!")


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
@app_commands.checks.has_permissions(kick_members=True)
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
@app_commands.checks.has_permissions(ban_members=True)
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
@app_commands.checks.has_permissions(ban_members=True)
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
@app_commands.checks.has_permissions(
    moderate_members=True
)
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
@app_commands.checks.has_permissions(
    moderate_members=True
)
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
@app_commands.checks.has_permissions(
    manage_messages=True
)
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

    try:
        await member.send(
            f"⚠️ You have received a warning in "
            f"**{interaction.guild.name}**.\n\n"
            f"Reason: {reason}\n"
            f"Warning #{total}"
        )
    except discord.Forbidden:
        pass

    await interaction.response.send_message(
        f"⚠️ **{member}** has been warned.\n"
        f"Reason: {reason}\n"
        f"Total warnings: **{total}**"
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
@app_commands.checks.has_permissions(
    manage_messages=True
)
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
# CLEAR WARNINGS
# -------------------------

@bot.tree.command(
    name="clearwarnings",
    description="Clear all warnings for a member."
)
@app_commands.describe(
    member="The member whose warnings to clear"
)
@app_commands.checks.has_permissions(
    manage_messages=True
)
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
@app_commands.checks.has_permissions(
    manage_messages=True
)
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
@app_commands.checks.has_permissions(
    manage_channels=True
)
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
@app_commands.checks.has_permissions(
    manage_channels=True
)
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
