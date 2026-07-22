import asyncio
import logging
import os
import uuid
import io
import random
import threading

from web import app as web_app

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, constants, ChatMemberUpdated,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes,
    filters, CallbackQueryHandler, ChatMemberHandler,
)
from telegram.error import TelegramError

import config
import database as db
from words import get_words_for_mode
from wordgrid import (
    build_grid, render_grid_image,
    get_hint_text, make_hint_for_word,
)

# ── Import Paheli module ───────────────────────────────────────────────────────
from paheli import register_paheli_handlers

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


def is_sudo(user_id: int) -> bool:
    return user_id in config.SUDO_USERS


def format_lb_row(rank: int, row: dict) -> str:
    name = row.get("first_name") or ""
    if row.get("last_name"):
        name += " " + row["last_name"]
    name = name.strip() or f"User{row['user_id']}"
    pts   = row.get("total_points", 0)
    words = row.get("words_found", 0)
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")
    words_str = f" · {words} words" if words else ""
    return f"{medal} <b>{name}</b> — {pts} pts{words_str}"


def grid_message_link(chat_id: int, message_id: int) -> str:
    numeric = str(abs(chat_id))
    if numeric.startswith("100"):
        numeric = numeric[3:]
    return f"https://t.me/c/{numeric}/{message_id}"


# ─── Keyboards ────────────────────────────────────────────────────────────────

def _grid_keyboard() -> InlineKeyboardMarkup | None:
    """Support Group button — always shown on the pinned grid photo."""
    if not config.SUPPORT_CHANNEL:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🌐 Support Group", url=config.SUPPORT_CHANNEL),
    ]])


def play_again_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Play Again",  callback_data=f"play:easy:{chat_id}"),
        InlineKeyboardButton("🔴 Hard Mode",   callback_data=f"play:hard:{chat_id}"),
    ]])


PERIOD_LABELS = {
    "day":   "Today",
    "week":  "This Week",
    "month": "This Month",
    "year":  "This Year",
    "all":   "All Time",
}

_PERIOD_EMOJI = {
    "day":   "🟡",
    "week":  "🟠",
    "month": "🔵",
    "year":  "🟣",
    "all":   "🏆",
}


def _lb_keyboard(period: str, scope: str, chat_type: str) -> InlineKeyboardMarkup:
    """Leaderboard keyboard — active button gets ✅, each has a distinct emoji colour."""

    def _btn(label: str, p: str, s: str) -> InlineKeyboardButton:
        active = (p == period and s == scope)
        mark   = " ✅" if active else ""
        return InlineKeyboardButton(f"{label}{mark}", callback_data=f"lb:{p}:{s}")

    rows = []

    # Scope row — only in group chats
    if chat_type in ("group", "supergroup"):
        rows.append([
            _btn("📍 Current Chat", period, "chat"),
            _btn("🌍 Global",       period, "global"),
        ])

    # Period rows
    rows.append([
        _btn(f"{_PERIOD_EMOJI['day']} Today",   "day",   scope),
        _btn(f"{_PERIOD_EMOJI['week']} Week",   "week",  scope),
        _btn(f"{_PERIOD_EMOJI['month']} Month", "month", scope),
    ])
    rows.append([
        _btn(f"{_PERIOD_EMOJI['year']} Year",       "year", scope),
        _btn(f"{_PERIOD_EMOJI['all']} All Time",    "all",  scope),
    ])

    return InlineKeyboardMarkup(rows)


# ─── Utility ──────────────────────────────────────────────────────────────────

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
    remaining  = len(words) - len(found)

    if mode == "hard":
        p_first, p_normal, p_last = (
            config.HARD_POINTS_FIRST, config.HARD_POINTS_NORMAL, config.HARD_POINTS_LAST,
        )
    else:
        p_first, p_normal, p_last = (
            config.POINTS_FIRST, config.POINTS_NORMAL, config.POINTS_LAST,
        )

    return (
        f"🎮 <b>WORD GRID CHALLENGE</b> — {mode_label}\n\n"
        f"<b>Find these {len(words)} words:</b>\n{word_lines}\n\n"
        f"⏰ <b>10 min</b> timer resets on every correct guess!\n"
        f"Words remaining: <b>{remaining}</b>\n"
        f"🏆 Points: <b>{p_first}</b> (1st) · <b>{p_normal}</b> (others) · <b>{p_last}</b> (last)"
    )


