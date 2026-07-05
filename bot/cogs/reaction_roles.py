"""Self-role panels using buttons and dropdowns (upgrade from reaction roles).

Components are persistent via discord.py DynamicItem: the role id is encoded in
each component's custom_id, so buttons and dropdowns keep working across bot
restarts with no per-message state to store or re-register.

/postpanel <panel> [channel] — post a self-role panel (Manage Server)

Needs the Manage Roles permission and the bot's top role above every role it
hands out.
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


def _display_role_name(name: str) -> str:
    """'🎤 ǀ · Music Club' -> 'Music Club'. Keeps original case (acronyms intact)."""
    if "·" in name:
        name = name.rsplit("·", 1)[-1]
    name = re.sub(r"[^0-9A-Za-z /&]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _clean_role_name(name: str) -> str:
    """Lowercased display name, used only to match roles by query."""
    return _display_role_name(name).lower()


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


async def _toggle_role(member: discord.Member, role: discord.Role) -> str | None:
    """Add or remove a role. Returns 'added'/'removed', or None on permission failure."""
    try:
        if role in member.roles:
            await member.remove_roles(role, reason="Self-role")
            return "removed"
        await member.add_roles(role, reason="Self-role")
        return "added"
    except discord.Forbidden:
        return None


# ── persistent components ─────────────────────────────────

class RoleButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rr:btn:(?P<rid>[0-9]+)"):
    def __init__(self, role_id: int, *, label: str = "role", emoji: str | None = None):
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label=label[:80],
                emoji=emoji,
                custom_id=f"rr:btn:{role_id}",
            )
        )
        self.role_id = role_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message("That role no longer exists.", ephemeral=True)
            return
        result = await _toggle_role(interaction.user, role)
        if result is None:
            await interaction.response.send_message(
                "I can't manage that role. An admin needs to move my role above it "
                "and give me Manage Roles.", ephemeral=True
            )
            return
        verb = "Added" if result == "added" else "Removed"
        await interaction.response.send_message(f"{verb} {role.mention}.", ephemeral=True)


class RoleSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"rr:sel"):
    def __init__(self, options: list[discord.SelectOption] | None = None, placeholder: str = "Select roles"):
        opts = options or [discord.SelectOption(label="placeholder", value="0")]
        super().__init__(
            discord.ui.Select(
                custom_id="rr:sel",
                placeholder=placeholder,
                min_values=0,
                max_values=len(opts),
                options=opts,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        values = interaction.data.get("values", [])
        if not values:
            await interaction.response.send_message("No changes made.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        added, removed, failed = [], [], []
        for rid in values:
            role = interaction.guild.get_role(int(rid))
            if role is None:
                continue
            result = await _toggle_role(interaction.user, role)
            (added if result == "added" else removed if result == "removed" else failed).append(role)
        parts = []
        if added:
            parts.append("➕ " + ", ".join(r.mention for r in added))
        if removed:
            parts.append("➖ " + ", ".join(r.mention for r in removed))
        if failed:
            parts.append("⚠️ Couldn't manage: " + ", ".join(r.name for r in failed)
                         + " (my role needs to be above them).")
        await interaction.followup.send("\n".join(parts) or "No changes made.", ephemeral=True)


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
            line = f"{emoji} {mention}"
            if desc:
                line += f" — {desc}"
            block.append(line)
        parts.append("\n".join(block))
    embed = discord.Embed(description="\n\n".join(parts), color=panel.get("color", 0x5865F2))
    embed.set_author(name=panel["author"])
    return embed


def _build_view(panel: dict, resolved: dict[str, discord.Role]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    if panel.get("style") == "select":
        options: list[discord.SelectOption] = []
        for heading, entries in panel["sections"]:
            category = re.sub(r"^\W+", "", heading).strip() if heading else None
            for emoji, role_query, _ in entries:
                role = resolved.get(role_query)
                if role is None:
                    continue
                options.append(discord.SelectOption(
                    label=_display_role_name(role.name)[:100],
                    value=str(role.id),
                    emoji=emoji,
                    description=(category[:100] if category else None),
                ))
        if options:
            view.add_item(RoleSelect(options, placeholder=panel.get("placeholder", "Select roles")))
    else:
        for emoji, role_query, _ in _iter_entries(panel):
            role = resolved.get(role_query)
            if role is None:
                continue
            view.add_item(RoleButton(role.id, label=_display_role_name(role.name), emoji=emoji))
    return view


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        # Register the dynamic components so their callbacks fire after restarts.
        self.bot.add_dynamic_items(RoleButton, RoleSelect)

    @app_commands.command(name="postpanel", description="Post a self-role panel (buttons or dropdown).")
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
            await target.send(embed=_build_embed(spec, resolved), view=_build_view(spec, resolved))
        except discord.Forbidden:
            await interaction.followup.send(
                f"I can't post in {target.mention} (need View Channel + Send Messages + Embed Links).",
                ephemeral=True,
            )
            return

        report = [f"✅ Posted **{panel.name}** in {target.mention} · **{len(resolved)}** roles wired."]
        if not interaction.guild.me.guild_permissions.manage_roles:
            report.append("⚠️ I don't have **Manage Roles**, so nothing will assign until you grant it.")
        if too_high:
            report.append("⚠️ Above my top role (won't assign until I'm moved up): " + ", ".join(too_high))
        if missing:
            report.append("⚠️ Couldn't find roles named: " + ", ".join(f"`{m}`" for m in missing)
                          + " (rename them to match, or tell me the exact names).")
        await interaction.followup.send("\n".join(report), ephemeral=True)

    @postpanel.error
    async def _perm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission for this.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
