"""
paheli.py — Complete Paheli (Riddle) game module for VelocityBots.
Plug-and-play: call register_paheli_handlers(app) in your main bot.py.

Commands:
  /paheli   /answer   /hint   /skip   /daily   /weekly
  /leaderboard (paheli)   /profile   /inventory   /shop
  /stats   /settings   /challenge   /clan
  Admin: /addriddle  /deleteriddle  /pban  /punban  /ridstats
"""

import asyncio
import json
import logging
import os
import random
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)
from telegram.error import TelegramError

import paheli_db as pdb

logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

OWNER_ID      = int(os.environ.get("OWNER_ID", "0"))
_sudo_raw     = os.environ.get("SUDO_USERS", "")
SUDO_USERS    = set(int(x.strip()) for x in _sudo_raw.split(",") if x.strip().isdigit())
if OWNER_ID:
    SUDO_USERS.add(OWNER_ID)

RIDDLE_TIMEOUT_SECONDS = 120   # 2 minutes per riddle in group
HINT_COOLDOWN_SECONDS  = 15    # cooldown between /hint uses
PAHELI_COOLDOWN_SECONDS = 10   # cooldown between /paheli uses
MAX_HINTS_PER_SESSION  = 3

POINTS_BY_DIFFICULTY = {
    "easy":      {"base": 10,  "no_hint": 12,  "xp": 10,  "coins": 5},
    "medium":    {"base": 25,  "no_hint": 30,  "xp": 25,  "coins": 15},
    "hard":      {"base": 50,  "no_hint": 60,  "xp": 50,  "coins": 30},
    "legendary": {"base": 100, "no_hint": 125, "xp": 100, "coins": 60},
}

DIFFICULTY_EMOJI = {
    "easy": "🟢", "medium": "🟡", "hard": "🔴", "legendary": "💀"
}

CATEGORY_EMOJI = {
    "general":  "🌐", "movies":  "🎬", "sports":  "⚽",
    "science":  "🔬", "math":    "🔢", "tech":    "💻",
    "history":  "📜",
}


# ─── Riddles loader ───────────────────────────────────────────────────────────

_ALL_RIDDLES: list[dict] = []


def _load_riddles():
    global _ALL_RIDDLES
    riddle_path = Path(__file__).parent / "riddles.json"
    try:
        with open(riddle_path, encoding="utf-8") as f:
            data = json.load(f)
        _ALL_RIDDLES = data.get("riddles", [])
        # Also merge any custom riddles from DB
        custom = pdb.get_custom_riddles()
        _ALL_RIDDLES.extend(custom)
        logger.info("Loaded %d riddles (%d custom)", len(_ALL_RIDDLES) - len(custom), len(custom))
    except Exception as e:
        logger.error("Failed to load riddles.json: %s", e)
        _ALL_RIDDLES = []


def _pick_riddle(group_id: int, difficulty: str = "all",
                 language: str = "all") -> dict | None:
    if not _ALL_RIDDLES:
        _load_riddles()

    used_ids = pdb.get_used_riddle_ids(group_id, limit=300)
    pool = [
        r for r in _ALL_RIDDLES
        if r["id"] not in used_ids
        and (difficulty == "all" or r.get("difficulty") == difficulty)
        and (language == "all" or r.get("language") == language)
    ]

    if not pool:
        # Reset if all riddles used
        pool = [
            r for r in _ALL_RIDDLES
            if (difficulty == "all" or r.get("difficulty") == difficulty)
            and (language == "all" or r.get("language") == language)
        ]

    return random.choice(pool) if pool else None


# ─── Answer normalisation ─────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Lowercase, strip punctuation/extra spaces, normalise Unicode."""
    text = unicodedata.normalize("NFC", text.strip().lower())
    text = re.sub(r"[^\w\s\u0900-\u097F]", "", text)  # keep devanagari
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _check_single(u: str, c: str) -> bool:
    """Check user input against one accepted answer string."""
    c = _normalise(c)
    if u == c:
        return True
    if u.replace(" ", "") == c.replace(" ", ""):
        return True
    c_words = c.split()
    if len(c_words) >= 3:
        u_words = set(u.split())
        matched = sum(1 for w in c_words if w in u_words)
        if matched / len(c_words) >= 0.8:
            return True
    return False


def _answers_match(user_input: str, correct) -> bool:
    """Accept correct as a string OR a list of accepted answers (Hinglish + English)."""
    u = _normalise(user_input)
    if isinstance(correct, list):
        return any(_check_single(u, c) for c in correct)
    return _check_single(u, correct)


def _primary_answer(correct) -> str:
    """Return the first (primary/display) answer from a string or list."""
    if isinstance(correct, list):
        return correct[0]
    return correct


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _display_name(user) -> str:
    name = (user.first_name or "").strip()
    if user.last_name:
        name = (name + " " + user.last_name).strip()
    return name or f"User{user.id}"


def _is_sudo(user_id: int) -> bool:
    return user_id in SUDO_USERS


def _make_riddle_text(riddle: dict, hint_count: int = 0,
                      show_category: bool = True) -> str:
    d_emoji = DIFFICULTY_EMOJI.get(riddle.get("difficulty", "easy"), "🟡")
    c_emoji = CATEGORY_EMOJI.get(riddle.get("category", "general"), "🌐")
    pts     = riddle.get("points", 10)
    lang    = "🇮🇳 Hindi" if riddle.get("language") == "hi" else "🇬🇧 English"

    lines = [
        "━━━━━━━━━━━━━━━━━━",
        "🧩 <b>PAHELI — RIDDLE CHALLENGE</b>",
        "━━━━━━━━━━━━━━━━━━\n",
        f"<b>{riddle['question']}</b>\n",
    ]

    if show_category:
        lines.append(
            f"{c_emoji} <b>Category:</b> {riddle.get('category','?').title()}  "
            f"{d_emoji} <b>{riddle.get('difficulty','?').upper()}</b>  "
            f"{lang}"
        )

    lines.append(f"🏆 <b>Points:</b> {pts}   ⏱ <b>Timeout:</b> 2 min")

    if hint_count > 0:
        hints = riddle.get("hints", [])
        shown = hints[:hint_count]
        hint_lines = "\n".join(f"  • {h}" for h in shown)
        lines.append(f"\n💡 <b>Hints used ({hint_count}):</b>\n{hint_lines}")

    lines.append("\n✏️ <i>Type your answer in the chat!</i>")
    lines.append("━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _riddle_keyboard(session_id: str, hints_used: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"💡 Hint ({hints_used}/{MAX_HINTS_PER_SESSION})",
                                 callback_data=f"ph:hint:{session_id}"),
            InlineKeyboardButton("⏭ Skip",
                                 callback_data=f"ph:skip:{session_id}"),
        ],
        [
            InlineKeyboardButton("📊 Leaderboard",
                                 callback_data="ph:lb:all:global"),
        ],
    ])


# ─── /game — Game selector (called from bot.py) ────────────────────────────────

