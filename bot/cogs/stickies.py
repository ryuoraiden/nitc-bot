"""Persistent bottom-of-channel messages, inspired by StickyBot.

The sticky is re-posted after either the configured number of new messages or
the configured amount of time has elapsed (checked when a new message arrives).
Only message metadata is used, so Discord's privileged Message Content intent
is not required.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Literal
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


def should_repost(
    message_count: int,
    elapsed_seconds: float,
    every_messages: int,
    after_seconds: int,
) -> bool:
    """Return whether either enabled repost threshold has been reached."""
    by_count = every_messages > 0 and message_count >= every_messages
    by_time = after_seconds > 0 and elapsed_seconds >= after_seconds
    return by_count or by_time


def _valid_image_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _embed_for(row) -> discord.Embed:
    embed = discord.Embed(description=row["content"], color=0xF2B84B)
    if row["image_url"]:
        embed.set_image(url=row["image_url"])
    embed.set_footer(text="Pinned reminder")
    return embed


async def _send_sticky(channel, row) -> discord.Message:
    kwargs = {"allowed_mentions": discord.AllowedMentions.none()}
    if row["style"] == "embed":
        return await channel.send(embed=_embed_for(row), **kwargs)
    return await channel.send(row["content"], **kwargs)


async def _delete_message(channel, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        message = channel.get_partial_message(message_id)
        await message.delete()
    except (discord.NotFound, discord.Forbidden):
        pass
    except discord.HTTPException:
        log.warning("Could not delete old sticky message %s", message_id, exc_info=True)


class Stickies(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Channels that have a sticky row; keeps on_message from paying a DB
        # query (and a Lock allocation) for every message server-wide.
        self._sticky_channels: set[int] = set()

    async def cog_load(self) -> None:
        self._sticky_channels = await self.bot.db.all_sticky_channel_ids()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        if message.channel.id not in self._sticky_channels:
            return

        async with self._locks[message.channel.id]:
            row = await self.bot.db.get_sticky(message.channel.id)
            if row is None or not row["active"]:
                return

            count = row["message_count"] + 1
            elapsed = max(0.0, time.time() - row["last_posted_at"])
            if not should_repost(count, elapsed, row["every_messages"], row["after_seconds"]):
                await self.bot.db.set_sticky_message_count(message.channel.id, count)
                return

            try:
                posted = await _send_sticky(message.channel, row)
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Could not repost sticky in channel %s", message.channel.id, exc_info=True)
                await self.bot.db.set_sticky_message_count(message.channel.id, count)
                return

            await self.bot.db.mark_sticky_posted(message.channel.id, posted.id, time.time())
            await _delete_message(message.channel, row["last_message_id"])

    @app_commands.command(name="stick", description="Keep a message at the bottom of this channel.")
    @app_commands.describe(
        message="Text to keep visible (mentions are displayed but never re-pinged)",
        style="Post as plain text or an embed",
        image_url="Optional image URL (embed style only)",
        every_messages="Repost after this many messages; 0 disables this trigger",
        after_seconds="Repost on the next message after this many seconds; 0 disables",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def stick(
        self,
        interaction: discord.Interaction,
        message: app_commands.Range[str, 1, 1900],
        style: Literal["plain", "embed"] = "plain",
        image_url: str | None = None,
        every_messages: app_commands.Range[int, 0, 50] = 5,
        after_seconds: app_commands.Range[int, 0, 3600] = 15,
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Use this command in a server channel.", ephemeral=True)
            return
        if every_messages == 0 and after_seconds == 0:
            await interaction.response.send_message(
                "Enable at least one trigger: messages or seconds.", ephemeral=True
            )
            return
        if image_url and (style != "embed" or not _valid_image_url(image_url)):
            await interaction.response.send_message(
                "An image needs **embed** style and a full `http://` or `https://` URL.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.channel
        # Hold the channel lock so an in-flight on_message repost can't
        # interleave and orphan one of the sticky messages.
        async with self._locks[channel.id]:
            old = await self.bot.db.get_sticky(channel.id)
            preview = {
                "content": message,
                "style": style,
                "image_url": image_url,
            }
            try:
                posted = await _send_sticky(channel, preview)
            except discord.Forbidden:
                await interaction.followup.send(
                    "I need **View Channel**, **Send Messages**, and **Embed Links** here.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException:
                await interaction.followup.send("Discord rejected the sticky message.", ephemeral=True)
                return

            await self.bot.db.upsert_sticky(
                guild_id=interaction.guild.id,
                channel_id=channel.id,
                content=message,
                style=style,
                image_url=image_url,
                every_messages=every_messages,
                after_seconds=after_seconds,
                last_message_id=posted.id,
                last_posted_at=time.time(),
                created_by=interaction.user.id,
            )
            self._sticky_channels.add(channel.id)
            if old:
                await _delete_message(channel, old["last_message_id"])
        await interaction.followup.send(
            f"✅ Sticky enabled: every **{every_messages or '—'}** messages or after "
            f"**{after_seconds or '—'}s**.",
            ephemeral=True,
        )

    @app_commands.command(name="stickstop", description="Pause this channel's sticky.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def stickstop(self, interaction: discord.Interaction) -> None:
        changed = await self.bot.db.set_sticky_active(interaction.channel_id, False)
        text = "✅ Sticky paused. Use `/stickstart` to resume it." if changed else "This channel has no sticky."
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="stickstart", description="Resume this channel's paused sticky.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def stickstart(self, interaction: discord.Interaction) -> None:
        row = await self.bot.db.get_sticky(interaction.channel_id)
        if row is None:
            await interaction.response.send_message("This channel has no saved sticky.", ephemeral=True)
            return
        await self.bot.db.set_sticky_active(interaction.channel_id, True)
        await interaction.response.send_message("✅ Sticky resumed.", ephemeral=True)

    @app_commands.command(name="stickremove", description="Delete this channel's sticky configuration.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def stickremove(self, interaction: discord.Interaction) -> None:
        async with self._locks[interaction.channel_id]:
            row = await self.bot.db.get_sticky(interaction.channel_id)
            if row is None:
                await interaction.response.send_message("This channel has no sticky.", ephemeral=True)
                return
            await self.bot.db.delete_sticky(interaction.channel_id)
            self._sticky_channels.discard(interaction.channel_id)
            if interaction.channel:
                await _delete_message(interaction.channel, row["last_message_id"])
        await interaction.response.send_message("✅ Sticky removed completely.", ephemeral=True)

    @app_commands.command(name="stickies", description="List this server's sticky messages.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def stickies(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.list_stickies(interaction.guild_id)
        if not rows:
            await interaction.response.send_message("This server has no stickies.", ephemeral=True)
            return
        lines = []
        total = 0
        for row in rows[:25]:
            state = "active" if row["active"] else "paused"
            excerpt = discord.utils.escape_markdown(row["content"].replace("\n", " ")[:70])
            line = f"<#{row['channel_id']}> · **{state}** · {excerpt}"
            if total + len(line) + 1 > 1900:  # stay under Discord's 2000-char limit
                lines.append(f"…and {len(rows) - len(lines)} more.")
                break
            lines.append(line)
            total += len(line) + 1
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="stickspeed", description="View or change this channel's sticky speed.")
    @app_commands.describe(
        every_messages="Repost after this many messages; 0 disables",
        after_seconds="Repost on the next message after this many seconds; 0 disables",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def stickspeed(
        self,
        interaction: discord.Interaction,
        every_messages: app_commands.Range[int, 0, 50] | None = None,
        after_seconds: app_commands.Range[int, 0, 3600] | None = None,
    ) -> None:
        row = await self.bot.db.get_sticky(interaction.channel_id)
        if row is None:
            await interaction.response.send_message("Create a sticky here first with `/stick`.", ephemeral=True)
            return
        if every_messages is None and after_seconds is None:
            await interaction.response.send_message(
                f"Current speed: **{row['every_messages'] or '—'}** messages or "
                f"**{row['after_seconds'] or '—'}s**.", ephemeral=True
            )
            return
        messages = row["every_messages"] if every_messages is None else every_messages
        seconds = row["after_seconds"] if after_seconds is None else after_seconds
        if messages == 0 and seconds == 0:
            await interaction.response.send_message("At least one trigger must stay enabled.", ephemeral=True)
            return
        await self.bot.db.set_sticky_speed(interaction.channel_id, messages, seconds)
        await interaction.response.send_message(
            f"✅ Speed set to **{messages or '—'}** messages or **{seconds or '—'}s**.",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "You need the **Manage Messages** permission for sticky commands."
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Stickies(bot))
