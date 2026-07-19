"""NITC notice watcher, high-signal bulletin, and optional daily digest.

/notices           - live, unfiltered view of the NITC boards
/bulletin          - classified student-facing notices from local history
/setnoticeschannel - configure immediate, digest, or combined delivery
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands

from ..bulletins import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    Classification,
    classify_notice,
    primary_color,
    tag_labels,
)
from ..config import config
from ..platforms import nitc
from ..platforms.nitc import BOARDS, Notice

log = logging.getLogger(__name__)

CHECK_HOURS = 3
MAX_POSTS_PER_PASS = 8
DIGEST_MAX_ITEMS = 15

_BOARD_CHOICES = [
    app_commands.Choice(name=label, value=key) for key, (label, _) in BOARDS.items()
]
_CATEGORY_CHOICES = [
    app_commands.Choice(name=CATEGORY_LABELS[key].split(" ", 1)[-1], value=key)
    for key in CATEGORY_ORDER
]
_DELIVERY_CHOICES = [
    app_commands.Choice(name="Immediate posts", value="immediate"),
    app_commands.Choice(name="Daily digest", value="daily_digest"),
    app_commands.Choice(name="Both", value="both"),
]
_DELIVERY_LABELS = {
    "immediate": "immediate posts",
    "daily_digest": "a daily digest",
    "both": "immediate posts and a daily digest",
}
_BOARD_COLOR = {"academic": 0xE67E22, "general": 0x95A5A6}


def _tags_from_row(row) -> tuple[str, ...]:
    present = set((row["tags"] or "").split(","))
    return tuple(tag for tag in CATEGORY_ORDER if tag in present)


def _discord_time(raw: str) -> str:
    try:
        dt = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return raw
    return discord.utils.format_dt(dt, "R")


def _safe_title(title: str, limit: int = 180) -> str:
    # escape_markdown leaves lone brackets alone, but these titles are embedded
    # in [title](url) links where a stray "]" would close the link early.
    title = discord.utils.escape_markdown(title, as_needed=True)
    title = title.replace("[", "\\[").replace("]", "\\]")
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def _notice_embed(n: Notice, classification: Classification | None = None) -> discord.Embed:
    classification = classification or classify_notice(n.title)
    tags = classification.tags
    details = f"New notice on the **{n.board_label}** board · [open PDF]({n.url})"
    if tags:
        details = f"{tag_labels(tags)}\n{details}"
    embed = discord.Embed(
        title=n.title[:256],
        url=n.url,
        description=details,
        color=primary_color(tags, _BOARD_COLOR.get(n.board, 0x5865F2)),
    )
    embed.set_author(name="NITC Notice Board", url=BOARDS[n.board][1])
    if classification.urgent:
        embed.set_footer(text="High priority · check the linked notice for the exact deadline")
    return embed


def _bulletin_embed(rows, category: str | None, urgent_only: bool) -> discord.Embed:
    scope = CATEGORY_LABELS[category] if category else "Student bulletin"
    if urgent_only:
        scope += " · urgent only"
    lines: list[str] = []
    truncated = False
    for row in rows:
        tags = _tags_from_row(row)
        line = (
            f"**[{_safe_title(row['title'])}]({row['url']})**\n"
            f"{tag_labels(tags)} · {BOARDS.get(row['board'], (row['board'], ''))[0]} · "
            f"seen {_discord_time(row['first_seen'])}"
        )
        if len("\n\n".join((*lines, line))) > 4000:
            truncated = True
            break
        lines.append(line)
    embed = discord.Embed(
        title=scope,
        description="\n\n".join(lines),
        color=primary_color(_tags_from_row(rows[0])) if rows else 0x5865F2,
    )
    footer = "Title-based tags · use /notices for the unfiltered live boards"
    if truncated:
        footer += " · some results omitted to fit Discord's message limit"
    embed.set_footer(text=footer)
    return embed


def _chunk_lines(lines: list[str], max_length: int = 1000) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(line) > max_length:
            # A single line must never exceed a field's limit (unbounded URLs).
            line = line[: max_length - 1].rstrip() + "…"
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > max_length and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


_OTHER_KEY = "other"
_OTHER_LABEL = "📌 Other"


def _digest_embed(rows, has_more: bool = False) -> discord.Embed:
    sections = (*CATEGORY_ORDER, _OTHER_KEY)
    grouped: dict[str, list[str]] = {section: [] for section in sections}
    for row in rows:
        tags = _tags_from_row(row)
        primary = tags[0] if tags else _OTHER_KEY
        meta = " · ".join(
            part for part in (tag_labels(tags), BOARDS.get(row["board"], (row["board"], ""))[0])
            if part
        )
        grouped[primary].append(
            f"• **[{_safe_title(row['title'], 160)}]({row['url']})**\n  {meta}"
        )

    embed = discord.Embed(
        title="📣 NITC daily bulletin",
        description=(
            "Student-facing notices found since the previous digest. "
            "Deadlines are listed first."
        ),
        color=0xE67E22,
        timestamp=datetime.now(timezone.utc),
    )
    for section in sections:
        label = CATEGORY_LABELS.get(section, _OTHER_LABEL)
        chunks = _chunk_lines(grouped[section])
        for index, chunk in enumerate(chunks):
            suffix = " (continued)" if index else ""
            embed.add_field(name=label + suffix, value=chunk, inline=False)
    footer = "Title-based classification · verify exact details in the linked PDF"
    if has_more:
        footer += " · more updates are available with /bulletin"
    embed.set_footer(text=footer)
    return embed


def _make_scheduler() -> AsyncIOScheduler:
    try:
        return AsyncIOScheduler(timezone=config.bulletin_timezone)
    except Exception as e:  # a BULLETIN_TIMEZONE typo must not stop the whole bot
        log.warning(
            "Invalid BULLETIN_TIMEZONE %r (%s); using Asia/Kolkata.",
            config.bulletin_timezone,
            e,
        )
        return AsyncIOScheduler(timezone="Asia/Kolkata")


class Notices(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = _make_scheduler()

    async def cog_load(self) -> None:
        self.scheduler.add_job(
            self.check_boards, "interval", hours=CHECK_HOURS, id="notices", max_instances=1
        )
        self.scheduler.add_job(self.check_boards, "date", id="notices_boot")
        self.scheduler.add_job(
            self.send_daily_digests,
            "cron",
            hour=config.bulletin_digest_hour,
            minute=0,
            id="bulletin_digest",
            max_instances=1,
            misfire_grace_time=3600,
        )
        self.scheduler.start()
        log.info(
            "Notice watcher started; bulletin digest is scheduled for %02d:00 %s.",
            config.bulletin_digest_hour,
            self.scheduler.timezone,
        )

    async def cog_unload(self) -> None:
        self.scheduler.shutdown(wait=False)

    async def check_boards(self) -> None:
        for board in BOARDS:
            try:
                notices = await nitc.fetch_notices(self.bot.session, board)
            except Exception as e:  # noqa: BLE001
                log.warning("Notice fetch failed for %s: %s", board, e)
                continue
            if not notices:
                log.warning("Notice board '%s' returned 0 items; skipping.", board)
                continue

            first_run = await self.bot.db.notices_seen_count(board) == 0
            new: list[Notice] = [n for n in notices if not await self.bot.db.notice_seen(n.key)]

            if first_run:
                for notice in new:
                    await self.bot.db.mark_notice_seen(
                        notice.key, notice.board, notice.title, notice.url
                    )
                log.info("Seeded %d existing notices for board '%s'.", len(new), board)
                continue

            posted = 0
            for notice in list(reversed(new))[:MAX_POSTS_PER_PASS]:
                classification = classify_notice(notice.title)
                await self._broadcast(notice, classification)
                await self.bot.db.mark_notice_seen(
                    notice.key, notice.board, notice.title, notice.url, classification
                )
                posted += 1
            if new:
                log.info("Processed %d new notice(s) from board '%s'.", posted, board)

    async def _broadcast(self, notice: Notice, classification: Classification) -> None:
        embed = _notice_embed(notice, classification)
        for row in await self.bot.db.all_notice_targets():
            channel = self.bot.get_channel(row["notices_channel"])
            if channel is None:
                continue
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.DiscordException as e:
                log.warning("Failed to post notice to %s: %s", row["notices_channel"], e)

    async def send_daily_digests(self) -> None:
        for target in await self.bot.db.all_digest_targets():
            channel = self.bot.get_channel(target["notices_channel"])
            if channel is None:
                continue
            rows = await self.bot.db.pending_digest_notices(
                target["guild_id"], DIGEST_MAX_ITEMS + 1
            )
            if not rows:
                continue
            shown = rows[:DIGEST_MAX_ITEMS]
            has_more = len(rows) > DIGEST_MAX_ITEMS
            embed = _digest_embed(shown, has_more=has_more)
            while len(embed) > 5900 and len(shown) > 1:
                # Stay under Discord's 6000-char embed cap; dropped items remain
                # pending and roll into the next digest.
                shown = shown[:-1]
                embed = _digest_embed(shown, has_more=True)
            try:
                await channel.send(
                    embed=embed, allowed_mentions=discord.AllowedMentions.none()
                )
            except discord.DiscordException as e:
                log.warning(
                    "Failed to post bulletin digest to %s: %s",
                    target["notices_channel"],
                    e,
                )
                if isinstance(e, discord.HTTPException) and e.status == 400:
                    # Rejected payload fails identically every retry; skip these
                    # items so the guild's digest doesn't stay stuck forever.
                    await self.bot.db.mark_digest_delivered(
                        target["guild_id"], [row["notice_key"] for row in shown]
                    )
                continue
            await self.bot.db.mark_digest_delivered(
                target["guild_id"], [row["notice_key"] for row in shown]
            )

    @app_commands.command(
        name="notices", description="Show the latest notices from the NITC website."
    )
    @app_commands.describe(
        board="Which notice board (default: academic)",
        limit="How many (default 5, max 10)",
    )
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
        lines = [f"• **[{_safe_title(n.title)}]({n.url})**" for n in items[:limit]]
        embed = discord.Embed(
            title=f"📌 Latest {label} notices",
            url=page_url,
            description="\n".join(lines),
            color=_BOARD_COLOR.get(key, 0x5865F2),
        )
        embed.set_footer(text="Source: nitc.ac.in · new notices post automatically")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="bulletin", description="Show high-signal, tagged NITC notices.")
    @app_commands.describe(
        category="Only show one kind of update",
        urgent_only="Only show deadline notices",
        limit="How many notices (default 8, max 15)",
    )
    @app_commands.choices(category=_CATEGORY_CHOICES)
    async def bulletin(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str] | None = None,
        urgent_only: bool = False,
        limit: int = 8,
    ):
        limit = max(1, min(limit, 15))
        category_key = category.value if category else None
        rows = await self.bot.db.bulletin_notices(category_key, urgent_only, limit)
        if not rows:
            detail = " matching that filter" if category_key or urgent_only else " yet"
            await interaction.response.send_message(
                f"No classified bulletin notices{detail}. "
                "Try `/notices` for the unfiltered boards.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=_bulletin_embed(rows, category_key, urgent_only),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="setnoticeschannel", description="Configure NITC notice and bulletin delivery."
    )
    @app_commands.describe(
        channel="Channel to use", delivery="Immediate posts, daily digest, or both"
    )
    @app_commands.choices(delivery=_DELIVERY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setnoticeschannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        delivery: app_commands.Choice[str] | None = None,
    ):
        target = channel or interaction.channel
        current = await self.bot.db.get_guild(interaction.guild_id)
        mode = delivery.value if delivery else (
            current["notice_delivery"] if current else "immediate"
        )
        await self.bot.db.set_notices_channel(interaction.guild_id, target.id, mode)
        schedule = ""
        if mode in {"daily_digest", "both"}:
            schedule = (
                f" Digests run at **{config.bulletin_digest_hour:02d}:00 "
                f"{self.scheduler.timezone}**."
            )
        await interaction.response.send_message(
            f"✅ NITC notices will use {target.mention} for **{_DELIVERY_LABELS[mode]}**.{schedule}",
            ephemeral=True,
        )

    @setnoticeschannel.error
    async def _perm_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission for this.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Notices(bot))