async def cmd_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the game picker with WordGrid and Paheli buttons."""
    chat = update.effective_chat
    chat_id = chat.id

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔤 Word Grid",
                                 callback_data=f"game:wordgrid:{chat_id}"),
            InlineKeyboardButton("🧩 Paheli (Riddles)",
                                 callback_data=f"game:paheli:{chat_id}"),
        ]
    ])

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "🎮 <b>VelocityBots Game Center</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Choose your game and let the fun begin!\n\n"
        "🔤 <b>Word Grid</b> — Find hidden words in a letter grid\n"
        "🧩 <b>Paheli</b> — Solve riddles in Hindi & English\n",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=keyboard,
    )


async def cb_game_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the game selector inline buttons."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 3:
        return

    game_type = parts[1]
    chat_id   = int(parts[2])
    user      = query.from_user
    chat      = query.message.chat

    if game_type == "wordgrid":
        # Check if group; if so, start wordgrid game
        if chat.type == "private":
            await query.answer("⚠️ Word Grid can only be played in groups!", show_alert=True)
            return
        # Import start_game from bot module to avoid circular imports
        await query.message.reply_text(
            "🔤 Starting Word Grid! Use /new to start an easy game or /new_hard for hard mode."
        )

    elif game_type == "paheli":
        # Start paheli directly
        context._user_id = user.id
        await _start_paheli_session(update, context, chat_id=chat_id,
                                    user=user, from_callback=True,
                                    reply_to=query.message)


# ─── /paheli ──────────────────────────────────────────────────────────────────

async def cmd_paheli(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if pdb.is_banned(user.id):
        await update.message.reply_text("🚫 You are banned from Paheli. Contact admin.")
        return

    # Cooldown check
    cd = pdb.check_cooldown(user.id, "paheli", PAHELI_COOLDOWN_SECONDS)
    if cd > 0:
        await update.message.reply_text(
            f"⏳ Please wait <b>{cd}s</b> before starting another riddle.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    await _start_paheli_session(update, context, chat_id=chat.id,
                                user=user, from_callback=False)


async def _start_paheli_session(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 chat_id: int, user, from_callback: bool = False,
                                 reply_to=None):
    """Core: pick a riddle, create session, send message."""

    # Ensure group only
    msg = reply_to or (update.message if hasattr(update, "message") else None)
    chat_type = (msg.chat.type if msg else "group") if hasattr(msg, "chat") else "group"

    if chat_type == "private":
        text = "⚠️ Paheli is best played in groups! Use /paheli in a group chat."
        if msg:
            await msg.reply_text(text)
        return

    # Check if already active
    existing = pdb.get_active_paheli(chat_id)
    if existing:
        session_id = existing["session_id"]
        riddle     = existing["riddle"]
        hints_used = existing.get("hints_used", 0)
        text = (
            "⚠️ A riddle is already active!\n\n"
            + _make_riddle_text(riddle, hints_used)
        )
        if msg:
            await msg.reply_text(
                text,
                parse_mode=constants.ParseMode.HTML,
                reply_markup=_riddle_keyboard(session_id, hints_used),
            )
        return

    # Get player settings for language/difficulty preference
    player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    settings = player.get("settings", {})
    lang_pref = settings.get("language", "both")
    diff_pref = settings.get("difficulty", "all")

    language   = "all" if lang_pref == "both" else lang_pref
    difficulty = diff_pref

    riddle = _pick_riddle(chat_id, difficulty=difficulty, language=language)
    if not riddle:
        if msg:
            await msg.reply_text("❌ No riddles available. Try again later!")
        return

    session_id = str(uuid.uuid4())
    pdb.create_paheli_session(session_id, riddle, chat_id, user.id)
    pdb.set_cooldown(user.id, "paheli", PAHELI_COOLDOWN_SECONDS)

    text = _make_riddle_text(riddle, hint_count=0)

    sent = None
    if msg:
        sent = await msg.reply_text(
            text,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_riddle_keyboard(session_id, 0),
        )

    # Schedule timeout
    context.job_queue.run_once(
        _paheli_timeout,
        when=RIDDLE_TIMEOUT_SECONDS,
        data={"session_id": session_id, "group_id": chat_id,
              "msg_id": sent.message_id if sent else None},
        name=f"ph_timeout_{session_id}",
    )


# ─── Timeout ──────────────────────────────────────────────────────────────────

async def _paheli_timeout(context: ContextTypes.DEFAULT_TYPE):
    data       = context.job.data
    session_id = data["session_id"]
    group_id   = data["group_id"]

    session = pdb.get_active_paheli_by_session(session_id)
    if not session or not session.get("active"):
        return

    riddle = session["riddle"]
    pdb.timeout_paheli(session_id)

    try:
        await context.bot.send_message(
            group_id,
            f"⏰ <b>Time's Up!</b>\n\n"
            f"❌ Nobody solved the riddle in time.\n\n"
            f"🔑 <b>Answer:</b> <code>{_primary_answer(riddle['answer']).title()}</code>\n\n"
            f"Use /paheli to try the next one! 🎯",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎯 Next Riddle", callback_data=f"ph:next:{group_id}"),
            ]]),
        )
    except TelegramError as e:
        logger.warning("Paheli timeout send error: %s", e)


def _cancel_timeout(context, session_id: str):
    for job in context.job_queue.get_jobs_by_name(f"ph_timeout_{session_id}"):
        job.schedule_removal()


# ─── Message handler — answer detection ───────────────────────────────────────

async def paheli_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercepts group messages and checks for correct riddle answers."""
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        return

    if pdb.is_banned(user.id):
        return

    text = update.message.text.strip()

    session = pdb.get_active_paheli(chat.id)
    if not session:
        return

    riddle     = session["riddle"]
    session_id = session["session_id"]

    if not _answers_match(text, riddle["answer"]):
        return

    # Correct!
    if not pdb.solve_paheli(session_id, user.id):
        return  # race condition — already solved

    _cancel_timeout(context, session_id)

    hints_used = session.get("hints_used", 0)
    difficulty = riddle.get("difficulty", "easy")
    pts_data   = POINTS_BY_DIFFICULTY.get(difficulty, POINTS_BY_DIFFICULTY["easy"])
    points     = pts_data["no_hint"] if hints_used == 0 else pts_data["base"]

    pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    pdb.record_paheli_score(user.id, chat.id, session_id, riddle["id"], points, difficulty)
    reward = pdb.grant_xp_coins(user.id, pts_data["xp"], pts_data["coins"])

    player   = pdb.get_player(user.id)
    name     = _display_name(user)
    d_emoji  = DIFFICULTY_EMOJI.get(difficulty, "🟡")
    no_hint_bonus = " (+Bonus no-hint!)" if hints_used == 0 else ""

    lines = [
        "━━━━━━━━━━━━━━━━━━",
        f"🎉 <b>CORRECT ANSWER!</b>",
        "━━━━━━━━━━━━━━━━━━\n",
        f"🏆 <b>{name}</b> solved the riddle! 🎊\n",
        f"🔑 <b>Answer:</b> <code>{_primary_answer(riddle['answer']).title()}</code>",
        f"{d_emoji} <b>Difficulty:</b> {difficulty.title()}",
        f"⭐ <b>Points:</b> +{points}{no_hint_bonus}",
        f"🔮 <b>XP:</b> +{reward.get('xp_gained', 0)}",
        f"🪙 <b>Coins:</b> +{reward.get('coins_gained', 0)}",
    ]

    if reward.get("boosted"):
        lines.append("⚡ <b>2× XP Boost active!</b>")

    if reward.get("leveled_up"):
        lines.append(
            f"\n🆙 <b>LEVEL UP!</b> → Level {reward['new_level']} "
            f"— {reward['new_title']} 🎊"
        )

    if player:
        lines.append(
            f"\n📊 <b>Your Stats:</b> Lv.{player.get('level',0)} | "
            f"{player.get('xp',0)} XP | {player.get('coins',0)} 🪙"
        )

    lines.append("\n━━━━━━━━━━━━━━━━━━")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎯 Next Riddle", callback_data=f"ph:next:{chat.id}"),
            InlineKeyboardButton("📊 Leaderboard", callback_data="ph:lb:all:global"),
        ]]),
    )


