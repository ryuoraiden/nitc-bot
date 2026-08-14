"""Giveaways: button entry, role requirements, bonus entries, auto-draw.

Entrants join with a persistent button (press again to leave), the embed shows
a live entrant count, and the draw fires on a scheduler at the deadline. A
startup sweep means a restart can't eat a giveaway: anything that expired while
the bot was down is drawn on boot, and the rest is rescheduled. Entrants are
re-validated at draw time, so someone who left the server or lost a required
role can't win.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from io import BytesIO

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from discord import app_commands
from discord.ext import commands

from ..giveaway import draw_winners, parse_duration

log = logging.getLogger(__name__)

ENTER_ID = "nitc_ga_enter"
WHO_ID = "nitc_ga_who"
EDIT_COOLDOWN = 3.0  # seconds between embed edits per giveaway


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


class GiveawayView(discord.ui.View):
    """Persistent Enter/Participants buttons; giveaway resolved by message id."""

    def __init__(self, cog: "Giveaways") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def _giveaway(self, interaction: discord.Interaction):
        g = await self.cog.db.giveaway_by_message(interaction.message.id)
        if g is None or g["ended"] or g["cancelled"]:
            await interaction.response.send_message("This giveaway is over.", ephemeral=True)
            return None
        return g

    @discord.ui.button(label="Enter", emoji="🎉", style=discord.ButtonStyle.success, custom_id=ENTER_ID)
    async def enter(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        g = await self._giveaway(interaction)
        if g is None:
            return
        member = interaction.user
        if member.bot or not isinstance(member, discord.Member):
            return
        db = self.cog.db
        if await db.has_entry(g["id"], member.id):
            await db.remove_entry(g["id"], member.id)
            await interaction.response.send_message(
                "You've left the giveaway. Hit Enter again if you change your mind.",
                ephemeral=True,
            )
            await self.cog.refresh_message(g["id"])
            return

        ok, missing = self.cog.check_eligibility(member, g)
        if not ok:
            await interaction.response.send_message(
                f"You can't enter yet, you're missing: {missing}", ephemeral=True
            )
            return
        weight = self.cog.entry_weight(member, g)
        await db.add_entry(g["id"], member.id, weight)
        if weight > 1:
            msg = (
                f"You're in with **{weight} entries** "
                f"(bonus for having <@&{g['bonus_role_id']}>). Good luck! 🎉"
            )
        else:
            msg = "You're in. Good luck! 🎉"
        await interaction.response.send_message(msg, ephemeral=True)
        await self.cog.refresh_message(g["id"])

    @discord.ui.button(label="Participants", style=discord.ButtonStyle.secondary, custom_id=WHO_ID)
    async def who(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        g = await self.cog.db.giveaway_by_message(interaction.message.id)
        if g is None:
            await interaction.response.send_message("Giveaway not found.", ephemeral=True)
            return
        rows = await self.cog.db.giveaway_entries(g["id"])
        total_entries = sum(r["entries"] for r in rows)
        mine = next((r for r in rows if r["user_id"] == interaction.user.id), None)
        lines = [f"**{len(rows)}** participants, **{total_entries}** total entries."]
        if mine:
            lines.append(f"You're in with **{mine['entries']}** entr{'ies' if mine['entries'] != 1 else 'y'}.")
        elif not g["ended"] and not g["cancelled"]:
            lines.append("You haven't entered yet.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class Giveaways(commands.Cog):
    giveaway = app_commands.Group(name="giveaway", description="Run giveaways")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self._last_edit: dict[int, float] = {}
        self._pending_refresh: set[int] = set()

    async def cog_load(self) -> None:
        self.bot.add_view(GiveawayView(self))
        self.scheduler.start()
        asyncio.create_task(self._sweep())

    async def cog_unload(self) -> None:
        self.scheduler.shutdown(wait=False)

    async def _sweep(self) -> None:
        """Draw giveaways that expired while offline; reschedule the rest."""
        try:
            await self.bot.wait_until_ready()
            for g in await self.db.unfinished_giveaways():
                ends = datetime.fromisoformat(g["ends_at"])
                if ends <= datetime.now(timezone.utc):
                    await self.finish_giveaway(g["id"])
                else:
                    self._schedule_end(g["id"], ends)
        except Exception:  # noqa: BLE001 — a dead sweep must not be silent
            log.exception("Giveaway startup sweep failed")

    # ── lookups + eligibility ─────────────────────────────

    async def resolve(self, interaction: discord.Interaction, gid: int | None, active_only=True):
        if gid is not None:
            return await self.db.get_giveaway(gid)
        if active_only:
            return await self.db.latest_active_giveaway(interaction.guild_id)
        return await self.db.latest_giveaway(interaction.guild_id)

    @staticmethod
    def required_role_ids(g) -> list[int]:
        return json.loads(g["required_roles"]) if g["required_roles"] else []

    def check_eligibility(self, member: discord.Member, g) -> tuple[bool, str]:
        req = self.required_role_ids(g)
        if not req:
            return True, ""
        have = {r.id for r in member.roles}
        if g["role_logic"] == "any":
            if have.intersection(req):
                return True, ""
            return False, "one of " + ", ".join(f"<@&{r}>" for r in req)
        missing = [r for r in req if r not in have]
        if not missing:
            return True, ""
        return False, ", ".join(f"<@&{r}>" for r in missing)

    def entry_weight(self, member: discord.Member, g) -> int:
        if g["bonus_role_id"] and any(r.id == g["bonus_role_id"] for r in member.roles):
            return 1 + max(0, g["bonus_entries"])
        return 1

    # ── embeds ────────────────────────────────────────────

    def build_embed(self, g, entrant_count: int, ended: bool = False,
                    winners: list[int] | None = None, cancelled: bool = False) -> discord.Embed:
        unix = _ts(g["ends_at"])
        if cancelled:
            color = discord.Color.dark_grey()
            title = f"✖️ {g['prize']}"
        elif ended:
            color = discord.Color.dark_grey()
            title = f"🎊 {g['prize']}"
        else:
            color = discord.Color.gold()
            title = f"🎉 {g['prize']}"
        embed = discord.Embed(title=title, color=color)

        lines = []
        if g["description"]:
            lines.append(g["description"])
            lines.append("")
        if cancelled:
            lines.append("This giveaway was cancelled.")
        elif ended:
            if winners:
                w = ", ".join(f"<@{u}>" for u in winners)
                lines.append(f"**Winner{'s' if len(winners) != 1 else ''}:** {w}")
            else:
                lines.append("**Winner:** nobody eligible entered.")
            lines.append(f"Ended <t:{unix}:R>")
        else:
            lines.append(f"**Ends:** <t:{unix}:R> (<t:{unix}:f>)")
            lines.append("Hit **Enter** to join. Hit it again to leave.")
        embed.description = "\n".join(lines)

        embed.add_field(name="Hosted by", value=f"<@{g['host_id']}>", inline=True)
        if not ended and not cancelled and g["winners_count"] > 1:
            embed.add_field(name="Winners", value=str(g["winners_count"]), inline=True)
        embed.add_field(name="Entrants", value=str(entrant_count), inline=True)

        req = self.required_role_ids(g)
        if req and not ended and not cancelled:
            joiner = " or " if g["role_logic"] == "any" else " and "
            embed.add_field(
                name="Required roles",
                value=joiner.join(f"<@&{r}>" for r in req),
                inline=False,
            )
        if g["bonus_role_id"] and not ended and not cancelled:
            extra = g["bonus_entries"]
            embed.add_field(
                name="Bonus entries",
                value=f"<@&{g['bonus_role_id']}> get **{1 + extra}x** entries",
                inline=False,
            )
        if g["image_name"]:
            embed.set_image(url=f"attachment://{g['image_name']}")
        embed.set_footer(text=f"Giveaway #{g['id']}")
        return embed

    async def refresh_message(self, gid: int) -> None:
        """Update the entrant count on the embed, throttled per giveaway."""
        now = time.monotonic()
        last = self._last_edit.get(gid, 0.0)
        if now - last < EDIT_COOLDOWN:
            if gid not in self._pending_refresh:
                self._pending_refresh.add(gid)
                asyncio.create_task(self._delayed_refresh(gid, EDIT_COOLDOWN - (now - last)))
            return
        self._last_edit[gid] = now
        await self._do_refresh(gid)

    async def _delayed_refresh(self, gid: int, wait: float) -> None:
        await asyncio.sleep(wait)
        self._pending_refresh.discard(gid)
        self._last_edit[gid] = time.monotonic()
        await self._do_refresh(gid)

    async def _do_refresh(self, gid: int) -> None:
        g = await self.db.get_giveaway(gid)
        if g is None or not g["message_id"] or g["ended"] or g["cancelled"]:
            return
        try:
            channel = self.bot.get_channel(g["channel_id"]) or await self.bot.fetch_channel(g["channel_id"])
            msg = await channel.fetch_message(g["message_id"])
            count = await self.db.count_giveaway_entrants(gid)
            await msg.edit(embed=self.build_embed(g, count))
        except discord.HTTPException:
            log.warning("Could not refresh giveaway #%d message", gid)

    # ── ending ────────────────────────────────────────────

    def _schedule_end(self, gid: int, ends_at: datetime) -> None:
        self.scheduler.add_job(
            self.finish_giveaway,
            DateTrigger(run_date=ends_at),
            args=[gid],
            id=f"giveaway_{gid}",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    def _cancel_job(self, gid: int) -> None:
        job = self.scheduler.get_job(f"giveaway_{gid}")
        if job:
            job.remove()

    async def _member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        """Cache lookup, falling back to the API. The cache is empty when the
        bot runs without the members intent, and a giveaway that drew zero
        winners for that reason would be a silent failure."""
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    async def _valid_weights(self, g, exclude: set[int] | None = None) -> dict[int, int]:
        """Re-validate entrants at draw time: still in the server, still meet
        the role requirements. Recomputes bonus weight from current roles."""
        guild = self.bot.get_guild(g["guild_id"])
        if guild is None:
            return {}
        exclude = exclude or set()
        weights: dict[int, int] = {}
        for row in await self.db.giveaway_entries(g["id"]):
            if row["user_id"] in exclude:
                continue
            member = await self._member(guild, row["user_id"])
            if member is None or member.bot:
                continue
            ok, _ = self.check_eligibility(member, g)
            if not ok:
                continue
            weights[member.id] = self.entry_weight(member, g)
        return weights

    async def finish_giveaway(self, gid: int) -> None:
        g = await self.db.get_giveaway(gid)
        if g is None or g["ended"] or g["cancelled"]:
            return
        weights = await self._valid_weights(g)
        winners = draw_winners(weights, g["winners_count"])
        await self.db.end_giveaway(gid, winners)
        self._cancel_job(gid)

        count = await self.db.count_giveaway_entrants(gid)
        try:
            channel = self.bot.get_channel(g["channel_id"]) or await self.bot.fetch_channel(g["channel_id"])
        except discord.HTTPException:
            log.error("Giveaway #%d channel missing", gid)
            return
        g = await self.db.get_giveaway(gid)
        msg = None
        try:
            msg = await channel.fetch_message(g["message_id"])
            await msg.edit(embed=self.build_embed(g, count, ended=True, winners=winners), view=None)
        except discord.HTTPException:
            log.warning("Giveaway #%d original message missing", gid)

        link = msg.jump_url if msg else ""
        if winners:
            pings = " ".join(f"<@{u}>" for u in winners)
            text = (
                f"🎉 Congrats {pings}, you won **{g['prize']}**!\n"
                f"Hosted by <@{g['host_id']}>. DM them to claim your prize.\n{link}"
            )
        else:
            text = (
                f"The **{g['prize']}** giveaway ended with no eligible entries. "
                f"<@{g['host_id']}> can reroll or run it again.\n{link}"
            )
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=True))
        except discord.HTTPException:
            log.exception("Could not announce giveaway #%d result", gid)
        log.info("Giveaway #%d ended: %d entrants, winners=%s", gid, count, winners)

    # ── commands ──────────────────────────────────────────

    @giveaway.command(name="create", description="Start a giveaway")
    @app_commands.describe(
        prize="What's being given away, e.g. a Codeforces T-shirt",
        duration="How long it runs: 1d, 12h, 2h30m...",
        channel="Where to post it (default: here)",
        host="Who is hosting/gifting (default: you)",
        winners="How many winners (default 1)",
        required_role_1="Role needed to enter",
        required_role_2="Another required role",
        required_role_3="Another required role",
        role_logic="Need ALL required roles or ANY one of them (default all)",
        extra_entry_role="Role that gets bonus entries",
        extra_entries="How many bonus entries that role gets (default 1)",
        image="Image shown on the giveaway",
        description="Extra details or claim instructions",
    )
    @app_commands.choices(
        role_logic=[
            app_commands.Choice(name="All required roles", value="all"),
            app_commands.Choice(name="Any one required role", value="any"),
        ],
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def create(
        self, interaction: discord.Interaction, prize: str, duration: str,
        channel: discord.TextChannel | None = None,
        host: discord.Member | None = None,
        winners: app_commands.Range[int, 1, 20] = 1,
        required_role_1: discord.Role | None = None,
        required_role_2: discord.Role | None = None,
        required_role_3: discord.Role | None = None,
        role_logic: app_commands.Choice[str] | None = None,
        extra_entry_role: discord.Role | None = None,
        extra_entries: app_commands.Range[int, 1, 10] = 1,
        image: discord.Attachment | None = None,
        description: str | None = None,
    ) -> None:
        delta = parse_duration(duration)
        if delta is None:
            await interaction.response.send_message(
                "Couldn't read that duration. Use things like `1d`, `12h`, `2h30m` "
                "(10 seconds to 60 days).", ephemeral=True
            )
            return
        image_bytes = None
        image_name = None
        if image is not None:
            if not (image.content_type or "").startswith("image/"):
                await interaction.response.send_message(
                    "That attachment isn't an image.", ephemeral=True
                )
                return
            image_bytes = await image.read()
            image_name = image.filename

        await interaction.response.defer(ephemeral=True)
        target = channel or interaction.channel
        host = host or interaction.user
        req = [r.id for r in (required_role_1, required_role_2, required_role_3) if r]
        ends_at = datetime.now(timezone.utc) + delta

        gid = await self.db.create_giveaway(
            prize=prize[:100],
            description=description,
            host_id=host.id,
            guild_id=interaction.guild_id,
            channel_id=target.id,
            winners_count=winners,
            required_roles=req,
            role_logic=(role_logic.value if role_logic else "all"),
            bonus_role_id=extra_entry_role.id if extra_entry_role else None,
            bonus_entries=extra_entries if extra_entry_role else 0,
            image_name=image_name,
            created_by=interaction.user.id,
            ends_at=ends_at.isoformat(),
        )
        g = await self.db.get_giveaway(gid)
        kwargs = {"embed": self.build_embed(g, 0), "view": GiveawayView(self)}
        if image_bytes:
            kwargs["file"] = discord.File(BytesIO(image_bytes), filename=image_name)
        try:
            msg = await target.send(**kwargs)
        except discord.HTTPException as e:
            await self.db.cancel_giveaway(gid)
            await interaction.followup.send(f"Couldn't post in {target.mention}: {e}", ephemeral=True)
            return
        await self.db.set_giveaway_message(gid, msg.id)
        self._schedule_end(gid, ends_at)
        unix = int(ends_at.timestamp())
        await interaction.followup.send(
            f"Giveaway #{gid} for **{prize}** is live in {target.mention}, "
            f"draws <t:{unix}:R>.", ephemeral=True
        )

    @giveaway.command(name="end", description="End a giveaway now and draw winners")
    @app_commands.describe(giveaway_id="Which giveaway (default: latest active)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def end(self, interaction: discord.Interaction, giveaway_id: int | None = None) -> None:
        g = await self.resolve(interaction, giveaway_id)
        if g is None or g["ended"] or g["cancelled"]:
            await interaction.response.send_message("No active giveaway found.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.finish_giveaway(g["id"])
        await interaction.followup.send(f"Giveaway #{g['id']} ended and drawn.", ephemeral=True)

    @giveaway.command(name="reroll", description="Draw replacement winners (excludes previous winners)")
    @app_commands.describe(
        giveaway_id="Which giveaway (default: most recent)",
        winners="How many new winners to draw (default 1)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reroll(
        self, interaction: discord.Interaction, giveaway_id: int | None = None,
        winners: app_commands.Range[int, 1, 20] = 1,
    ) -> None:
        g = await self.resolve(interaction, giveaway_id, active_only=False)
        if g is None or not g["ended"]:
            await interaction.response.send_message(
                "No ended giveaway to reroll. End it first with `/giveaway end`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        previous = json.loads(g["winners_json"] or "[]")
        weights = await self._valid_weights(g, exclude=set(previous))
        new_winners = draw_winners(weights, winners)
        if not new_winners:
            await interaction.followup.send(
                "Nobody eligible left to draw from.", ephemeral=True
            )
            return
        await self.db.set_giveaway_winners(g["id"], previous + new_winners)
        channel = self.bot.get_channel(g["channel_id"]) or await self.bot.fetch_channel(g["channel_id"])
        pings = " ".join(f"<@{u}>" for u in new_winners)
        await channel.send(
            f"🎲 Reroll! New winner{'s' if len(new_winners) != 1 else ''} of **{g['prize']}**: "
            f"{pings}. Congrats!",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await interaction.followup.send("Rerolled.", ephemeral=True)

    @giveaway.command(name="cancel", description="Cancel a giveaway, nobody wins")
    @app_commands.describe(giveaway_id="Which giveaway (default: latest active)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cancel(self, interaction: discord.Interaction, giveaway_id: int | None = None) -> None:
        g = await self.resolve(interaction, giveaway_id)
        if g is None or g["ended"] or g["cancelled"]:
            await interaction.response.send_message("No active giveaway found.", ephemeral=True)
            return
        await self.db.cancel_giveaway(g["id"])
        self._cancel_job(g["id"])
        count = await self.db.count_giveaway_entrants(g["id"])
        g = await self.db.get_giveaway(g["id"])
        try:
            channel = self.bot.get_channel(g["channel_id"]) or await self.bot.fetch_channel(g["channel_id"])
            msg = await channel.fetch_message(g["message_id"])
            await msg.edit(embed=self.build_embed(g, count, cancelled=True), view=None)
        except discord.HTTPException:
            pass
        await interaction.response.send_message(f"Cancelled giveaway #{g['id']}.", ephemeral=True)

    @giveaway.command(name="list", description="Show active giveaways")
    async def list_cmd(self, interaction: discord.Interaction) -> None:
        rows = await self.db.active_giveaways(interaction.guild_id)
        if not rows:
            await interaction.response.send_message("No active giveaways right now.", ephemeral=True)
            return
        lines = []
        for g in rows:
            unix = _ts(g["ends_at"])
            count = await self.db.count_giveaway_entrants(g["id"])
            lines.append(
                f"`#{g['id']}` **{g['prize']}** in <#{g['channel_id']}> · "
                f"{count} entrants · draws <t:{unix}:R>"
            )
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🎉 Active giveaways", description="\n".join(lines),
                color=discord.Color.gold(),
            ),
            ephemeral=True,
        )

    @giveaway.command(name="entries", description="See who entered a giveaway")
    @app_commands.describe(giveaway_id="Which giveaway (default: most recent)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def entries(self, interaction: discord.Interaction, giveaway_id: int | None = None) -> None:
        g = await self.resolve(interaction, giveaway_id, active_only=False)
        if g is None:
            await interaction.response.send_message("No giveaway found.", ephemeral=True)
            return
        rows = await self.db.giveaway_entries(g["id"])
        total = sum(r["entries"] for r in rows)
        header = f"**{g['prize']}** (#{g['id']}): {len(rows)} participants, {total} entries."
        names = "\n".join(
            f"<@{r['user_id']}>" + (f" x{r['entries']}" if r["entries"] > 1 else "")
            for r in rows[:40]
        )
        if len(rows) > 40:
            names += f"\nand {len(rows) - 40} more"
        await interaction.response.send_message(
            f"{header}\n{names}" if rows else header,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Giveaways(bot))
