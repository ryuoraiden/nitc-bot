"""Study materials: searchable index of public Google Drive folders.

/pyq       — search past papers (falls back to all materials)
/material  — search everything (notes, slides, textbooks, papers)
/addsource — register another public Drive folder (open to everyone)
/sources   — list indexed Drive folders
/reindex   — re-crawl all sources now (Manage Server)
"""
from __future__ import annotations

import asyncio
import logging

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands

from ..config import config
from ..platforms import drive

log = logging.getLogger(__name__)

# Seeded on first run so the bot is useful out of the box for the NITC server.
DEFAULT_SOURCES = [
    ("1SEQD8DihaA-5nt1kjI79rbQ1PVunXsnj", "CSED Study Resources"),
    ("1uTvtfpbCd61YOb2CLIAFZokUspfGC7xX", "Sem-2"),
]

_CATEGORY_ICON = {"pyq": "📝", "notes": "📓", "textbook": "📚", "other": "📄"}


def _short_path(path: str, keep: int = 3) -> str:
    parts = path.split("/")
    return "/".join(parts[-keep:]) if len(parts) > keep else path


def _results_embed(title: str, rows) -> discord.Embed:
    lines = []
    for row in rows:
        icon = _CATEGORY_ICON.get(row["category"], "📄")
        url = f"https://drive.google.com/file/d/{row['file_id']}/view"
        lines.append(f"{icon} **[{row['name']}]({url})**\n└ `{_short_path(row['path'])}`")
    embed = discord.Embed(title=title, description="\n".join(lines), color=0x2ECC71)
    embed.set_footer(text="Sourced from community Drive folders · /addsource to contribute more")
    return embed


class Study(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self._indexing = False

    async def cog_load(self) -> None:
        sources = await self.bot.db.list_drive_sources()
        if not sources:
            for folder_id, label in DEFAULT_SOURCES:
                await self.bot.db.add_drive_source(folder_id, label, added_by=None)
        # Index in the background so startup isn't blocked; refresh daily.
        if await self.bot.db.drive_file_count() == 0:
            asyncio.create_task(self.reindex_all())
        self.scheduler.add_job(self.reindex_all, "interval", hours=24, id="drive_reindex")
        self.scheduler.start()

    async def cog_unload(self) -> None:
        self.scheduler.shutdown(wait=False)

    # ── indexing ──────────────────────────────────────────
    async def reindex_all(self) -> dict[str, int]:
        """Crawl every registered source. Returns {label: file_count}."""
        if self._indexing:
            return {}
        self._indexing = True
        stats: dict[str, int] = {}
        try:
            for src in await self.bot.db.list_drive_sources():
                try:
                    title, files = await drive.crawl(
                        self.bot.session, src["folder_id"],
                        api_key=config.google_api_key or None,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("Reindex failed for %s: %s", src["label"], e)
                    continue
                await self.bot.db.replace_drive_files(
                    src["folder_id"],
                    [(f.file_id, f.name, f.path, f.mime, f.category) for f in files],
                )
                stats[src["label"]] = len(files)
                log.info("Indexed %d files from '%s'.", len(files), src["label"])
        finally:
            self._indexing = False
        return stats

    # ── search commands ───────────────────────────────────
    @app_commands.command(name="pyq", description="Find past papers (midsem/endsem/quiz) for a subject.")
    @app_commands.describe(query="Subject, course code, or keywords, e.g. 'logic design midsem'")
    async def pyq(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)
        rows = await self.bot.db.search_drive(query, category="pyq")
        note = ""
        if not rows:
            rows = await self.bot.db.search_drive(query)
            note = " (no exam papers matched, showing all materials)"
        if not rows:
            await interaction.followup.send(
                f"Nothing found for **{query}**. Try broader keywords, or add a Drive "
                "folder that has it with `/addsource`."
            )
            return
        await interaction.followup.send(embed=_results_embed(f"📝 Papers for “{query}”{note}", rows))

    @app_commands.command(name="material", description="Search all study materials (notes, slides, books, papers).")
    @app_commands.describe(query="Subject or keywords, e.g. 'discrete structures notes'")
    async def material(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)
        rows = await self.bot.db.search_drive(query)
        if not rows:
            await interaction.followup.send(f"Nothing found for **{query}**.")
            return
        await interaction.followup.send(embed=_results_embed(f"📚 Materials for “{query}”", rows))

    # ── source management ─────────────────────────────────
    @app_commands.command(name="addsource", description="Add a public Google Drive folder to the study index.")
    @app_commands.describe(url="Link to a Drive folder shared as 'anyone with the link'")
    async def addsource(self, interaction: discord.Interaction, url: str):
        folder_id = drive.extract_folder_id(url)
        if not folder_id:
            await interaction.response.send_message("That doesn't look like a Drive folder link.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        title = await drive.get_folder_title(self.bot.session, folder_id)
        if title is None:
            await interaction.followup.send(
                "Couldn't read that folder. Make sure it's shared as **anyone with the link**.",
                ephemeral=True,
            )
            return
        await self.bot.db.add_drive_source(folder_id, title, interaction.user.id)
        asyncio.create_task(self.reindex_all())
        await interaction.followup.send(
            f"✅ Added **{title}**. Indexing has started; files will be searchable in a minute or two.",
            ephemeral=True,
        )
        # Announce publicly so contributions are visible (and attributable).
        if interaction.channel:
            try:
                await interaction.channel.send(
                    f"📂 {interaction.user.mention} added **[{title}]"
                    f"(https://drive.google.com/drive/folders/{folder_id})** to the study index."
                )
            except discord.DiscordException:
                pass

    @app_commands.command(name="sources", description="List the Drive folders in the study index.")
    async def sources(self, interaction: discord.Interaction):
        rows = await self.bot.db.list_drive_sources()
        if not rows:
            await interaction.response.send_message("No sources registered yet.", ephemeral=True)
            return
        lines = [
            f"• **[{r['label']}](https://drive.google.com/drive/folders/{r['folder_id']})** — {r['n_files']} files"
            for r in rows
        ]
        total = sum(r["n_files"] for r in rows)
        embed = discord.Embed(
            title="📂 Study material sources",
            description="\n".join(lines),
            color=0x3498DB,
        )
        embed.set_footer(text=f"{total} files indexed · admins can add more with /addsource")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="reindex", description="Re-crawl all Drive sources now.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reindex(self, interaction: discord.Interaction):
        if self._indexing:
            await interaction.response.send_message("Already indexing, hang on.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        stats = await self.reindex_all()
        summary = "\n".join(f"• {label}: {n} files" for label, n in stats.items()) or "nothing indexed"
        await interaction.followup.send(f"✅ Reindex done:\n{summary}", ephemeral=True)

    @reindex.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission for this.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Study(bot))
