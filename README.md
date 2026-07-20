# VelocityBots — WordGrid + Paheli (Riddles) Game Bot

A complete Telegram game bot with two games:
- **🔤 Word Grid** — Find hidden words in a letter grid
- **🧩 Paheli** — Solve riddles in Hindi & English

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Main bot entry point (updated with `/game` command + Paheli import) |
| `paheli.py` | Complete Paheli game module (plug-and-play) |
| `paheli_db.py` | MongoDB operations for Paheli |
| `riddles.json` | 200 riddles (English & Hindi, all categories) |
| `config.py` | Bot configuration |
| `database.py` | WordGrid MongoDB operations |
| `wordgrid.py` | Grid generation & rendering |
| `words.py` | Word lists |

---

## Setup

### 1. Add environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
BOT_TOKEN=your_telegram_bot_token
MONGO_URL=mongodb+srv://user:pass@cluster.mongodb.net/
OWNER_ID=your_telegram_id
LOG_GROUP_ID=-100your_log_group_id   # optional
SUPPORT_CHANNEL=https://t.me/your_group  # optional
SUDO_USERS=id1,id2  # comma-separated admin IDs
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run locally

```bash
python bot.py
```

---

## Heroku Deployment

### Using existing Heroku app

Since your bot is already on Heroku, just add/update these files:

1. Copy the new files to your Heroku project directory:
   - `paheli.py`
   - `paheli_db.py`
   - `riddles.json`

2. Replace `bot.py` with the updated version.

3. Update `requirements.txt` (add `python-dotenv>=1.0.0`).

4. Push to Heroku:
```bash
git add paheli.py paheli_db.py riddles.json bot.py requirements.txt
git commit -m "Add Paheli riddle game + /game command"
git push heroku main
```

5. Restart dyno:
```bash
heroku restart
```

---

## Commands

### Game Selector
| Command | Description |
|---------|-------------|
| `/game` | Show game picker (WordGrid or Paheli) |

### WordGrid
| Command | Description |
|---------|-------------|
| `/new` | Start easy Word Grid (10×10) |
| `/new_hard` | Start hard Word Grid (12×12) |
| `/hint` | Get a word hint |
| `/end` | End game (admins only) |
| `/lb` | Word Grid leaderboard |
| `/stats` | Your WordGrid stats |

### Paheli (Riddles)
| Command | Description |
|---------|-------------|
| `/paheli` | Start a new riddle |
| `/answer TEXT` | Answer the riddle |
| `/hint` | Get a hint (free first, costs token after) |
| `/skip` | Skip riddle (costs Skip Token) |
| `/daily` | Claim daily reward |
| `/weekly` | Claim weekly reward |
| `/profile` | Your profile (level, XP, coins, gems) |
| `/inventory` | Your items |
| `/shop` | Buy hints, skips, boosts, chests |
| `/settings` | Change language/difficulty preference |
| `/challenge` | PvP riddle challenge (reply to a user) |
| `/clan` | Clan system |
| `/plb` | Paheli leaderboard |
| `/paheli_stats` | Paheli stats |
| `/paheli_help` | Full paheli help |

### Admin (SUDO_USERS only)
| Command | Description |
|---------|-------------|
| `/addriddle` | Add a custom riddle |
| `/deleteriddle ID` | Delete a custom riddle |
| `/pban USER_ID` | Ban user from Paheli |
| `/punban USER_ID` | Unban user from Paheli |
| `/ridstats` | Admin statistics |
| `/broadcast` | Broadcast to all groups |

---

## Player System

### XP & Levels
- 16 levels (Novice → God of Puzzles)
- XP earned every riddle solved
- 2× XP Boost available in shop

### Economy
- 🪙 **Coins** — earned by solving riddles, daily rewards
- 💎 **Gems** — rare, earned via weekly rewards + streaks
- Daily streak bonus: +25 coins/day (capped at 500)
- Gem every 7-day streak

### Points by Difficulty
| Difficulty | Points | No-Hint Bonus |
|-----------|--------|---------------|
| 🟢 Easy | 10 | 12 |
| 🟡 Medium | 25 | 30 |
| 🔴 Hard | 50 | 60 |
| 💀 Legendary | 100 | 125 |

### Shop Items
| Item | Cost |
|------|------|
| 1 Hint Token | 50 🪙 |
| 5 Hints Pack | 200 🪙 |
| Skip Token | 75 🪙 |
| 3 Skips Pack | 200 🪙 |
| Lucky Wheel | 100 🪙 |
| Silver Chest | 150 🪙 |
| Gold Chest | 400 🪙 |
| 2× XP Boost (1h) | 500 🪙 |

---

## Adding More Riddles

Edit `riddles.json` and add entries following this format:

```json
{
  "id": 201,
  "question": "Your riddle question here?",
  "answer": "answer in lowercase",
  "hints": ["hint 1", "hint 2", "hint 3"],
  "category": "general",
  "difficulty": "easy",
  "language": "en",
  "points": 10
}
```

**Categories:** `general`, `movies`, `sports`, `science`, `math`, `tech`, `history`  
**Difficulties:** `easy` (10pts), `medium` (25pts), `hard` (50pts), `legendary` (100pts)  
**Languages:** `en` (English), `hi` (Hindi)

Or use the admin command `/addriddle` directly in your Telegram group.

---

## MongoDB Collections

The Paheli module uses these collections in the **same `wordgrid` database**:
- `paheli_players` — Player profiles
- `paheli_sessions` — Active/completed riddle sessions
- `paheli_scores` — Score history
- `paheli_challenges` — PvP challenges
- `paheli_clans` — Clan data
- `paheli_riddles` — Custom riddles (added via /addriddle)
- `paheli_cooldowns` — Anti-spam cooldowns
- `paheli_banned` — Banned users

No separate database needed — all in one.
