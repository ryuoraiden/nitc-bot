"""Hackathon team-up board: /lft cards in a forum, private match threads.

/lft <type> [ping]                                   post a looking-for-team card
/setteamup <forum> <connect_channel> [ping_role]     configure it (Manage Server)
/teamupinfo [channel]                                post a public how-to guide (Manage Server)

Each card is a forum post with two persistent buttons (DynamicItem with the post
id in the custom_id, so they keep working across restarts):
  👋 I'm interested  opens a private thread in the connect channel with just the
                     clicker and the poster (public reply on the card as fallback)
  ✅ Team full       poster or mods close the card: it greys out, locks, archives
Open cards expire automatically after EXPIRE_DAYS.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands

from ..platforms.registry import REGISTRY
from .reaction_roles import _find_role

log = logging.getLogger(__name__)

MAX_OPEN_POSTS = 3
EXPIRE_DAYS = 30
PING_COOLDOWN_HOURS = 24
MAX_TAGS = 5  # Discord's per-post limit
FORUM_TAG_LIMIT = 20  # Discord's per-forum limit

KINDS = {"members": "Looking for members", "team": "Looking for team"}
CLOSED_TAG = "Closed"
_TAG_EMOJI = {"Looking for members": "📣", "Looking for team": "🙋", CLOSED_TAG: "✅"}

# Canonical skill -> lowercase aliases. Canonical names double as forum tag names
# (Discord caps tag names at 20 characters).
SKILLS: dict[str, tuple[str, ...]] = {
    "Frontend": ("front end", "front-end", "web", "react", "html", "css", "nextjs",
                 "next.js", "vue", "angular"),
    "Backend": ("back end", "back-end", "api", "apis", "server", "node", "nodejs",
                "django", "flask", "fastapi", "express", "spring"),
    "Full Stack": ("fullstack", "full-stack", "mern"),
    "Mobile": ("android", "ios", "flutter", "react native", "kotlin", "swift", "app dev"),
    "ML/AI": ("ml", "ai", "ai/ml", "machine learning", "deep learning", "dl", "llm", "llms",
              "nlp", "computer vision", "genai", "gen ai"),
    "Data": ("data science", "data analysis", "analytics", "sql", "data engineering"),
    "Design": ("ui", "ux", "ui/ux", "uiux", "figma", "graphic design", "product design"),
    "Hardware": ("iot", "embedded", "arduino", "raspberry pi", "electronics", "robotics",
                 "esp32", "pcb"),
    "Blockchain": ("web3", "solidity", "crypto", "smart contracts"),
    "Security": ("cybersecurity", "cyber security", "cyber", "infosec", "ctf"),
    "Game Dev": ("gamedev", "game development", "games", "unity", "unreal", "godot"),
    "DevOps": ("cloud", "aws", "gcp", "azure", "docker", "kubernetes", "ci/cd"),
    "Pitching": ("pitch", "presentation", "business", "product", "management", "ppt"),
}
_ALIAS = {alias: canon for canon, aliases in SKILLS.items() for alias in (canon.lower(), *aliases)}
ALL_TAG_NAMES = [*KINDS.values(), *SKILLS, CLOSED_TAG]

_COLORS = {"members": 0x2ECC71, "team": 0x5865F2}
_CLOSED_COLOR = 0x95A5A6


# ── pure helpers (unit-tested) ─────────────────────────────

def normalize_skills(raw: str | None) -> list[str]:
    """'react, ML , figma, rust' -> ['Frontend', 'ML/AI', 'Design', 'rust'].

    Known skills are canonicalized (so they map onto forum tags); anything else
    is kept as typed. Deduplicated case-insensitively, order kept, max 10.
    """
    out: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;\n|]+", raw or ""):
        item = re.sub(r"\s+", " ", part).strip(" .•-")
        if not item:
            continue
        canon = _ALIAS.get(item.lower(), item[:30])
        if canon.lower() not in seen:
            seen.add(canon.lower())
            out.append(canon)
    return out[:10]


_URL = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)


def format_links(raw: str | None) -> str | None:
    """Tidy the Links field into one entry per line.

    Line breaks are kept (including a typed literal \\n, like /stick). A line made
    up only of URLs separated by spaces/commas is split one URL per line; lines
    with labels ("GitHub: https://...") are left exactly as typed.
    """
    if not raw:
        return None
    out: list[str] = []
    for line in raw.replace(r"\n", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        tokens = [t for t in re.split(r"[\s,;|]+", line) if t]
        if len(tokens) > 1 and all(_URL.match(t) for t in tokens):
            out.extend(tokens)
        else:
            out.append(line)
    return "\n".join(out) or None


def pick_tags(available, kind: str, skills: list[str], *, closed: bool = False) -> list:
    """Map a post onto the forum's existing tags by name (case-insensitive), max 5."""
    by_name = {t.name.lower(): t for t in available}
    wanted = ([CLOSED_TAG] if closed else []) + [KINDS.get(kind, ""), *skills]
    picked: list = []
    for name in wanted:
        tag = by_name.get(name.lower())
        if tag is not None and all(tag is not p for p in picked):
            picked.append(tag)
        if len(picked) == MAX_TAGS:
            break
    return picked


