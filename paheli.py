"""
paheli.py — Complete Paheli (Riddle) game module — MCQ Edition.
Riddles are now shown with 8 multiple-choice buttons (1 correct, 7 wrong).
Difficulty rotates automatically: General → Medium → Hard → Legendary → repeat.
Plug-and-play: call register_paheli_handlers(app) in your main bot.py.

Commands:
  /paheli   /hint   /skip   /daily   /weekly
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

RIDDLE_TIMEOUT_SECONDS  = 150   # 2.5 minutes per riddle in group
PAHELI_COOLDOWN_SECONDS = 10    # cooldown between /paheli uses
HINT_COOLDOWN_SECONDS   = 15    # cooldown between /hint uses
MAX_HINTS_PER_SESSION   = 2     # max hints per riddle

# Difficulty rotation order
DIFFICULTY_ROTATION = ["easy", "medium", "hard", "legendary"]

POINTS_BY_DIFFICULTY = {
    "easy":      {"base": 10,  "no_hint": 13,  "xp": 10,  "coins": 5},
    "medium":    {"base": 25,  "no_hint": 32,  "xp": 25,  "coins": 15},
    "hard":      {"base": 50,  "no_hint": 65,  "xp": 50,  "coins": 30},
    "legendary": {"base": 100, "no_hint": 130, "xp": 100, "coins": 60},
}

DIFFICULTY_EMOJI = {
    "easy":      "🟢",
    "medium":    "🟡",
    "hard":      "🔴",
    "legendary": "💀",
}

DIFFICULTY_LABEL = {
    "easy":      "Saral (आसान)",
    "medium":    "Madhyam (मध्यम)",
    "hard":      "Kathin (कठिन)",
    "legendary": "Ati Kathin (अति कठिन)",
}

# Option labels A-H
OPTION_LABELS = ["🅰", "🅱", "🅲", "🅳", "🅴", "🅵", "🅶", "🅷"]


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


def _get_next_difficulty(group_id: int) -> str:
    """Return the next difficulty in rotation based on how many riddles this group has done."""
    count = pdb.get_group_session_count(group_id)
    return DIFFICULTY_ROTATION[count % 4]


def _pick_riddle(group_id: int, difficulty: str = "auto") -> dict | None:
    if not _ALL_RIDDLES:
        _load_riddles()

    if difficulty == "auto":
        difficulty = _get_next_difficulty(group_id)

    used_ids = pdb.get_used_riddle_ids(group_id, limit=200)
    pool = [
        r for r in _ALL_RIDDLES
        if r["id"] not in used_ids
        and r.get("difficulty") == difficulty
    ]

    if not pool:
        # Reset if all riddles of this difficulty are used
        pool = [r for r in _ALL_RIDDLES if r.get("difficulty") == difficulty]

    return random.choice(pool) if pool else None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _display_name(user) -> str:
    name = (user.first_name or "").strip()
    if user.last_name:
        name = (name + " " + user.last_name).strip()
    return name or f"User{user.id}"


def _is_sudo(user_id: int) -> bool:
    return user_id in SUDO_USERS


def _make_riddle_text(riddle: dict, hint_count: int = 0) -> str:
    d_emoji = DIFFICULTY_EMOJI.get(riddle.get("difficulty", "easy"), "🟡")
    d_label = DIFFICULTY_LABEL.get(riddle.get("difficulty", "easy"), "Saral")
    pts     = riddle.get("points", 10)

    lines = [
        "━━━━━━━━━━━━━━━━━━",
        "🧩 <b>DESI PAHELI — CHALLENGE!</b>",
        "━━━━━━━━━━━━━━━━━━\n",
        f"<b>{riddle['question']}</b>\n",
        f"{d_emoji} <b>Level:</b> {d_label}",
        f"🏆 <b>Points:</b> {pts}  ⏱ <b>2.5 min</b>",
        "",
        "👇 <b>Sahi jawab button dabao!</b>",
    ]

    if hint_count > 0:
        hints  = riddle.get("hints", [])
        shown  = hints[:hint_count]
        hlines = "\n".join(f"  • {h}" for h in shown)
        lines.append(f"\n💡 <b>Hints ({hint_count}):</b>\n{hlines}")

    lines.append("━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _options_keyboard(session_id: str, riddle: dict, hints_used: int, disabled: bool = False) -> InlineKeyboardMarkup:
    """Build keyboard with 8 MCQ option buttons (2 columns × 4 rows) + hint/skip row."""
    options = riddle.get("options", [])
    rows    = []

    # 2 options per row → 4 rows for 8 options
    for i in range(0, min(len(options), 8), 2):
        row = []
        label_a = OPTION_LABELS[i]
        label_b = OPTION_LABELS[i + 1] if i + 1 < len(options) else None

        opt_a = options[i]
        row.append(InlineKeyboardButton(
            f"{label_a} {opt_a}",
            callback_data=f"ph:opt:{session_id}:{i}" if not disabled else "ph:noop"
        ))

        if label_b and i + 1 < len(options):
            opt_b = options[i + 1]
            row.append(InlineKeyboardButton(
                f"{label_b} {opt_b}",
                callback_data=f"ph:opt:{session_id}:{i+1}" if not disabled else "ph:noop"
            ))
        rows.append(row)

    if not disabled:
        rows.append([
            InlineKeyboardButton(
                f"💡 Hint ({hints_used}/{MAX_HINTS_PER_SESSION})",
                callback_data=f"ph:hint:{session_id}"
            ),
            InlineKeyboardButton("⏭ Skip", callback_data=f"ph:skip:{session_id}"),
        ])
        rows.append([
            InlineKeyboardButton("📊 Leaderboard", callback_data="ph:lb:all:global"),
        ])

    return InlineKeyboardMarkup(rows)


# ─── /game — Game selector ─────────────────────────────────────────────────────

async def cmd_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the game picker with WordGrid and Paheli buttons."""
    chat = update.effective_chat

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔤 Word Grid",
                                 callback_data=f"game:wordgrid:{chat.id}"),
            InlineKeyboardButton("🧩 Paheli (Paheliyan)",
                                 callback_data=f"game:paheli:{chat.id}"),
        ]
    ])

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "🎮 <b>VelocityBots Game Center</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Apna game chunlo aur maza karo!\n\n"
        "🔤 <b>Word Grid</b> — Letter grid mein chhupe shabd dhoondhon\n"
        "🧩 <b>Paheli</b> — Desi Hinglish paheliyan bujho!\n",
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
        if chat.type == "private":
            await query.answer("⚠️ Word Grid sirf groups mein khelo!", show_alert=True)
            return
        await query.message.reply_text(
            "🔤 Word Grid shuru karo! /new (easy) ya /new_hard (hard) use karo."
        )

    elif game_type == "paheli":
        await _start_paheli_session(update, context, chat_id=chat_id,
                                    user=user, from_callback=True,
                                    reply_to=query.message)


