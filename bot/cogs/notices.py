"""NITC website notice watcher.

Polls the official notice boards every few hours and posts anything new to
each guild's configured notices channel. The first pass for a board seeds
the seen-ledger without posting, so enabling the feature doesn't flood the
channel with 20 old notices.

/notices           — show the latest notices on demand
/setnoticeschannel — choose where new notices get posted (Manage Server)
"""
from __future__ import annotations

import logging

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands

from ..platforms import nitc
from ..platforms.nitc import BOARDS, Notice

log = logging.getLogger(__name__)

CHECK_HOURS = 3
MAX_POSTS_PER_PASS = 8  # safety valve if the site bulk-adds items

_BOARD_CHOICES = [app_commands.Choice(name=label, value=key) for key, (label, _) in BOARDS.items()]
_BOARD_COLOR = {"academic": 0xE67E22, "general": 0x95A5A6}


def _notice_embed(n: Notice) -> discord.Embed:
    embed = discord.Embed(
        title=n.title[:256],
        url=n.url,
        description=f"New notice on the **{n.board_label}** board · [open PDF]({n.url})",
        color=_BOARD_COLOR.get(n.board, 0x5865F2),
    )
    embed.set_author(name="NITC Notice Board", url=BOARDS[n.board][1])
    return embed


class Notices(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    async def cog_load(self) -> None:
        self.scheduler.add_job(self.check_boards, "interval", hours=CHECK_HOURS, id="notices")
        # One pass right after startup (seeds the ledger on first ever run).
        self.scheduler.add_job(self.check_boards, "date", id="notices_boot")
        self.scheduler.start()

    async def cog_unload(self) -> None:
        self.scheduler.shutdown(wait=False)

    # ── watcher ───────────────────────────────────────────
    async def check_boards(self) -> None:
        for board in BOARDS:
            try:
                notices = await nitc.fetch_notices(self.bot.session, board)
            except Exception as e:  # noqa: BLE001
                log.warning("Notice fetch failed for %s: %s", board, e)
                continue
            if not notices:
                # Parse mismatch or empty page: don't seed/post on bad data.
                log.warning("Notice board '%s' returned 0 items; skipping.", board)
                continue

            first_run = await self.bot.db.notices_seen_count(board) == 0
            new: list[Notice] = [n for n in notices if not await self.bot.db.notice_seen(n.key)]

            if first_run:
                for n in new:
                    await self.bot.db.mark_notice_seen(n.key, n.board, n.title, n.url)
                log.info("Seeded %d existing notices for board '%s'.", len(new), board)
                continue

            for n in list(reversed(new))[:MAX_POSTS_PER_PASS]:  # oldest first
                await self._broadcast(n)
                await self.bot.db.mark_notice_seen(n.key, n.board, n.title, n.url)
            if new:
                log.info("Posted %d new notice(s) from board '%s'.", min(len(new), MAX_POSTS_PER_PASS), board)

    async def _broadcast(self, n: Notice) -> None:
        embed = _notice_embed(n)
        for row in await self.bot.db.all_notice_targets():
            channel = self.bot.get_channel(row["notices_channel"])
            if channel is None:
                continue
            try:
                await channel.send(embed=embed)
            except discord.DiscordException as e:
                log.warning("Failed to post notice to %s: %s", row["notices_channel"], e)

    # ── commands ──────────────────────────────────────────
    @app_commands.command(name="notices", description="Show the latest notices from the NITC website.")
    @app_commands.describe(board="Which notice board (default: academic)", limit="How many (default 5, max 10)")
    @app_commands.choices(board=_BOARD_CHOICES)
    async def notices(
        self,
        interaction: discord.Interaction,
        board: app_commands.Choice[str] | None = None,
        limit: int = 5,
    ):
        key = board.value if board else "academic"
        limit = max(1, min(limit, 10))
        await interaction.response.defer(thinking=True)
        try:
            items = await nitc.fetch_notices(self.bot.session, key)
        except Exception as e:  # noqa: BLE001
            await interaction.followup.send(f"Couldn't reach the NITC site right now ({e}).")
            return
        if not items:
            await interaction.followup.send("No notices found (the site layout may have changed).")
            return
        label, page_url = BOARDS[key]
        lines = [f"• **[{n.title}]({n.url})**" for n in items[:limit]]
        embed = discord.Embed(
            title=f"📌 Latest {label} notices",
            url=page_url,
            description="\n".join(lines),
            color=_BOARD_COLOR.get(key, 0x5865F2),
        )
        embed.set_footer(text="Source: nitc.ac.in · new notices post automatically")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="setnoticeschannel", description="Post new NITC website notices in a channel.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setnoticeschannel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ):
        target = channel or interaction.channel
        await self.bot.db.set_notices_channel(interaction.guild_id, target.id)
        await interaction.response.send_message(
            f"✅ New NITC notices will be posted in {target.mention} "
            f"(checked every {CHECK_HOURS} hours).",
            ephemeral=True,
        )

    @setnoticeschannel.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission for this.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Notices(bot))
