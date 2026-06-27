# Word Grid Challenge Bot — Deployment Guide

## Prerequisites

1. A Telegram Bot Token from [@BotFather](https://t.me/botfather)
2. A [Heroku](https://heroku.com) account (free or paid)
3. [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli) installed

---

## Step 1 — Create your Bot

1. Message [@BotFather](https://t.me/botfather) on Telegram
2. Send `/newbot` and follow the steps
3. Copy the **Bot Token** (looks like `123456:ABCdef...`)
4. Also send `/setprivacy` → select your bot → set to **Disabled**  
   *(This lets the bot read group messages)*

---

## Step 2 — Get your IDs (optional but recommended)

- **Your user ID**: Message [@userinfobot](https://t.me/userinfobot) — this is your `OWNER_ID`
- **Log group ID**: Create a group, add your bot, then message [@getmyid_bot](https://t.me/getmyid_bot) — this is your `LOG_GROUP_ID`
- **Support channel**: Your support channel username e.g. `https://t.me/mychannel`

---

## Step 3 — Deploy to Heroku

```bash
# 1. Navigate to the bot folder
cd wordgrid_bot

# 2. Log in to Heroku
heroku login

# 3. Create a new Heroku app
heroku create your-wordgrid-bot

# 4. Set environment variables
heroku config:set BOT_TOKEN="your_bot_token_here"
heroku config:set OWNER_ID="your_telegram_user_id"        # optional
heroku config:set LOG_GROUP_ID="-100your_group_id"        # optional
heroku config:set SUPPORT_CHANNEL="https://t.me/channel"  # optional

# 5. Add Heroku Postgres addon (for persistent data across deploys/restarts)
heroku addons:create heroku-postgresql:mini

# 6. Push the code
git init
git add .
git commit -m "Initial deploy"
git push heroku main

# 7. Scale the worker dyno
heroku ps:scale worker=1

# 8. Check logs
heroku logs --tail
```

---

## Step 4 — Using Postgres (recommended for Heroku)

Heroku's filesystem resets on every deploy, so SQLite data would be lost.  
When you add the Postgres addon, Heroku sets `DATABASE_URL` automatically.

To use Postgres, install `psycopg2` and update `database.py` to use `DATABASE_URL`.  
The current setup uses SQLite by default (`DB_PATH=wordgrid.db`).  
For a quick persistent option on Heroku, add the Heroku Postgres addon and use the  
`heroku-postgresql:mini` plan (free tier).

Alternatively, keep SQLite by setting:
```bash
heroku config:set DB_PATH=/app/wordgrid.db
```
Note: SQLite data on Heroku **will be lost** on dyno restart. Use Postgres for production.

---

## Step 5 — Add the bot to your group

1. Add your bot to a Telegram group
2. Make the bot an **admin** (so it can pin/unpin messages)
3. Send `/new` to start a game!

---

## Commands

| Command | Description |
|---------|-------------|
| `/new` | Start an easy game |
| `/new_hard` | Start a hard game |
| `/hint` | Get a hint (2 per game, private) |
| `/leaderboard` or `/lb` | Global leaderboard |
| `/lb day` | Today's top players |
| `/lb week` | This week's top players |
| `/lb month` | This month's top players |
| `/lb year` | This year's top players |
| `/stats` | Your personal stats |
| `/help` | Show help |

---

## Scoring

- **First word found**: 4 points
- **Words 2–11**: 3 points each
- **Last word (12th)**: 5 points

## Word lengths

### Easy mode (/new)
| Words | Length |
|-------|--------|
| 1–4   | 4 letters |
| 5–7   | 5 letters |
| 8–9   | 6 letters |
| 10    | 7 letters |
| 11–12 | 8 letters |

### Hard mode (/new_hard)
| Words | Length |
|-------|--------|
| 1–4   | 6 letters |
| 5–6   | 7 letters |
| 7–8   | 8 letters |
| 9–10  | 9 letters |
| 11–12 | 10 letters |

---

## Updating the bot

Your data (user points, leaderboard) is stored in the database and is **never reset**  
on bot updates. Just push new code:

```bash
git add .
git commit -m "Update bot"
git push heroku main
```

The Heroku Postgres database persists across all deploys.