# ─── /paheli ──────────────────────────────────────────────────────────────────

async def cmd_paheli(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if pdb.is_banned(user.id):
        await update.message.reply_text("🚫 Aap Paheli se ban hain. Admin se contact karo.")
        return

    cd = pdb.check_cooldown(user.id, "paheli", PAHELI_COOLDOWN_SECONDS)
    if cd > 0:
        await update.message.reply_text(
            f"⏳ <b>{cd}s</b> baad try karo.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    await _start_paheli_session(update, context, chat_id=chat.id,
                                user=user, from_callback=False)


async def _start_paheli_session(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 chat_id: int, user, from_callback: bool = False,
                                 reply_to=None):
    """Core: pick a riddle, create session, send MCQ message."""
    msg       = reply_to or (update.message if hasattr(update, "message") else None)
    chat_type = (msg.chat.type if msg else "group") if hasattr(msg, "chat") else "group"

    if chat_type == "private":
        text = "⚠️ Paheli groups mein khelo! Kisi group mein /paheli type karo."
        if msg:
            await msg.reply_text(text)
        return

    # Check if already active
    existing = pdb.get_active_paheli(chat_id)
    if existing:
        session_id = existing["session_id"]
        riddle     = existing["riddle"]
        hints_used = existing.get("hints_used", 0)
        text = "⚠️ Ek paheli pehle se chal rahi hai!\n\n" + _make_riddle_text(riddle, hints_used)
        if msg:
            await msg.reply_text(
                text,
                parse_mode=constants.ParseMode.HTML,
                reply_markup=_options_keyboard(session_id, riddle, hints_used),
            )
        return

    pdb.ensure_player(user.id, user.first_name or "", user.username or "")

    riddle = _pick_riddle(chat_id)
    if not riddle:
        if msg:
            await msg.reply_text("❌ Abhi koi paheli nahi hai. Baad mein try karo!")
        return

    # Shuffle options before showing
    options = riddle.get("options", [])
    random.shuffle(options)
    riddle = {**riddle, "options": options}

    session_id = str(uuid.uuid4())
    pdb.create_paheli_session(session_id, riddle, chat_id, user.id)
    pdb.set_cooldown(user.id, "paheli", PAHELI_COOLDOWN_SECONDS)

    text = _make_riddle_text(riddle, hint_count=0)

    sent = None
    if msg:
        sent = await msg.reply_text(
            text,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_options_keyboard(session_id, riddle, 0),
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

    # Find correct answer
    correct_ans = riddle.get("answer", "?")
    options     = riddle.get("options", [])
    answer_line = ""
    for i, opt in enumerate(options):
        if opt == correct_ans:
            answer_line = f"{OPTION_LABELS[i]} <b>{correct_ans}</b>"
            break
    if not answer_line:
        answer_line = f"<b>{correct_ans}</b>"

    try:
        await context.bot.send_message(
            group_id,
            f"⏰ <b>Time's Up!</b>\n\n"
            f"❌ Kisi ne sahi jawab nahi diya.\n\n"
            f"🔑 <b>Sahi Jawab:</b> {answer_line}\n\n"
            f"Agli paheli ke liye /paheli likhein! 🎯",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎯 Agli Paheli", callback_data=f"ph:next:{group_id}"),
            ]]),
        )
    except TelegramError as e:
        logger.warning("Paheli timeout send error: %s", e)


def _cancel_timeout(context, session_id: str):
    for job in context.job_queue.get_jobs_by_name(f"ph_timeout_{session_id}"):
        job.schedule_removal()


# ─── MCQ Option callback ───────────────────────────────────────────────────────

