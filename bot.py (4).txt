import logging
import os
import uuid
import io
import random

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


def grid_message_link(chat_id: int, message_id: int) -> str:
    """Build a t.me/c deep link to a specific group message."""
    # For supergroups: chat_id is like -1001234567890 → numeric part is 1234567890
    numeric = str(abs(chat_id))
    if numeric.startswith("100"):
        numeric = numeric[3:]
    return f"https://t.me/c/{numeric}/{message_id}"


def play_again_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ Play Again",  callback_data=f"play:easy:{chat_id}"),
        InlineKeyboardButton("🎮 Hard Mode",  callback_data=f"play:hard:{chat_id}"),
    ]])


def support_keyboard() -> InlineKeyboardMarkup | None:
    if not config.SUPPORT_CHANNEL:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🤝 Support Group", url=config.SUPPORT_CHANNEL),
    ]])


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
    reply_markup=None,
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

    kwargs = dict(
        chat_id=chat_id,
        message_id=message_id,
        media=InputMediaPhoto(
            media=io.BytesIO(img_bytes),
            caption=caption,
            parse_mode=constants.ParseMode.HTML,
        ),
    )
    if reply_markup:
        kwargs["reply_markup"] = reply_markup

    try:
        await bot.edit_message_media(**kwargs)
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

    pin_msg_id = game.get("pin_msg_id")
    if pin_msg_id and found:
        await update_grid_photo(context.bot, group_id, pin_msg_id, game, found)

    if pin_msg_id:
        try:
            await context.bot.unpin_chat_message(group_id, pin_msg_id)
        except TelegramError as e:
            logger.warning("Unpin error: %s", e)

    scores = db.get_game_scores(game_id)
    summary_lines = ["⏰ <b>TIME'S UP!</b> 10 minutes are up.\n"]
    if remaining:
        summary_lines.append(f"❌ Unfound words: <b>{', '.join(remaining)}</b>\n")
    else:
        summary_lines.append("🎉 All words were found!\n")

    summary_lines.append(f"📊 Words Found: <b>{len(found)}/{len(words)}</b>")

    if scores:
        summary_lines.append("\n🏆 <b>Scores:</b>")
        for i, row in enumerate(scores, 1):
            n = row.get("first_name") or f"User{row['user_id']}"
            summary_lines.append(f"  {i}. {n} — {row['total_points']} pts")
    else:
        summary_lines.append("No words were found this round.")

    await context.bot.send_message(
        group_id,
        "\n".join(summary_lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=play_again_keyboard(group_id),
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
        support_btn = []
        if config.SUPPORT_CHANNEL:
            support_btn = [[InlineKeyboardButton("🤝 Support Group", url=config.SUPPORT_CHANNEL)]]
        await update.message.reply_text(
            f"👋 Hello <b>{user.first_name}</b>!\n\n"
            "🎮 I'm the <b>Word Grid Challenge</b> bot!\n"
            "Add me to a group and use:\n"
            "• /new — Start an easy game\n"
            "• /new_hard — Start a hard game\n"
            "• /hint — Get a hint (per game)\n"
            "• /leaderboard or /lb — Global leaderboard\n"
            "• /help — Show all commands",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(support_btn) if support_btn else None,
        )
    else:
        await update.message.reply_text(
            "🎮 Word Grid Challenge is ready! Use /new to start a game.",
        )


# ─── /help ────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_btn = []
    if config.SUPPORT_CHANNEL:
        support_btn = [[InlineKeyboardButton("🤝 Support Group", url=config.SUPPORT_CHANNEL)]]
    await update.message.reply_text(
        "🎮 <b>Word Grid Challenge — Commands</b>\n\n"
        "/new — Start an easy game (10×10)\n"
        "/new_hard — Start a hard game (12×12)\n"
        "/end — End the current game early (admins only)\n"
        "/hint — Get a hint (group message, only you see it)\n"
        "/leaderboard or /lb — Global leaderboard\n"
        "/lb day · /lb week · /lb month · /lb year\n"
        "/stats — Your personal stats\n"
        "/help — This message\n\n"
        "📌 The grid is pinned when a game starts.\n"
        "🎨 Found words light up in the grid as oval highlights!\n"
        "⏰ Games last 10 minutes.\n"
        "🏆 Points: 4 (1st word) · 3 (other words) · 5 (last word)",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(support_btn) if support_btn else None,
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
        pin_msg_id = existing.get("pin_msg_id") or existing.get("message_id")
        kb = []
        if pin_msg_id:
            kb = [[InlineKeyboardButton("🎯 Go to Grid", url=grid_message_link(chat.id, pin_msg_id))]]
        await update.message.reply_text(
            "⚠️ A game is already running in this group!\n"
            "Wait for it to finish or time out before starting a new one.",
            reply_markup=InlineKeyboardMarkup(kb) if kb else None,
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

    # Build keyboard for grid photo
    kb_rows = []
    if config.SUPPORT_CHANNEL:
        kb_rows.append([InlineKeyboardButton("🤝 Support Group", url=config.SUPPORT_CHANNEL)])

    photo_msg = await context.bot.send_photo(
        chat_id=chat.id,
        photo=io.BytesIO(img_bytes),
        caption=caption,
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb_rows) if kb_rows else None,
    )

    db.update_game_message(game_id, photo_msg.message_id)

    try:
        await context.bot.pin_chat_message(
            chat_id=chat.id,
            message_id=photo_msg.message_id,
            disable_notification=True,
        )
        db.update_game_pin(game_id, photo_msg.message_id)
    except TelegramError as e:
        logger.warning("Could not pin message: %s", e)

    context.job_queue.run_once(
        game_timeout,
        when=config.GAME_TIMEOUT_SECONDS,
        data={"game_id": game_id, "group_id": chat.id},
        name=f"timeout_{game_id}",
    )

    await log_to_group(
        context.application,
        f"🆕 New <b>{mode}</b> game started\n"
        f"👥 Group: <b>{chat.title}</b> (<code>{chat.id}</code>)\n"
        f"👤 By: <a href='tg://user?id={user.id}'>{display_name(user)}</a> (<code>{user.id}</code>)\n"
        f"🎮 Game ID: <code>{game_id}</code>",
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, "easy")


async def cmd_new_hard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, "hard")


# ─── callback: Play Again / Hard Mode ─────────────────────────────────────────

async def cb_play_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")   # play:easy:-1001234567890
    if len(parts) != 3:
        return
    mode = parts[1]
    chat_id = int(parts[2])

    user = query.from_user
    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")

    existing = db.get_active_game(chat_id)
    if existing:
        await query.answer("⚠️ A game is already running!", show_alert=True)
        return

    words = get_words_for_mode(mode)
    game_id = str(uuid.uuid4())
    grid_size = 10 if mode == "easy" else 12
    grid, placed = build_grid(words, size=grid_size)
    db.create_game(game_id, chat_id, mode, words, grid=grid, placed=placed)

    img_bytes = render_grid_image(
        grid, title="WORD GRID CHALLENGE",
        placed_words=placed, found_words=[], word_order=words,
    )
    caption = build_caption(words, [], mode)

    kb_rows = []
    if config.SUPPORT_CHANNEL:
        kb_rows.append([InlineKeyboardButton("🤝 Support Group", url=config.SUPPORT_CHANNEL)])

    photo_msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=io.BytesIO(img_bytes),
        caption=caption,
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb_rows) if kb_rows else None,
    )

    db.update_game_message(game_id, photo_msg.message_id)

    try:
        await context.bot.pin_chat_message(
            chat_id=chat_id,
            message_id=photo_msg.message_id,
            disable_notification=True,
        )
        db.update_game_pin(game_id, photo_msg.message_id)
    except TelegramError as e:
        logger.warning("Could not pin: %s", e)

    context.job_queue.run_once(
        game_timeout,
        when=config.GAME_TIMEOUT_SECONDS,
        data={"game_id": game_id, "group_id": chat_id},
        name=f"timeout_{game_id}",
    )

    await log_to_group(
        context.application,
        f"🔁 Rematch <b>{mode}</b> game started in <code>{chat_id}</code> "
        f"by <a href='tg://user?id={user.id}'>{display_name(user)}</a>",
    )