async def update_grid_photo(
    bot,
    chat_id: int,
    message_id: int,
    game: dict,
    found_words: list,
) -> None:
    grid   = db.decode_grid(game)
    placed = db.decode_placed(game)
    words  = [w for w in game["words"].split(",") if w]

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
            reply_markup=_grid_keyboard(),
        )
    except TelegramError as e:
        logger.warning("Could not edit grid photo: %s", e)


def _reschedule_timeout(context, game_id: str, group_id: int):
    # Remove previous timeout job
    for job in context.job_queue.get_jobs_by_name(f"timeout_{game_id}"):
        job.schedule_removal()

    # Remove previous warning job
    for job in context.job_queue.get_jobs_by_name(f"warning_{game_id}"):
        job.schedule_removal()

    # Schedule warning after 7 minutes
    context.job_queue.run_once(
        game_warning,
        when=420,
        data={
            "game_id": game_id,
            "group_id": group_id,
        },
        name=f"warning_{game_id}",
    )

    # Schedule timeout after 10 minutes
    context.job_queue.run_once(
        game_timeout,
        when=config.GAME_TIMEOUT_SECONDS,
        data={
            "game_id": game_id,
            "group_id": group_id,
        },
        name=f"timeout_{game_id}",
    )


# ─── Game timeout ─────────────────────────────────────────────────────────────

async def game_warning(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    game_id = data["game_id"]
    group_id = data["group_id"]

    game = db.get_game(game_id)

    if not game or not game["active"]:
        return

    # Delete warning message if it exists
    warning_msg_id = context.application.bot_data.pop(
        f"warning_{game_id}",
        None,
    )

    if warning_msg_id:
        try:
            await context.bot.delete_message(
                group_id,
                warning_msg_id,
            )
        except TelegramError:
            pass

    words = [w for w in game["words"].split(",") if w]
    found = [w for w in game["found_words"].split(",") if w]
    remaining = [w for w in words if w not in found]

    if not remaining:
        return

    # Pick a random remaining word
    hint_word = random.choice(remaining)

    # Reveal first 2 letters
    hint = make_hint_for_word(hint_word, revealed=2)

    warning_msg = await context.bot.send_message(
        group_id,
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>GAME WARNING</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "⏰ <b>Only 3 minutes remaining!</b>\n\n"
        f"💡 <b>Free Hint:</b>\n<code>{hint}</code>\n\n"
        "🎯 Guess any word to reset the timer!",
        parse_mode=constants.ParseMode.HTML,
    )

    # Save warning message id
    context.application.bot_data[f"warning_{game_id}"] = warning_msg.message_id

async def game_timeout(context: ContextTypes.DEFAULT_TYPE):
    data     = context.job.data
    game_id  = data["game_id"]
    group_id = data["group_id"]

    game = db.get_game(game_id)
    if not game or not game["active"]:
        return

    words     = [w for w in game["words"].split(",") if w]
    found     = [w for w in game["found_words"].split(",") if w]
    remaining = [w for w in words if w not in found]

    db.end_game(game_id)

    pin_msg_id = game.get("pin_msg_id")
    if pin_msg_id and found:
        await update_grid_photo(context.bot, group_id, pin_msg_id, game, found)

    if pin_msg_id:
        try:
            await context.bot.unpin_chat_message(group_id, pin_msg_id)
        except TelegramError as e:
            logger.warning("Unpin error: %s", e)

    scores = db.get_game_scores(game_id)
    lines  = ["⏰ <b>TIME'S UP!</b> 10 minutes are up.\n"]
    if remaining:
        lines.append(f"❌ Unfound words: <b>{', '.join(remaining)}</b>\n")
    else:
        lines.append("🎉 All words were found!\n")

    lines.append(f"📊 Words Found: <b>{len(found)}/{len(words)}</b>")

    if scores:
        lines.append("\n🏆 <b>Scores:</b>")
        for i, row in enumerate(scores, 1):
            n     = row.get("first_name") or f"User{row['user_id']}"
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal} <b>{n}</b> — {row['total_points']} pts")
    else:
        lines.append("No words were found this round.")

    await context.bot.send_message(
        group_id,
        "\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=play_again_keyboard(group_id),
    )

    await log_to_group(
        context.application,
        f"🕹 Game timed out in <code>{group_id}</code>. "
        f"Words: {len(words)}, Found: {len(found)}",
    )

