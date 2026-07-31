# Ludo King Mini App — Integration Guide

## What's Been Added

| File | Description |
|---|---|
| `ludo_auth.py` | Telegram WebApp `initData` HMAC validation |
| `ludo_engine.py` | Server-authoritative game logic (dice, moves, captures, rankings) |
| `ludo_rooms_db.py` | MongoDB CRUD for Mini App rooms |
| `ludo_webapp.py` | Flask Blueprint + all SocketIO event handlers |
| `ludo_webapp_patch.py` | Patches `/ludo` command to send WebApp button |
| `web.py` | Updated Flask + Flask-SocketIO server |
| `templates/ludo/index.html` | Mini App single-page HTML |
| `static/ludo/style.css` | Full dark-mode Telegram-style CSS |
| `static/ludo/app.js` | Complete JavaScript app |
| `requirements.txt` | Updated with flask-socketio, eventlet |
| `Procfile` | Updated with `web:` dyno |
| `env.example` | Updated with new variables |

---

## Step 1 — Add 2 lines to `bot.py`

Open `bot.py` and find these two lines (they're already there):

```python
from ludo import register_ludo_handlers
```

After it, add:

```python
from ludo_webapp_patch import patch_ludo_command
```

Then find this line in `main()`:

```python
register_ludo_handlers(app)
```

After it, add:

```python
patch_ludo_command(app)          # Upgrades /ludo to send the Mini App button
```

That's the only change to `bot.py`.

---

## Step 2 — Environment Variables

Set these in your Render services:

### Both services (web + worker) need:
```
BOT_TOKEN=your_telegram_bot_token
MONGO_URL=mongodb+srv://...
OWNER_ID=...
```

### Web service only needs additionally:
```
WEBAPP_SECRET_KEY=some-random-secret-string-here
LUDO_TURN_TIMEOUT=60
```

### Worker (bot) service only needs additionally:
```
LUDO_WEB_APP_URL=https://your-web-service.onrender.com/ludo
```
*(Set this AFTER deploying the web service so you know its URL)*

---

## Step 3 — Deploy on Render

### A. Create Web Service (for Mini App)

1. New Service → **Web Service**
2. Connect your GitHub repo
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:** `python web.py`
5. Set environment variables (Step 2)
6. Deploy → copy the URL (e.g. `https://wordxgrid-web.onrender.com`)

### B. Create Worker Service (for Bot)

1. New Service → **Background Worker**
2. Same repo
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:** `python bot.py`
5. Set environment variables including `LUDO_WEB_APP_URL=<url from step A>/ludo`
6. Deploy

---

## Step 4 — Register Mini App with BotFather

1. Open @BotFather in Telegram
2. `/mybots` → select your bot
3. **Bot Settings → Menu Button** → Set URL to your web service URL: `https://yourapp.onrender.com/ludo`
4. Alternatively: just use `/ludo` command — it sends the button automatically

---

## How It Works

```
User types /ludo in a group
    ↓
Bot sends a message with [🎲 Play Ludo] WebApp button
    ↓
User taps button → Telegram opens Mini App at LUDO_WEB_APP_URL
    ↓
Mini App reads Telegram.WebApp.initData (auto-login, no separate login)
    ↓
Mini App connects via Socket.IO to the web service
    ↓
User creates or joins a room
    ↓
2-4 players connect → host clicks Start Game
    ↓
Full server-authoritative Ludo game via WebSocket events
    ↓
Game ends → rankings shown → Play Again → back to lobby
```

---

## Security

- All `initData` validated server-side with HMAC-SHA256 (`ludo_auth.py`)
- All game logic runs on the server (`ludo_engine.py`) — no client-side moves
- Dice rolled server-side — cannot be faked
- Room host controls kick / start

---

## Fallback Behavior

If `LUDO_WEB_APP_URL` is **not** set in the worker environment:
- `patch_ludo_command()` does nothing
- `/ludo` falls back to the original text-based Ludo game
- All existing functionality preserved

---

## MongoDB Collections Added

| Collection | Purpose |
|---|---|
| `ludo_webapp_rooms` | Mini App rooms (auto-expires after 1 hour) |

Existing collections (`ludo_games`, `games`, `scores`, etc.) are untouched.
