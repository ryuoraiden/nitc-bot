"""Account linking: /link, /verify, /unlink, /profile."""
from __future__ import annotations

import logging
import secrets

import discord
from discord import app_commands
from discord.ext import commands

from ..platforms.base import PlatformUser
from ..platforms.registry import PLATFORM_CHOICES, REGISTRY

log = logging.getLogger(__name__)

_CHOICES = [app_commands.Choice(name=label, value=key) for label, key in PLATFORM_CHOICES]


def _profile_embed(user: PlatformUser) -> discord.Embed:
    embed = discord.Embed(title=f"{user.platform} · {user.handle}", url=user.profile_url, color=0x5865F2)
    if user.display and user.display != user.handle:
        embed.add_field(name="Name", value=user.display, inline=True)
    if user.rating is not None:
        embed.add_field(name="Rating", value=str(user.rating), inline=True)
    if user.rank:
        embed.add_field(name="Rank", value=user.rank, inline=True)
    if user.extra:
        embed.add_field(name="​", value=user.extra, inline=False)
    return embed


class Linking(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="link", description="Link a competitive-programming handle to your Discord.")
    @app_commands.describe(platform="Which platform", handle="Your handle/username on that platform")
    @app_commands.choices(platform=_CHOICES)
    async def link(self, interaction: discord.Interaction, platform: app_commands.Choice[str], handle: str):
        adapter = REGISTRY[platform.value]
        token = "verify-" + secrets.token_hex(4)
        await self.bot.db.upsert_link(
            interaction.user.id, adapter.key, handle.strip(), verified=False, token=token
        )
        embed = discord.Embed(
            title=f"Linking {adapter.label}: {handle}",
            description=adapter.instructions(token),
            color=0xFEE75C,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="verify", description="Verify a pending handle link.")
    @app_commands.choices(platform=_CHOICES)
    async def verify(self, interaction: discord.Interaction, platform: app_commands.Choice[str]):
        adapter = REGISTRY[platform.value]
        row = await self.bot.db.get_link(interaction.user.id, adapter.key)
        if not row:
            await interaction.response.send_message(
                f"No pending {adapter.label} link. Use `/link {adapter.key}` first.", ephemeral=True
            )
            return
        if row["verified"]:
            await interaction.response.send_message(
                f"✅ Your {adapter.label} handle **{row['handle']}** is already verified.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            ok = await adapter.verify(self.bot.session, row["handle"], row["token"])
        except Exception as e:  # noqa: BLE001
            await interaction.followup.send(f"⚠️ Verification error: {e}", ephemeral=True)
            return

        if not ok:
            await interaction.followup.send(
                "❌ Couldn't find the token yet. Make sure you saved it, then try `/verify` again.",
                ephemeral=True,
            )
            return

        await self.bot.db.upsert_link(
            interaction.user.id, adapter.key, row["handle"], verified=True, token=None
        )
        await interaction.followup.send(
            f"✅ Verified! **{row['handle']}** is now linked on {adapter.label}.", ephemeral=True
        )

    @app_commands.command(name="unlink", description="Remove a linked handle.")
    @app_commands.choices(platform=_CHOICES)
    async def unlink(self, interaction: discord.Interaction, platform: app_commands.Choice[str]):
        adapter = REGISTRY[platform.value]
        removed = await self.bot.db.delete_link(interaction.user.id, adapter.key)
        msg = (
            f"🗑️ Removed your {adapter.label} link."
            if removed
            else f"You have no {adapter.label} link to remove."
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="profile", description="Show linked CP profiles for you or another member.")
    @app_commands.describe(member="Whose profiles to show (defaults to you).")
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        links = await self.bot.db.get_links(target.id)
        verified = [row for row in links if row["verified"]]
        if not verified:
            await interaction.response.send_message(
                f"{target.display_name} has no verified CP handles yet.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        embeds: list[discord.Embed] = []
        for row in verified:
            adapter = REGISTRY.get(row["platform"])
            if not adapter:
                continue
            try:
                user = await adapter.get_user(self.bot.session, row["handle"])
                embeds.append(_profile_embed(user))
            except Exception as e:  # noqa: BLE001
                embeds.append(
                    discord.Embed(
                        title=f"{adapter.label} · {row['handle']}",
                        description=f"⚠️ Couldn't load stats: {e}",
                        color=0xED4245,
                    )
                )
        await interaction.followup.send(
            content=f"**{target.display_name}**'s CP profiles:", embeds=embeds[:10]
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Linking(bot))
