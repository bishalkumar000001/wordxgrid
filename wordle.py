"""
wordle.py — Wordle-style group game for Telegram.

Commands:
  /wordle or /new5  — Start a 5-letter Wordle game
  /new6             — Start a 6-letter Wordle game
  /wend             — End the current Wordle game (admins only)
  /wlb              — Wordle leaderboard for this group
  /wstats           — Your Wordle stats

Feedback per letter:
  🟩 right letter, right position
  🟨 right letter, wrong position
  🟥 letter not in the word

Points: 30 for 1st-attempt solve, 29 for 2nd, …, 1 minimum.
Wins are recorded in the shared Word Grid scores table so they
appear on /lb (the unified cross-game leaderboard).
No hints. 30 attempts total per game (shared across all players).
"""

import uuid
import html
import logging
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
import database as db       # shared scoring — wins appear on /lb
import wordle_db
from wordle_words import WORDS_BY_LENGTH

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 30

# Pre-build valid-word sets (uppercase) for fast O(1) lookup
VALID_WORDS: dict[int, set[str]] = {
    length: {w.upper() for w in words}
    for length, words in WORDS_BY_LENGTH.items()
}


def _preferred_pool(length: int, top_n: int = 500) -> list[str]:
    """
    Return a list of the top_n words for the given length ranked by letter
    frequency. This produces easier / more guessable words (common letters
    and varied letters) for daily or default play.
    """
    # Prefer an "easier" pool for common daily games (5- and 6-letter)
    if length in (5, 6):
        pool = _preferred_pool(length)
    else:
        pool = list(VALID_WORDS.get(length, set()))
    if not pool:
        return pool

    # Build letter frequency across the pool
    freq: dict[str, int] = {}
    for w in pool:
        for ch in w:
            freq[ch] = freq.get(ch, 0) + 1

    # Score words by unique-letter frequency (prefer varied common letters)
    def score(word: str) -> int:
        seen = set()
        s = 0
        for ch in word:
            if ch in seen:
                continue
            seen.add(ch)
            s += freq.get(ch, 0)
        return s

    scored = sorted(pool, key=score, reverse=True)
    return scored[: min(top_n, len(scored))]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _feedback(guess: str, target: str) -> str:
    """
    Return emoji-per-letter feedback string.
    Example: 🟩M🟨O🟥N🟩E🟥Y
    """
    guess  = guess.upper()
    target = target.upper()

    marks        = [""] * len(guess)
    target_chars = list(target)

    # Pass 1 — exact matches (green 🟩)
    for i, (g, t) in enumerate(zip(guess, target_chars)):
        if g == t:
            marks[i]        = "🟩"
            target_chars[i] = None

    # Pass 2 — misplaced (yellow 🟨) and absent (red 🟥)
    for i, g in enumerate(guess):
        if marks[i]:
            continue
        if g in target_chars:
            marks[i] = "🟨"
            target_chars[target_chars.index(g)] = None
        else:
            marks[i] = "🟥"

    return "".join(f"{m}{c}" for m, c in zip(marks, guess.upper()))


def _unicode_bold_upper(char: str) -> str:
    if "A" <= char <= "Z":
        return chr(0x1D400 + (ord(char) - ord("A")))
    return char.upper()


def _format_guess_line(guess: str, target: str) -> str:
    guess = guess.upper()
    target_chars = list(target.upper())
    marks = [None] * len(guess)

    for i, c in enumerate(guess):
        if c == target_chars[i]:
            marks[i] = "🟩"
            target_chars[i] = None

    for i, c in enumerate(guess):
        if marks[i] is not None:
            continue
        if c in target_chars:
            marks[i] = "🟨"
            target_chars[target_chars.index(c)] = None
        else:
            marks[i] = "🟥"

    return " ".join(marks)


def _build_wordle_status(game: dict, opening: bool = True) -> str:
    """Build the Wordle display. The opening includes instructions; later
    updates are intentionally compact and show only the counter + guess grid."""
    guesses = game.get("guesses", [])
    word = game.get("word", "")
    length = game.get("length", len(word))

    if opening:
        lines = [
            f"❝ <b>WORDLE · {length}-LETTER CHALLENGE</b> ❞",
            "",
            f"<blockquote>🧠 <b>Guess the hidden {length}-letter word.</b>",
            "",
            "🟩 Right letter · right place",
            "🟨 Right letter · wrong place",
            "🟥 Letter not in the word",
            "",
            f"⚡ <b>{MAX_ATTEMPTS} attempts</b> shared by everyone",
            "🏆 <b>Reward:</b> 30 points on the first guess, down to 1 minimum",
            "🚫 No hints",
            "",
            f"✦ <b>Make your move.</b> Type your {length}-letter guess now.",
            "</blockquote>",
            "",
            f"<b>{length}-letter mode · {len(guesses)}/{MAX_ATTEMPTS}</b>",
            "",
        ]
    else:
        lines = [
            f"<b>{length}-letter mode · {len(guesses)}/{MAX_ATTEMPTS}</b>",
            "",
        ]

    for entry in guesses:
        styled_guess = "".join(_unicode_bold_upper(c) for c in entry["guess"].upper())
        lines.append(f"{_format_guess_line(entry['guess'], word)} {styled_guess}")

    return "\n".join(lines)


