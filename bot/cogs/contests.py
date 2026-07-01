"""Contest listing, the reminder scheduler, and channel configuration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands

from ..config import config
from ..platforms.base import Contest
from ..platforms.clist import RESOURCES
from ..services import fetch_all_contests

log = logging.getLogger(__name__)

# Small colour palette per host for nicer embeds.
_COLORS = {
    "codeforces.com": 0x1F8ACB,
    "leetcode.com": 0xFFA116,
    "codechef.com": 0x5B4638,
    "atcoder.jp": 0x222222,
}


def _label(host: str) -> str:
    return RESOURCES.get(host, host)


class Contests(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cache: list[Contest] = []
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    async def cog_load(self) -> None:
        # Prime cache, then run refresh + reminder passes on a schedule.
        await self.refresh_cache()
        self.scheduler.add_job(
            self.refresh_cache, "interval", minutes=config.refresh_interval_minutes, id="refresh"
        )
        self.scheduler.add_job(self.reminder_pass, "interval", minutes=1, id="reminders")
        self.scheduler.add_job(self.bot.db.prune_reminders, "interval", hours=24, id="prune")
        self.scheduler.start()
        log.info("Contest scheduler started (refresh every %d min).", config.refresh_interval_minutes)

    async def cog_unload(self) -> None:
        self.scheduler.shutdown(wait=False)

    # ── data ──────────────────────────────────────────────
    async def refresh_cache(self) -> None:
        self.cache = await fetch_all_contests(self.bot.session)
        log.info("Contest cache refreshed: %d upcoming.", len(self.cache))

    # ── reminder loop ─────────────────────────────────────
    async def reminder_pass(self) -> None:
        if not self.cache:
            return
        now = datetime.now(timezone.utc)
        targets = await self.bot.db.all_reminder_targets()
        if not targets:
            return

        for contest in self.cache:
            minutes_until = (contest.start - now).total_seconds() / 60
            for lead in config.reminder_lead_minutes:
                # Fire when we're within the 1-minute window at/just past the lead mark.
                if lead - 1 <= minutes_until <= lead:
                    if await self.bot.db.was_sent(contest.key, lead):
                        continue
                    await self._broadcast(contest, lead, targets)
                    await self.bot.db.mark_sent(contest.key, lead)

    async def _broadcast(self, contest: Contest, lead: int, targets) -> None:
        embed = self._contest_embed(contest, lead_minutes=lead)
        for row in targets:
            channel = self.bot.get_channel(row["reminder_channel"])
            if channel is None:
                continue
            content = ""
            if row["mention_role"]:
                content = f"<@&{row['mention_role']}>"
            try:
                await channel.send(content=content or None, embed=embed)
            except discord.DiscordException as e:
                log.warning("Failed to send reminder to %s: %s", row["reminder_channel"], e)

    # ── embeds ────────────────────────────────────────────
    def _contest_embed(self, c: Contest, lead_minutes: int | None = None) -> discord.Embed:
        start_ts = int(c.start.timestamp())
        title = f"⏰ {_label(c.platform)} contest soon" if lead_minutes else _label(c.platform)
        desc_lines = [
            f"**[{c.name}]({c.url})**",
            f"🕒 <t:{start_ts}:F> (<t:{start_ts}:R>)",
            f"⌛ Duration: {c.duration_human()}",
        ]
        embed = discord.Embed(
            title=title,
            description="\n".join(desc_lines),
            color=_COLORS.get(c.platform, 0x5865F2),
        )
        return embed

    # ── slash commands ────────────────────────────────────
    @app_commands.command(name="contests", description="Show upcoming programming contests.")
    @app_commands.describe(limit="How many to show (default 8, max 20).")
    async def contests(self, interaction: discord.Interaction, limit: int = 8):
        limit = max(1, min(limit, 20))
        if not self.cache:
            await interaction.response.send_message(
                "No upcoming contests cached yet — try again in a moment.", ephemeral=True
            )
            return
        embed = discord.Embed(title="📅 Upcoming contests", color=0x5865F2)
        for c in self.cache[:limit]:
            ts = int(c.start.timestamp())
            embed.add_field(
                name=f"{_label(c.platform)} — {c.name}",
                value=f"<t:{ts}:F> · <t:{ts}:R> · {c.duration_human()}\n[Link]({c.url})",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="setchannel", description="Set this channel for contest reminders.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        target = channel or interaction.channel
        await self.bot.db.set_reminder_channel(interaction.guild_id, target.id)
        await interaction.response.send_message(
            f"✅ Contest reminders will be posted in {target.mention}.", ephemeral=True
        )

    @app_commands.command(name="setrole", description="Set a role to ping on reminders (or clear it).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setrole(self, interaction: discord.Interaction, role: discord.Role | None = None):
        await self.bot.db.set_mention_role(interaction.guild_id, role.id if role else None)
        msg = f"✅ Reminders will ping {role.mention}." if role else "✅ Reminder ping cleared."
        await interaction.response.send_message(msg, ephemeral=True)

    @setchannel.error
    @setrole.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission for this.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Contests(bot))
