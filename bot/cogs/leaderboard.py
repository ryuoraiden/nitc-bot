"""Server leaderboard: rank verified members by their platform metric."""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..platforms import codeforces
from ..platforms.base import PlatformUser
from ..platforms.registry import PLATFORM_CHOICES, REGISTRY

log = logging.getLogger(__name__)

_CHOICES = [app_commands.Choice(name=label, value=key) for label, key in PLATFORM_CHOICES]
_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


async def _resolve_member(guild: discord.Guild, discord_id: int) -> discord.Member | None:
    """Return the member if they're in this guild, else None. No privileged intent needed."""
    member = guild.get_member(discord_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(discord_id)
    except (discord.NotFound, discord.HTTPException):
        return None


class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _fetch_stats(self, platform: str, handles: list[str]) -> dict[str, PlatformUser]:
        """handle(lowercased) -> PlatformUser. Batches Codeforces; loops others."""
        results: dict[str, PlatformUser] = {}
        if platform == "codeforces":
            try:
                for u in await codeforces.get_users(self.bot.session, handles):
                    results[u.handle.lower()] = u
            except Exception as e:  # noqa: BLE001
                log.warning("CF batch lookup failed: %s", e)
            return results

        adapter = REGISTRY[platform]

        async def one(h: str):
            try:
                results[h.lower()] = await adapter.get_user(self.bot.session, h)
            except Exception as e:  # noqa: BLE001
                log.debug("stats lookup failed for %s/%s: %s", platform, h, e)

        # Bounded concurrency so we don't hammer unofficial endpoints.
        sem = asyncio.Semaphore(5)

        async def guarded(h: str):
            async with sem:
                await one(h)

        await asyncio.gather(*(guarded(h) for h in handles))
        return results

    @app_commands.command(name="leaderboard", description="Rank this server's members on a platform.")
    @app_commands.describe(platform="Which platform to rank by")
    @app_commands.choices(platform=_CHOICES)
    async def leaderboard(self, interaction: discord.Interaction, platform: app_commands.Choice[str]):
        adapter = REGISTRY[platform.value]
        rows = await self.bot.db.get_verified_by_platform(platform.value)
        if not rows:
            await interaction.response.send_message(
                f"No one has a verified {adapter.label} handle yet. Use `/link {platform.value}`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        # Keep only members present in this guild.
        member_by_handle: dict[str, discord.Member] = {}
        for row in rows:
            member = await _resolve_member(interaction.guild, row["discord_id"])
            if member is not None:
                member_by_handle[row["handle"]] = member

        if not member_by_handle:
            await interaction.followup.send(
                f"No verified {adapter.label} users are in this server yet."
            )
            return

        stats = await self._fetch_stats(platform.value, list(member_by_handle.keys()))

        # Build (member, user) pairs; users with no score sort last.
        entries: list[tuple[discord.Member, PlatformUser]] = []
        for handle, member in member_by_handle.items():
            user = stats.get(handle.lower())
            if user is not None:
                entries.append((member, user))
        entries.sort(key=lambda e: (e[1].score if e[1].score is not None else -1), reverse=True)

        if not entries:
            await interaction.followup.send(
                f"Couldn't load {adapter.label} stats right now — try again shortly."
            )
            return

        metric = entries[0][1].score_label or "Score"
        lines = []
        for i, (member, user) in enumerate(entries, start=1):
            badge = _MEDALS.get(i, f"`{i}.`")
            value = user.score if user.score is not None else "—"
            handle_link = f"[{user.handle}]({user.profile_url})" if user.profile_url else user.handle
            lines.append(f"{badge} **{member.display_name}** — {handle_link} · {metric}: **{value}**")

        embed = discord.Embed(
            title=f"🏆 {adapter.label} leaderboard — {interaction.guild.name}",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        embed.set_footer(text=f"{len(entries)} ranked · sorted by {metric.lower()}")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))
