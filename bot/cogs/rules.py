"""Server rules embed (replaces the Carl Bot rules post).

/postrules [channel] — post the rules embed (Manage Server)
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

RULES_TITLE = "RULES"

RULES_BODY = """## Welcome to NITC's Discord Server
This is a student-run community for NIT Calicut where batches, branches, and clubs come together. Whether you're here for academics, fests, memes, or networking, you'll find your space.

Before exploring the channels, please go through these rules:
## Server Rules
1️⃣ Identity and Nicknames. Use your real name or a recognizable version. No impersonation, offensive names, or joke accounts.
2️⃣ Respect and Inclusion. Treat everyone with dignity. Harassment, witch hunting, sexism, racism, casteism, or hate speech is not allowed.
3️⃣ Content Policy. Keep it clean. No NSFW, obscene, or illegal content. No exam leaks, pirated material, or plagiarism.
4️⃣ Spam, Promotions, and Alts. No unsolicited ads or promotions. No unsolicited DMs or mass pings. No ban evasion or alternate accounts.
5️⃣ Privacy and Safety. Do not share personal information without consent. No doxxing. If something makes you uncomfortable or breaks rules, contact moderators immediately.
6️⃣ Quality Participation. Use the correct channels, threads, and tags. Stay on topic. In voice, mute when not speaking and avoid background noise.
7️⃣ Enforcement and Appeals. Moderators may act on behavior not covered above to keep the server safe. Penalties can include warnings, mutes, kicks, or bans. For disputes or appeals, DM one of the admins.

✨ Let's make this the digital hub that NITC deserves!"""


def _rules_embed() -> discord.Embed:
    embed = discord.Embed(description=RULES_BODY, color=0x5865F2)
    embed.set_author(name=RULES_TITLE)
    return embed


class Rules(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="postrules", description="Post the server rules embed in a channel.")
    @app_commands.describe(channel="Where to post (defaults to the current channel)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def postrules(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        target = channel or interaction.channel
        try:
            await target.send(embed=_rules_embed())
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I don't have permission to post in {target.mention}. "
                "Give me View Channel + Send Messages + Embed Links there.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(f"✅ Rules posted in {target.mention}.", ephemeral=True)

    @postrules.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission for this.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Rules(bot))
