"""Telegram Web App launcher for the Ludo game.

Drop this file and ludo_db.py beside WordXGrid's bot.py, set LUDO_WEB_APP_URL,
then call register_ludo_handlers(app). The web app stays independent from the
existing Word Grid and Paheli handlers.
"""

from __future__ import annotations

import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

LUDO_WEB_APP_URL = os.environ.get("LUDO_WEB_APP_URL", "").strip()


def _ludo_keyboard() -> InlineKeyboardMarkup:
    if not LUDO_WEB_APP_URL:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("Ludo setup required", callback_data="ludo:missing-url")]]
        )
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Play Ludo", web_app=WebAppInfo(url=LUDO_WEB_APP_URL))]]
    )


async def cmd_ludo(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    if not LUDO_WEB_APP_URL:
        await message.reply_text(
            "Ludo is almost ready. Set the LUDO_WEB_APP_URL environment variable "
            "to the deployed web game URL first."
        )
        return
    await message.reply_text(
        "Ludo is ready.\n\n"
        "Create a room, share the invite link, and take turns rolling the dice.",
        reply_markup=_ludo_keyboard(),
    )


async def cb_ludo_missing_url(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer(
        "The Ludo web URL has not been configured yet.", show_alert=True
    )


async def cb_ludo_from_game(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not LUDO_WEB_APP_URL:
        await query.message.reply_text(
            "Set LUDO_WEB_APP_URL in Render before opening Ludo."
        )
        return
    await query.message.reply_text(
        "Open Ludo and invite your friends:",
        reply_markup=_ludo_keyboard(),
    )


def register_ludo_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("ludo", cmd_ludo))
    app.add_handler(CallbackQueryHandler(cb_ludo_missing_url, pattern=r"^ludo:missing-url$"))
    app.add_handler(CallbackQueryHandler(cb_ludo_from_game, pattern=r"^game:ludo:"))