async def cb_paheli_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles when a user taps one of the 8 MCQ option buttons."""
    query      = update.callback_query
    parts      = query.data.split(":")  # ph:opt:{session_id}:{index}
    session_id = parts[2]
    opt_index  = int(parts[3])
    user       = query.from_user

    session = pdb.get_active_paheli_by_session(session_id)
    if not session or not session.get("active"):
        await query.answer("⏰ Yeh paheli pehle se khatam ho gayi.", show_alert=True)
        return

    riddle  = session["riddle"]
    options = riddle.get("options", [])

    if opt_index >= len(options):
        await query.answer("❌ Invalid option.", show_alert=True)
        return

    chosen    = options[opt_index]
    correct   = riddle.get("answer", "")
    is_right  = (chosen.strip().lower() == correct.strip().lower())

    if not is_right:

        attempts, maximum = pdb.add_wrong_attempt(session_id)

        remaining = maximum - attempts

        if remaining > 0:
            await query.answer(
                f"❌ Galat!\n\n{remaining} chance left.",
                show_alert=True
            )
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎯 Agli Paheli", callback_data="paheli_next"),
                InlineKeyboardButton("📊 Leaderboard", callback_data="paheli_leaderboard"),
            ]
        ])

        await query.message.reply_text(
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🧩 <b>DESI PAHELI — CHALLENGE!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "❌ <b>2 Wrong Attempts Completed!</b>\n\n"
            f"✅ <b>Correct Answer:</b> <code>{correct}</code>\n\n"
            "👇 Choose an option below.",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=keyboard,
        )

        return

    # ── Correct answer ──────────────────────────────────────────────────────
    if not pdb.solve_paheli(session_id, user.id):
        # Race condition — someone else already solved it
        await query.answer("Paheli pehle hi solve ho gayi! 🎉", show_alert=True)
        return

    _cancel_timeout(context, session_id)

    hints_used = session.get("hints_used", 0)
    difficulty = riddle.get("difficulty", "easy")
    pts_data   = POINTS_BY_DIFFICULTY.get(difficulty, POINTS_BY_DIFFICULTY["easy"])
    points     = pts_data["no_hint"] if hints_used == 0 else pts_data["base"]

    pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    pdb.record_paheli_score(user.id, query.message.chat.id, session_id,
                            riddle["id"], points, difficulty)
    reward = pdb.grant_xp_coins(user.id, pts_data["xp"], pts_data["coins"])

    player   = pdb.get_player(user.id)
    name     = _display_name(user)
    d_emoji  = DIFFICULTY_EMOJI.get(difficulty, "🟡")
    hint_str = " (+No-hint bonus!)" if hints_used == 0 else ""

    # Find which label was correct
    correct_label = ""
    for i, opt in enumerate(options):
        if opt.strip().lower() == correct.strip().lower():
            correct_label = OPTION_LABELS[i]
            break

    lines = [
        "━━━━━━━━━━━━━━━━━━",
        f"🎉 <b>SAHI JAWAB!</b>",
        "━━━━━━━━━━━━━━━━━━\n",
        f"🏆 <b>{name}</b> ne paheli bujhi! 🎊\n",
        f"🔑 <b>Jawab:</b> {correct_label} <b>{correct}</b>",
        f"{d_emoji} <b>Level:</b> {difficulty.title()}",
        f"⭐ <b>Points:</b> +{points}{hint_str}",
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
            f"\n📊 <b>Aapke Stats:</b> Lv.{player.get('level', 0)} | "
            f"{player.get('xp', 0)} XP | {player.get('coins', 0)} 🪙"
        )

    lines.append("\n━━━━━━━━━━━━━━━━━━")

    await query.answer("✅ SAHI JAWAB! Shabaash!")

    # Edit original message to disable buttons
    try:
        await query.edit_message_reply_markup(
            reply_markup=_options_keyboard(session_id, riddle, hints_used, disabled=True)
        )
    except TelegramError:
        pass

    await query.message.reply_text(
        "\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎯 Agli Paheli", callback_data=f"ph:next:{query.message.chat.id}"),
            InlineKeyboardButton("📊 Leaderboard", callback_data="ph:lb:all:global"),
        ]]),
    )


# ─── /hint via button ─────────────────────────────────────────────────────────

async def cb_paheli_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline hint button callback."""
    query      = update.callback_query
    session_id = query.data.split(":", 2)[2]
    user       = query.from_user

    await query.answer()

    session = pdb.get_active_paheli_by_session(session_id)
    if not session or not session.get("active"):
        await query.answer("⏰ Yeh paheli pehle se khatam ho gayi.", show_alert=True)
        return

    riddle     = session["riddle"]
    hints      = riddle.get("hints", [])
    hints_used = session.get("hints_used", 0)

    if hints_used >= MAX_HINTS_PER_SESSION or hints_used >= len(hints):
        await query.answer(
            f"Aur hints nahi hain! ({hints_used}/{MAX_HINTS_PER_SESSION} use ho gaye)",
            show_alert=True
        )
        return

    cd = pdb.check_cooldown(user.id, "ph_hint", HINT_COOLDOWN_SECONDS)
    if cd > 0:
        await query.answer(f"⏳ {cd}s baad hint lo.", show_alert=True)
        return

    player     = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    inventory  = player.get("inventory", {})
    hint_tokens = inventory.get("hints", 0)

    if hints_used >= 1 and hint_tokens <= 0:
        await query.answer(
            "❌ Hint Token chahiye! /shop se 50 coins mein kharido.",
            show_alert=True
        )
        return

    if hints_used >= 1:
        pdb.use_inventory_item(user.id, "hint")

    new_count = pdb.increment_hints(session_id)
    pdb.set_cooldown(user.id, "ph_hint", HINT_COOLDOWN_SECONDS)

    hint_text = hints[hints_used] if hints_used < len(hints) else "Aur hints nahi hain!"
    name      = _display_name(user)

    # Update riddle message with new hint count
    try:
        await query.edit_message_text(
            _make_riddle_text(riddle, hint_count=new_count),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_options_keyboard(session_id, riddle, new_count),
        )
    except TelegramError:
        pass

    await query.message.reply_text(
        f"💡 <b>Hint #{new_count}</b> ({name} ne manga):\n\n"
        f"<i>{hint_text}</i>\n\n"
        f"⚠️ Hint lene se points kam hote hain!",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /hint command ────────────────────────────────────────────────────────────

async def cmd_paheli_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if pdb.is_banned(user.id):
        return

    cd = pdb.check_cooldown(user.id, "ph_hint", HINT_COOLDOWN_SECONDS)
    if cd > 0:
        await update.message.reply_text(
            f"⏳ <b>{cd}s</b> baad hint lena.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    session = pdb.get_active_paheli(chat.id)
    if not session:
        await update.message.reply_text("❌ Koi paheli active nahi. /paheli se shuru karo!")
        return

    riddle     = session["riddle"]
    hints      = riddle.get("hints", [])
    hints_used = session.get("hints_used", 0)

    if hints_used >= MAX_HINTS_PER_SESSION or hints_used >= len(hints):
        await update.message.reply_text(
            f"❌ Aur hints nahi hain! ({hints_used}/{MAX_HINTS_PER_SESSION} use ho gaye)"
        )
        return

    player     = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    inventory  = player.get("inventory", {})
    hint_tokens = inventory.get("hints", 0)

    if hints_used >= 1 and hint_tokens <= 0:
        await update.message.reply_text(
            "❌ Hint Token chahiye!\n"
            "/shop se 50 coins mein kharido.",
        )
        return

    if hints_used >= 1:
        pdb.use_inventory_item(user.id, "hint")

    new_count = pdb.increment_hints(session["session_id"])
    pdb.set_cooldown(user.id, "ph_hint", HINT_COOLDOWN_SECONDS)

    hint_text = hints[hints_used] if hints_used < len(hints) else "Aur hints nahi hain!"
    name      = _display_name(user)

    await update.message.reply_text(
        f"💡 <b>Hint #{new_count}</b> ({name} ne manga):\n\n"
        f"<i>{hint_text}</i>\n\n"
        f"💡 Hints used: <b>{new_count}/{MAX_HINTS_PER_SESSION}</b>\n"
        f"⚠️ Hint lene se points kam hote hain!",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /skip ─────────────────────────────────────────────────────────────────────

async def cmd_paheli_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    session = pdb.get_active_paheli(chat.id)
    if not session:
        await update.message.reply_text("❌ Koi paheli active nahi!")
        return

    player    = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    inventory = player.get("inventory", {})
    skips     = inventory.get("skips", 0)

    if skips <= 0:
        await update.message.reply_text(
            "❌ Aapke paas Skip Token nahi hai!\n"
            "/shop se 75 coins mein kharido.",
        )
        return

    riddle     = session["riddle"]
    correct    = riddle.get("answer", "?")
    options    = riddle.get("options", [])
    ans_label  = ""
    for i, opt in enumerate(options):
        if opt.strip().lower() == correct.strip().lower():
            ans_label = f"{OPTION_LABELS[i]} "
            break

    pdb.use_inventory_item(user.id, "skip")
    pdb.skip_paheli(session["session_id"])
    _cancel_timeout(context, session["session_id"])

    await update.message.reply_text(
        f"⏭ <b>Paheli Skip Kar Di!</b>\n\n"
        f"🔑 <b>Sahi Jawab:</b> {ans_label}<b>{correct}</b>\n\n"
        f"Agli paheli ke liye /paheli likhein!",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎯 Agli Paheli", callback_data=f"ph:next:{chat.id}"),
        ]]),
    )


