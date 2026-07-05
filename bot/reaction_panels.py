"""Reaction-role panel definitions (transcribed from the Carl Bot setup).

Each panel: an author line (emoji + title), optional intro text, and sections.
A section is (heading_or_None, [(emoji, role_query, description_or_None), ...]).

`role_query` is matched against server roles by their "clean" name (the text
after the last '·' separator, emojis stripped), so it survives the fancy
"<emoji> ǀ · Name" role naming without hardcoding role IDs. Reaction emoji are
what the bot adds and listens for; they need not match the emoji inside the
role's own name. Discord allows at most 20 reactions per message.
"""
from __future__ import annotations

PANELS: dict[str, dict] = {
    "pronouns": {
        "author": "❓ What are your pronouns?",
        "color": 0x5865F2,
        "sections": [
            (None, [
                ("🔵", "He/Him", None),
                ("🟡", "She/Her", None),
            ]),
        ],
    },
    "interests": {
        "author": "⭐ What are your interests?",
        "color": 0xF1C40F,
        "sections": [
            (None, [
                ("🎤", "Music", None),
                ("🍿", "Movies", None),
                ("㊙️", "Anime", None),
                ("🎮", "Gaming", None),
                ("💻", "Tech", None),
                ("🏋️", "Fitness", None),
            ]),
        ],
    },
    "notifications": {
        "author": "📢 What do you want notifications for?",
        "color": 0xE67E22,
        "sections": [
            (None, [
                ("📰", "Updates Ping", "Important server/campus updates, used sparingly"),
                ("🎮", "Event Ping", "Game nights, fests, watch parties"),
                ("👥", "Team Up", "Projects, hackathons, lab partners"),
                ("⭐", "Revive Chat", "Low-frequency nudges to keep chat alive"),
                ("📊", "Poll Ping", "Fun polls in daily-poll"),
            ]),
        ],
    },
    "clubs": {
        "author": "🏛️ Official Club & Student Body Membership Roles",
        "color": 0x2ECC71,
        "intro": (
            "If you are an officially affiliated member of any NITC club, student body, "
            "service organization, or fest team, you may assign your role below.\n\n"
            "Please react only if you are genuinely part of the respective organization.\n"
            "This system operates on mutual trust and maturity."
        ),
        "sections": [
            ("🏫 Student Bodies", [
                ("🏛️", "SAC", None),
                ("🎓", "SGB", None),
            ]),
            ("🛠️ Technical & Professional Clubs", [
                ("⚡", "IEEE SB", None),
                ("🌐", "GDSC", None),
                ("🐧", "FOSS Cell", None),
                ("🤖", "AI Club", None),
                ("🧠", "CP Hub", None),
                ("🏎️", "Team Unwired", None),
                ("✈️", "Aero Unwired", None),
                ("🔢", "Math Club", None),
                ("❓", "Quiz Club", None),
            ]),
            ("🎭 Cultural & Arts", [
                ("🎭", "CCAR", None),
                ("🎶", "Music Club", None),
                ("💃", "DnD", None),
                ("🎞️", "AVC", None),
                ("🌸", "ICA", None),
            ]),
            ("🌿 Service & Outreach", [
                ("🌿", "NSS", None),
                ("⚓", "NCC", None),
            ]),
            ("🎉 Fest Teams", [
                ("🎤", "Ragam Crew", None),
                ("⚙️", "Tathva Crew", None),
            ]),
        ],
    },
}