# ─── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")

    if update.effective_chat.type == "private":
        rows = []
        if config.SUPPORT_CHANNEL:
            rows.append([InlineKeyboardButton("🌐 Support Group", url=config.SUPPORT_CHANNEL)])
        await update.message.reply_text(
            f"👋 Hello <b>{user.first_name}</b>!\n\n"
            "🎮 I'm <b>VelocityBots</b>! Add me to a group and use:\n"
            "• /game — Choose your game (Word Grid or Paheli)\n"
            "• /new — Start a Word Grid game\n"
            "• /paheli — Start a riddle game\n"
            "• /help — All commands",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(rows) if rows else None,
        )
    else:
        await update.message.reply_text(
            "🎮 VelocityBots is ready!\n"
            "Use /game to choose a game, or /new for Word Grid, /paheli for riddles.",
        )


# ─── /help ────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = []
    if config.SUPPORT_CHANNEL:
        rows.append([InlineKeyboardButton("🌐 Support Group", url=config.SUPPORT_CHANNEL)])
    await update.message.reply_text(
        "🎮 <b>VelocityBots — Commands</b>\n\n"
        "━ <b>Game Selector</b> ━\n"
        "/game — Choose Word Grid or Paheli 🎮\n\n"
        "━ <b>Word Grid</b> ━\n"
        "/new — Start an easy game (10×10)\n"
        "/new_hard — Start a hard game (12×12)\n"
        "/end — End current game (admins only)\n"
        "/hint — Get a word grid hint\n"
        "/lb — Word Grid leaderboard\n"
        "/stats — Your Word Grid stats\n\n"
        "━ <b>Paheli (Riddles)</b> ━\n"
        "/paheli — Start a riddle\n"
        "/answer — Answer a riddle\n"
        "/daily — Daily reward\n"
        "/weekly — Weekly reward\n"
        "/profile — Your paheli profile\n"
        "/shop — Buy hints, skips & more\n"
        "/inventory — Your items\n"
        "/settings — Language & difficulty\n"
        "/plb — Paheli leaderboard\n"
        "/paheli_help — Full paheli help\n\n"
        "📌 Grid is pinned when game starts.\n"
        "🎨 Found words get strikethrough!\n"
        "⏰ Timer resets on every correct guess.\n",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows) if rows else None,
    )


# ─── Core game start ──────────────────────────────────────────────────────────

