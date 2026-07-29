"""Telegram group lobby for Ludo.

Flow:
  1. /ludo in a group → bot posts a lobby card with player list + Join button
  2. Others tap "Join Game" → added to the room, card updates
  3. Once 2+ players joined, "Open Game" button appears for everyone
  4. Tapping "Open Game" opens the web app at the correct room

Requires two Render environment variables:
  LUDO_WEB_APP_URL  – base URL of the deployed Ludo React app
                      e.g. https://abc123.replit.dev
  LUDO_API_URL      – base URL of the Ludo API (same Replit project, /api path)
                      e.g. https://abc123.replit.dev/api
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

LUDO_WEB_APP_URL = os.environ.get("LUDO_WEB_APP_URL", "").strip().rstrip("/")
LUDO_API_URL = os.environ.get("LUDO_API_URL", "").strip().rstrip("/")

COLORS = ["🔴 Red", "🔵 Blue", "🟢 Green", "🟡 Yellow"]


# ── API helpers ──────────────────────────────────────────────────────────────

async def _api_post(path: str, body: dict[str, Any]) -> dict[str, Any] | None:
    if not LUDO_API_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{LUDO_API_URL}{path}", json=body)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return None


async def _api_get(path: str) -> dict[str, Any] | None:
    if not LUDO_API_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{LUDO_API_URL}{path}")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return None


# ── Message helpers ──────────────────────────────────────────────────────────

def _player_display(room: dict[str, Any]) -> str:
    lines = ["🎲 *Ludo — Game Lobby*\n"]
    for i, player in enumerate(room.get("players", [])):
        lines.append(f"{COLORS[i]}: *{player['name']}*")
    for i in range(len(room.get("players", [])), 4):
        lines.append(f"{COLORS[i]}: _waiting…_")
    count = len(room.get("players", []))
    status = room.get("status", "waiting")
    if status == "waiting":
        lines.append(f"\n_{count}/4 joined — tap Join to play_")
    else:
        lines.append(f"\n_{count}/4 joined — tap Open Game to start playing_")
    return "\n".join(lines)


def _lobby_keyboard(room_id: str, player_count: int, is_private: bool) -> InlineKeyboardMarkup:
    row = []
    if player_count < 4:
        row.append(InlineKeyboardButton("➕ Join Game", callback_data=f"ludo:join:{room_id}"))
    if player_count >= 2 and LUDO_WEB_APP_URL:
        game_url = f"{LUDO_WEB_APP_URL}/room/{room_id}"
        if is_private:
            row.append(InlineKeyboardButton("🎮 Open Game", web_app=WebAppInfo(url=game_url)))
        else:
            row.append(InlineKeyboardButton("🎮 Open Game", url=game_url))
    return InlineKeyboardMarkup([row]) if row else InlineKeyboardMarkup([[]])


def _player_name(user) -> str:
    return " ".join(filter(None, [user.first_name, user.last_name])) or user.username or "Player"


# ── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_ludo(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    if not LUDO_WEB_APP_URL or not LUDO_API_URL:
        await message.reply_text(
            "⚠️ Ludo is not fully configured yet.\n"
            "Set LUDO_WEB_APP_URL and LUDO_API_URL in Render."
        )
        return

    name = _player_name(user)
    room = await _api_post("/ludo/rooms", {
        "name": f"{name}'s game",
        "playerName": name,
        "playerId": str(user.id),
    })

    if not room:
        await message.reply_text("⚠️ Could not create a Ludo room. Please try again.")
        return

    room_id = room["id"]
    is_private = update.effective_chat.type == Chat.PRIVATE

    await message.reply_text(
        _player_display(room),
        parse_mode="Markdown",
        reply_markup=_lobby_keyboard(room_id, len(room.get("players", [])), is_private),
    )


async def cb_ludo_join(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not user:
        await query.answer()
        return

    room_id = query.data.split(":", 2)[2]
    name = _player_name(user)

    room = await _api_post(f"/ludo/rooms/{room_id}/join", {
        "playerName": name,
        "playerId": str(user.id),
    })

    if not room:
        existing = await _api_get(f"/ludo/rooms/{room_id}")
        if not existing:
            await query.answer("This game no longer exists.", show_alert=True)
        else:
            await query.answer("Could not join — the room may be full or already started.", show_alert=True)
        return

    await query.answer(f"✅ You joined as {name}!")
    is_private = update.effective_chat.type == Chat.PRIVATE
    player_count = len(room.get("players", []))

    await query.edit_message_text(
        _player_display(room),
        parse_mode="Markdown",
        reply_markup=_lobby_keyboard(room_id, player_count, is_private),
    )


async def cb_ludo_from_game(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the Ludo button inside the /game selector."""
    query = update.callback_query
    await query.answer()
    await cmd_ludo(update, _context)


def register_ludo_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("ludo", cmd_ludo))
    app.add_handler(CallbackQueryHandler(cb_ludo_join, pattern=r"^ludo:join:"))
    app.add_handler(CallbackQueryHandler(cb_ludo_from_game, pattern=r"^game:ludo:"))