async def cb_paheli_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query      = update.callback_query
    session_id = query.data.split(":", 2)[2]
    user       = query.from_user

    session = pdb.get_active_paheli_by_session(session_id)
    if not session or not session.get("active"):
        await query.answer("Yeh paheli pehle hi khatam ho gayi.", show_alert=True)
        return

    player    = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    inventory = player.get("inventory", {})
    skips     = inventory.get("skips", 0)

    if skips <= 0:
        await query.answer(
            "❌ Skip Token nahi hai! /shop se 75 coins mein kharido.",
            show_alert=True
        )
        return

    riddle  = session["riddle"]
    correct = riddle.get("answer", "?")
    pdb.use_inventory_item(user.id, "skip")
    pdb.skip_paheli(session_id)
    _cancel_timeout(context, session_id)

    await query.answer("Paheli skip!")
    try:
        await query.edit_message_text(
            f"⏭ <b>Paheli Skip Kar Di!</b>\n"
            f"🔑 <b>Sahi Jawab:</b> <b>{correct}</b>",
            parse_mode=constants.ParseMode.HTML,
        )
    except TelegramError:
        pass

    await query.message.reply_text(
        "Agli paheli ke liye /paheli likhein! 🎯"
    )


# ─── Next riddle callback ─────────────────────────────────────────────────────

async def cb_paheli_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    parts   = query.data.split(":")
    chat_id = int(parts[2]) if len(parts) > 2 else query.message.chat.id
    user    = query.from_user

    await query.answer("Agli paheli la raha hoon…")

    cd = pdb.check_cooldown(user.id, "paheli", PAHELI_COOLDOWN_SECONDS)
    if cd > 0:
        await query.answer(f"⏳ {cd}s baad try karo.", show_alert=True)
        return

    existing = pdb.get_active_paheli(chat_id)
    if existing:
        await query.answer("Ek paheli pehle se chal rahi hai!", show_alert=True)
        return

    pdb.ensure_player(user.id, user.first_name or "", user.username or "")

    riddle = _pick_riddle(chat_id)
    if not riddle:
        await query.message.reply_text("❌ Aur paheliyan nahi hain!")
        return

    options = riddle.get("options", [])
    random.shuffle(options)
    riddle = {**riddle, "options": options}

    session_id = str(uuid.uuid4())
    pdb.create_paheli_session(session_id, riddle, chat_id, user.id)
    pdb.set_cooldown(user.id, "paheli", PAHELI_COOLDOWN_SECONDS)

    text = _make_riddle_text(riddle, hint_count=0)
    sent = await query.message.reply_text(
        text,
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_options_keyboard(session_id, riddle, 0),
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
                f"⏳ Daily pehle le chuke ho!\n"
                f"Agli daily mein: <b>{h}h {m}m {s}s</b>",
                parse_mode=constants.ParseMode.HTML,
            )
        return

    streak       = reward["streak"]
    streak_bonus = "🔥 Streak bonus!" if streak > 1 else ""
    weekly_hint  = f"\n🎁 <b>7 din poore!</b> +1 💎 Gem!" if streak % 7 == 0 else ""

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "🌅 <b>DAILY REWARD MILA!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🪙 +<b>{reward['coins']}</b> Coins\n"
        f"🔮 +<b>{reward['xp']}</b> XP\n"
        + (f"💎 +<b>{reward['gems']}</b> Gem(s)\n" if reward.get("gems") else "") +
        f"\n🔥 <b>Streak:</b> {streak} din {streak_bonus}"
        + weekly_hint +
        "\n\n✅ Kal wapas aana streak ke liye!",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /weekly ──────────────────────────────────────────────────────────────────

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pdb.ensure_player(user.id, user.first_name or "", user.username or "")

    reward = pdb.claim_weekly(user.id)
    if not reward:
        await update.message.reply_text(
            "⏳ Weekly reward pehle le chuke ho!\n"
            "Agle hafte aana. 📅"
        )
        return

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "📅 <b>WEEKLY REWARD MILA!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🪙 +<b>{reward['coins']}</b> Coins\n"
        f"🔮 +<b>{reward['xp']}</b> XP\n"
        f"💎 +<b>{reward['gems']}</b> Gems\n\n"
        "🎉 Agle hafte milenge!",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /profile ─────────────────────────────────────────────────────────────────

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    name   = _display_name(user)

    xp        = player.get("xp", 0)
    level     = player.get("level", 0)
    title     = player.get("title", pdb.TITLES[0])
    coins     = player.get("coins", 0)
    gems      = player.get("gems", 0)
    streak    = player.get("daily_streak", 0)
    solved    = player.get("riddles_solved", 0)
    clan_id   = player.get("clan_id")
    inventory = player.get("inventory", {})
    xp_needed, next_thresh = pdb.xp_for_next_level(xp)

    if next_thresh > 0:
        current_level_xp = pdb.LEVEL_THRESHOLDS[level] if level < len(pdb.LEVEL_THRESHOLDS) else 0
        span   = next_thresh - current_level_xp
        filled = max(0, xp - current_level_xp)
        bar_filled = int((filled / span) * 10) if span else 10
        bar    = "█" * bar_filled + "░" * (10 - bar_filled)
        xp_line = f"[{bar}] {xp}/{next_thresh} XP"
    else:
        xp_line = f"{xp} XP (MAX LEVEL)"

    badges    = player.get("badges", [])
    badge_str = " ".join(badges[:5]) if badges else "Abhi koi nahi"
    clan_str  = f"🏰 {clan_id}" if clan_id else "Kisi clan mein nahi"

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{name} ka Profile</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎖 <b>Unvan:</b> {title}\n"
        f"⚡ <b>Level:</b> {level}\n"
        f"📊 <b>XP:</b> {xp_line}\n\n"
        f"🪙 <b>Coins:</b> {coins}\n"
        f"💎 <b>Gems:</b> {gems}\n"
        f"🔥 <b>Streak:</b> {streak} din\n\n"
        f"🧩 <b>Paheliyan Bujhi:</b> {solved}\n"
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
            active_boost = f"\n⚡ <b>2× XP Boost:</b> {remaining}m baaki"

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎒 <b>{name} ka Inventory</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💡 <b>Hint Tokens:</b> {hints}\n"
        f"⏭ <b>Skip Tokens:</b> {skips}\n"
        f"⚡ <b>XP Boosts:</b> {boosts}"
        + active_boost +
        "\n\n🛍 /shop se aur items kharido!\n"
        f"💰 Balance: <b>{player.get('coins', 0)} 🪙</b> | <b>{player.get('gems', 0)} 💎</b>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍 Shop Kholo", callback_data="ph:shop:main"),
        ]]),
    )