async def _do_start_game(bot, application, job_queue, chat_id, chat_title, user, mode):
    words = get_words_for_mode(mode)
    game_id = str(uuid.uuid4())

    grid_size = 10 if mode == "easy" else 12
    grid, placed = build_grid(words, size=grid_size)

    img_bytes = render_grid_image(
        grid,
        title="WORD GRID CHALLENGE",
        placed_words=placed,
        found_words=[],
        word_order=words,
    )
    caption = build_caption(words, [], mode)

    # Try to send the game image first
    try:
        photo_msg = await bot.send_photo(
            chat_id=chat_id,
            photo=io.BytesIO(img_bytes),
            caption=caption,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_grid_keyboard(),
        )
    except TelegramError:
        await bot.send_message(
            chat_id,
            "⚠️ <b>I can't start the game!</b>\n\n"
            "Please give me the <b>Send Photos</b> permission and try again with <code>/new</code>.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    # Create the game ONLY after the photo was sent successfully
    db.create_game(
        game_id,
        chat_id,
        mode,
        words,
        grid=grid,
        placed=placed,
    )

    db.update_game_message(game_id, photo_msg.message_id)

    # Try to pin the game (don't fail if permission is missing)
    try:
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=photo_msg.message_id,
            disable_notification=True,
        )
        db.update_game_pin(game_id, photo_msg.message_id)

    except TelegramError:
        await bot.send_message(
            chat_id,
            "📌 <b>Game started successfully!</b>\n\n"
            "I couldn't pin the game because I don't have the <b>Pin Messages</b> permission.",
            parse_mode=constants.ParseMode.HTML,
        )

    # Start warning job (7 minutes)
    job_queue.run_once(
        game_warning,
        when=420,
        data={
            "game_id": game_id,
            "group_id": chat_id,
        },
        name=f"warning_{game_id}",
    )

    # Start timeout job (10 minutes)
    job_queue.run_once(
        game_timeout,
        when=config.GAME_TIMEOUT_SECONDS,
        data={
            "game_id": game_id,
            "group_id": chat_id,
        },
        name=f"timeout_{game_id}",
    )

    # Log to your log group
    await log_to_group(
        application,
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎮 <b>NEW {mode.upper()} GAME</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Chat:</b> {chat_title}\n"
        f"🆔 <b>Chat ID:</b> <code>{chat_id}</code>\n\n"
        f"👤 <b>Name:</b> <a href='tg://user?id={user.id}'>{display_name(user)}</a>\n"
        f"📛 <b>Username:</b> @{user.username if user.username else 'No Username'}\n"
        f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )


async def _auto_track_group(bot, chat) -> None:
    """Silently upsert the group into the tracking collection."""
    try:
        count = await bot.get_chat_member_count(chat.id)
    except TelegramError:
        count = 0
    db.track_group(
        chat_id=chat.id,
        title=chat.title or "",
        username=getattr(chat, "username", "") or "",
        member_count=count,
    )


async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    user = update.effective_user
    chat = update.effective_chat

    if chat.type == "private":
        await update.message.reply_text("⚠️ This game can only be played in groups.")
        return

    # Always keep the group registered, even for pre-existing groups
    await _auto_track_group(context.bot, chat)

    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")

    existing = db.get_active_game(chat.id)
    if existing:
        pin_msg_id = existing.get("pin_msg_id") or existing.get("message_id")
        kb = []
        if pin_msg_id:
            kb = [[InlineKeyboardButton(
                "🎯 Go to Grid",
                url=grid_message_link(chat.id, pin_msg_id),
            )]]
        await update.message.reply_text(
            "⚠️ A game is already running!\n"
            "Wait for it to finish or an admin can /end it.",
            reply_markup=InlineKeyboardMarkup(kb) if kb else None,
        )
        return

    await update.message.reply_text("🔄 Generating word grid, please wait…")
    await _do_start_game(
        context.bot, context.application, context.job_queue,
        chat.id, chat.title or str(chat.id), user, mode,
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, "easy")


async def cmd_new_hard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, "hard")


# ─── Play Again callback ───────────────────────────────────────────────────────

async def cb_play_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    mode    = parts[1]
    chat_id = int(parts[2])
    user    = query.from_user

    existing = db.get_active_game(chat_id)
    if existing:
        await query.answer("⚠️ A game is already running!", show_alert=True)
        return

    await query.answer("🔄 Starting new game…")
    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")
    chat_title = getattr(query.message.chat, "title", None) or str(chat_id)

    await _do_start_game(
        context.bot, context.application, context.job_queue,
        chat_id, chat_title, user, mode,
    )


