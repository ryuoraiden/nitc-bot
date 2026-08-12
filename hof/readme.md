# NITC Bot

A community Discord bot for the NIT Calicut server. It pulls past papers and
study material out of Google Drive, watches the official NITC notice boards, and
keeps the competitive-programming crowd on schedule — all from slash commands,
running 24/7 on a small VM.

## What it does

- **Study materials** — `/pyq` and `/material` search a full-text index built
  from public Google Drive folders (past papers, notes, slides, textbooks).
  Anyone can register another folder with `/addsource`; the index refreshes
  daily.
- **Notice watcher** — polls the academic and general NITC notice boards every
  3 hours and posts new circulars, fee deadlines and scholarships to a chosen
  channel.
- **High-signal bulletin** — tags notices as deadlines, workshops, placements or
  admin updates, so `/bulletin` shows only what matters. Servers pick immediate
  posts, a morning digest, or both.
- **Contest reminders** — Codeforces, LeetCode, CodeChef and AtCoder schedules
  from clist.by, with configurable lead times and an optional role ping.
- **Handle linking** — `/link` and `/verify` prove you own a CP handle without
  passwords (you place a one-time token in your profile), then `/profile` and
  `/leaderboard` show live ratings and solved counts.
- **Server plumbing** — welcome cards, self-role panels with buttons and
  dropdowns, and sticky messages that survive restarts.

## Built with

Python 3.11 and discord.py, SQLite via aiosqlite, Pillow for the welcome cards.
Deployed with a one-line setup script to a systemd unit that restarts on crash
and starts on boot.

Contributions are welcome — see `CONTRIBUTING.md` in the repository.