# ─── /shop ─────────────────────────────────────────────────────────────────────

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
        InlineKeyboardButton("🔙 Wapas", callback_data="ph:shop:main"),
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
        f"💰 <b>Aapka Balance:</b> {coins} 🪙 | {gems} 💎\n\n"
        "Koi item chunen:",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_shop_keyboard("main"),
    )


async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")

    if parts[1] == "shop":
        user   = query.from_user
        player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
        coins  = player.get("coins", 0)
        gems   = player.get("gems", 0)
        await query.answer()
        try:
            await query.edit_message_text(
                f"🛍 <b>PAHELI SHOP</b>\n\n"
                f"💰 Balance: {coins} 🪙 | {gems} 💎\n\n"
                "Koi item chunen:",
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
        await query.answer("Item nahi mila.", show_alert=True)
        return

    cost = item["cost"]
    if not pdb.spend_coins(user.id, cost):
        await query.answer(
            f"❌ Coins kam hain! {cost}🪙 chahiye.",
            show_alert=True
        )
        return

    item_type = item["type"]
    qty       = item["quantity"]

    if item_type in ("hint", "skip"):
        pdb.add_inventory(user.id, item_type, qty)
        await query.answer(
            f"✅ {item['name']} kharida! /inventory check karo.",
            show_alert=True
        )

    elif item_type == "lucky":
        prizes  = pdb.LUCKY_WHEEL_PRIZES
        weights = [p["weight"] for p in prizes]
        prize   = random.choices(prizes, weights=weights, k=1)[0]
        _give_prize(user.id, prize)
        await query.answer(
            f"🎡 Aapko mila: {prize['label']}!",
            show_alert=True
        )

    elif item_type in ("chest", "chest_gold"):
        prize_pool   = pdb.CHEST_PRIZES.get(item_type, pdb.CHEST_PRIZES["chest"])
        result_lines = []
        for p in prize_pool:
            amt = random.randint(p["min"], p["max"])
            _give_prize(user.id, {"type": p["type"], "amount": amt})
            result_lines.append(f"+{amt} {p['type'].title()}")
        await query.answer(
            f"📦 Chest khula!\n" + "\n".join(result_lines),
            show_alert=True
        )

    elif item_type == "boost":
        from datetime import timedelta
        boost_until = datetime.now(timezone.utc) + timedelta(hours=1)
        pdb.update_player(user.id, {"$set": {"xp_boost_until": boost_until}})
        await query.answer("⚡ 1 ghante ke liye 2× XP Boost active!", show_alert=True)

    updated = pdb.get_player(user.id)
    try:
        await query.edit_message_text(
            f"🛍 <b>PAHELI SHOP</b>\n\n"
            f"💰 Balance: {updated.get('coins', 0)} 🪙 | {updated.get('gems', 0)} 💎\n\n"
            "Koi item chunen:",
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
        "day": "Aaj", "week": "Is Hafte",
        "month": "Is Mahine", "year": "Is Saal", "all": "Sab Time"
    }
    period_emoji = {"day": "🟡", "week": "🟠", "month": "🔵", "year": "🟣", "all": "🏆"}

    scope_label = f"📍 {chat.title}" if scope == "chat" and hasattr(chat, "title") else "🌍 Global"
    p_label     = period_labels.get(period, period)

    if not rows:
        text = f"📊 <b>{scope_label} — {p_label}</b> mein abhi koi score nahi.\n/paheli se paheliyan bujho!"
    else:
        lines = [f"🏆 <b>Paheli Leaderboard</b>\n{scope_label} — {p_label}\n"]
        for i, row in enumerate(rows, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"#{i}")
            name  = row.get("first_name", "Unknown")
            pts   = row.get("total_points", 0)
            solved = row.get("riddles_solved", 0)
            lvl   = row.get("level", 0)
            lines.append(
                f"{medal} <b>{name}</b> [Lv.{lvl}] — {pts} pts · {solved} bujhi"
            )
        text = "\n".join(lines)

    kb_rows = []
    if hasattr(chat, "type") and chat.type in ("group", "supergroup"):
        kb_rows.append([
            _lb_btn("📍 Chat",   period, "chat",   scope),
            _lb_btn("🌍 Global", period, "global", scope),
        ])
    kb_rows.append([
        _lb_btn(f"{period_emoji['day']}Aaj",    "day",   scope, period),
        _lb_btn(f"{period_emoji['week']}Hafta",  "week",  scope, period),
        _lb_btn(f"{period_emoji['month']}Mahina","month", scope, period),
    ])
    kb_rows.append([
        _lb_btn(f"{period_emoji['year']}Saal",   "year", scope, period),
        _lb_btn(f"{period_emoji['all']}Sab Time","all",  scope, period),
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
    user         = update.effective_user
    player       = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    rows         = pdb.get_paheli_leaderboard(period="all", limit=1000)
    rank         = next((i for i, r in enumerate(rows, 1) if r["user_id"] == user.id), None)
    name         = _display_name(user)
    global_stats = pdb.get_global_paheli_stats()

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{name} ke Paheli Stats</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🌍 <b>Global Rank:</b> #{rank or 'Unranked'}\n"
        f"⚡ <b>Level:</b> {player.get('level', 0)} — {player.get('title', 'Novice')}\n"
        f"🔮 <b>XP:</b> {player.get('xp', 0)}\n"
        f"🪙 <b>Coins:</b> {player.get('coins', 0)}\n"
        f"💎 <b>Gems:</b> {player.get('gems', 0)}\n"
        f"🧩 <b>Paheliyan Bujhi:</b> {player.get('riddles_solved', 0)}\n"
        f"🔥 <b>Daily Streak:</b> {player.get('daily_streak', 0)}\n\n"
        "━ <b>Global Paheli Stats</b> ━\n"
        f"👥 Total Players: <b>{global_stats['total_players']}</b>\n"
        f"🎮 Total Games: <b>{global_stats['total_sessions']}</b>\n"
        f"✅ Solve Hui: <b>{global_stats['total_solved']}</b>\n"
        f"⏭ Skip Hui: <b>{global_stats['total_skipped']}</b>",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /settings ────────────────────────────────────────────────────────────────

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    player = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
    s      = player.get("settings", {})

    diff_label = {
        "all":       "🎲 Sab",
        "easy":      "🟢 Saral",
        "medium":    "🟡 Madhyam",
        "hard":      "🔴 Kathin",
        "legendary": "💀 Ati Kathin",
    }.get(s.get("difficulty", "all"), "🎲 Sab")

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>Paheli Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "ℹ️ <b>Note:</b> Difficulty ab auto-rotate karta hai:\n"
        "Saral → Madhyam → Kathin → Ati Kathin → repeat\n\n"
        f"🎯 <b>Manual Override:</b> {diff_label}\n\n"
        "Badlne ke liye button dabao:",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_settings_keyboard(s),
    )


def _settings_keyboard(s: dict) -> InlineKeyboardMarkup:
    diff = s.get("difficulty", "all")

    def _lb(label, val, current, key):
        mark = " ✅" if val == current else ""
        return InlineKeyboardButton(f"{label}{mark}", callback_data=f"ph:set:{key}:{val}")

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Difficulty", callback_data="ph:noop")],
        [
            _lb("🎲 Auto",    "all",    diff, "difficulty"),
            _lb("🟢 Saral",   "easy",   diff, "difficulty"),
            _lb("🟡 Madhyam", "medium", diff, "difficulty"),
        ],
        [
            _lb("🔴 Kathin",       "hard",      diff, "difficulty"),
            _lb("💀 Ati Kathin",   "legendary", diff, "difficulty"),
        ],
    ])


