"""Welcome & goodbye messages (replaces Maki's module).

Requires the Server Members privileged intent (Discord dev portal -> Bot ->
Server Members Intent). Without it these listeners simply never fire; the
rest of the bot is unaffected.

/setwelcome [channel]  — post welcome cards there (Manage Server)
/setgoodbye [channel]  — post leave messages there (Manage Server)
"""
from __future__ import annotations

import asyncio
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

# Font candidates per platform; falls back to PIL's default if none exist.
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # Ubuntu
    "C:/Windows/Fonts/arialbd.ttf",                           # Windows dev
]

CARD_W, CARD_H = 1000, 560
_AVATAR = 260

# Channel where /pyq is pointed in the welcome checklist (NITC study-materials channel).
PYQ_CHANNEL_ID = 1411342416170057738


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _load_font(size: int):
    from PIL import ImageFont

    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _render_card(avatar_png: bytes, username: str) -> bytes:
    """Dark card: circular avatar on top, WELCOME headline, username under it."""
    from PIL import Image, ImageDraw, ImageOps

    # Transparent background so the card blends into Discord's message area
    # (matches Maki) instead of sitting in a visible grey box.
    card = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    # Circular avatar with a subtle ring, centered horizontally.
    avatar = Image.open(io.BytesIO(avatar_png)).convert("RGBA").resize((_AVATAR, _AVATAR))
    mask = Image.new("L", (_AVATAR, _AVATAR), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, _AVATAR, _AVATAR), fill=255)
    avatar = ImageOps.fit(avatar, (_AVATAR, _AVATAR))
    ax, ay = (CARD_W - _AVATAR) // 2, 55
    ring_pad = 8
    draw.ellipse(
        (ax - ring_pad, ay - ring_pad, ax + _AVATAR + ring_pad, ay + _AVATAR + ring_pad),
        outline=(255, 255, 255, 230), width=6,
    )
    card.paste(avatar, (ax, ay), mask)

    # Headline + username, centered.
    head_font = _load_font(120)
    name_font = _load_font(48)
    head = "WELCOME"
    hw = draw.textlength(head, font=head_font)
    draw.text(((CARD_W - hw) / 2, ay + _AVATAR + 30), head, font=head_font, fill=(255, 255, 255, 255))
    name = username[:32]
    nw = draw.textlength(name, font=name_font)
    draw.text(((CARD_W - nw) / 2, ay + _AVATAR + 175), name, font=name_font, fill=(220, 220, 220, 255))

    out = io.BytesIO()
    card.save(out, format="PNG")  # keep alpha channel for transparency
    return out.getvalue()


def _find_channel(guild: discord.Guild, *keywords: str) -> discord.TextChannel | None:
    """First text channel whose name contains any keyword (handles fancy unicode names)."""
    for ch in guild.text_channels:
        low = ch.name.lower()
        if any(k in low for k in keywords):
            return ch
    return None


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── events ────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        row = await self.bot.db.get_guild(member.guild.id)
        if not row or not row["welcome_channel"]:
            return
        channel = member.guild.get_channel(row["welcome_channel"])
        if channel is None:
            return

        lines = ["## 👋 Welcome " + member.mention + " to **NITC** 🎓", "", "📌 **Start here:**"]
        for emoji, verb, ch in [
            ("📜", "Read", _find_channel(member.guild, "rules")),
            ("🎭", "Choose roles in", _find_channel(member.guild, "self-roles", "self roles")),
            ("📢", "Check", _find_channel(member.guild, "announcement")),
            ("🗣️", "Say hi in", _find_channel(member.guild, "general")),
        ]:
            if ch:
                lines.append(f"- {emoji} {verb} {ch.mention}")
        lines.append(f"- 📝 Need past papers? Try **/pyq** in <#{PYQ_CHANNEL_ID}>")
        lines.append("")
        lines.append(f"You are the **{_ordinal(member.guild.member_count)} member** of our community!")

        file = None
        try:
            avatar_png = await member.display_avatar.replace(size=256, format="png").read()
            card = await asyncio.to_thread(_render_card, avatar_png, member.display_name)
            file = discord.File(io.BytesIO(card), filename="welcome.png")
        except Exception as e:  # noqa: BLE001 — card is decoration, never block the welcome
            log.warning("Welcome card render failed: %s", e)

        try:
            await channel.send(content="\n".join(lines), file=file)
        except discord.DiscordException as e:
            log.warning("Welcome message failed: %s", e)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        row = await self.bot.db.get_guild(member.guild.id)
        if not row or not row["goodbye_channel"]:
            return
        channel = member.guild.get_channel(row["goodbye_channel"])
        if channel is None:
            return
        msg = (
            f"👋 **{member.display_name}** has just left the server.\n"
            f"🪪 User ID: {member.id}\n"
            f"📊 We now have **{member.guild.member_count} members** in the community."
        )
        try:
            await channel.send(msg)
        except discord.DiscordException as e:
            log.warning("Goodbye message failed: %s", e)

    # ── commands ──────────────────────────────────────────
    @app_commands.command(name="setwelcome", description="Post welcome messages in a channel (or here).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setwelcome(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        target = channel or interaction.channel
        await self.bot.db.set_guild_channel(interaction.guild_id, "welcome_channel", target.id)
        note = ""
        if not interaction.guild.me.guild_permissions.attach_files:
            note = " (I'll need the Attach Files permission there for the welcome card image.)"
        await interaction.response.send_message(
            f"✅ Welcome messages will be posted in {target.mention}.{note}", ephemeral=True
        )

    @app_commands.command(name="setgoodbye", description="Post goodbye messages in a channel (or here).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setgoodbye(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        target = channel or interaction.channel
        await self.bot.db.set_guild_channel(interaction.guild_id, "goodbye_channel", target.id)
        await interaction.response.send_message(
            f"✅ Goodbye messages will be posted in {target.mention}.", ephemeral=True
        )

    @setwelcome.error
    @setgoodbye.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission for this.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