# ─── Message handler — guess words ────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        return

    text = update.message.text.strip().upper()
    if " " in text or not text.isalpha() or len(text) < 3:
        return

    game = db.get_active_game(chat.id)
    if not game:
        return

    # Keep group tracked even if it existed before the tracking feature was added
    db.track_group(
        chat_id=chat.id,
        title=chat.title or "",
        username=getattr(chat, "username", "") or "",
        member_count=0,
    )

    words = [w for w in game["words"].split(",") if w]
    found = [w for w in game["found_words"].split(",") if w]

    if text not in words or text in found:
        return

    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")

    found_count = len(found)
    total       = len(words)
    is_hard     = game.get("mode") == "hard"

    p_first  = config.HARD_POINTS_FIRST  if is_hard else config.POINTS_FIRST
    p_normal = config.HARD_POINTS_NORMAL if is_hard else config.POINTS_NORMAL
    p_last   = config.HARD_POINTS_LAST   if is_hard else config.POINTS_LAST

    if found_count == 0:
        points     = p_first
        rank_emoji = "🎆"
    elif found_count == total - 1:
        points     = p_last
        rank_emoji = "🏆"
    else:
        points     = p_normal
        rank_emoji = "💫"

    if not db.mark_word_found(game["game_id"], text):
        return

    db.add_score(user.id, chat.id, game["game_id"], text, points)

    # Delete warning message if it exists
    warning_msg_id = context.application.bot_data.pop(
        f"warning_{game['game_id']}",
        None,
    )

    if warning_msg_id:
        try:
            await context.bot.delete_message(
                chat.id,
                warning_msg_id,
            )
        except TelegramError:
            pass

    now_found = found + [text]
    remaining = [w for w in words if w not in now_found]
    name = display_name(user)

    # Reset warning timer + timeout timer
    _reschedule_timeout(context, game["game_id"], chat.id)

    pin_msg_id = game.get("pin_msg_id") or game.get("message_id")

    msg_parts = [
        f"✅ +{points} points for <b>{name}</b> {rank_emoji}! You found <b>{text}</b>."
    ]

    go_to_grid_kb = None
    if pin_msg_id:
        go_to_grid_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎯 Go to Grid", url=grid_message_link(chat.id, pin_msg_id))
        ]])

    if not remaining:
        msg_parts.append("\n🎉 <b>GAME COMPLETE!</b> All words found! 🥳")
        db.end_game(game["game_id"])

        # Cancel timeout job
        for job in context.job_queue.get_jobs_by_name(
            f"timeout_{game['game_id']}"
        ):
            job.schedule_removal()

        # Cancel warning job
        for job in context.job_queue.get_jobs_by_name(
            f"warning_{game['game_id']}"
        ):
            job.schedule_removal()

        # Reply instantly first, then update grid + unpin in background
        scores = db.get_game_scores(game["game_id"])
        if scores:
            msg_parts.append("\n🏆 <b>Round Summary</b>")
            for i, row in enumerate(scores, 1):
                n     = row.get("first_name") or f"User{row['user_id']}"
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
                msg_parts.append(f"{medal} <b>{n}</b>: {row['total_points']} pts")

        msg_parts.append("\nNew round: /new or /new_hard 🚀")
        await update.message.reply_text(
            "\n".join(msg_parts),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=play_again_keyboard(chat.id),
        )

        # Background: update grid image + unpin (non-blocking)
        async def _finish_game():
            if pin_msg_id:
                await update_grid_photo(context.bot, chat.id, pin_msg_id, game, now_found)
                try:
                    await context.bot.unpin_chat_message(chat.id, pin_msg_id)
                except TelegramError as e:
                    logger.warning("Unpin error: %s", e)

        asyncio.create_task(_finish_game())
        return

    # Mid-game: reply instantly, update grid photo in background
    await update.message.reply_text(
        "\n".join(msg_parts),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=go_to_grid_kb,
    )

    if pin_msg_id:
        asyncio.create_task(
            update_grid_photo(context.bot, chat.id, pin_msg_id, game, now_found)
        )


# ─── /hint ────────────────────────────────────────────────────────────────────

