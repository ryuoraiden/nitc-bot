# NITC Bot

A community Discord bot for the NITC server. It's meant to grow into a general
purpose bot for whatever the server finds useful, and contributions are welcome
(see [CONTRIBUTING.md](CONTRIBUTING.md)).

The first feature set is competitive-programming tooling: it reminds a server of
upcoming contests (Codeforces, LeetCode, CodeChef, AtCoder, …) and lets members
link and show off their handles. More features will be added over time based on
suggestions from the server.

## Features

- **Contest reminders** — posts to a chosen channel at configurable lead times
  (default 24 h and 1 h before). Sourced from [clist.by](https://clist.by) with a
  Codeforces-API fallback.
- **`/contests`** — list upcoming contests on demand.
- **Account linking** — `/link`, `/verify`, `/unlink` with ownership verification
  (no passwords: you place a one-time token in your platform profile).
- **`/profile`** — show verified handles with live rating / solved counts.
- **Study materials** — `/pyq` and `/material` search a full-text index built from
  public Google Drive folders (past papers, notes, slides, textbooks). Anyone can
  register more folders with `/addsource`; the index refreshes daily.
- **Notice watcher** — polls the official NITC notice boards (academic + general)
  every 3 hours and posts new notices (fee deadlines, scholarships, circulars) to
  a configured channel. `/notices` shows the latest on demand.

## Slash commands

| Command | What it does |
|---|---|
| `/contests [limit]` | List upcoming contests |
| `/setchannel [channel]` | Choose the reminder channel *(Manage Server)* |
| `/setrole [role]` | Ping a role on reminders, or clear it *(Manage Server)* |
| `/link <platform> <handle>` | Start linking a handle |
| `/verify <platform>` | Confirm the token you placed on your profile |
| `/unlink <platform>` | Remove a linked handle |
| `/profile [member]` | Show CP profiles |
| `/leaderboard <platform>` | Rank the server's verified members |
| `/pyq <query>` | Find past papers (midsem/endsem/quiz) |
| `/material <query>` | Search all study materials |
| `/sources` | List indexed Drive folders |
| `/addsource <url>` | Add a public Drive folder to the index (open to everyone) |
| `/renamesource <source> <name>` | Rename a source *(Manage Server)* |
| `/removesource <source>` | Remove a source and its files *(Manage Server)* |
| `/reindex` | Re-crawl all sources *(Manage Server)* |
| `/notices [board] [limit]` | Latest notices from the NITC website |
| `/setnoticeschannel [channel]` | Auto-post new NITC notices there *(Manage Server)* |

## Setup

1. **Create the bot application**
   - Go to <https://discord.com/developers/applications> → *New Application*.
   - *Bot* tab → *Reset Token* → copy the token.
   - *Installation* / *OAuth2* → invite with the `bot` and `applications.commands`
     scopes and permissions: *Send Messages*, *Embed Links*.

2. **Get a clist.by key** (recommended)
   - Register at <https://clist.by>, then open <https://clist.by/api/v4/doc/> to
     find your username and API key.
   - Without it the bot still works but only shows Codeforces contests.

3. **Configure**
   ```sh
   cp .env.example .env      # then edit .env with your tokens
   ```

4. **Install & run** (Python 3.11+)
   ```sh
   python -m venv .venv
   .venv\Scripts\activate        # Windows (PowerShell: .venv\Scripts\Activate.ps1)
   pip install -r requirements.txt
   python -m bot.main
   ```

On first run the bot syncs its slash commands globally (can take a minute to appear).
In your server, run `/setchannel` to pick where reminders go.

## Project layout

```
bot/
  main.py            entrypoint + bot lifecycle
  config.py          env/.env configuration
  db.py              SQLite (aiosqlite) persistence
  services.py        contest fetching (clist + CF fallback, de-dup)
  platforms/
    base.py          Contest / PlatformUser models
    clist.py         aggregator schedule source
    codeforces.py    official CF API (schedule, user, verify)
    leetcode.py      unofficial GraphQL (user, verify)
    registry.py      platform lookup/verify registry
  cogs/
    contests.py      /contests, /setchannel, /setrole + reminder scheduler
    linking.py       /link, /verify, /unlink, /profile
```

## Deploying (24/7 on a VPS)

On a fresh Ubuntu 24.04 server (a $4/mo DigitalOcean droplet is plenty):

```sh
curl -fsSL https://raw.githubusercontent.com/ryuoraiden/nitc-bot/main/deploy/setup.sh | bash
```

Then copy your `.env` (and optionally `data/bot.db`) to `/opt/nitc-bot/` and run
`systemctl start nitc-bot`. The service auto-restarts on crash and starts on boot.
To ship new code later: push to `main`, then run `/opt/nitc-bot/deploy/update.sh`
on the server.

## Notes & limits

- **LeetCode / CodeChef have no official APIs.** LeetCode uses the site's GraphQL
  endpoint; it can break if they change it. CodeChef schedule comes via clist.by.
- Verification proves handle *ownership*, not a real login — platforms don't offer
  third-party OAuth.
- Reminders are per-server subscriptions, not per-user registration (the platforms
  don't expose who registered for a contest).
- For 24/7 uptime, host on a small always-on VPS / Railway / Fly.io — free tiers that
  sleep will drop the gateway connection.

## Roadmap

- CodeChef & AtCoder user lookup for `/profile`
- Per-user timezone + DM reminders
- Server leaderboards and post-contest rating-change announcements
