import logging
import os
import uuid
import io
import random

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, constants,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes,
    filters, CallbackQueryHandler,
)
from telegram.error import TelegramError

import config
import database as db
from words import get_words_for_mode
from wordgrid import (
    build_grid, render_grid_image,
    get_hint_text, make_hint_for_word,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─── helpers ──────────────────────────────────────────────────────────────────

def display_name(user) -> str:
    name = user.first_name or ""
    if user.last_name:
        name += " " + user.last_name
    return name.strip() or f"User{user.id}"


def format_lb_row(rank: int, row: dict) -> str:
    name = row.get("first_name") or ""
    if row.get("last_name"):
        name += " " + row["last_name"]
    name = name.strip() or f"User{row['user_id']}"
    pts = row["total_points"]
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")
    return f"{medal} {name} — <b>{pts} pts</b>"


async def log_to_group(app: Application, text: str):
    if config.LOG_GROUP_ID:
        try:
            await app.bot.send_message(
                config.LOG_GROUP_ID, text,
                parse_mode=constants.ParseMode.HTML,
            )
        except TelegramError as e:
            logger.warning("Log group error: %s", e)


def build_caption(words: list, found: list, mode: str) -> str:
    mode_label = "🟢 EASY" if mode == "easy" else "🔴 HARD"
    word_lines = get_hint_text(words, found)
    remaining = len(words) - len(found)
    return (
        f"🎮 <b>WORD GRID CHALLENGE</b> — {mode_label}\n\n"
        f"<b>Find these {len(words)} words:</b>\n{word_lines}\n\n"
        f"⏰ <b>10 minutes</b> to find them all!\n"
        f"Words remaining: <b>{remaining}</b>\n"
        f"🏆 Points: <b>4</b> (1st) · <b>3</b> (others) · <b>5</b> (last)"
    )


async def update_grid_photo(
    bot,
    chat_id: int,
    message_id: int,
    game: dict,
    found_words: list,
) -> None:
    """Re-render the grid with highlights and edit the pinned photo."""
    grid = db.decode_grid(game)
    placed = db.decode_placed(game)
    words = [w for w in game["words"].split(",") if w]

    if not grid or not placed:
        return

    img_bytes = render_grid_image(
        grid,
        title="WORD GRID CHALLENGE",
        placed_words=placed,
        found_words=found_words,
        word_order=words,
    )
    caption = build_caption(words, found_words, game["mode"])

    try:
        await bot.edit_message_media(
            chat_id=chat_id,
            message_id=message_id,
            media=InputMediaPhoto(
                media=io.BytesIO(img_bytes),
                caption=caption,
                parse_mode=constants.ParseMode.HTML,
            ),
        )
    except TelegramError as e:
        logger.warning("Could not edit grid photo: %s", e)


# ─── game timeout job ─────────────────────────────────────────────────────────

async def game_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    game_id = data["game_id"]
    group_id = data["group_id"]

    game = db.get_game(game_id)
    if not game or not game["active"]:
        return

    words = [w for w in game["words"].split(",") if w]
    found = [w for w in game["found_words"].split(",") if w]
    remaining = [w for w in words if w not in found]

    db.end_game(game_id)

    # Update grid photo with all found highlights before unpin
    pin_msg_id = game.get("pin_msg_id")
    if pin_msg_id and found:
        await update_grid_photo(context.bot, group_id, pin_msg_id, game, found)

    # Unpin
    if pin_msg_id:
        try:
            await context.bot.unpin_chat_message(group_id, pin_msg_id)
        except TelegramError as e:
            logger.warning("Unpin error: %s", e)

    # Summary message
    scores = db.get_game_scores(game_id)
    summary_lines = ["⏰ <b>Time's up!</b> The game has ended.\n"]
    if remaining:
        summary_lines.append(f"❌ Unfound words: <b>{', '.join(remaining)}</b>\n")
    else:
        summary_lines.append("🎉 All words were found!\n")

    if scores:
        summary_lines.append("🏆 <b>Round Scores:</b>")
        for i, row in enumerate(scores, 1):
            n = row.get("first_name") or f"User{row['user_id']}"
            summary_lines.append(f"  {i}. {n} — {row['total_points']} pts")
    else:
        summary_lines.append("No words were found this round.")

    await context.bot.send_message(
        group_id,
        "\n".join(summary_lines),
        parse_mode=constants.ParseMode.HTML,
    )

    await log_to_group(
        context.application,
        f"🕹 Game timed out in group <code>{group_id}</code>. "
        f"Words: {len(words)}, Found: {len(found)}",
    )


# ─── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")

    if update.effective_chat.type == "private":
        support = (f'\n\n📢 Support: {config.SUPPORT_CHANNEL}'
                   if config.SUPPORT_CHANNEL else "")
        await update.message.reply_text(
            f"👋 Hello <b>{user.first_name}</b>!\n\n"
            "🎮 I'm the <b>Word Grid Challenge</b> bot!\n"
            "Add me to a group and use:\n"
            "• /new — Start an easy game\n"
            "• /new_hard — Start a hard game\n"
            "• /hint — Get a hint (2 per game)\n"
            "• /leaderboard or /lb — Global leaderboard\n"
            f"• /help — Show all commands{support}",
            parse_mode=constants.ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            "🎮 Word Grid Challenge is ready! Use /new to start a game.",
        )


# ─── /help ────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support = f'\n📢 Support: {config.SUPPORT_CHANNEL}' if config.SUPPORT_CHANNEL else ""
    await update.message.reply_text(
        "🎮 <b>Word Grid Challenge — Commands</b>\n\n"
        "/new — Start an easy game\n"
        "/new_hard — Start a hard game\n"
        "/end — End the current game early (admins only)\n"
        "/hint — Get a hint (2 hints/game, private to you)\n"
        "/leaderboard or /lb — Global leaderboard\n"
        "/lb day · /lb week · /lb month · /lb year\n"
        "/stats — Your personal stats\n"
        "/help — This message\n\n"
        "📌 The grid is pinned when a game starts.\n"
        "🎨 Correctly guessed words light up in the grid!\n"
        "⏰ Games last 10 minutes.\n"
        "🏆 Points: 4 (1st word) · 3 (other words) · 5 (last word)"
        f"{support}",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /new and /new_hard ───────────────────────────────────────────────────────

async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    user = update.effective_user
    chat = update.effective_chat

    if chat.type == "private":
        await update.message.reply_text("⚠️ This game can only be played in groups.")
        return

    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")

    existing = db.get_active_game(chat.id)
    if existing:
        await update.message.reply_text(
            "⚠️ A game is already running in this group!\n"
            "Wait for it to finish or time out before starting a new one."
        )
        return

    await update.message.reply_text("🔄 Generating word grid, please wait...")

    words = get_words_for_mode(mode)
    game_id = str(uuid.uuid4())

    grid_size = 10 if mode == "easy" else 12
    grid, placed = build_grid(words, size=grid_size)
    db.create_game(game_id, chat.id, mode, words, grid=grid, placed=placed)

    img_bytes = render_grid_image(
        grid,
        title="WORD GRID CHALLENGE",
        placed_words=placed,
        found_words=[],
        word_order=words,
    )
    caption = build_caption(words, [], mode)

    photo_msg = await context.bot.send_photo(
        chat_id=chat.id,
        photo=io.BytesIO(img_bytes),
        caption=caption,
        parse_mode=constants.ParseMode.HTML,
    )

    db.update_game_message(game_id, photo_msg.message_id)

    # Pin
    try:
        await context.bot.pin_chat_message(
            chat_id=chat.id,
            message_id=photo_msg.message_id,
            disable_notification=True,
        )
        db.update_game_pin(game_id, photo_msg.message_id)
    except TelegramError as e:
        logger.warning("Could not pin message: %s", e)

    # Schedule timeout
    context.job_queue.run_once(
        game_timeout,
        when=config.GAME_TIMEOUT_SECONDS,
        data={"game_id": game_id, "group_id": chat.id},
        name=f"timeout_{game_id}",
    )

    await log_to_group(
        context.application,
        f"🆕 New <b>{mode}</b> game in <code>{chat.id}</code> "
        f"by <code>{user.id}</code> ({display_name(user)}). "
        f"Game: <code>{game_id}</code>",
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, "easy")


async def cmd_new_hard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, "hard")


# ─── Message handler — guess words ───────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        return

    text = update.message.text.strip().upper()

    # Only plain single words
    if " " in text or not text.isalpha() or len(text) < 3:
        return

    game = db.get_active_game(chat.id)
    if not game:
        return

    words = [w for w in game["words"].split(",") if w]
    found = [w for w in game["found_words"].split(",") if w]

    if text not in words or text in found:
        return

    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")

    # Determine points
    found_count = len(found)
    total = len(words)
    if found_count == 0:
        points = config.POINTS_FIRST
        rank_msg = "🥇 <b>First word!</b>"
    elif found_count == total - 1:
        points = config.POINTS_LAST
        rank_msg = "🎯 <b>Last word!</b>"
    else:
        points = config.POINTS_NORMAL
        rank_msg = ""

    if not db.mark_word_found(game["game_id"], text):
        return

    db.add_score(user.id, chat.id, game["game_id"], text, points)

    now_found = found + [text]
    remaining = [w for w in words if w not in now_found]
    name = display_name(user)

    # Re-render grid with the new highlight
    pin_msg_id = game.get("pin_msg_id") or game.get("message_id")
    if pin_msg_id:
        await update_grid_photo(context.bot, chat.id, pin_msg_id, game, now_found)

    msg_parts = [f"✅ <b>{name}</b> found <b>{text}</b>! +{points} pts"]
    if rank_msg:
        msg_parts.append(rank_msg)

    # Game over — all words found
    if not remaining:
        msg_parts.append("\n🎉 <b>All words found! Game over!</b>")
        db.end_game(game["game_id"])

        jobs = context.job_queue.get_jobs_by_name(f"timeout_{game['game_id']}")
        for j in jobs:
            j.schedule_removal()

        if pin_msg_id:
            try:
                await context.bot.unpin_chat_message(chat.id, pin_msg_id)
            except TelegramError as e:
                logger.warning("Unpin error: %s", e)

        scores = db.get_game_scores(game["game_id"])
        if scores:
            msg_parts.append("\n🏆 <b>Round Results:</b>")
            for i, row in enumerate(scores, 1):
                n = row.get("first_name") or f"User{row['user_id']}"
                msg_parts.append(f"  {i}. {n} — {row['total_points']} pts")

    await update.message.reply_text(
        "\n".join(msg_parts),
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /hint ────────────────────────────────────────────────────────────────────

async def _delete_messages(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: delete hint + command messages from the group."""
    data = context.job.data
    for msg_id in data["msg_ids"]:
        try:
            await context.bot.delete_message(data["chat_id"], msg_id)
        except TelegramError:
            pass


async def cmd_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text("⚠️ Hints are only available during group games.")
        return

    game = db.get_active_game(chat.id)
    if not game:
        await update.message.reply_text("❌ No active game in this group.")
        return

    used = db.get_hint_count(game["game_id"])
    if used >= config.MAX_HINTS_PER_GAME:
        await update.message.reply_text(
            f"❌ No hints left! This game has used all {config.MAX_HINTS_PER_GAME} hints."
        )
        return

    words = [w for w in game["words"].split(",") if w]
    found = [w for w in game["found_words"].split(",") if w]
    remaining = [w for w in words if w not in found]

    if not remaining:
        await update.message.reply_text("✅ All words have already been found!")
        return

    hint_word = random.choice(remaining)
    hint_text = make_hint_for_word(hint_word, revealed=2)

    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")
    db.record_hint_used(game["game_id"], user.id, chat.id)

    left = config.MAX_HINTS_PER_GAME - used - 1

    # Send hint as a reply in the group — auto-delete both messages after 15 s
    hint_msg = await update.message.reply_text(
        f"🔍 <a href='tg://user?id={user.id}'><b>{display_name(user)}</b></a> — "
        f"your hint: <b>{hint_text}</b>\n"
        f"<i>({left} hint{'s' if left != 1 else ''} left • this message deletes in 15 s)</i>",
        parse_mode=constants.ParseMode.HTML,
    )

    # Schedule deletion of both the /hint command and the hint reply
    msg_ids = [hint_msg.message_id]
    if update.message:
        msg_ids.append(update.message.message_id)

    context.job_queue.run_once(
        _delete_messages,
        when=15,
        data={"chat_id": chat.id, "msg_ids": msg_ids},
        name=f"del_hint_{hint_msg.message_id}",
    )


# ─── /leaderboard / /lb ───────────────────────────────────────────────────────

async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    period_map = {
        "day":    "Today",
        "week":   "This Week",
        "month":  "This Month",
        "year":   "This Year",
        "global": "All Time (Global)",
        "all":    "All Time (Global)",
    }

    arg = (args[0].lower() if args else "global")
    if arg not in period_map:
        arg = "global"

    rows = db.get_period_leaderboard(
        "all" if arg in ("global", "all") else arg,
        group_id=None,
        limit=20,
    )

    if not rows:
        await update.message.reply_text(
            f"📊 No scores yet for <b>{period_map[arg]}</b>.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    lines = [f"🏆 <b>Global Leaderboard — {period_map[arg]}</b>\n"]
    for i, row in enumerate(rows, 1):
        lines.append(format_lb_row(i, row))
    lines.append(
        "\n📌 Points are cumulative across all groups.\n"
        "Tap a button to switch period:"
    )

    keyboard = [
        [
            InlineKeyboardButton("Today",  callback_data="lb:day"),
            InlineKeyboardButton("Week",   callback_data="lb:week"),
            InlineKeyboardButton("Month",  callback_data="lb:month"),
        ],
        [
            InlineKeyboardButton("Year",      callback_data="lb:year"),
            InlineKeyboardButton("🌍 Global", callback_data="lb:global"),
        ],
    ]

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cb_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    period = query.data.split(":")[1]
    period_map = {
        "day":    "Today",
        "week":   "This Week",
        "month":  "This Month",
        "year":   "This Year",
        "global": "All Time (Global)",
    }

    rows = db.get_period_leaderboard(
        "all" if period == "global" else period,
        group_id=None,
        limit=20,
    )

    if not rows:
        text = f"📊 No scores yet for <b>{period_map.get(period, period)}</b>."
    else:
        lines = [f"🏆 <b>Global Leaderboard — {period_map.get(period, period)}</b>\n"]
        for i, row in enumerate(rows, 1):
            lines.append(format_lb_row(i, row))
        lines.append("\n📌 Points are cumulative across all groups.")
        text = "\n".join(lines)

    keyboard = [
        [
            InlineKeyboardButton("Today",  callback_data="lb:day"),
            InlineKeyboardButton("Week",   callback_data="lb:week"),
            InlineKeyboardButton("Month",  callback_data="lb:month"),
        ],
        [
            InlineKeyboardButton("Year",      callback_data="lb:year"),
            InlineKeyboardButton("🌍 Global", callback_data="lb:global"),
        ],
    ]

    try:
        await query.edit_message_text(
            text,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except TelegramError:
        pass


# ─── /end ────────────────────────────────────────────────────────────────────

async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text("⚠️ This command is for groups only.")
        return

    # Only admins can end a game
    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ Only group admins can end the game.")
        return

    game = db.get_active_game(chat.id)
    if not game:
        await update.message.reply_text("❌ No active game in this group.")
        return

    words = [w for w in game["words"].split(",") if w]
    found = [w for w in game["found_words"].split(",") if w]
    remaining = [w for w in words if w not in found]

    db.end_game(game["game_id"])

    # Cancel timeout job
    jobs = context.job_queue.get_jobs_by_name(f"timeout_{game['game_id']}")
    for j in jobs:
        j.schedule_removal()

    # Update grid with final highlights then unpin
    pin_msg_id = game.get("pin_msg_id") or game.get("message_id")
    if pin_msg_id and found:
        await update_grid_photo(context.bot, chat.id, pin_msg_id, game, found)

    if pin_msg_id:
        try:
            await context.bot.unpin_chat_message(chat.id, pin_msg_id)
        except TelegramError as e:
            logger.warning("Unpin error: %s", e)

    # Build summary
    scores = db.get_game_scores(game["game_id"])
    lines = [f"🛑 <b>Game ended by {display_name(user)}.</b>\n"]

    if remaining:
        lines.append(f"❌ Unfound words: <b>{', '.join(remaining)}</b>\n")
    else:
        lines.append("🎉 All words had been found!\n")

    if scores:
        lines.append("🏆 <b>Round Scores:</b>")
        for i, row in enumerate(scores, 1):
            n = row.get("first_name") or f"User{row['user_id']}"
            lines.append(f"  {i}. {n} — {row['total_points']} pts")
    else:
        lines.append("No words were found this round.")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
    )

    await log_to_group(
        context.application,
        f"🛑 Game ended manually in <code>{chat.id}</code> "
        f"by admin <code>{user.id}</code> ({display_name(user)}). "
        f"Found: {len(found)}/{len(words)}",
    )


# ─── /stats ───────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rows = db.get_period_leaderboard("all", limit=1000)
    user_row = next((r for r in rows if r["user_id"] == user.id), None)
    rank = next((i for i, r in enumerate(rows, 1) if r["user_id"] == user.id), None)

    if not user_row:
        await update.message.reply_text(
            "You haven't scored any points yet. Start playing with /new!"
        )
        return

    await update.message.reply_text(
        f"📊 <b>Your Global Stats</b>\n\n"
        f"🌍 Global Rank: <b>#{rank}</b>\n"
        f"⭐ Total Points: <b>{user_row['total_points']}</b>\n"
        f"🎮 Games Played: <b>{user_row['games_played']}</b>",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── error handler ────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling update:", exc_info=context.error)
    if config.LOG_GROUP_ID:
        try:
            await context.bot.send_message(
                config.LOG_GROUP_ID,
                f"⚠️ Bot error: <code>{context.error}</code>",
                parse_mode=constants.ParseMode.HTML,
            )
        except Exception:
            pass


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    db.init_db()

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("new_hard", cmd_new_hard))
    app.add_handler(CommandHandler("hint", cmd_hint))
    app.add_handler(CommandHandler(["leaderboard", "lb"], cmd_leaderboard))
    app.add_handler(CommandHandler("end", cmd_end))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(cb_leaderboard, pattern=r"^lb:"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        handle_message,
    ))
    app.add_error_handler(error_handler)

    logger.info("Bot starting with polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