# ─── Message handler — guess words ───────────────────────────────────────────

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

    words = [w for w in game["words"].split(",") if w]
    found = [w for w in game["found_words"].split(",") if w]

    if text not in words or text in found:
        return

    db.upsert_user(user.id, user.username, user.first_name, user.last_name or "")

    found_count = len(found)
    total = len(words)
    if found_count == 0:
        points = config.POINTS_FIRST
        rank_emoji = "🎆"
    elif found_count == total - 1:
        points = config.POINTS_LAST
        rank_emoji = "🏆"
    else:
        points = config.POINTS_NORMAL
        rank_emoji = "💫"

    if not db.mark_word_found(game["game_id"], text):
        return

    db.add_score(user.id, chat.id, game["game_id"], text, points)

    now_found = found + [text]
    remaining = [w for w in words if w not in now_found]
    name = display_name(user)

    pin_msg_id = game.get("pin_msg_id") or game.get("message_id")
    if pin_msg_id:
        await update_grid_photo(context.bot, chat.id, pin_msg_id, game, now_found)

    msg_parts = [f"✅ +{points} points for <b>{name}</b> {rank_emoji}! You found <b>{text}</b>."]

    go_to_grid_kb = None
    if pin_msg_id:
        go_to_grid_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎯 Go to Grid", url=grid_message_link(chat.id, pin_msg_id))
        ]])

    # Game over — all words found
    if not remaining:
        msg_parts.append("\n🎉 <b>GAME COMPLETE!</b> All words found! 🥳")
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
            msg_parts.append("\n🏆 <b>Round Summary</b>")
            for i, row in enumerate(scores, 1):
                n = row.get("first_name") or f"User{row['user_id']}"
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
                msg_parts.append(f"{medal} <b>{n}</b>: {row['total_points']} pts")

        msg_parts.append(f"\nNew round: /new or /new_hard 🚀")

        await update.message.reply_text(
            "\n".join(msg_parts),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=play_again_keyboard(chat.id),
        )
        return

    await update.message.reply_text(
        "\n".join(msg_parts),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=go_to_grid_kb,
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

    # Store hint data under a short token so callback_data stays small (<64 bytes)
    token = str(uuid.uuid4())[:8]
    context.application.bot_data[f"hint_{token}"] = {
        "hint_text": hint_text,
        "full_word": hint_word,
        "user_id": user.id,
        "left": left,
    }

    username_link = f"<a href='tg://user?id={user.id}'><b>{display_name(user)}</b></a>"
    hint_msg = await update.message.reply_text(
        f"💡 Hint requested by {username_link}!\n"
        f"You now have <b>{left}</b> hint{'s' if left != 1 else ''} left!",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Click to Reveal Hint 💡", callback_data=f"hint:reveal:{token}")
        ]]),
    )

    # Auto-delete the /hint command after 30s (keep the reveal button longer)
    if update.message:
        context.job_queue.run_once(
            _delete_messages,
            when=30,
            data={"chat_id": chat.id, "msg_ids": [update.message.message_id]},
            name=f"del_hint_cmd_{update.message.message_id}",
        )