def _card_url(post) -> str:
    return f"https://discord.com/channels/{post['guild_id']}/{post['thread_id']}"


def build_card(post, *, author=None, handles: list[str] | None = None, interested: int = 0) -> discord.Embed:
    members = post["kind"] == "members"
    status = post["status"]
    icon = "📣" if members else "🙋"
    embed = discord.Embed(
        title=f"{icon} {KINDS.get(post['kind'], 'Team-up')} · {post['hackathon']}"[:256],
        description=post["about"] or None,
        color=_COLORS.get(post["kind"], 0x5865F2) if status == "open" else _CLOSED_COLOR,
    )
    skills = [s for s in (post["skills"] or "").split(",") if s]
    embed.add_field(
        name="Skills needed" if members else "My skills",
        value=" · ".join(skills)[:1024] or "Any",
        inline=True,
    )
    embed.add_field(
        name="Team size" if members else "Preferred team size",
        value=(post["slots"] or "Flexible")[:1024],
        inline=True,
    )
    poster = f"<@{post['author_id']}>"
    if handles:
        poster += "\n" + " · ".join(handles)
    embed.add_field(name="Posted by", value=poster[:1024], inline=False)
    if post["links"]:
        embed.add_field(name="Links", value=post["links"][:1024], inline=False)
    if author is not None:
        embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)

    if status == "closed":
        footer = "✅ Team full · this card is closed"
    elif status == "expired":
        footer = f"⌛ Expired after {EXPIRE_DAYS} days · post a fresh one with /lft"
    elif interested:
        footer = f"👋 {interested} interested · tap I'm interested to chat privately with the poster"
    else:
        footer = "Be the first to reach out · 👋 opens a private chat with the poster"
    embed.set_footer(text=footer)
    try:
        embed.timestamp = datetime.fromisoformat(post["created_at"]).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    return embed


def build_guide(forum_mention: str, connect_mention: str | None, role_mention: str | None) -> discord.Embed:
    """Public, member-facing explainer for the team-up board."""
    embed = discord.Embed(
        title="🏆 Find hackathon teammates",
        description=("No more asking around in chat. Post a card, and anyone who wants "
                     "to team up can reach you privately in one tap."),
        color=0xF1C40F,
    )
    embed.add_field(
        name="📝 Post a card",
        value=("`/lft type:Looking for members` if you have an idea and need people\n"
               "`/lft type:Looking for a team` if you want to join one\n"
               "Then fill in the form: hackathon, skills, team size, and a short about."),
        inline=False,
    )
    embed.add_field(
        name="📌 Browse",
        value=(f"Every card lands in {forum_mention}, tagged by skill. Filter by tag "
               "to find exactly what you need."),
        inline=False,
    )
    where = f" in {connect_mention}" if connect_mention else ""
    embed.add_field(
        name="👋 Found one you like?",
        value=(f"Tap **I'm interested**. The bot opens a private thread{where} with just you "
               "and the poster. No cold DMs."),
        inline=False,
    )
    embed.add_field(
        name="✅ Team formed?",
        value=(f"The poster taps **Team full** and the card closes. Open cards expire "
               f"after {EXPIRE_DAYS} days."),
        inline=False,
    )
    if role_mention:
        embed.add_field(
            name="🔔 Get notified",
            value=(f"Grab {role_mention} in self-roles to hear about new cards. Posters can "
                   f"add `ping:true` to notify it (once per {PING_COOLDOWN_HOURS}h)."),
            inline=False,
        )
    embed.set_footer(text=f"Max {MAX_OPEN_POSTS} open cards per person")
    return embed