async def _delete_messages(context: ContextTypes.DEFAULT_TYPE):
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
            f"❌ No hints left! ({config.MAX_HINTS_PER_GAME} used this game)"
        )
        return

    words     = [w for w in game["words"].split(",") if w]
    found     = [w for w in game["found_words"].split(",") if w]
    remaining = [w for w in words if w not in found]

    if not remaining:
        await update.message.reply_text("✅ All words already found!")
        return

    hint_word = random.choice(remaining)
    hint_text = make_hint_for_word(hint_word, revealed=2)

    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")
    db.record_hint_used(game["game_id"], user.id, chat.id)

    left  = config.MAX_HINTS_PER_GAME - used - 1
    token = str(uuid.uuid4())[:8]
    context.application.bot_data[f"hint_{token}"] = {
        "hint_text": hint_text,
        "user_id":   user.id,
        "left":      left,
    }

    ulink = f"<a href='tg://user?id={user.id}'><b>{display_name(user)}</b></a>"
    hint_msg = await update.message.reply_text(
        f"💡 Hint requested by {ulink}!\n"
        f"You now have <b>{left}</b> hint{'s' if left != 1 else ''} left!",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔆 Click to Reveal Hint 💡", callback_data=f"hint:reveal:{token}")
        ]]),
    )

    if update.message:
        context.job_queue.run_once(
            _delete_messages,
            when=30,
            data={"chat_id": chat.id, "msg_ids": [update.message.message_id]},
            name=f"del_hint_cmd_{update.message.message_id}",
        )


async def cb_hint_reveal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    token     = query.data.split(":", 2)[2]
    hint_data = context.application.bot_data.get(f"hint_{token}")

    if not hint_data:
        await query.answer("⏰ This hint has expired.", show_alert=True)
        return

    if query.from_user.id != hint_data["user_id"]:
        await query.answer("❌ Only the requester can reveal this hint!", show_alert=True)
        return

    left = hint_data["left"]
    await query.answer(
        f"🔍 Your hint: {hint_data['hint_text']}\n"
        f"({left} hint{'s' if left != 1 else ''} remaining)",
        show_alert=True,
    )
    context.application.bot_data.pop(f"hint_{token}", None)


# ─── /leaderboard / /lb ───────────────────────────────────────────────────────

async def _send_leaderboard(
    target, context, period, scope, chat_id, chat_title, chat_type, edit=False
):
    group_id_filter = chat_id if scope == "chat" else None
    rows            = db.get_period_leaderboard(period, group_id=group_id_filter, limit=20)
    period_label    = PERIOD_LABELS.get(period, period)
    scope_label     = f"📍 {chat_title}" if scope == "chat" else "🌍 Global"

    if not rows:
        text = f"📊 No scores yet for <b>{scope_label} — {period_label}</b>."
    else:
        lines = [f"🏆 <b>{scope_label}</b>\n— {period_label}\n"]
        for i, row in enumerate(rows, 1):
            lines.append(format_lb_row(i, row))
        text = "\n".join(lines)

    kb = _lb_keyboard(period, scope, chat_type)

    if edit:
        try:
            await target.edit_message_text(
                text, parse_mode=constants.ParseMode.HTML, reply_markup=kb,
            )
        except TelegramError:
            pass
    else:
        await target.reply_text(
            text, parse_mode=constants.ParseMode.HTML, reply_markup=kb,
        )


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args   = context.args or []
    period = args[0].lower() if args else "all"
    if period not in PERIOD_LABELS:
        period = "all"

    chat      = update.effective_chat
    scope     = "chat" if chat.type in ("group", "supergroup") else "global"

    await _send_leaderboard(
        update.message, context, period, scope,
        chat_id=chat.id, chat_title=chat.title or "This Chat",
        chat_type=chat.type, edit=False,
    )


async def cb_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 3:
        return
    period = parts[1]
    scope  = parts[2]
    chat   = query.message.chat

    await _send_leaderboard(
        query, context, period, scope,
        chat_id=chat.id, chat_title=chat.title or "This Chat",
        chat_type=chat.type, edit=True,
    )


# ─── /end ─────────────────────────────────────────────────────────────────────

