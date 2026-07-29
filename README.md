# Ludo Telegram add-on

These files plug into the existing WordXGrid bot without touching any of its existing handlers.

| File | Purpose |
|---|---|
| `ludo.py` | Telegram `/ludo` command and Web App launch button |
| `ludo_db.py` | MongoDB helpers — same `MONGO_URL` / `wordgrid` DB as the bot |
| `ludo_api.py` | Flask blueprint (`/api/ludo/*`) — serves all room state for the web app |

## 1 — Copy the files

Copy all three Python files next to the existing `bot.py`, `paheli.py`, etc.

## 2 — Register the Telegram command

In `bot.py`, import and register the handler after the existing ones:

```python
from ludo import register_ludo_handlers

# inside main() after existing handler registrations:
register_ludo_handlers(app)
```

## 3 — Register the Flask API blueprint

In `web.py`, register the blueprint so the web app can call the room API:

```python
from ludo_api import ludo_api, init_ludo_api

init_ludo_api()          # creates MongoDB indexes on startup
app.register_blueprint(ludo_api)
```

## 4 — Add the "/game" Ludo button (optional)

If you want Ludo to appear in the existing `/game` selector inside `paheli.py`,
add a third button row to the inline keyboard where the other games are listed:

```python
[InlineKeyboardButton("🎲 Ludo", callback_data="game:ludo:start")]
```

The `register_ludo_handlers` call already handles the `game:ludo:` callback pattern.

## 5 — Set the Render environment variable

```text
LUDO_WEB_APP_URL=https://your-ludo-web-app.example.com/
```

Set this in your Render service's **Environment** tab.
The value must be the public HTTPS URL of the deployed React web app.

## 6 — Verify

```
/ludo
```

The bot replies with a **Play Ludo** button that opens the web app inside Telegram.

---

### Architecture note

The Flask process (`web.py`) now serves the Ludo room API at `/api/ludo/*`.
The React web app calls this API directly. Both the bot and the web app share
the same MongoDB `wordgrid` database. Word Grid and Paheli remain unchanged.