# ── persistent components ──────────────────────────────────

class InterestButton(discord.ui.DynamicItem[discord.ui.Button], template=r"lft:int:(?P<pid>[0-9]+)"):
    def __init__(self, post_id: int, *, disabled: bool = False):
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.success,
                label="I'm interested",
                emoji="👋",
                custom_id=f"lft:int:{post_id}",
                disabled=disabled,
            )
        )
        self.post_id = post_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["pid"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("TeamUp")
        if cog is None:
            await interaction.response.send_message("Team-up is unavailable right now.", ephemeral=True)
            return
        await cog.handle_interest(interaction, self.post_id)


class CloseButton(discord.ui.DynamicItem[discord.ui.Button], template=r"lft:close:(?P<pid>[0-9]+)"):
    def __init__(self, post_id: int, *, disabled: bool = False):
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="Team full",
                emoji="✅",
                custom_id=f"lft:close:{post_id}",
                disabled=disabled,
            )
        )
        self.post_id = post_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["pid"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("TeamUp")
        if cog is None:
            await interaction.response.send_message("Team-up is unavailable right now.", ephemeral=True)
            return
        await cog.handle_close(interaction, self.post_id)


def card_view(post_id: int, *, open_: bool = True) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(InterestButton(post_id, disabled=not open_))
    view.add_item(CloseButton(post_id, disabled=not open_))
    return view


class LFTModal(discord.ui.Modal):
    def __init__(self, cog: "TeamUp", kind: str, ping: bool):
        super().__init__(title=KINDS[kind], timeout=900)
        self.cog, self.kind, self.ping = cog, kind, ping
        members = kind == "members"
        self.hackathon = discord.ui.TextInput(
            label="Hackathon", placeholder="e.g. Smart India Hackathon 2026", max_length=100
        )
        self.skills = discord.ui.TextInput(
            label="Skills you need" if members else "Your skills",
            placeholder="e.g. frontend, ML, UI/UX, hardware",
            max_length=200,
        )
        self.slots = discord.ui.TextInput(
            label="Team size / open slots" if members else "Preferred team size",
            placeholder="e.g. 2 of 4, need 2 more" if members else "e.g. 3-4 people",
            required=False,
            max_length=60,
        )
        self.about = discord.ui.TextInput(
            label="About the idea" if members else "About you",
            style=discord.TextStyle.paragraph,
            placeholder=("What you're building, what you've got so far"
                         if members else "What you're good at, what you want to build"),
            required=False,
            max_length=1000,
        )
        self.links = discord.ui.TextInput(
            label="Links (one per line)",
            style=discord.TextStyle.paragraph,
            placeholder="GitHub, portfolio, idea doc... press Enter between links",
            required=False,
            max_length=500,
        )
        for item in (self.hackathon, self.skills, self.slots, self.about, self.links):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.create_post(
            interaction,
            self.kind,
            self.ping,
            hackathon=self.hackathon.value.strip(),
            skills_raw=self.skills.value,
            slots=self.slots.value.strip() or None,
            about=self.about.value.strip() or None,
            links=format_links(self.links.value),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("LFT post failed", exc_info=error)
        text = "Something went wrong posting your card. Try again in a moment."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


# ── cog ────────────────────────────────────────────────────

class TeamUp(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(InterestButton, CloseButton)
        self.scheduler.add_job(
            self.expire_stale_posts, "interval", hours=24, id="lft_expire", max_instances=1
        )
        self.scheduler.add_job(self.expire_stale_posts, "date", id="lft_expire_boot")
        self.scheduler.start()

    async def cog_unload(self) -> None:
        self.scheduler.shutdown(wait=False)
        self.bot.remove_dynamic_items(InterestButton, CloseButton)

    # ── lookups ───────────────────────────────────────────
    async def _config(self, guild: discord.Guild):
        """-> (forum, connect_channel, ping_role); any may be None."""
        row = await self.bot.db.get_guild(guild.id)
        if not row or not row["teamup_forum"]:
            return None, None, None
        forum = guild.get_channel(row["teamup_forum"])
        connect = guild.get_channel(row["teamup_connect"]) if row["teamup_connect"] else None
        role = guild.get_role(row["teamup_role"]) if row["teamup_role"] else None
        return (
            forum if isinstance(forum, discord.ForumChannel) else None,
            connect if isinstance(connect, discord.TextChannel) else None,
            role,
        )

    async def _handles(self, user_id: int) -> list[str]:
        out = []
        for row in await self.bot.db.get_links(user_id):
            if not row["verified"]:
                continue
            adapter = REGISTRY.get(row["platform"])
            label = adapter.label if adapter else row["platform"].title()
            out.append(f"{label}: `{row['handle']}`")
        return out

    async def _card_thread(self, post) -> discord.Thread | None:
        guild = self.bot.get_guild(post["guild_id"])
        if guild is None or not post["thread_id"]:
            return None
        thread = guild.get_channel_or_thread(post["thread_id"])
        if thread is None:  # archived threads aren't cached
            try:
                thread = await self.bot.fetch_channel(post["thread_id"])
            except discord.HTTPException:
                return None
        return thread if isinstance(thread, discord.Thread) else None

    # ── posting ───────────────────────────────────────────
    @app_commands.command(name="lft", description="Find hackathon teammates: post a looking-for-team card.")
    @app_commands.rename(kind="type")
    @app_commands.describe(
        kind="Do you need members, or are you looking for a team?",
        ping=f"Also ping the Team Up role (at most once per {PING_COOLDOWN_HOURS}h)",
    )
    @app_commands.choices(kind=[
        app_commands.Choice(name="Looking for members", value="members"),
        app_commands.Choice(name="Looking for a team", value="team"),
    ])
    @app_commands.guild_only()
    async def lft(
        self, interaction: discord.Interaction, kind: app_commands.Choice[str], ping: bool = False
    ):
        forum, _, _ = await self._config(interaction.guild)
        if forum is None:
            await interaction.response.send_message(
                "The team-up board isn't set up yet. Ask a mod to run `/setteamup`.", ephemeral=True
            )
            return
        if await self.bot.db.count_open_lft_posts(interaction.guild_id, interaction.user.id) >= MAX_OPEN_POSTS:
            await interaction.response.send_message(
                f"You already have {MAX_OPEN_POSTS} open cards. Close one with its **Team full** "
                "button before posting another.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(LFTModal(self, kind.value, ping))

    async def create_post(
        self,
        interaction: discord.Interaction,
        kind: str,
        ping: bool,
        *,
        hackathon: str,
        skills_raw: str,
        slots: str | None,
        about: str | None,
        links: str | None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        db = self.bot.db
        guild = interaction.guild
        forum, _, role = await self._config(guild)
        if forum is None:
            await interaction.followup.send("The team-up board isn't set up anymore.", ephemeral=True)
            return
        # Re-check: the form may have been open a while.
        if await db.count_open_lft_posts(guild.id, interaction.user.id) >= MAX_OPEN_POSTS:
            await interaction.followup.send(
                f"You already have {MAX_OPEN_POSTS} open cards. Close one first.", ephemeral=True
            )
            return
        if not hackathon:
            await interaction.followup.send("The hackathon name can't be empty.", ephemeral=True)
            return

        skills = normalize_skills(skills_raw)
        notes: list[str] = []
        do_ping = False
        if ping:
            if role is None:
                notes.append("No Team Up role is configured, so nobody was pinged.")
            elif await db.lft_pinged_recently(guild.id, interaction.user.id, PING_COOLDOWN_HOURS):
                notes.append(f"You already pinged in the last {PING_COOLDOWN_HOURS}h, "
                             "so this one went out without a ping.")
            else:
                do_ping = True

        post_id = await db.create_lft_post(
            guild_id=guild.id,
            author_id=interaction.user.id,
            kind=kind,
            hackathon=hackathon,
            skills=",".join(skills),
            slots=slots,
            about=about,
            links=links,
        )
        post = await db.get_lft_post(post_id)
        embed = build_card(post, author=interaction.user, handles=await self._handles(interaction.user.id))
        name = f"{hackathon} · {'LF members' if kind == 'members' else 'LF team'}"[:100]
        try:
            created = await forum.create_thread(
                name=name,
                content=role.mention if do_ping else None,
                embed=embed,
                view=card_view(post_id),
                applied_tags=pick_tags(forum.available_tags, kind, skills),
                auto_archive_duration=10080,
                allowed_mentions=(discord.AllowedMentions(roles=[role]) if do_ping
                                  else discord.AllowedMentions.none()),
            )
        except discord.Forbidden:
            await db.delete_lft_post(post_id)
            await interaction.followup.send(
                f"I can't post in {forum.mention}. A mod needs to give me Send Messages there.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await db.delete_lft_post(post_id)
            await interaction.followup.send(f"Discord rejected the card ({e.text or e}).", ephemeral=True)
            return

        await db.set_lft_post_message(post_id, created.thread.id, created.message.id, do_ping)
        lines = [f"✅ Your card is up: {created.thread.jump_url}",
                 "Anyone who taps **I'm interested** gets a private thread with you."]
        await interaction.followup.send("\n".join(lines + notes), ephemeral=True)

    # ── interest ──────────────────────────────────────────
    async def _already_connected(self, interaction: discord.Interaction, row) -> None:
        if row["thread_id"]:
            text = f"You already reached out on this one: <#{row['thread_id']}>"
        else:
            text = "Already connecting you, give it a second and check your threads."
        await interaction.response.send_message(text, ephemeral=True)

    async def handle_interest(self, interaction: discord.Interaction, post_id: int) -> None:
        db = self.bot.db
        post = await db.get_lft_post(post_id)
        if post is None or post["status"] != "open":
            await interaction.response.send_message(
                "This card is closed. Browse the open ones in the forum, or post your own with `/lft`.",
                ephemeral=True,
            )
            return
        user = interaction.user
        if user.id == post["author_id"]:
            await interaction.response.send_message(
                "That's your own card 🙂 You'll get a private thread whenever someone taps this.",
                ephemeral=True,
            )
            return
        existing = await db.get_lft_interest(post_id, user.id)
        if existing is not None:
            await self._already_connected(interaction, existing)
            return
        if not await db.add_lft_interest(post_id, user.id):  # lost a double-click race
            await self._already_connected(interaction, await db.get_lft_interest(post_id, user.id))
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        author = guild.get_member(post["author_id"])
        if author is None:
            try:
                author = await guild.fetch_member(post["author_id"])
            except discord.HTTPException:
                author = None
        if author is None:
            await db.remove_lft_interest(post_id, user.id)
            await interaction.followup.send(
                "The poster isn't in the server anymore, so this card can't be answered.", ephemeral=True
            )
            return

        _, connect, _ = await self._config(guild)
        thread = await self._open_private_thread(connect, post, user, author)
        if thread is not None:
            await db.set_lft_interest_thread(post_id, user.id, thread.id)
            await interaction.followup.send(
                f"👋 Done! You and {author.mention} can talk here: {thread.mention}", ephemeral=True
            )
        else:
            fallback = await self._public_fallback(post, user, author)
            if fallback is not None:
                await db.set_lft_interest_thread(post_id, user.id, fallback.id)
                await interaction.followup.send(
                    f"I couldn't open a private thread, so I pinged {author.mention} on the card "
                    f"instead: {fallback.mention}",
                    ephemeral=True,
                )
            else:
                await db.remove_lft_interest(post_id, user.id)
                await interaction.followup.send(
                    f"I couldn't reach the poster right now. Try messaging {author.mention} directly.",
                    ephemeral=True,
                )
                return
        await self.refresh_card(post_id)

    async def _open_private_thread(self, connect, post, user, author) -> discord.Thread | None:
        if connect is None:
            return None
        name = f"{user.display_name} × {author.display_name} · {post['hackathon']}"[:100]
        try:
            thread = await connect.create_thread(
                name=name,
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=10080,
                reason=f"LFT #{post['id']} match",
            )
        except discord.HTTPException as e:
            log.warning("Couldn't create LFT private thread in %s: %s", connect.id, e)
            return None
        try:
            await thread.add_user(author)
            await thread.add_user(user)
            await thread.send(
                f"{author.mention} {user.mention}\n"
                f"👋 **{user.display_name}** is interested in your **{post['hackathon']}** card: "
                f"{_card_url(post)}\n"
                "This thread is private to you two. Share skills and ideas, and hit **Team full** "
                "on the card once your team is set.",
                allowed_mentions=discord.AllowedMentions(users=[author, user]),
            )
        except discord.HTTPException as e:
            log.warning("Couldn't set up LFT thread %s: %s", thread.id, e)
            try:
                await thread.delete()
            except discord.HTTPException:
                pass
            return None
        return thread

    async def _public_fallback(self, post, user, author) -> discord.Thread | None:
        thread = await self._card_thread(post)
        if thread is None:
            return None
        try:
            if thread.archived and not thread.locked:
                await thread.edit(archived=False)
            await thread.send(
                f"{author.mention} 👋 {user.mention} is interested in joining! "
                "Reply here or DM each other.",
                allowed_mentions=discord.AllowedMentions(users=[author, user]),
            )
        except discord.HTTPException as e:
            log.warning("LFT public fallback failed for post %s: %s", post["id"], e)
            return None
        return thread

    async def refresh_card(self, post_id: int) -> None:
        db = self.bot.db
        post = await db.get_lft_post(post_id)
        if post is None or not post["message_id"]:
            return
        thread = await self._card_thread(post)
        if thread is None:
            return
        embed = build_card(
            post,
            author=thread.guild.get_member(post["author_id"]),
            handles=await self._handles(post["author_id"]),
            interested=await db.count_lft_interests(post_id),
        )
        try:
            if thread.archived and not thread.locked:
                await thread.edit(archived=False)
            await thread.get_partial_message(post["message_id"]).edit(
                embed=embed, view=card_view(post_id, open_=post["status"] == "open")
            )
        except discord.HTTPException as e:
            log.warning("Couldn't refresh LFT card %s: %s", post_id, e)

    # ── closing ───────────────────────────────────────────
    async def handle_close(self, interaction: discord.Interaction, post_id: int) -> None:
        db = self.bot.db
        post = await db.get_lft_post(post_id)
        if post is None or post["status"] != "open":
            await interaction.response.send_message("This card is already closed.", ephemeral=True)
            return
        perms = interaction.permissions
        if interaction.user.id != post["author_id"] and not (perms.manage_threads or perms.manage_guild):
            await interaction.response.send_message(
                "Only the poster (or a mod) can close this card.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await db.close_lft_post(post_id, "closed"):
            await interaction.followup.send("This card is already closed.", ephemeral=True)
            return
        await self._finalize(post_id)
        await interaction.followup.send("✅ Closed. Good luck at the hackathon!", ephemeral=True)

    async def _finalize(self, post_id: int) -> None:
        """Grey out the card, then tag it Closed, lock and archive its post."""
        await self.refresh_card(post_id)  # must happen before archiving
        post = await self.bot.db.get_lft_post(post_id)
        thread = await self._card_thread(post)
        if thread is None:
            return
        tags = list(thread.applied_tags)
        forum = thread.parent
        if isinstance(forum, discord.ForumChannel):
            closed = next((t for t in forum.available_tags if t.name.lower() == CLOSED_TAG.lower()), None)
            if closed is not None and all(t.id != closed.id for t in tags):
                tags = [closed, *tags][:MAX_TAGS]
        try:
            await thread.edit(applied_tags=tags, locked=True, archived=True, reason="LFT card closed")
        except discord.HTTPException as e:
            log.warning("Couldn't archive LFT post %s: %s", post_id, e)

    async def expire_stale_posts(self) -> None:
        await self.bot.wait_until_ready()
        for post in await self.bot.db.stale_lft_posts(EXPIRE_DAYS):
            if await self.bot.db.close_lft_post(post["id"], "expired"):
                await self._finalize(post["id"])

    # ── setup ─────────────────────────────────────────────
    @app_commands.command(name="setteamup", description="Configure the hackathon team-up board (/lft).")
    @app_commands.describe(
        forum="Forum channel where /lft cards are posted",
        connect_channel="Text channel where private match threads are opened",
        ping_role="Role that /lft ping:true pings (default: a role named Team Up)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setteamup(
        self,
        interaction: discord.Interaction,
        forum: discord.ForumChannel,
        connect_channel: discord.TextChannel,
        ping_role: discord.Role | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        role = ping_role or _find_role(guild, "Team Up")
        await self.bot.db.set_teamup_config(guild.id, forum.id, connect_channel.id, role.id if role else None)

        report = [f"✅ Team-up board: cards in {forum.mention}, private chats in {connect_channel.mention}."]
        report.append(f"Ping role: {role.mention}" if role else
                      "No ping role set, so `/lft ping:true` won't ping anyone.")
        report.extend(await self._ensure_tags(forum))

        me = guild.me
        fp, cp = forum.permissions_for(me), connect_channel.permissions_for(me)
        missing = []
        if connect_channel.is_news():
            missing.append(f"a regular text channel instead of {connect_channel.mention} "
                           "(announcement channels can't hold private threads)")
        if not (fp.view_channel and fp.send_messages):
            missing.append(f"View Channel + Send Messages in {forum.mention}")
        if not fp.manage_threads:
            missing.append(f"Manage Threads in {forum.mention} (to lock closed cards)")
        if not (cp.view_channel and cp.create_private_threads and cp.send_messages_in_threads):
            missing.append(f"View Channel + Create Private Threads + Send Messages in Threads "
                           f"in {connect_channel.mention}")
        if role and not role.mentionable and not fp.mention_everyone:
            missing.append(f"{role.mention} isn't mentionable, so pings won't notify. Make it "
                           "mentionable or give me Mention @everyone")
        if missing:
            report.append("⚠️ I still need: " + "; ".join(missing))
        report.append("Run `/teamupinfo` to post a public how-to guide for members.")
        await interaction.followup.send(
            "\n".join(report), ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(name="teamupinfo", description="Post a public guide to the hackathon team-up board.")
    @app_commands.describe(channel="Where to post it (defaults to this channel)")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def teamupinfo(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        forum, connect, role = await self._config(interaction.guild)
        if forum is None:
            await interaction.response.send_message("Run `/setteamup` first.", ephemeral=True)
            return
        target = channel or interaction.channel
        embed = build_guide(
            forum.mention,
            connect.mention if connect else None,
            role.mention if role else None,
        )
        try:
            await target.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.response.send_message(
                f"I can't post in {target.mention} (need Send Messages + Embed Links).", ephemeral=True
            )
            return
        await interaction.response.send_message(f"✅ Guide posted in {target.mention}.", ephemeral=True)

    async def _ensure_tags(self, forum: discord.ForumChannel) -> list[str]:
        existing = {t.name.lower() for t in forum.available_tags}
        missing = [n for n in ALL_TAG_NAMES if n.lower() not in existing]
        if not missing:
            return ["🏷️ All forum tags already exist."]
        to_add = missing[: max(0, FORUM_TAG_LIMIT - len(forum.available_tags))]
        skipped = missing[len(to_add):]
        lines: list[str] = []
        if to_add:
            if not forum.permissions_for(forum.guild.me).manage_channels:
                return [f"🏷️ Couldn't create tags (I need Manage Channels on {forum.mention}). "
                        "Missing: " + ", ".join(missing)]
            new_tags = [
                *forum.available_tags,
                *(discord.ForumTag(name=n, emoji=_TAG_EMOJI.get(n), moderated=(n == CLOSED_TAG))
                  for n in to_add),
            ]
            try:
                await forum.edit(available_tags=new_tags)
                lines.append(f"🏷️ Created {len(to_add)} tags: " + ", ".join(to_add))
            except discord.HTTPException as e:
                lines.append(f"🏷️ Couldn't create tags ({e.text or e}).")
        if skipped:
            lines.append(f"🏷️ Forum is at Discord's {FORUM_TAG_LIMIT}-tag limit, skipped: "
                         + ", ".join(skipped))
        return lines

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "You need the **Manage Server** permission for this."
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeamUp(bot))