async def cb_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")

    if parts[1] == "noop":
        await query.answer()
        return

    key   = parts[2]
    value = parts[3]
    user  = query.from_user

    pdb.update_player(user.id, {"$set": {f"settings.{key}": value}})
    await query.answer(f"✅ {key.title()} set ho gaya: {value}!")

    player = pdb.get_player(user.id)
    s      = player.get("settings", {})
    try:
        await query.edit_message_reply_markup(reply_markup=_settings_keyboard(s))
    except TelegramError:
        pass


# ─── /challenge (PvP) ─────────────────────────────────────────────────────────

async def cmd_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    msg  = update.message

    if chat.type == "private":
        await msg.reply_text("⚠️ Challenge sirf groups mein start hota hai!")
        return

    if not msg.reply_to_message:
        await msg.reply_text(
            "Kisi ki message reply karke /challenge karo\n"
            "Example: @kisiko reply karo → /challenge"
        )
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await msg.reply_text("❌ Khud ko challenge nahi kar sakte!")
        return
    if target.is_bot:
        await msg.reply_text("❌ Bot ko challenge nahi kar sakte!")
        return

    riddle = _pick_riddle(chat.id)
    if not riddle:
        await msg.reply_text("❌ Challenge ke liye koi paheli nahi mili!")
        return

    options = riddle.get("options", [])
    random.shuffle(options)
    riddle = {**riddle, "options": options}

    challenge_id = str(uuid.uuid4())[:8]
    pdb.ensure_player(user.id,   user.first_name or "",   user.username or "")
    pdb.ensure_player(target.id, target.first_name or "", target.username or "")
    pdb.create_challenge(challenge_id, user.id, target.id, chat.id, riddle)

    challenger_name = _display_name(user)
    target_name     = _display_name(target)

    await msg.reply_text(
        f"⚔️ <b>PvP CHALLENGE!</b>\n\n"
        f"⚔️ <b>{challenger_name}</b> ne <b>{target_name}</b> ko challenge kiya!\n\n"
        f"🧩 <b>Paheli:</b>\n{riddle['question']}\n\n"
        f"⏱ Pehle sahi button dabane wala jeeta!\n"
        f"Expires: <b>10 minutes</b>\n\n"
        f"Challenge ID: <code>{challenge_id}</code>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Challenge Accept Karo",
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
        await query.answer("Challenge nahi mila ya expire ho gaya.", show_alert=True)
        return

    if user.id not in (ch["challenger_id"], ch["challenged_id"]):
        await query.answer("❌ Yeh challenge tumhara nahi!", show_alert=True)
        return

    if ch["status"] != "pending":
        await query.answer("Challenge pehle se start ya khatam ho gaya.", show_alert=True)
        return

    pdb.accept_challenge(challenge_id)
    await query.answer("Challenge accept! Sahi button dabo!")

    riddle  = ch["riddle"]
    options = riddle.get("options", [])
    session_id = str(uuid.uuid4())
    pdb.create_paheli_session(session_id, riddle, query.message.chat.id, user.id)

    await query.message.reply_text(
        f"⚔️ <b>CHALLENGE SHURU!</b>\n\n"
        f"🧩 <b>Paheli:</b>\n{riddle['question']}\n\n"
        f"👇 <b>Sahi jawab button dabao!</b>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_options_keyboard(session_id, riddle, 0),
    )

    context.job_queue.run_once(
        _paheli_timeout,
        when=RIDDLE_TIMEOUT_SECONDS,
        data={"session_id": session_id, "group_id": query.message.chat.id,
              "msg_id": query.message.message_id},
        name=f"ph_timeout_{session_id}",
    )


# ─── /clan ─────────────────────────────────────────────────────────────────────

async def cmd_clan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args or []

    if not args:
        player  = pdb.ensure_player(user.id, user.first_name or "", user.username or "")
        clan_id = player.get("clan_id")

        if clan_id:
            clan = pdb.get_clan(clan_id)
            if clan:
                members_count = len(clan.get("members", []))
                level         = clan.get("level", 1)
                xp            = clan.get("xp", 0)
                max_members   = clan.get("max_members", 10)

                next_level = pdb.CLAN_LEVELS.get(level + 1)
                if next_level:
                    prev     = pdb.CLAN_LEVELS[level]
                    progress = xp - prev
                    needed   = next_level - prev
                    filled   = int((progress / needed) * 10)
                    bar      = "█" * filled + "░" * (10 - filled)
                    xp_bar   = f"{bar} {xp}/{next_level}"
                else:
                    xp_bar = "MAX LEVEL"

                await update.message.reply_text(
                    f"🏰 <b>Tumhara Clan: {clan['clan_name']}</b> [{clan['clan_tag']}]\n\n"
                    f"⭐ <b>Level:</b> {level}\n"
                    f"👥 <b>Members:</b> {members_count}/{max_members}\n"
                    f"📊 <b>Clan XP:</b>\n{xp_bar}\n\n"
                    "Commands:\n"
                    "/clan leave\n"
                    "/clan info TAG\n"
                    "/clan top",
                    parse_mode=constants.ParseMode.HTML,
                )
                return

        await update.message.reply_text(
            "🏰 <b>Clan System</b>\n\n"
            "Tum kisi clan mein nahi ho!\n\n"
            "Commands:\n"
            "/clan create TAG NAME — Clan banao (TAG: 3-5 letters)\n"
            "/clan join TAG — Clan join karo\n"
            "/clan info TAG — Clan info dekho\n"
            "/clan top — Top clans",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    sub = args[0].lower()

    if sub == "create" and len(args) >= 3:
        clan_tag  = args[1].upper()
        clan_name = " ".join(args[2:])
        if not (3 <= len(clan_tag) <= 5 and clan_tag.isalpha()):
            await update.message.reply_text("❌ Clan tag 3-5 letters ka hona chahiye.")
            return
        pdb.ensure_player(user.id, user.first_name or "", user.username or "")
        success = pdb.create_clan(clan_tag, clan_name, user.id)
        if success:
            await update.message.reply_text(
                f"🏰 Clan <b>{clan_name}</b> [{clan_tag}] bana gaya!\n"
                f"Tum clan owner ho!",
                parse_mode=constants.ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                f"❌ Clan tag [{clan_tag}] pehle se le gaya. Doosra tag chunein."
            )

    elif sub == "join" and len(args) >= 2:
        tag    = args[1].upper()
        pdb.ensure_player(user.id, user.first_name or "", user.username or "")
        player = pdb.get_player(user.id)
        if player.get("clan_id"):
            await update.message.reply_text("❌ Tum pehle se ek clan mein ho! /clan leave pehle.")
            return
        success = pdb.join_clan(user.id, tag)
        if success:
            await update.message.reply_text(f"✅ Clan [{tag}] join kar liya!")
        else:
            await update.message.reply_text(f"❌ Clan [{tag}] nahi mila ya full hai.")

    elif sub == "leave":
        success = pdb.leave_clan(user.id)
        if success:
            await update.message.reply_text("👋 Clan chhodh diya.")
        else:
            await update.message.reply_text("❌ Tum kisi clan mein nahi ho.")

    elif sub == "top":
        clans = pdb.get_clan_leaderboard(10)
        if not clans:
            await update.message.reply_text("Koi clan nahi! /clan create TAG NAME se banao")
            return
        lines = ["🏆 <b>Top Clans</b>\n"]
        for i, c in enumerate(clans, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"#{i}")
            lines.append(
                f"{medal} <b>{c['clan_name']}</b> [{c['clan_tag']}] "
                f"— Lv.{c.get('level', 1)} • "
                f"{len(c.get('members', []))}/{c.get('max_members', 10)} members • "
                f"{c.get('xp', 0)} XP"
            )
        await update.message.reply_text(
            "\n".join(lines), parse_mode=constants.ParseMode.HTML
        )

    elif sub == "info" and len(args) >= 2:
        tag  = args[1].upper()
        clan = pdb.get_clan(tag)
        if not clan:
            await update.message.reply_text(f"❌ Clan [{tag}] nahi mila.")
            return
        level       = clan.get("level", 1)
        xp          = clan.get("xp", 0)
        members     = len(clan.get("members", []))
        max_members = clan.get("max_members", 10)

        await update.message.reply_text(
            f"🏰 <b>{clan['clan_name']}</b> [{clan['clan_tag']}]\n\n"
            f"⭐ Level: {level}\n"
            f"👥 Members: {members}/{max_members}\n"
            f"🏆 XP: {xp}\n"
            f"📅 Bana: {clan['created_at'].strftime('%Y-%m-%d') if clan.get('created_at') else 'N/A'}",
            parse_mode=constants.ParseMode.HTML,
        )

    elif sub == "delete":
        if not pdb.delete_clan(user.id):
            await update.message.reply_text("❌ Aap kisi clan ke owner nahi hain.")
            return
        await update.message.reply_text("🗑️ Clan delete ho gaya!")

    else:
        await update.message.reply_text(
            "Commands:\n"
            "/clan — Aapka clan info\n"
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
        await update.message.reply_text("❌ Sirf admin.")
        return

    text = update.message.text
    parts = text.split("\n", 1)
    if len(parts) < 2:
        await update.message.reply_text(
            "Usage (har field alag line mein):\n"
            "/addriddle\n"
            "Question: ...\n"
            "Answer: ...\n"
            "Options: opt1, opt2, opt3, opt4, opt5, opt6, opt7, opt8\n"
            "Hints: hint1, hint2\n"
            "Difficulty: easy/medium/hard/legendary\n"
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
        options_raw = [o.strip() for o in lines.get("options", answer).split(",") if o.strip()]
        if len(options_raw) < 2:
            options_raw = [answer, "Doosra", "Teesra", "Chautha", "Paanchwa", "Chhata", "Saatwa", "Aathwa"]
        # Ensure answer is in options
        if answer not in options_raw:
            options_raw[0] = answer
        hints      = [h.strip() for h in lines.get("hints", "").split(",") if h.strip()]
        difficulty = lines.get("difficulty", "easy")

        max_id = max((r.get("id", 0) for r in _ALL_RIDDLES), default=0)
        new_id = max_id + 1
        pts    = {"easy": 10, "medium": 25, "hard": 50, "legendary": 100}.get(difficulty, 10)

        riddle = {
            "id": new_id, "question": question, "answer": answer,
            "options": options_raw[:8], "hints": hints,
            "category": "desi", "difficulty": difficulty,
            "language": "hi", "points": pts, "custom": True,
        }
        pdb.add_custom_riddle(riddle)
        _ALL_RIDDLES.append(riddle)

        await update.message.reply_text(
            f"✅ Paheli #{new_id} add ho gayi!\n"
            f"Q: {question}\nA: {answer}\nDifficulty: {difficulty}"
        )
    except KeyError as e:
        await update.message.reply_text(f"❌ Field missing: {e}")


async def cmd_deleteriddle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_sudo(user.id):
        await update.message.reply_text("❌ Sirf admin.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /deleteriddle <riddle_id>")
        return

    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Riddle ID number hona chahiye.")
        return

    success = pdb.delete_custom_riddle(rid)
    if success:
        global _ALL_RIDDLES
        _ALL_RIDDLES = [r for r in _ALL_RIDDLES if r["id"] != rid]
        await update.message.reply_text(f"✅ Paheli #{rid} delete ho gayi.")
    else:
        await update.message.reply_text(f"❌ Paheli #{rid} nahi mili (sirf custom delete hoti hain).")


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
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason"
    pdb.ban_user(target_id, reason=reason, banned_by=user.id)
    await update.message.reply_text(
        f"🚫 User <code>{target_id}</code> ban ho gaya.\nReason: {reason}",
        parse_mode=constants.ParseMode.HTML
    )


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
    await update.message.reply_text(
        f"✅ User <code>{target_id}</code> unban ho gaya.",
        parse_mode=constants.ParseMode.HTML
    )


async def cmd_ridstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_sudo(user.id):
        return
    stats        = pdb.get_global_paheli_stats()
    custom_count = len([r for r in _ALL_RIDDLES if r.get("custom")])

    await update.message.reply_text(
        "📊 <b>Paheli Admin Stats</b>\n\n"
        f"🧩 Total Paheliyan: <b>{len(_ALL_RIDDLES)}</b>\n"
        f"✍️ Custom Paheliyan: <b>{custom_count}</b>\n"
        f"👥 Players: <b>{stats['total_players']}</b>\n"
        f"🎮 Sessions: <b>{stats['total_sessions']}</b>\n"
        f"✅ Solve Hui: <b>{stats['total_solved']}</b>\n"
        f"⏭ Skip Hui: <b>{stats['total_skipped']}</b>\n"
        f"🏰 Clans: <b>{stats['total_clans']}</b>",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── /paheli_help ─────────────────────────────────────────────────────────────

async def cmd_paheli_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        "🧩 <b>Paheli — Desi Hinglish Paheliyan!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>🎮 Khelne ka Tarika:</b>\n"
        "/paheli — Nai paheli shuru karo\n"
        "→ 8 buttons dikhenge, sahi wala dabao!\n"
        "→ Galat dabane par sirf tumhe pata chalega\n"
        "→ Pehle sahi button dabane wala jeet jaata hai!\n\n"
        "<b>🔄 Difficulty Rotation:</b>\n"
        "🟢 Saral → 🟡 Madhyam → 🔴 Kathin → 💀 Ati Kathin → repeat\n\n"
        "<b>🏆 Commands:</b>\n"
        "/paheli — Paheli shuru karo\n"
        "/hint — Hint lo (tokens kharche honge)\n"
        "/skip — Paheli skip karo (Skip Token chahiye)\n"
        "/daily — Roz ka reward lo\n"
        "/weekly — Hafte ka reward lo\n"
        "/challenge — PvP challenge (reply karke)\n"
        "/clan — Clan system\n"
        "/profile — Apna profile\n"
        "/inventory — Apna inventory\n"
        "/shop — Items kharido\n"
        "/paheli_stats — Stats dekho\n"
        "/settings — Settings\n"
        "/plb — Leaderboard\n\n"
        "<b>⭐ Points:</b>\n"
        "🟢 Saral: 10 pts | 🟡 Madhyam: 25 pts\n"
        "🔴 Kathin: 50 pts | 💀 Ati Kathin: 100 pts\n"
        "No-hint bonus: +30% points!\n",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── Callback router ──────────────────────────────────────────────────────────

async def cb_paheli_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data

    if data.startswith("ph:opt:"):
        await cb_paheli_option(update, context)
    elif data.startswith("ph:hint:"):
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

    # Game selector
    app.add_handler(CommandHandler("game", cmd_game))
    app.add_handler(CallbackQueryHandler(cb_game_selector, pattern=r"^game:"))

    # Paheli commands
    app.add_handler(CommandHandler("paheli",        cmd_paheli))
    app.add_handler(CommandHandler("hint",          cmd_paheli_hint))
    app.add_handler(CommandHandler("skip",          cmd_paheli_skip))
    app.add_handler(CommandHandler("daily",         cmd_daily))
    app.add_handler(CommandHandler("weekly",        cmd_weekly))
    app.add_handler(CommandHandler(["pleaderboard", "plb"], cmd_paheli_lb))
    app.add_handler(CommandHandler("profile",       cmd_profile))
    app.add_handler(CommandHandler("inventory",     cmd_inventory))
    app.add_handler(CommandHandler("shop",          cmd_shop))
    app.add_handler(CommandHandler("paheli_stats",  cmd_paheli_stats))
    app.add_handler(CommandHandler("settings",      cmd_settings))
    app.add_handler(CommandHandler("challenge",     cmd_challenge))
    app.add_handler(CommandHandler("clan",          cmd_clan))
    app.add_handler(CommandHandler("paheli_help",   cmd_paheli_help))

    # Admin commands
    app.add_handler(CommandHandler("addriddle",     cmd_addriddle))
    app.add_handler(CommandHandler("deleteriddle",  cmd_deleteriddle))
    app.add_handler(CommandHandler("pban",          cmd_pban))
    app.add_handler(CommandHandler("punban",        cmd_punban))
    app.add_handler(CommandHandler("ridstats",      cmd_ridstats))

    # Callback router — handles all ph: callbacks (including MCQ options)
    app.add_handler(CallbackQueryHandler(cb_paheli_router, pattern=r"^ph:"))

    # NOTE: Text-based answer handler removed — MCQ buttons handle everything now.

    logger.info("✅ Paheli handlers registered (MCQ mode, 400 desi paheliyan)")