async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text("⚠️ Groups only.")
        return

    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ Only group admins can end the game.")
        return

    game = db.get_active_game(chat.id)
    if not game:
        await update.message.reply_text("❌ No active game.")
        return

    words     = [w for w in game["words"].split(",") if w]
    found     = [w for w in game["found_words"].split(",") if w]
    remaining = [w for w in words if w not in found]

    db.end_game(game["game_id"])

    # Remove timeout job
    for job in context.job_queue.get_jobs_by_name(
        f"timeout_{game['game_id']}"
    ):
        job.schedule_removal()

    # Remove warning job
    for job in context.job_queue.get_jobs_by_name(
        f"warning_{game['game_id']}"
    ):
        job.schedule_removal()

    # Delete warning message
    warning_msg_id = context.application.bot_data.pop(
        f"warning_{game['game_id']}",
        None,
    )

    if warning_msg_id:
        try:
            await context.bot.delete_message(
                chat.id,
                warning_msg_id,
            )
        except TelegramError:
            pass

    pin_msg_id = game.get("pin_msg_id") or game.get("message_id")
    if pin_msg_id and found:
        await update_grid_photo(context.bot, chat.id, pin_msg_id, game, found)
    if pin_msg_id:
        try:
            await context.bot.unpin_chat_message(chat.id, pin_msg_id)
        except TelegramError:
            pass

    scores = db.get_game_scores(game["game_id"])
    lines  = [f"🛑 <b>Game ended by {display_name(user)}.</b>\n"]
    if remaining:
        lines.append(f"❌ Unfound: <b>{', '.join(remaining)}</b>\n")
    else:
        lines.append("🎉 All words had been found!\n")
    if scores:
        lines.append("🏆 <b>Round Scores:</b>")
        for i, row in enumerate(scores, 1):
            n     = row.get("first_name") or f"User{row['user_id']}"
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal} <b>{n}</b> — {row['total_points']} pts")
    else:
        lines.append("No words were found.")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=play_again_keyboard(chat.id),
    )
    await log_to_group(
        context.application,
        f"🛑 Game ended in <b>{chat.title}</b> (<code>{chat.id}</code>)\n"
        f"👤 By: <a href='tg://user?id={user.id}'>{display_name(user)}</a> — "
        f"Found: {len(found)}/{len(words)}",
    )


# ─── /stats ───────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rows = db.get_period_leaderboard("all", limit=1000)
    user_row = next((r for r in rows if r["user_id"] == user.id), None)
    rank     = next((i for i, r in enumerate(rows, 1) if r["user_id"] == user.id), None)

    if not user_row:
        await update.message.reply_text("No scores yet. Start playing with /new!")
        return

    await update.message.reply_text(
        f"📊 <b>Your Word Grid Stats</b>\n\n"
        f"🌍 Rank: <b>#{rank}</b>\n"
        f"⭐ Total Points: <b>{user_row['total_points']}</b>\n"
        f"🔤 Words Found: <b>{user_row.get('words_found', 0)}</b>\n"
        f"🎮 Games Played: <b>{user_row['games_played']}</b>",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /broadcast ───────────────────────────────────────────────────────────────

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not is_sudo(user.id):
        await update.message.reply_text("❌ You are not authorised to use this command.")
        return

    broadcast_text = None
    reply_msg      = update.message.reply_to_message

    if context.args:
        broadcast_text = " ".join(context.args)
    elif reply_msg and reply_msg.text:
        broadcast_text = reply_msg.text
    else:
        await update.message.reply_text(
            "⚠️ Usage:\n"
            "• <code>/broadcast Your message here</code>\n"
            "• Or reply to a message with <code>/broadcast</code>",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    groups = db.get_all_groups()
    if not groups:
        await update.message.reply_text("⚠️ No groups found in database yet.")
        return

    status_msg = await update.message.reply_text(
        f"📡 Broadcasting to <b>{len(groups)}</b> groups…",
        parse_mode=constants.ParseMode.HTML,
    )

    sent = 0
    failed = 0
    blocked = 0

    for group in groups:
        chat_id = group["chat_id"]
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📢 <b>Broadcast Message</b>\n\n{broadcast_text}",
                parse_mode=constants.ParseMode.HTML,
            )
            sent += 1
        except TelegramError as e:
            err = str(e).lower()
            if "bot was kicked" in err or "chat not found" in err or "blocked" in err:
                db.remove_group(chat_id)
                blocked += 1
            else:
                failed += 1
            logger.warning("Broadcast failed for %s: %s", chat_id, e)

        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"📡 <b>Broadcast Complete!</b>\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"🚫 Blocked/Left: <b>{blocked}</b>\n"
        f"❌ Failed: <b>{failed}</b>\n"
        f"📊 Total: <b>{len(groups)}</b> groups",
        parse_mode=constants.ParseMode.HTML,
    )

    await log_to_group(
        context.application,
        f"📡 Broadcast by <a href='tg://user?id={user.id}'>{display_name(user)}</a>\n"
        f"✅ {sent} sent · 🚫 {blocked} blocked · ❌ {failed} failed",
    )


