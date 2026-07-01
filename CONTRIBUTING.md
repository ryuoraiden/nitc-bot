# Contributing

Thanks for wanting to work on the bot. It's built for the NITC server and anyone
from the server is welcome to add to it, whether that's a whole new feature or a
small fix. You don't need to be an expert. If you're new to this, the setup below
should get you running locally in a few minutes.

## Getting set up

You need Python 3.11 or newer.

1. Fork the repo on GitHub, then clone your fork:
   ```sh
   git clone https://github.com/YOUR_USERNAME/nitc-bot.git
   cd nitc-bot
   ```

2. Make a virtual environment and install the dependencies:
   ```sh
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   # source .venv/bin/activate   # macOS / Linux
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in the values. For local testing you'll
   want your own test bot (make one at https://discord.com/developers/applications)
   and your own test server. Set `GUILD_ID` to your test server so commands show up
   instantly instead of waiting on Discord's global sync.

4. Run it:
   ```sh
   python -m bot.main
   ```

Never commit your `.env`. It's already in `.gitignore`, keep it that way.

## How the code is laid out

```
bot/
  main.py            starts the bot, loads the cogs, syncs slash commands
  config.py          reads settings from .env
  db.py              SQLite storage
  services.py        fetches and de-duplicates contest data
  platforms/         one file per external platform (Codeforces, LeetCode, clist, ...)
  cogs/              the actual commands, grouped by area
    contests.py      /contests, /setchannel, /setrole + the reminder loop
    linking.py       /link, /verify, /unlink, /profile
    leaderboard.py   /leaderboard
```

If you're adding a **command**, it usually goes in an existing cog or a new one under
`bot/cogs/`. New cogs need to be added to `INITIAL_COGS` in `bot/main.py`.

If you're adding support for a **new platform**, add a file under `bot/platforms/`
following the shape of `codeforces.py` (a `get_user` and, if it can be verified, a
`verify_token`), then register it in `bot/platforms/registry.py`.

## Making a change

1. Branch off `main`:
   ```sh
   git checkout -b my-feature
   ```
2. Make your change. Try to match the style of the code already there.
3. Test it on your own server before opening the PR. At minimum, make sure the bot
   still starts and your command works.
4. Push and open a pull request against `main`. Describe what it does and how you
   tested it. Screenshots of it working in Discord are always helpful.

## Ideas to start with

- Add `/profile` and `/leaderboard` support for CodeChef and AtCoder
- Let users set their timezone so reminders and times show in local time
- Opt-in DM reminders in addition to the channel post
- Simple fun or utility commands the server asks for in #server-suggestions

If you're unsure about something or want to claim an idea before building it, open an
issue or ask in the server first so two people don't build the same thing.
