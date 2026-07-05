"""Reaction roles (replaces Carl Bot's reaction-role panels).

/postpanel <panel> [channel] — post a self-role panel and wire its reactions
/clearpanel <message_id>      — stop tracking a panel message

Needs the Manage Roles permission and the bot's top role above every role it
hands out. Reaction events are non-privileged (included in default intents).
"""
from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from ..reaction_panels import PANELS

log = logging.getLogger(__name__)

_PANEL_CHOICES = [app_commands.Choice(name=key.title(), value=key) for key in PANELS]


def _norm_emoji(e: str) -> str:
    """Drop the variation selector (U+FE0F) so add/remove events compare equal."""
    return e.replace(chr(0xFE0F), "")


def _clean_role_name(name: str) -> str:
    """'🎤 ǀ · Music Club' -> 'music club'. Used to match roles by query."""
    if "·" in name:
        name = name.rsplit("·", 1)[-1]
    name = re.sub(r"[^0-9A-Za-z /]+", " ", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def _find_role(guild: discord.Guild, query: str) -> discord.Role | None:
    q = query.lower()
    for role in guild.roles:
        if _clean_role_name(role.name) == q:
            return role
    return None


def _iter_entries(panel: dict):
    for _, entries in panel["sections"]:
        for emoji, role_query, desc in entries:
            yield emoji, role_query, desc


def _build_embed(panel: dict, resolved: dict[str, discord.Role]) -> discord.Embed:
    parts: list[str] = []
    if panel.get("intro"):
        parts.append(panel["intro"])
    for heading, entries in panel["sections"]:
        block: list[str] = []
        if heading:
            block.append(f"## {heading}")
        for emoji, role_query, desc in entries:
            role = resolved.get(role_query)
            mention = role.mention if role else f"`{role_query}` (missing)"
            block.append(f"{emoji} → {mention}")
            if desc:
                block.append(f"• {desc}")
        parts.append("\n".join(block))
    embed = discord.Embed(description="\n\n".join(parts), color=panel.get("color", 0x5865F2))
    embed.set_author(name=panel["author"])
    return embed


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── reaction listeners ────────────────────────────────
    async def _apply(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        emoji = _norm_emoji(str(payload.emoji))
        role_id = await self.bot.db.get_reaction_role(payload.message_id, emoji)
        if role_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(role_id)
        if role is None:
            return
        member = payload.member or guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return
        if member.bot:
            return
        try:
            if add:
                await member.add_roles(role, reason="Reaction role")
            else:
                await member.remove_roles(role, reason="Reaction role removed")
        except discord.Forbidden:
            log.warning("Missing permission/hierarchy to toggle role %s for %s", role.name, member)
        except discord.HTTPException as e:
            log.warning("Reaction role update failed: %s", e)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._apply(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._apply(payload, add=False)

    # ── commands ──────────────────────────────────────────
    @app_commands.command(name="postpanel", description="Post a self-role reaction panel.")
    @app_commands.describe(panel="Which panel", channel="Where to post (defaults to here)")
    @app_commands.choices(panel=_PANEL_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def postpanel(
        self,
        interaction: discord.Interaction,
        panel: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ):
        target = channel or interaction.channel
        spec = PANELS[panel.value]
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Resolve roles up front so we can report anything missing.
        resolved: dict[str, discord.Role] = {}
        missing: list[str] = []
        too_high: list[str] = []
        me_top = interaction.guild.me.top_role
        for _, role_query, _ in _iter_entries(spec):
            role = _find_role(interaction.guild, role_query)
            if role is None:
                missing.append(role_query)
            else:
                resolved[role_query] = role
                if role >= me_top:
                    too_high.append(role.name)

        try:
            msg = await target.send(embed=_build_embed(spec, resolved))
        except discord.Forbidden:
            await interaction.followup.send(
                f"I can't post in {target.mention} (need View Channel + Send Messages + Embed Links).",
                ephemeral=True,
            )
            return

        # Add reactions + store mappings only for resolved roles.
        bound = 0
        for emoji, role_query, _ in _iter_entries(spec):
            role = resolved.get(role_query)
            if role is None:
                continue
            try:
                await msg.add_reaction(emoji)
            except discord.HTTPException as e:
                log.warning("Couldn't add reaction %s: %s", emoji, e)
                continue
            await self.bot.db.add_reaction_role(msg.id, _norm_emoji(emoji), interaction.guild_id, role.id)
            bound += 1

        # Build the admin summary.
        report = [f"✅ Posted **{panel.name}** in {target.mention} · **{bound}** roles wired."]
        if not interaction.guild.me.guild_permissions.manage_roles:
            report.append("⚠️ I don't have **Manage Roles**, so reactions won't assign anything until you grant it.")
        if too_high:
            report.append("⚠️ These roles are above my top role and won't assign until I'm moved up: "
                          + ", ".join(too_high))
        if missing:
            report.append("⚠️ Couldn't find roles named: " + ", ".join(f"`{m}`" for m in missing)
                          + " (rename them to match, or tell me the exact names).")
        report.append(f"Message ID: `{msg.id}` (use with `/clearpanel` to untrack).")
        await interaction.followup.send("\n".join(report), ephemeral=True)

    @app_commands.command(name="clearpanel", description="Stop tracking a reaction-role message.")
    @app_commands.describe(message_id="The panel message's ID")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def clearpanel(self, interaction: discord.Interaction, message_id: str):
        if not message_id.isdigit():
            await interaction.response.send_message("That's not a valid message ID.", ephemeral=True)
            return
        removed = await self.bot.db.clear_reaction_roles(int(message_id))
        msg = (
            f"🗑️ Stopped tracking {removed} reaction-role mapping(s)."
            if removed
            else "That message has no tracked reaction roles."
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @postpanel.error
    @clearpanel.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission for this.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
