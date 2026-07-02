"""Entry point: wires up the bot, database, HTTP session, cogs, and slash sync.

Run from the project root with:  python -m bot.main
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
import discord
from discord.ext import commands

from .config import config
from .db import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")

INITIAL_COGS = ["bot.cogs.contests", "bot.cogs.linking", "bot.cogs.leaderboard", "bot.cogs.study"]


class ContestBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()  # slash commands need no privileged intents
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.db = Database(config.db_path)
        self.session: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession()
        await self.db.connect()
        for ext in INITIAL_COGS:
            await self.load_extension(ext)
        if config.guild_id:
            guild = discord.Object(id=config.guild_id)
            # Copy our commands into the guild scope for instant availability,
            # then clear the GLOBAL scope and push it empty so previously-synced
            # global commands don't linger as duplicates. Order matters: copy first.
            self.tree.copy_global_to(guild=guild)
            self.tree.clear_commands(guild=None)
            await self.tree.sync()                       # removes any remote global commands
            synced = await self.tree.sync(guild=guild)   # registers the guild copies
            log.info("Synced %d slash commands to guild %s (instant).", len(synced), config.guild_id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global slash commands (may take up to ~1h to appear).", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s). In %d guild(s).", self.user, self.user.id, len(self.guilds))

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        await self.db.close()
        await super().close()


async def main() -> None:
    problems = config.validate()
    for p in problems:
        log.warning("Config: %s", p)
    if not config.discord_token:
        log.error("DISCORD_TOKEN is required. Copy .env.example to .env and fill it in.")
        return

    bot = ContestBot()
    async with bot:
        await bot.start(config.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