# ─── /answer (explicit answer command for DMs) ────────────────────────────────

async def cmd_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if not context.args:
        await update.message.reply_text(
            "Usage: /answer <your answer>\nExample: /answer keyboard"
        )
        return

    answer_text = " ".join(context.args)
    session = pdb.get_active_paheli(chat.id)

    if not session:
        await update.message.reply_text("❌ No active riddle. Use /paheli to start one!")
        return

    riddle = session["riddle"]
    if _answers_match(answer_text, riddle["answer"]):
        # Simulate correct answer
        update.message.text = answer_text
        await paheli_answer_handler(update, context)
    else:
        await update.message.reply_text(
            f"❌ <b>Wrong answer!</b> Try again.\n"
            f"💡 Use /hint if you're stuck.",
            parse_mode=constants.ParseMode.HTML,
        )


# ─── /hint ────────────────────────────────────────────────────────────────────

async def cmd_paheli_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if pdb.is_banned(user.id):
        return

    cd = pdb.check_cooldown(user.id, "ph_hint", HINT_COOLDOWN_SECONDS)
    if cd > 0:
        await update.message.reply_text(
            f"⏳ Wait <b>{cd}s</b> before requesting another hint.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    session = pdb.get_active_paheli(chat.id)
    if not session:
        await update.message.reply_text("❌ No active riddle. Use /paheli to start one!")
        return

    riddle     = session["riddle"]
    hints      = riddle.get("hints", [])
    hints_used = session.get("hints_used", 0)

    if hints_used >= MAX_HINTS_PER_SESSION or hints_used >= len(hints):
        await update.message.reply_text(
            f"❌ No more hints available! ({hints_used}/{MAX_HINTS_PER_SESSION} used)"
        )
        return

    # Check if player has hint tokens or use free hints
    player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    inventory = player.get("inventory", {})
    hint_tokens = inventory.get("hints", 0)

    if hints_used >= 1 and hint_tokens <= 0:
        await update.message.reply_text(
            "❌ You need a <b>Hint Token</b> for extra hints!\n"
            "Buy them in /shop (50 coins each) or earn them daily.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    if hints_used >= 1:
        pdb.use_inventory_item(user.id, "hint")

    new_count = pdb.increment_hints(session["session_id"])
    pdb.set_cooldown(user.id, "ph_hint", HINT_COOLDOWN_SECONDS)

    hint_text = hints[hints_used] if hints_used < len(hints) else "No more hints!"
    name = _display_name(user)

    await update.message.reply_text(
        f"💡 <b>Hint #{new_count}</b> (requested by {name}):\n\n"
        f"<i>{hint_text}</i>\n\n"
        f"💡 Hints used: <b>{new_count}/{MAX_HINTS_PER_SESSION}</b>\n"
        f"⚠️ Using hints reduces your points!",
        parse_mode=constants.ParseMode.HTML,
    )


async def cb_paheli_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline hint button callback."""
    query      = update.callback_query
    session_id = query.data.split(":", 2)[2]
    user       = query.from_user

    await query.answer()

    session = pdb.get_active_paheli_by_session(session_id)
    if not session or not session.get("active"):
        await query.answer("⏰ This riddle has expired.", show_alert=True)
        return

    riddle     = session["riddle"]
    hints      = riddle.get("hints", [])
    hints_used = session.get("hints_used", 0)

    if hints_used >= MAX_HINTS_PER_SESSION or hints_used >= len(hints):
        await query.answer(
            f"No more hints! ({hints_used}/{MAX_HINTS_PER_SESSION} used)",
            show_alert=True
        )
        return

    cd = pdb.check_cooldown(user.id, "ph_hint", HINT_COOLDOWN_SECONDS)
    if cd > 0:
        await query.answer(f"⏳ Wait {cd}s before next hint.", show_alert=True)
        return

    player     = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    inventory  = player.get("inventory", {})
    hint_tokens = inventory.get("hints", 0)

    if hints_used >= 1 and hint_tokens <= 0:
        await query.answer(
            "❌ You need a Hint Token! Buy in /shop (50 coins each).",
            show_alert=True
        )
        return

    if hints_used >= 1:
        pdb.use_inventory_item(user.id, "hint")

    new_count = pdb.increment_hints(session_id)
    pdb.set_cooldown(user.id, "ph_hint", HINT_COOLDOWN_SECONDS)

    hint_text = hints[hints_used] if hints_used < len(hints) else "No more hints!"
    name = _display_name(user)

    # Edit the message to show updated hint count
    try:
        await query.edit_message_text(
            _make_riddle_text(session["riddle"], hint_count=new_count),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_riddle_keyboard(session_id, new_count),
        )
    except TelegramError:
        pass

    await query.message.reply_text(
        f"💡 <b>Hint #{new_count}</b> by {name}: <i>{hint_text}</i>",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /skip ────────────────────────────────────────────────────────────────────

async def cmd_paheli_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    session = pdb.get_active_paheli(chat.id)
    if not session:
        await update.message.reply_text("❌ No active riddle!")
        return

    player    = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    inventory = player.get("inventory", {})
    skips     = inventory.get("skips", 0)

    if skips <= 0:
        await update.message.reply_text(
            "❌ You have no Skip Tokens!\n"
            "Buy them in /shop (75 coins each).",
        )
        return

    riddle = session["riddle"]
    pdb.use_inventory_item(user.id, "skip")
    pdb.skip_paheli(session["session_id"])
    _cancel_timeout(context, session["session_id"])

    await update.message.reply_text(
        f"⏭ <b>Riddle Skipped!</b>\n\n"
        f"🔑 <b>Answer was:</b> <code>{_primary_answer(riddle['answer']).title()}</code>\n\n"
        f"Use /paheli for the next riddle!",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎯 Next Riddle", callback_data=f"ph:next:{chat.id}"),
        ]]),
    )


async def cb_paheli_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query      = update.callback_query
    session_id = query.data.split(":", 2)[2]
    user       = query.from_user

    session = pdb.get_active_paheli_by_session(session_id)
    if not session or not session.get("active"):
        await query.answer("This riddle has already ended.", show_alert=True)
        return

    player    = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    inventory = player.get("inventory", {})
    skips     = inventory.get("skips", 0)

    if skips <= 0:
        await query.answer(
            "❌ No Skip Tokens! Buy in /shop (75 coins each).",
            show_alert=True
        )
        return

    riddle = session["riddle"]
    pdb.use_inventory_item(user.id, "skip")
    pdb.skip_paheli(session_id)
    _cancel_timeout(context, session_id)

    await query.answer("Riddle skipped!")
    try:
        await query.edit_message_text(
            f"⏭ <b>Riddle Skipped!</b>\n"
            f"🔑 <b>Answer:</b> <code>{_primary_answer(riddle['answer']).title()}</code>",
            parse_mode=constants.ParseMode.HTML,
        )
    except TelegramError:
        pass

    await query.message.reply_text(
        "Use /paheli for the next riddle! 🎯"
    )


# ─── Next riddle callback ─────────────────────────────────────────────────────

async def cb_paheli_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    parts   = query.data.split(":")
    chat_id = int(parts[2]) if len(parts) > 2 else query.message.chat.id
    user    = query.from_user

    await query.answer("Starting next riddle…")

    cd = pdb.check_cooldown(user.id, "paheli", PAHELI_COOLDOWN_SECONDS)
    if cd > 0:
        await query.answer(f"⏳ Wait {cd}s before next riddle.", show_alert=True)
        return

    existing = pdb.get_active_paheli(chat_id)
    if existing:
        await query.answer("A riddle is already active!", show_alert=True)
        return

    player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    settings   = player.get("settings", {})
    language   = "all" if settings.get("language", "both") == "both" else settings.get("language")
    difficulty = settings.get("difficulty", "all")

    riddle = _pick_riddle(chat_id, difficulty=difficulty, language=language)
    if not riddle:
        await query.message.reply_text("❌ No more riddles available!")
        return

    session_id = str(uuid.uuid4())
    pdb.create_paheli_session(session_id, riddle, chat_id, user.id)
    pdb.set_cooldown(user.id, "paheli", PAHELI_COOLDOWN_SECONDS)

    text = _make_riddle_text(riddle, hint_count=0)
    sent = await query.message.reply_text(
        text,
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_riddle_keyboard(session_id, 0),
    )

    context.job_queue.run_once(
        _paheli_timeout,
        when=RIDDLE_TIMEOUT_SECONDS,
        data={"session_id": session_id, "group_id": chat_id,
              "msg_id": sent.message_id},
        name=f"ph_timeout_{session_id}",
    )


# ─── /daily ───────────────────────────────────────────────────────────────────

async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")

    if pdb.is_banned(user.id):
        return

    reward = pdb.claim_daily(user.id)

    if not reward:
        doc = pdb.get_player(user.id)
        last = doc.get("last_daily")
        if last:
            next_time = last.timestamp() + 86400
            now_ts    = datetime.now(timezone.utc).timestamp()
            remaining = int(next_time - now_ts)
            h, rem    = divmod(remaining, 3600)
            m, s      = divmod(rem, 60)
            await update.message.reply_text(
                f"⏳ Daily already claimed!\n"
                f"Next daily in: <b>{h}h {m}m {s}s</b>",
                parse_mode=constants.ParseMode.HTML,
            )
        return

    streak = reward["streak"]
    streak_bonus = "🔥 Streak bonus!" if streak > 1 else ""
    weekly_hint  = f"\n🎁 <b>Week complete!</b> +1 💎 Gem!" if streak % 7 == 0 else ""

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "🌅 <b>DAILY REWARD CLAIMED!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🪙 +<b>{reward['coins']}</b> Coins\n"
        f"🔮 +<b>{reward['xp']}</b> XP\n"
        + (f"💎 +<b>{reward['gems']}</b> Gem(s)\n" if reward.get("gems") else "") +
        f"\n🔥 <b>Streak:</b> {streak} day{'s' if streak != 1 else ''} {streak_bonus}"
        + weekly_hint +
        "\n\n✅ Come back tomorrow to keep your streak!",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /weekly ──────────────────────────────────────────────────────────────────

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    pdb.ensure_player(user.id, user.first_name or "", user.username or "")

    reward = pdb.claim_weekly(user.id)
    if not reward:
        await update.message.reply_text(
            "⏳ Weekly reward already claimed!\n"
            "Come back next week for your next reward. 📅"
        )
        return

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "📅 <b>WEEKLY REWARD CLAIMED!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🪙 +<b>{reward['coins']}</b> Coins\n"
        f"🔮 +<b>{reward['xp']}</b> XP\n"
        f"💎 +<b>{reward['gems']}</b> Gems\n\n"
        "🎉 See you next week!",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /profile ─────────────────────────────────────────────────────────────────

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    name   = _display_name(user)

    xp         = player.get("xp", 0)
    level      = player.get("level", 0)
    title      = player.get("title", pdb.TITLES[0])
    coins      = player.get("coins", 0)
    gems       = player.get("gems", 0)
    streak     = player.get("daily_streak", 0)
    solved     = player.get("riddles_solved", 0)
    clan_id    = player.get("clan_id")
    inventory  = player.get("inventory", {})
    xp_needed, next_thresh = pdb.xp_for_next_level(xp)

    # XP progress bar
    if next_thresh > 0:
        current_level_xp = pdb.LEVEL_THRESHOLDS[level] if level < len(pdb.LEVEL_THRESHOLDS) else 0
        span = next_thresh - current_level_xp
        filled = max(0, xp - current_level_xp)
        bar_filled = int((filled / span) * 10) if span else 10
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        xp_line = f"[{bar}] {xp}/{next_thresh} XP"
    else:
        xp_line = f"{xp} XP (MAX LEVEL)"

    achievements = player.get("achievements", [])
    badges       = player.get("badges", [])
    badge_str    = " ".join(badges[:5]) if badges else "None yet"
    clan_str     = f"🏰 {clan_id}" if clan_id else "No clan"

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{name}'s Profile</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎖 <b>Title:</b> {title}\n"
        f"⚡ <b>Level:</b> {level}\n"
        f"📊 <b>XP:</b> {xp_line}\n\n"
        f"🪙 <b>Coins:</b> {coins}\n"
        f"💎 <b>Gems:</b> {gems}\n"
        f"🔥 <b>Streak:</b> {streak} day{'s' if streak != 1 else ''}\n\n"
        f"🧩 <b>Riddles Solved:</b> {solved}\n"
        f"💡 <b>Hint Tokens:</b> {inventory.get('hints', 0)}\n"
        f"⏭ <b>Skip Tokens:</b> {inventory.get('skips', 0)}\n\n"
        f"🏅 <b>Badges:</b> {badge_str}\n"
        f"🏰 <b>Clan:</b> {clan_str}",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍 Shop", callback_data="ph:shop:main"),
            InlineKeyboardButton("🎒 Inventory", callback_data="ph:inv:main"),
        ]]),
    )


# ─── /inventory ───────────────────────────────────────────────────────────────

async def cmd_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user      = update.effective_user
    player    = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    inventory = player.get("inventory", {})
    name      = _display_name(user)

    hints  = inventory.get("hints",  0)
    skips  = inventory.get("skips",  0)
    boosts = inventory.get("boosts", 0)

    active_boost = ""
    if player.get("xp_boost_until"):
        boost_until = player["xp_boost_until"]
        if boost_until.tzinfo is None:
            boost_until = boost_until.replace(tzinfo=timezone.utc)
        if boost_until > datetime.now(timezone.utc):
            remaining = (boost_until - datetime.now(timezone.utc)).seconds // 60
            active_boost = f"\n⚡ <b>2× XP Boost:</b> {remaining}m remaining"

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎒 <b>{name}'s Inventory</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💡 <b>Hint Tokens:</b> {hints}\n"
        f"⏭ <b>Skip Tokens:</b> {skips}\n"
        f"⚡ <b>XP Boosts:</b> {boosts}"
        + active_boost +
        "\n\n🛍 Visit /shop to buy more items!\n"
        "💰 Current balance: "
        f"<b>{player.get('coins', 0)} 🪙</b> | <b>{player.get('gems', 0)} 💎</b>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍 Open Shop", callback_data="ph:shop:main"),
        ]]),
    )


# ─── /shop ────────────────────────────────────────────────────────────────────

def _shop_keyboard(page: str = "main") -> InlineKeyboardMarkup:
    if page == "main":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💡 Hint Token — 50🪙",    callback_data="ph:buy:hint_single"),
             InlineKeyboardButton("💡 5 Hints — 200🪙",      callback_data="ph:buy:hint_pack")],
            [InlineKeyboardButton("⏭ Skip Token — 75🪙",    callback_data="ph:buy:skip_token"),
             InlineKeyboardButton("⏭ 3 Skips — 200🪙",      callback_data="ph:buy:skip_pack")],
            [InlineKeyboardButton("🎡 Lucky Wheel — 100🪙",  callback_data="ph:buy:lucky_wheel")],
            [InlineKeyboardButton("📦 Silver Chest — 150🪙", callback_data="ph:buy:chest_silver"),
             InlineKeyboardButton("🥇 Gold Chest — 400🪙",   callback_data="ph:buy:chest_gold")],
            [InlineKeyboardButton("⚡ 2× XP (1h) — 500🪙",  callback_data="ph:buy:double_xp")],
        ])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙 Back", callback_data="ph:shop:main"),
    ]])


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    coins  = player.get("coins", 0)
    gems   = player.get("gems", 0)

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "🛍 <b>PAHELI SHOP</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Your Balance:</b> {coins} 🪙 | {gems} 💎\n\n"
        "Select an item to purchase:",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_shop_keyboard("main"),
    )


async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")

    if parts[1] == "shop":
        # Show shop
        user   = query.from_user
        player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
        coins  = player.get("coins", 0)
        gems   = player.get("gems", 0)
        await query.answer()
        try:
            await query.edit_message_text(
                f"🛍 <b>PAHELI SHOP</b>\n\n"
                f"💰 Balance: {coins} 🪙 | {gems} 💎\n\n"
                "Select an item:",
                parse_mode=constants.ParseMode.HTML,
                reply_markup=_shop_keyboard("main"),
            )
        except TelegramError:
            pass
        return

    if parts[1] != "buy":
        await query.answer()
        return

    item_key = parts[2]
    user     = query.from_user
    player   = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    item     = pdb.SHOP_ITEMS.get(item_key)

    if not item:
        await query.answer("Unknown item.", show_alert=True)
        return

    cost = item["cost"]
    if not pdb.spend_coins(user.id, cost):
        await query.answer(
            f"❌ Not enough coins! You need {cost}🪙.",
            show_alert=True
        )
        return

    item_type = item["type"]
    qty       = item["quantity"]

    # Handle special types
    if item_type in ("hint", "skip"):
        pdb.add_inventory(user.id, item_type, qty)
        await query.answer(
            f"✅ Purchased {item['name']}! Check /inventory.",
            show_alert=True
        )

    elif item_type == "lucky":
        # Spin the wheel
        prizes = pdb.LUCKY_WHEEL_PRIZES
        weights = [p["weight"] for p in prizes]
        prize  = random.choices(prizes, weights=weights, k=1)[0]
        _give_prize(user.id, prize)
        await query.answer(
            f"🎡 You won: {prize['label']}!",
            show_alert=True
        )

    elif item_type in ("chest", "chest_gold"):
        prize_pool = pdb.CHEST_PRIZES.get(item_type, pdb.CHEST_PRIZES["chest"])
        result_lines = []
        for p in prize_pool:
            amt = random.randint(p["min"], p["max"])
            _give_prize(user.id, {"type": p["type"], "amount": amt})
            result_lines.append(f"+{amt} {p['type'].title()}")
        await query.answer(
            f"📦 Chest opened!\n" + "\n".join(result_lines),
            show_alert=True
        )

    elif item_type == "boost":
        from datetime import timedelta
        boost_until = datetime.now(timezone.utc) + timedelta(hours=1)
        pdb.update_player(user.id, {"$set": {"xp_boost_until": boost_until}})
        await query.answer("⚡ 2× XP Boost activated for 1 hour!", show_alert=True)

    # Refresh shop message
    updated = pdb.get_player(user.id)
    try:
        await query.edit_message_text(
            f"🛍 <b>PAHELI SHOP</b>\n\n"
            f"💰 Balance: {updated.get('coins',0)} 🪙 | {updated.get('gems',0)} 💎\n\n"
            "Select an item:",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_shop_keyboard("main"),
        )
    except TelegramError:
        pass


def _give_prize(user_id: int, prize: dict):
    ptype  = prize.get("type")
    amount = prize.get("amount", 0)
    if ptype == "coins":
        pdb.update_player(user_id, {"$inc": {"coins": amount}})
    elif ptype == "xp":
        pdb.grant_xp_coins(user_id, amount, 0)
    elif ptype == "gems":
        pdb.update_player(user_id, {"$inc": {"gems": amount}})
    elif ptype == "hint":
        pdb.add_inventory(user_id, "hint", amount)
    elif ptype == "skip":
        pdb.add_inventory(user_id, "skip", amount)


# ─── /leaderboard ─────────────────────────────────────────────────────────────

async def cmd_paheli_lb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat   = update.effective_chat
    args   = context.args or []
    period = args[0].lower() if args else "all"
    if period not in ("day", "week", "month", "year", "all"):
        period = "all"

    scope    = "chat" if chat.type in ("group", "supergroup") else "global"
    group_id = chat.id if scope == "chat" else None

    await _send_paheli_lb(update.message, period, scope, group_id, chat)


async def _send_paheli_lb(target, period: str, scope: str,
                          group_id: int | None, chat, edit: bool = False):
    group_filter = group_id if scope == "chat" else None
    rows = pdb.get_paheli_leaderboard(period=period, group_id=group_filter, limit=15)

    period_labels = {
        "day": "Today", "week": "This Week",
        "month": "This Month", "year": "This Year", "all": "All Time"
    }
    period_emoji = {"day": "🟡", "week": "🟠", "month": "🔵", "year": "🟣", "all": "🏆"}

    scope_label = f"📍 {chat.title}" if scope == "chat" and hasattr(chat, "title") else "🌍 Global"
    p_label     = period_labels.get(period, period)

    if not rows:
        text = f"📊 No scores yet for <b>{scope_label} — {p_label}</b>.\nSolve riddles with /paheli!"
    else:
        lines = [f"🏆 <b>Paheli Leaderboard</b>\n{scope_label} — {p_label}\n"]
        for i, row in enumerate(rows, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"#{i}")
            name  = row.get("first_name", "Unknown")
            pts   = row.get("total_points", 0)
            solved = row.get("riddles_solved", 0)
            lvl   = row.get("level", 0)
            title = row.get("title", "Novice")
            lines.append(
                f"{medal} <b>{name}</b> [Lv.{lvl}] — {pts} pts · {solved} solved"
            )
        text = "\n".join(lines)

    kb_rows = []
    if hasattr(chat, "type") and chat.type in ("group", "supergroup"):
        kb_rows.append([
            _lb_btn("📍 Chat",   period, "chat",   scope),
            _lb_btn("🌍 Global", period, "global", scope),
        ])
    kb_rows.append([
        _lb_btn(f"{period_emoji['day']}Day",   "day",   scope, period),
        _lb_btn(f"{period_emoji['week']}Week",  "week",  scope, period),
        _lb_btn(f"{period_emoji['month']}Month","month", scope, period),
    ])
    kb_rows.append([
        _lb_btn(f"{period_emoji['year']}Year",    "year", scope, period),
        _lb_btn(f"{period_emoji['all']}All Time", "all",  scope, period),
    ])
    kb = InlineKeyboardMarkup(kb_rows)

    if edit:
        try:
            await target.edit_message_text(
                text, parse_mode=constants.ParseMode.HTML, reply_markup=kb
            )
        except TelegramError:
            pass
    else:
        await target.reply_text(
            text, parse_mode=constants.ParseMode.HTML, reply_markup=kb
        )


def _lb_btn(label: str, period: str, scope: str, current_period: str) -> InlineKeyboardButton:
    active = "✅ " if period == current_period else ""
    return InlineKeyboardButton(
        f"{active}{label}",
        callback_data=f"ph:lb:{period}:{scope}"
    )


async def cb_paheli_lb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    parts  = query.data.split(":")
    period = parts[2] if len(parts) > 2 else "all"
    scope  = parts[3] if len(parts) > 3 else "global"
    chat   = query.message.chat

    group_id = chat.id if scope == "chat" else None
    await query.answer()
    await _send_paheli_lb(query, period, scope, group_id, chat, edit=True)


# ─── /stats ───────────────────────────────────────────────────────────────────

async def cmd_paheli_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    rows   = pdb.get_paheli_leaderboard(period="all", limit=1000)
    rank   = next((i for i, r in enumerate(rows, 1) if r["user_id"] == user.id), None)
    name   = _display_name(user)

    global_stats = pdb.get_global_paheli_stats()

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{name}'s Paheli Stats</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🌍 <b>Global Rank:</b> #{rank or 'Unranked'}\n"
        f"⚡ <b>Level:</b> {player.get('level', 0)} — {player.get('title', 'Novice')}\n"
        f"🔮 <b>XP:</b> {player.get('xp', 0)}\n"
        f"🪙 <b>Coins:</b> {player.get('coins', 0)}\n"
        f"💎 <b>Gems:</b> {player.get('gems', 0)}\n"
        f"🧩 <b>Riddles Solved:</b> {player.get('riddles_solved', 0)}\n"
        f"🔥 <b>Daily Streak:</b> {player.get('daily_streak', 0)}\n\n"
        "━ <b>Global Paheli Stats</b> ━\n"
        f"👥 Total Players: <b>{global_stats['total_players']}</b>\n"
        f"🎮 Total Games: <b>{global_stats['total_sessions']}</b>\n"
        f"✅ Solved: <b>{global_stats['total_solved']}</b>\n"
        f"⏭ Skipped: <b>{global_stats['total_skipped']}</b>",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /settings ────────────────────────────────────────────────────────────────

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    s      = player.get("settings", {})

    lang_label = {"both": "🌐 Both", "en": "🇬🇧 English", "hi": "🇮🇳 Hindi"}.get(
        s.get("language", "both"), "🌐 Both"
    )
    diff_label = {
        "all": "🎲 All",
        "easy": "🟢 Easy",
        "medium": "🟡 Medium",
        "hard": "🔴 Hard",
        "legendary": "💀 Legendary",
    }.get(s.get("difficulty", "all"), "🎲 All")

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>Paheli Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🌐 <b>Language:</b> {lang_label}\n"
        f"🎯 <b>Difficulty:</b> {diff_label}\n\n"
        "Tap a button to change:",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_settings_keyboard(s),
    )


def _settings_keyboard(s: dict) -> InlineKeyboardMarkup:
    lang = s.get("language", "both")
    diff = s.get("difficulty", "all")

    def _lb(label, val, current, key):
        mark = " ✅" if val == current else ""
        return InlineKeyboardButton(f"{label}{mark}", callback_data=f"ph:set:{key}:{val}")

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Language", callback_data="ph:noop")],
        [
            _lb("🌐 Both",     "both", lang, "language"),
            _lb("🇬🇧 English", "en",   lang, "language"),
            _lb("🇮🇳 Hindi",   "hi",   lang, "language"),
        ],
        [InlineKeyboardButton("🎯 Difficulty", callback_data="ph:noop")],
        [
            _lb("🎲 All",    "all",    diff, "difficulty"),
            _lb("🟢 Easy",   "easy",   diff, "difficulty"),
            _lb("🟡 Medium", "medium", diff, "difficulty"),
        ],
        [
            _lb("🔴 Hard", "hard", diff, "difficulty"),
            _lb("💀 Legendary", "legendary", diff, "difficulty"),
        ],
    ])


async def cb_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    parts  = query.data.split(":")

    if parts[1] == "noop":
        await query.answer()
        return

    key   = parts[2]  # language / difficulty
    value = parts[3]
    user  = query.from_user

    pdb.update_player(user.id, {"$set": {f"settings.{key}": value}})
    await query.answer(f"✅ {key.title()} set to {value}!")

    player = pdb.get_player(user.id)
    s = player.get("settings", {})
    try:
        await query.edit_message_reply_markup(reply_markup=_settings_keyboard(s))
    except TelegramError:
        pass


# ─── /challenge (PvP) ─────────────────────────────────────────────────────────

async def cmd_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    chat  = update.effective_chat
    msg   = update.message

    if chat.type == "private":
        await msg.reply_text("⚠️ Challenges can only be started in groups!")
        return

    if not msg.reply_to_message:
        await msg.reply_text(
            "Usage: Reply to a user's message and use /challenge\n"
            "Example: Reply to @someone → /challenge"
        )
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await msg.reply_text("❌ You can't challenge yourself!")
        return
    if target.is_bot:
        await msg.reply_text("❌ You can't challenge a bot!")
        return

    riddle = _pick_riddle(chat.id)
    if not riddle:
        await msg.reply_text("❌ No riddles available for a challenge!")
        return

    challenge_id = str(uuid.uuid4())[:8]
    pdb.ensure_player(user.id,   user.first_name or "",   user.username or "")
    pdb.ensure_player(target.id, target.first_name or "", target.username or "")
    pdb.create_challenge(challenge_id, user.id, target.id, chat.id, riddle)

    challenger_name = _display_name(user)
    target_name     = _display_name(target)

    await msg.reply_text(
        f"⚔️ <b>PvP CHALLENGE!</b>\n\n"
        f"⚔️ <b>{challenger_name}</b> challenged <b>{target_name}</b>!\n\n"
        f"🧩 <b>Riddle:</b>\n{riddle['question']}\n\n"
        f"⏱ First to answer wins the challenge!\n"
        f"Expires in <b>10 minutes</b>.\n\n"
        f"Challenge ID: <code>{challenge_id}</code>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"✅ Accept Challenge",
                callback_data=f"ph:accept_challenge:{challenge_id}"
            ),
        ]]),
    )


async def cb_accept_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query        = update.callback_query
    challenge_id = query.data.split(":", 2)[2]
    user         = query.from_user

    ch = pdb.get_challenge(challenge_id)
    if not ch:
        await query.answer("Challenge not found or expired.", show_alert=True)
        return

    if user.id not in (ch["challenger_id"], ch["challenged_id"]):
        await query.answer("❌ This challenge is not for you!", show_alert=True)
        return

    if ch["status"] != "pending":
        await query.answer("This challenge has already started or ended.", show_alert=True)
        return

    pdb.accept_challenge(challenge_id)
    await query.answer("Challenge accepted! Type your answer first to win!")

    riddle = ch["riddle"]
    await query.message.reply_text(
        f"⚔️ <b>CHALLENGE IS ON!</b>\n\n"
        f"🧩 <b>Riddle:</b>\n{riddle['question']}\n\n"
        f"⏱ First to type the correct answer wins!\n"
        f"💡 Use /hint for help (reduces reward)",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /clan ────────────────────────────────────────────────────────────────────

async def cmd_clan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    args  = context.args or []

    if not args:
        player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
        clan_id = player.get("clan_id")

        if clan_id:
            clan = pdb.get_clan(clan_id)
            if clan:
                members_count = len(clan.get("members", []))
                level = clan.get("level", 1)
                xp = clan.get("xp", 0)
                max_members = clan.get("max_members", 10)

                next_level = pdb.CLAN_LEVELS.get(level + 1)

                if next_level:
                    prev = pdb.CLAN_LEVELS[level]
                    progress = xp - prev
                    needed = next_level - prev
                    filled = int((progress / needed) * 10)
                    bar = "█" * filled + "░" * (10 - filled)
                    xp_bar = f"{bar} {xp}/{next_level}"
                else:
                    xp_bar = "MAX LEVEL"

                await update.message.reply_text(
                    f"🏰 <b>Your Clan: {clan['clan_name']}</b> [{clan['clan_tag']}]\n\n"
                    f"⭐ <b>Level:</b> {level}\n"
                    f"👥 <b>Members:</b> {members_count}/{max_members}\n"
                    f"📊 <b>Clan XP:</b>\n{xp_bar}\n"
                    f"🏆 <b>Total XP:</b> {xp}\n\n"
                    "Commands:\n"
                    "/clan leave\n"
                    "/clan info TAG\n"
                    "/clan top",
                    parse_mode=constants.ParseMode.HTML,
                )
                return

        await update.message.reply_text(
            "🏰 <b>Clan System</b>\n\n"
            "You're not in a clan!\n\n"
            "Commands:\n"
            "/clan create TAG NAME — Create a clan (TAG: 3-5 letters)\n"
            "/clan join TAG — Join a clan\n"
            "/clan info TAG — View clan info\n"
            "/clan top — Top clans",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    sub = args[0].lower()

    if sub == "create" and len(args) >= 3:
        clan_tag  = args[1].upper()
        clan_name = " ".join(args[2:])
        if not (3 <= len(clan_tag) <= 5 and clan_tag.isalpha()):
            await update.message.reply_text("❌ Clan tag must be 3-5 letters only.")
            return
        pdb.ensure_player(user.id, user.first_name or "", user.username or "")
        success = pdb.create_clan(clan_tag, clan_name, user.id)
        if success:
            await update.message.reply_text(
                f"🏰 Clan <b>{clan_name}</b> [{clan_tag}] created successfully!"
                f"\nYou are the clan owner!",
                parse_mode=constants.ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                f"❌ Clan tag [{clan_tag}] is already taken. Choose a different tag."
            )

    elif sub == "join" and len(args) >= 2:
        tag = args[1].upper()
        pdb.ensure_player(user.id, user.first_name or "", user.username or "")
        player = pdb.get_player(user.id)
        if player.get("clan_id"):
            await update.message.reply_text("❌ You're already in a clan! /clan leave first.")
            return
        success = pdb.join_clan(user.id, tag)
        if success:
            await update.message.reply_text(f"✅ You joined clan [{tag}]!")
        else:
            await update.message.reply_text(f"❌ Clan [{tag}] not found or the clan is full.")

    elif sub == "leave":
        success = pdb.leave_clan(user.id)
        if success:
            await update.message.reply_text("👋 You left your clan.")
        else:
            await update.message.reply_text("❌ You're not in a clan.")

    elif sub == "top":
        clans = pdb.get_clan_leaderboard(10)
        if not clans:
            await update.message.reply_text("No clans yet! Create one with /clan create TAG NAME")
            return
        lines = ["🏆 <b>Top Clans</b>\n"]
        for i, c in enumerate(clans, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"#{i}")
            lines.append(
                f"{medal} <b>{c['clan_name']}</b> [{c['clan_tag']}] "
                f"— Lv.{c.get('level',1)} • "
                f"{len(c.get('members',[]))}/{c.get('max_members',10)} members • "
                f"{c.get('xp',0)} XP"
            )
        await update.message.reply_text(
            "\n".join(lines), parse_mode=constants.ParseMode.HTML
        )

    elif sub == "info" and len(args) >= 2:
        tag  = args[1].upper()
        clan = pdb.get_clan(tag)
        if not clan:
            await update.message.reply_text(f"❌ Clan [{tag}] not found.")
            return
        level = clan.get("level", 1)
        xp = clan.get("xp", 0)
        members = len(clan.get("members", []))
        max_members = clan.get("max_members", 10)

        await update.message.reply_text(
            f"🏰 <b>{clan['clan_name']}</b> [{clan['clan_tag']}]\n\n"
            f"⭐ Level: {level}\n"
            f"👥 Members: {members}/{max_members}\n"
            f"🏆 XP: {xp}\n"
            f"📅 Created: {clan['created_at'].strftime('%Y-%m-%d') if clan.get('created_at') else 'N/A'}",
            parse_mode=constants.ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            "Usage:\n"
            "/clan — Your clan info\n"
            "/clan create TAG NAME\n"
            "/clan join TAG\n"
            "/clan leave\n"
            "/clan top\n"
            "/clan info TAG"
        )


# ─── Admin commands ───────────────────────────────────────────────────────────

async def cmd_addriddle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_sudo(user.id):
        await update.message.reply_text("❌ Admin only.")
        return

    text = update.message.text
    # Format: /addriddle question | answer | hint1 | hint2 | hint3 | category | difficulty | language
    parts = text.split("\n", 1)
    if len(parts) < 2:
        await update.message.reply_text(
            "Usage (one field per line):\n"
            "/addriddle\n"
            "Question: ...\n"
            "Answer: ...\n"
            "Hints: hint1, hint2, hint3\n"
            "Category: general/movies/sports/science/math/tech/history\n"
            "Difficulty: easy/medium/hard/legendary\n"
            "Language: en/hi"
        )
        return

    lines = dict()
    for line in parts[1].split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            lines[k.strip().lower()] = v.strip()

    try:
        question   = lines["question"]
        answer     = lines["answer"]
        hints      = [h.strip() for h in lines.get("hints", "").split(",") if h.strip()]
        category   = lines.get("category", "general")
        difficulty = lines.get("difficulty", "easy")
        language   = lines.get("language", "en")

        # Get a unique ID
        max_id = max((r.get("id", 0) for r in _ALL_RIDDLES), default=0)
        new_id = max_id + 1

        pts = {"easy": 10, "medium": 25, "hard": 50, "legendary": 100}.get(difficulty, 10)

        riddle = {
            "id": new_id, "question": question, "answer": answer.lower(),
            "hints": hints, "category": category, "difficulty": difficulty,
            "language": language, "points": pts, "custom": True,
        }
        pdb.add_custom_riddle(riddle)
        _ALL_RIDDLES.append(riddle)

        await update.message.reply_text(
            f"✅ Riddle #{new_id} added!\n"
            f"Q: {question}\nA: {answer}\nDifficulty: {difficulty}"
        )
    except KeyError as e:
        await update.message.reply_text(f"❌ Missing field: {e}")


async def cmd_deleteriddle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_sudo(user.id):
        await update.message.reply_text("❌ Admin only.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /deleteriddle <riddle_id>")
        return

    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Riddle ID must be a number.")
        return

    success = pdb.delete_custom_riddle(rid)
    if success:
        global _ALL_RIDDLES
        _ALL_RIDDLES = [r for r in _ALL_RIDDLES if r["id"] != rid]
        await update.message.reply_text(f"✅ Riddle #{rid} deleted.")
    else:
        await update.message.reply_text(f"❌ Riddle #{rid} not found (only custom riddles can be deleted).")


async def cmd_pban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_sudo(user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /pban <user_id> [reason]")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason given"
    pdb.ban_user(target_id, reason=reason, banned_by=user.id)
    await update.message.reply_text(f"🚫 User <code>{target_id}</code> banned from Paheli.\nReason: {reason}",
                                    parse_mode=constants.ParseMode.HTML)


async def cmd_punban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_sudo(user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /punban <user_id>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    pdb.unban_user(target_id)
    await update.message.reply_text(f"✅ User <code>{target_id}</code> unbanned from Paheli.",
                                    parse_mode=constants.ParseMode.HTML)


async def cmd_ridstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_sudo(user.id):
        return

    stats = pdb.get_global_paheli_stats()
    custom_count = len([r for r in _ALL_RIDDLES if r.get("custom")])

    await update.message.reply_text(
        "📊 <b>Paheli Admin Stats</b>\n\n"
        f"🧩 Total Riddles: <b>{len(_ALL_RIDDLES)}</b>\n"
        f"✍️ Custom Riddles: <b>{custom_count}</b>\n"
        f"👥 Players: <b>{stats['total_players']}</b>\n"
        f"🎮 Sessions: <b>{stats['total_sessions']}</b>\n"
        f"✅ Solved: <b>{stats['total_solved']}</b>\n"
        f"⏭ Skipped: <b>{stats['total_skipped']}</b>\n"
        f"🏰 Clans: <b>{stats['total_clans']}</b>",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /paheli_help ─────────────────────────────────────────────────────────────

async def cmd_paheli_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "🧩 <b>Paheli — Riddle Game</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Gameplay:</b>\n"
        "/paheli — Start a new riddle\n"
        "/answer TEXT — Answer the riddle\n"
        "/hint — Get a hint (uses token after 1st)\n"
        "/skip — Skip riddle (uses Skip Token)\n\n"
        "<b>Rewards:</b>\n"
        "/daily — Claim daily reward\n"
        "/weekly — Claim weekly reward\n\n"
        "<b>Social:</b>\n"
        "/challenge — PvP riddle challenge (reply to user)\n"
        "/clan — Clan system\n\n"
        "<b>Profile:</b>\n"
        "/profile — Your profile & level\n"
        "/inventory — Your items\n"
        "/shop — Buy hints, skips & more\n"
        "/stats — Your paheli stats\n"
        "/settings — Language & difficulty\n"
        "/leaderboard — Top players\n\n"
        "<b>Points:</b>\n"
        "🟢 Easy: 10 pts | 🟡 Medium: 25 pts\n"
        "🔴 Hard: 50 pts | 💀 Legendary: 100 pts\n"
        "No-hint bonus: +20% points!\n",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── Callback router ──────────────────────────────────────────────────────────

async def cb_paheli_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    data   = query.data

    if data.startswith("ph:hint:"):
        await cb_paheli_hint(update, context)
    elif data.startswith("ph:skip:"):
        await cb_paheli_skip(update, context)
    elif data.startswith("ph:next:"):
        await cb_paheli_next(update, context)
    elif data.startswith("ph:lb:"):
        await cb_paheli_lb(update, context)
    elif data.startswith("ph:shop:") or data.startswith("ph:buy:"):
        await cb_shop(update, context)
    elif data.startswith("ph:set:"):
        await cb_settings(update, context)
    elif data.startswith("ph:noop"):
        await query.answer()
    elif data.startswith("ph:accept_challenge:"):
        await cb_accept_challenge(update, context)
    elif data.startswith("ph:inv:"):
        await query.answer()
        await cmd_inventory(update, context)
    else:
        await query.answer()


# ─── Registration ─────────────────────────────────────────────────────────────

def register_paheli_handlers(app: Application):
    """Call this from bot.py main() to register all paheli handlers."""
    _load_riddles()
    pdb.init_paheli_db()

    # Game selector (shared command)
    app.add_handler(CommandHandler("game", cmd_game))
    app.add_handler(CallbackQueryHandler(cb_game_selector, pattern=r"^game:"))

    # Paheli commands
    app.add_handler(CommandHandler("paheli",         cmd_paheli))
    app.add_handler(CommandHandler("answer",         cmd_answer))
    app.add_handler(CommandHandler("hint",           cmd_paheli_hint))
    app.add_handler(CommandHandler("skip",           cmd_paheli_skip))
    app.add_handler(CommandHandler("daily",          cmd_daily))
    app.add_handler(CommandHandler("weekly",         cmd_weekly))
    app.add_handler(CommandHandler(["pleaderboard", "plb"],  cmd_paheli_lb))
    app.add_handler(CommandHandler("profile",        cmd_profile))
    app.add_handler(CommandHandler("inventory",      cmd_inventory))
    app.add_handler(CommandHandler("shop",           cmd_shop))
    app.add_handler(CommandHandler("paheli_stats",   cmd_paheli_stats))
    app.add_handler(CommandHandler("settings",       cmd_settings))
    app.add_handler(CommandHandler("challenge",      cmd_challenge))
    app.add_handler(CommandHandler("clan",           cmd_clan))
    app.add_handler(CommandHandler("paheli_help",    cmd_paheli_help))

    # Admin commands
    app.add_handler(CommandHandler("addriddle",      cmd_addriddle))
    app.add_handler(CommandHandler("deleteriddle",   cmd_deleteriddle))
    app.add_handler(CommandHandler("pban",           cmd_pban))
    app.add_handler(CommandHandler("punban",         cmd_punban))
    app.add_handler(CommandHandler("ridstats",       cmd_ridstats))

    # Callback router for all ph: callbacks
    app.add_handler(CallbackQueryHandler(cb_paheli_router, pattern=r"^ph:"))

    # Message handler — answer detection (runs AFTER wordgrid handler, lower priority)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        paheli_answer_handler,
    ), group=1)  # group=1 so it runs AFTER the main message handler in group 0

    logger.info("✅ Paheli handlers registered")