async def cb_hint_reveal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the hint ONLY to the person who pressed the button."""
    query = update.callback_query
    token = query.data.split(":", 2)[2]
    hint_data = context.application.bot_data.get(f"hint_{token}")

    if not hint_data:
        await query.answer("⏰ This hint has expired.", show_alert=True)
        return

    # Only the user who requested the hint can reveal it
    if query.from_user.id != hint_data["user_id"]:
        await query.answer(
            "❌ Only the person who requested this hint can reveal it!",
            show_alert=True,
        )
        return

    await query.answer(
        f"🔍 Your hint: {hint_data['hint_text']}\n"
        f"({hint_data['left']} hint{'s' if hint_data['left'] != 1 else ''} left this game)",
        show_alert=True,
    )

    # Remove stored hint so it can only be revealed once
    context.application.bot_data.pop(f"hint_{token}", None)


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
    lines.append("\n📌 Points are cumulative across all groups.")

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

    jobs = context.job_queue.get_jobs_by_name(f"timeout_{game['game_id']}")
    for j in jobs:
        j.schedule_removal()

    pin_msg_id = game.get("pin_msg_id") or game.get("message_id")
    if pin_msg_id and found:
        await update_grid_photo(context.bot, chat.id, pin_msg_id, game, found)

    if pin_msg_id:
        try:
            await context.bot.unpin_chat_message(chat.id, pin_msg_id)
        except TelegramError as e:
            logger.warning("Unpin error: %s", e)

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
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal} <b>{n}</b> — {row['total_points']} pts")
    else:
        lines.append("No words were found this round.")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=play_again_keyboard(chat.id),
    )

    await log_to_group(
        context.application,
        f"🛑 Game ended manually in <b>{chat.title}</b> (<code>{chat.id}</code>)\n"
        f"👤 Admin: <a href='tg://user?id={user.id}'>{display_name(user)}</a>\n"
        f"📊 Found: {len(found)}/{len(words)}",
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


# ─── Group join / bot added handler ──────────────────────────────────────────

async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when the bot's status changes in a chat (added/removed from group)."""
    result: ChatMemberUpdated = update.my_chat_member
    if not result:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    chat = result.chat
    added_by = result.from_user

    # Bot was added / promoted to member or admin
    bot_joined = (
        old_status in ("left", "kicked", "restricted")
        and new_status in ("member", "administrator")
    )
    if not bot_joined:
        return

    if chat.type not in ("group", "supergroup"):
        return

    # Try to get member count
    try:
        count = await context.bot.get_chat_member_count(chat.id)
        members_str = f"<b>{count}</b> members"
    except TelegramError:
        members_str = "unknown members"

    added_by_str = (
        f"<a href='tg://user?id={added_by.id}'>{display_name(added_by)}</a> "
        f"(<code>{added_by.id}</code>)"
        if added_by else "unknown"
    )

    username_str = f"@{chat.username}" if chat.username else "private group"

    await log_to_group(
        context.application,
        f"🤖 <b>Bot added to a new group!</b>\n\n"
        f"👥 <b>Group:</b> {chat.title}\n"
        f"🆔 <b>Chat ID:</b> <code>{chat.id}</code>\n"
        f"🔗 <b>Username:</b> {username_str}\n"
        f"👤 <b>Members:</b> {members_str}\n"
        f"➕ <b>Added by:</b> {added_by_str}\n"
        f"📋 <b>Type:</b> {chat.type}",
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

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("new",    cmd_new))
    app.add_handler(CommandHandler("new_hard", cmd_new_hard))
    app.add_handler(CommandHandler("hint",   cmd_hint))
    app.add_handler(CommandHandler(["leaderboard", "lb"], cmd_leaderboard))
    app.add_handler(CommandHandler("end",    cmd_end))
    app.add_handler(CommandHandler("stats",  cmd_stats))

    app.add_handler(CallbackQueryHandler(cb_leaderboard,  pattern=r"^lb:"))
    app.add_handler(CallbackQueryHandler(cb_hint_reveal,  pattern=r"^hint:reveal:"))
    app.add_handler(CallbackQueryHandler(cb_play_again,   pattern=r"^play:"))

    app.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        handle_message,
    ))
    app.add_error_handler(error_handler)

    logger.info("Bot starting with polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
