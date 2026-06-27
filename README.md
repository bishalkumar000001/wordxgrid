# 🎮 Word Grid Challenge Bot

A feature-rich Telegram group game bot where players find hidden words in a letter grid — with live color highlights, a global leaderboard, hints, and timed gameplay.

---

## 📸 How It Looks

- Bot sends a **word grid image** pinned to the group
- Below the image: numbered word list with first-letter hints (e.g. `W--- (4)`)
- When a word is guessed, its cells **light up in color** on the grid image
- After 10 minutes or all words found, grid is **unpinned automatically**

---

## ✨ Features

- 🟢 **Easy mode** (`/new`) and 🔴 **Hard mode** (`/new_hard`)
- 🎨 **Live color highlights** — each found word gets its own color in the grid
- ⏰ **10-minute timer** — auto-closes and unpins on timeout
- 🏆 **Scoring** — 4 pts (1st word) · 3 pts (others) · 5 pts (last word)
- 🔍 **Hints** — 2 per game, sent privately only to the person who asked
- 📊 **Global leaderboard** across all groups — daily / weekly / monthly / yearly / all-time
- 💾 **Persistent data** — scores and leaderboard survive bot updates/restarts
- 📌 **Auto-pin / auto-unpin** — grid pinned on start, unpinned on end

---

## 🃏 Word Length Sequence

### Easy Mode (`/new`)
| Words | Length |
|-------|--------|
| 1 – 4  | 4 letters |
| 5 – 7  | 5 letters |
| 8 – 9  | 6 letters |
| 10     | 7 letters |
| 11 – 12 | 8 letters |

### Hard Mode (`/new_hard`)
| Words | Length |
|-------|--------|
| 1 – 4  | 6 letters |
| 5 – 6  | 7 letters |
| 7 – 8  | 8 letters |
| 9 – 10  | 9 letters |
| 11 – 12 | 10 letters |

---

## 🤖 Commands

| Command | Description |
|---------|-------------|
| `/new` | Start an easy game |
| `/new_hard` | Start a hard game |
| `/hint` | Get a hint (sent privately, 2 per game) |
| `/leaderboard` or `/lb` | Global leaderboard (all-time) |
| `/lb day` | Today's top players |
| `/lb week` | This week's top players |
| `/lb month` | This month's top players |
| `/lb year` | This year's top players |
| `/stats` | Your personal global rank and points |
| `/help` | Show all commands |
| `/start` | Welcome message |

---

## 🚀 Deploy to Heroku

### Step 1 — Create your bot
1. Message [@BotFather](https://t.me/botfather) → `/newbot`
2. Copy your **Bot Token**
3. Send `/setprivacy` → select your bot → set to **Disabled** *(lets bot read group messages)*

### Step 2 — Push to GitHub
Upload all these files to a **new GitHub repository** (no subfolder):
```
bot.py
config.py
database.py
wordgrid.py
words.py
requirements.txt
Procfile
runtime.txt
app.json
```

### Step 3 — Deploy on Heroku
1. Go to [heroku.com](https://heroku.com) → **New App**
2. Connect your GitHub repo under **Deploy** tab
3. Go to **Settings** → **Buildpacks** → Add **`heroku/python`**
4. Go to **Settings** → **Config Vars** → add:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | Your bot token from BotFather |
| `OWNER_ID` | Your Telegram user ID (optional) |
| `LOG_GROUP_ID` | Group ID for logs (optional) |
| `SUPPORT_CHANNEL` | Your support channel link (optional) |

5. Go to **Deploy** tab → click **Deploy Branch**
6. Go to **Resources** tab → turn **ON** the `worker` dyno (turn OFF the `web` dyno if shown)

### Step 4 — Add bot to your group
1. Add the bot to a Telegram group
2. Make it **Admin** with **pin messages** permission
3. Send `/new` to start your first game!

---

## 🗂 File Structure

```
├── bot.py           # Main bot — all commands and game logic
├── config.py        # Environment variable config
├── database.py      # SQLite database — scores, games, users
├── wordgrid.py      # Grid generator + image renderer (Pillow)
├── words.py         # Word lists for lengths 4–10
├── requirements.txt # Python dependencies
├── Procfile         # Heroku worker dyno config
├── runtime.txt      # Python version (3.12.9)
└── app.json         # Heroku app manifest
```

---

## ⚙️ Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ Yes | Telegram bot token from @BotFather |
| `OWNER_ID` | No | Your Telegram user ID |
| `LOG_GROUP_ID` | No | Group ID for bot event logs |
| `SUPPORT_CHANNEL` | No | Support channel shown in /start |
| `DB_PATH` | No | Path for SQLite file (default: `wordgrid.db`) |

---

## ⚠️ Important Notes

- **Heroku filesystem resets** on dyno restart — SQLite data will be lost unless you use a persistent storage addon or an external database
- The bot requires **Admin rights** in the group to pin/unpin messages
- Players must type words exactly as-is (case insensitive) in the group chat to score points
- Only **one game per group** can run at a time

---

## 🛠 Tech Stack

- **Python 3.12**
- **python-telegram-bot 21.6** (async, job queue)
- **Pillow** — grid image generation with RGBA color overlays
- **SQLite** — persistent storage via `database.py`

---

## 📄 License

MIT — free to use, modify, and deploy.