# ─── Group join / leave handler ───────────────────────────────────────────────

async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result: ChatMemberUpdated = update.my_chat_member
    if not result:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    chat       = result.chat
    added_by   = result.from_user

    if chat.type not in ("group", "supergroup"):
        return

    if old_status in ("left", "kicked", "restricted") and new_status in ("member", "administrator"):
        try:
            count = await context.bot.get_chat_member_count(chat.id)
        except TelegramError:
            count = 0

        db.track_group(
            chat_id=chat.id,
            title=chat.title or "",
            username=chat.username or "",
            member_count=count,
        )

        added_by_str = (
            f"<a href='tg://user?id={added_by.id}'>{display_name(added_by)}</a> "
            f"(<code>{added_by.id}</code>)"
            if added_by else "unknown"
        )
        await log_to_group(
            context.application,
            f"🤖 <b>Bot added to a new group!</b>\n\n"
            f"👥 <b>{chat.title}</b>\n"
            f"🆔 <code>{chat.id}</code>\n"
            f"🔗 {'@' + chat.username if chat.username else 'private'}\n"
            f"👤 Members: <b>{count}</b>\n"
            f"➕ Added by: {added_by_str}",
        )

    elif old_status in ("member", "administrator") and new_status in ("left", "kicked"):
        db.remove_group(chat.id)
        await log_to_group(
            context.application,
            f"👋 Bot removed from <b>{chat.title}</b> (<code>{chat.id}</code>)",
        )


# ─── Error handler ─────────────────────────────────────────────────────────────

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


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    db.init_db()

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .build()
    )

    # ── WordGrid handlers ─────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",               cmd_start))
    app.add_handler(CommandHandler("help",                cmd_help))
    app.add_handler(CommandHandler("new",                 cmd_new))
    app.add_handler(CommandHandler("new_hard",            cmd_new_hard))
    app.add_handler(CommandHandler("hint",                cmd_hint))
    app.add_handler(CommandHandler(["leaderboard", "lb"], cmd_leaderboard))
    app.add_handler(CommandHandler("end",                 cmd_end))
    app.add_handler(CommandHandler("stats",               cmd_stats))
    app.add_handler(CommandHandler("broadcast",           cmd_broadcast))

    app.add_handler(CallbackQueryHandler(cb_leaderboard, pattern=r"^lb:"))
    app.add_handler(CallbackQueryHandler(cb_hint_reveal, pattern=r"^hint:reveal:"))
    app.add_handler(CallbackQueryHandler(cb_play_again,  pattern=r"^play:"))

    app.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    # WordGrid message handler — group=0 (higher priority, runs first)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        handle_message,
    ), group=0)

    # ── Paheli handlers (group=1 for message handler, won't conflict) ─────────
    register_paheli_handlers(app)

    app.add_error_handler(error_handler)

    logger.info("VelocityBots starting… (WordGrid + Paheli)")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

def run_web():
    web_app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )

threading.Thread(target=run_web, daemon=True).start()

if __name__ == "__main__":
    main()
