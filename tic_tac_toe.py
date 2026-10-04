"""Tic Tac Toe for the existing VelocityBots leaderboard.

A match is started with /tictactoe in a group. Another member joins via button.
The winner receives exactly 10 points through database.add_score(), so those
points appear in the bot's existing shared leaderboard.
"""
import asyncio
import logging
import uuid
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import database as db

logger = logging.getLogger(__name__)
GAMES: Dict[str, Dict[str, Any]] = {}
LOCK = asyncio.Lock()


def _name(user) -> str:
    name = " ".join(part for part in [user.first_name, user.last_name] if part)
    return name or (f"@{user.username}" if user.username else f"User {user.id}")


def _board_keyboard(game_id: str, game: dict) -> InlineKeyboardMarkup:
    rows = []
    board = game["board"]
    for r in range(3):
        row = []
        for c in range(3):
            idx = r * 3 + c
            value = board[idx]
            label = value if value else "⬜"
            row.append(InlineKeyboardButton(label, callback_data=f"ttt:move:{game_id}:{idx}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🛑 End match", callback_data=f"ttt:cancel:{game_id}")])
    return InlineKeyboardMarkup(rows)


def _status(game: dict) -> str:
    x = game["players"].get("X")
    o = game["players"].get("O")
    x_name = x["name"] if x else "Waiting for player…"
    o_name = o["name"] if o else "Waiting to join…"
    if not game["started"]:
        return ("🎮 <b>Tic Tac Toe challenge!</b>\n\n"
                f"❌ X: {x_name}\n⭕ O: {o_name}\n\n"
                "Another member can join below. The match starts when they join.")
    turn = game["players"][game["turn"]]["name"]
    symbol = "❌" if game["turn"] == "X" else "⭕"
    return ("🎮 <b>Tic Tac Toe</b>\n\n"
            f"❌ X: {x_name}\n⭕ O: {o_name}\n\n"
            f"{symbol} <b>{turn}</b>'s turn")


def _winner(board):
    lines = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for a, b, c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    if all(board):
        return "draw"
    return None


async def cmd_tictactoe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("🎮 Start Tic Tac Toe in a group so another member can join: /tictactoe")
        return
    game_id = uuid.uuid4().hex[:10]
    game = {
        "chat_id": chat.id, "message_id": None, "board": [""] * 9,
        "players": {"X": {"id": user.id, "name": _name(user)} , "O": None},
        "turn": "X", "started": False, "active": True, "winner": None,
    }
    async with LOCK:
        GAMES[game_id] = game
    sent = await message.reply_text(_status(game), reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🙋 Join as O", callback_data=f"ttt:join:{game_id}")],
        [InlineKeyboardButton("❌ Cancel challenge", callback_data=f"ttt:cancel:{game_id}")],
    ]))
    game["message_id"] = sent.message_id


async def cb_tictactoe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        await query.answer("Invalid game action.", show_alert=True)
        return
    action, game_id = parts[1], parts[2]
    async with LOCK:
        game = GAMES.get(game_id)
        if not game or not game["active"]:
            await query.answer("This match has ended or expired.", show_alert=True)
            return
        if action == "join":
            if game["started"]:
                await query.answer("This match has already started.", show_alert=True)
                return
            if user.id == game["players"]["X"]["id"]:
                await query.answer("You cannot play against yourself.", show_alert=True)
                return
            game["players"]["O"] = {"id": user.id, "name": _name(user)}
            game["started"] = True
            await query.answer("You joined as O!")
            await query.edit_message_text(_status(game), reply_markup=_board_keyboard(game_id, game))
            return
        if action == "cancel":
            is_player = any(p and p["id"] == user.id for p in game["players"].values())
            if not is_player:
                await query.answer("Only a player in this match can end it.", show_alert=True)
                return
            game["active"] = False
            await query.answer("Match ended.")
            await query.edit_message_text("🛑 <b>Tic Tac Toe match ended.</b>")
            GAMES.pop(game_id, None)
            return
        if action == "move" and len(parts) == 4:
            if not game["started"]:
                await query.answer("Wait for another player to join.", show_alert=True)
                return
            try:
                idx = int(parts[3])
            except ValueError:
                await query.answer("Invalid square.", show_alert=True)
                return
            if idx < 0 or idx > 8:
                await query.answer("Invalid square.", show_alert=True)
                return
            symbol = game["turn"]
            player = game["players"].get(symbol)
            if not player or player["id"] != user.id:
                await query.answer("It is not your turn.", show_alert=True)
                return
            if game["board"][idx]:
                await query.answer("That square is already taken.", show_alert=True)
                return
            game["board"][idx] = symbol
            result = _winner(game["board"])
            if result == "draw":
                game["active"] = False
                await query.answer("Draw!")
                await query.edit_message_text("🤝 <b>Tic Tac Toe — Draw!</b>\n\n" + _board_text(game["board"]) + "\n\nNo points awarded.")
                GAMES.pop(game_id, None)
                return
            if result in ("X", "O"):
                game["active"] = False
                winner = game["players"][result]
                loser_symbol = "O" if result == "X" else "X"
                loser = game["players"][loser_symbol]
                # Use the same scores collection as every existing leaderboard game.
                db.add_score(winner["id"], game["chat_id"], f"tictactoe_{game_id}", "__tic_tac_toe_win__", 10)
                await query.answer("You won! +10 points" if winner["id"] == user.id else "Match finished!")
                final_text = (
                    "🏆 <b>Tic Tac Toe — Match Over!</b>\n\n" + _board_text(game["board"]) + "\n\n"
                    f"🥇 Winner: <a href='tg://user?id={winner['id']}'>{winner['name']}</a>\n"
                    "⭐ <b>+10 points</b> added to the existing leaderboard!\n"
                    f"👏 Opponent: {loser['name']}"
                )
                await query.edit_message_text(final_text)
                GAMES.pop(game_id, None)
                return
            game["turn"] = "O" if symbol == "X" else "X"
            await query.answer()
            await query.edit_message_text(_status(game), reply_markup=_board_keyboard(game_id, game))


def _board_text(board):
    def cell(v):
        return "❌" if v == "X" else ("⭕" if v == "O" else "⬜")
    return "\n".join("  ".join(cell(board[r * 3 + c]) for c in range(3)) for r in range(3))


def register_tictactoe_handlers(app: Application):
    app.add_handler(CommandHandler(["tictactoe", "ttt"], cmd_tictactoe))
    app.add_handler(CallbackQueryHandler(cb_tictactoe, pattern=r"^ttt:"))
