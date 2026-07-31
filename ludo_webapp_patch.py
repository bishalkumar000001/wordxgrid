"""
ludo_webapp_patch.py — Patches the /ludo command to send a Telegram WebApp button.

HOW TO USE (2 lines to add in bot.py):
---------------------------------------
After:
    from ludo import register_ludo_handlers

Add:
    from ludo_webapp_patch import patch_ludo_command

After:
    register_ludo_handlers(app)

Add:
    patch_ludo_command(app)

That's it! When LUDO_WEB_APP_URL env var is set, /ludo sends the Mini App button.
When LUDO_WEB_APP_URL is not set, /ludo falls back to the original text-based game.
"""

import logging
import os

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, constants,
)
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

LUDO_WEB_APP_URL = os.environ.get("LUDO_WEB_APP_URL", "")


async def cmd_ludo_webapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Replacement /ludo command that sends a Telegram WebApp Mini App button.
    Falls back to original text-based ludo if LUDO_WEB_APP_URL is not set.
    """
    if not LUDO_WEB_APP_URL:
        # Fall back to original text-based ludo
        from ludo import cmd_ludo as _original
        await _original(update, context)
        return

    user = update.effective_user
    if not update.effective_message:
        return

    chat = update.effective_chat
    msg_lines = [
        "🎲 <b>Ludo King — Mini App</b>",
        "",
        "Apne doston ke saath Ludo khelo! 🏆",
        "• Room create karo ya join karo",
        "• 2–4 players real-time multiplayer",
        "• Invite code se doston ko bulao",
        "• Andar hi andar Telegram mein khelo!",
        "",
        "👇 Button dabao aur game shuru karo:",
    ]

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🎲 Ludo King khelo!",
            url=LUDO_WEB_APP_URL,
        )
    ]])

    await update.effective_message.reply_text(
        "\n".join(msg_lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=keyboard,
    )


def patch_ludo_command(app: Application) -> bool:
    """
    Replace the /ludo CommandHandler with the WebApp version.
    Call AFTER register_ludo_handlers(app) in bot.py.

    Returns True if the patch was applied, False otherwise (env var not set).
    """
    if not LUDO_WEB_APP_URL:
        logger.info("LUDO_WEB_APP_URL not set — /ludo will use text-based mode")
        return False

    # Find and remove existing /ludo handler from all handler groups
    removed = False
    for group_id in list(app.handlers.keys()):
        for handler in list(app.handlers.get(group_id, [])):
            if isinstance(handler, CommandHandler):
                cmds = getattr(handler, "commands", set())
                if "ludo" in cmds:
                    app.remove_handler(handler, group_id)
                    removed = True

    # Register new WebApp handler
    app.add_handler(CommandHandler("ludo", cmd_ludo_webapp))
    logger.info("Ludo WebApp patch applied — /ludo now sends Mini App button")
    return True