def _display_name(user) -> str:
    name = (user.first_name or "").strip()
    if user.last_name:
        name = f"{name} {user.last_name}".strip()
    return name or f"User{user.id}"


def _user_link(user) -> str:
    return f"<a href='tg://user?id={user.id}'>{html.escape(_display_name(user))}</a>"


def _is_admin(member) -> bool:
    return member.status in ("administrator", "creator")


# ─── Core start (reusable from commands AND from /game callback) ───────────────

async def _do_start_wordle(bot, chat, length: int) -> None:
    """
    Create a new Wordle game and send the opening message.
    Can be called from a command handler *or* from the /game inline button callback.
    """
    existing = wordle_db.get_active_wordle(chat.id)
    if existing:
        used      = existing["attempts"]
        remaining = MAX_ATTEMPTS - used
        await bot.send_message(
            chat.id,
            f"❝ <b>WORDLE</b> ❞\n\n"
            f"⚠️ <b>A game is already in progress.</b>\n\n"
            f"🔤 <b>Word Length:</b> {existing['length']} letters\n"
            f"🎯 <b>Attempts:</b> {used}/{MAX_ATTEMPTS} · {remaining} remaining\n\n"
            f"✦ Enter your <b>{existing['length']}-letter</b> guess below.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    pool = list(VALID_WORDS.get(length, set()))
    if not pool:
        await bot.send_message(chat.id, f"❝ <b>WORDLE</b> ❞\n\n❌ No {length}-letter words are available right now. Please try again shortly.")
        return

    word    = random.choice(pool)
    game_id = str(uuid.uuid4())
    wordle_db.create_wordle_game(game_id, chat.id, word, length)

    status_msg = await bot.send_message(
        chat.id,
        _build_wordle_status({
            "length": length,
            "word": word,
            "guesses": [],
        }),
        parse_mode=constants.ParseMode.HTML,
    )
    wordle_db.update_wordle_status_message(game_id, status_msg.message_id)



# ─── Command handlers ──────────────────────────────────────────────────────────

async def _start_wordle(update: Update, context: ContextTypes.DEFAULT_TYPE, length: int):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("❝ <b>WORDLE</b> ❞\n\n⚠️ This challenge is available in groups only.")
        return
    await _do_start_wordle(context.bot, chat, length)


async def cmd_wordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _start_wordle(update, context, length=5)


async def cmd_new5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _start_wordle(update, context, length=5)


async def cmd_new6(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _start_wordle(update, context, length=6)


async def cb_new_wordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = query.data
    length = 5 if data == "new5" else 6
    chat = query.message.chat

    if chat.type not in ("group", "supergroup"):
        await query.message.reply_text("❝ <b>WORDLE</b> ❞\n\n⚠️ This challenge is available in groups only.")
        return

    await _do_start_wordle(context.bot, chat, length)


async def cmd_wend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        return

    member = await chat.get_member(user.id)
    if not _is_admin(member) and user.id not in config.SUDO_USERS:
        await update.message.reply_text("❝ <b>WORDLE</b> ❞\n\n⚠️ Only group admins can close the current challenge.")
        return

    game = wordle_db.get_active_wordle(chat.id)
    if not game:
        await update.message.reply_text("❝ <b>WORDLE</b> ❞\n\nℹ️ No active challenge is running in this group.")
        return

    wordle_db.end_wordle_game(game["game_id"])
    await update.message.reply_text(
        f"❝ <b>WORDLE · CHALLENGE CLOSED</b> ❞\n\n"
        f"🛑 Closed by {_user_link(user)}.\n\n"
        f"🔑 <b>The hidden word was:</b> {game['word']}",
        parse_mode=constants.ParseMode.HTML,
    )


async def cmd_wlb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the same main leaderboard used by WordGrid /lb.

    It includes WordGrid, Wordle and the three new mini-games only.
    Paheli and Find-the-Spy remain outside this leaderboard.
    """
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        scope = "chat"
        group_id = chat.id
        scope_label = chat.title or "This Chat"
    else:
        scope = "global"
        group_id = None
        scope_label = "Global"

    rows = db.get_period_leaderboard("all", group_id=group_id, limit=10)
    if not rows:
        await update.message.reply_text(
            f"📊 No scores yet for <b>{html.escape(scope_label)} — All Time</b>.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    lines = [
        "❝ <b>WordGrid — Leaderboard</b> ❞",
        f"📍 <b>{html.escape(scope_label)}</b> | All Time",
        "",
    ]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows, 1):
        medal = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
        name = html.escape((row.get("first_name") or row.get("username") or "Unknown").strip())
        lines.append(f"{medal} {name} — <b>{row.get('total_points', 0)}</b> pts")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
    )


async def cmd_wstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    stats = wordle_db.get_wordle_stats(user.id)
    await update.message.reply_text(
        f"❝ <b>WORDLE · YOUR STATS</b> ❞\n\n<blockquote>"
        f"🏆 Total Points: <b>{stats.get('total_points', 0)}</b>\n"
        f"🎯 <b>Games Won:</b> {stats.get('games_won', 0)}\n"
        f"</blockquote>",
        parse_mode=constants.ParseMode.HTML,
    )


# ─── Message handler ───────────────────────────────────────────────────────────

async def handle_wordle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    user = update.effective_user
    text = update.message.text.strip().upper()

    if chat.type not in ("group", "supergroup"):
        return

    game = wordle_db.get_active_wordle(chat.id)
    if not game:
        return

    length = game["length"]

    # Guess must be exactly the right length and all alphabetic
    if len(text) != length or not text.isalpha():
        return

    # Validate against word list
    if text not in VALID_WORDS.get(length, set()):
        await update.message.reply_text(
            f"❝ <b>WORDLE</b> ❞\n\n❌ <b>{text.lower()}</b> is not in the word list. Try another word.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    # Ignore repeated guesses within the same game
    previous = {entry["guess"] for entry in game.get("guesses", [])}
    if text in previous:
        await update.message.reply_text(
            "❝ <b>WORDLE</b> ❞\n\n⚠️ You already tried that word. Choose a different guess.",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    # Atomically record the guess and get the updated game doc
    updated_game = wordle_db.record_guess(
        game["game_id"], user.id, _display_name(user), text
    )
    if updated_game is None:
        return

    attempt   = updated_game["attempts"]
    target    = updated_game["word"]
    correct   = text == target
    remaining = MAX_ATTEMPTS - attempt
    status_msg_id = updated_game.get("status_msg_id")

    if correct:
        points = max(1, MAX_ATTEMPTS - attempt + 1)
        wordle_db.end_wordle_game(updated_game["game_id"])

        db.upsert_user(
            user.id,
            user.username or "",
            user.first_name or "",
            user.last_name or "",
        )
        db.add_score(
            user.id, chat.id, updated_game["game_id"],
            target,
            points,
        )
        wordle_db.add_wordle_score(
            user.id, chat.id, updated_game["game_id"], points,
            user.first_name or "", user.username or "",
        )

    elif remaining <= 0:
        wordle_db.end_wordle_game(updated_game["game_id"])

    status_text = _build_wordle_status(updated_game, opening=False)
    if correct:
        status_text += (
            "\n\n "
            f"<blockquote>"
            f"🎉{_user_link(user)} solved it!\n"
            f"🔤 Word: <b>{target}</b>\n"
            f"🎯 Attempt: <b>{attempt}/{MAX_ATTEMPTS}</b>\n"
            f"🏆 Points Awarded: <b>+{points}</b> (shown on /lb)"
            f"</blockquote>"
        )
    elif remaining <= 0:
        status_text += (
            "\n\n💀 <b>Game Over!</b> All "
            f"{MAX_ATTEMPTS} attempts used.\n"
            f"🔤 The word was: <b>{target}</b>"
        )

    keyboard = None
    if correct or remaining <= 0:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🟩 New 5-letter", callback_data="new5"),
            InlineKeyboardButton("🟦 New 6-letter", callback_data="new6"),
        ]])

    sent_status = await context.bot.send_message(
        chat.id,
        status_text,
        parse_mode=constants.ParseMode.HTML,
        reply_markup=keyboard,
    )
    wordle_db.update_wordle_status_message(
        updated_game["game_id"], sent_status.message_id
    )


# ─── Registration ──────────────────────────────────────────────────────────────

def register_wordle_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("wordle", cmd_wordle))
    app.add_handler(CommandHandler("new5",   cmd_new5))
    app.add_handler(CommandHandler("new6",   cmd_new6))
    app.add_handler(CommandHandler("wend",   cmd_wend))
    app.add_handler(CommandHandler("wlb",    cmd_wlb))
    app.add_handler(CommandHandler("wstats", cmd_wstats))
    app.add_handler(CallbackQueryHandler(cb_new_wordle, pattern=r"^new[56]$"))

    # group=2 → runs after WordGrid (0) and Paheli (1)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            handle_wordle_guess,
        ),
        group=2,
    )
