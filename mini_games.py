"""Extra lightweight Telegram group games: Code Breaker, Word Scramble, Memory Test."""
import asyncio
import random
import string
import uuid
from collections import Counter
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.error import TelegramError

import database as db
from words import WORDS_BY_LENGTH

SESSIONS = {}  # chat_id -> session; active rounds intentionally reset on process restart


def _name(user):
    return user.first_name or user.username or f"User{user.id}"


def _score(user, chat_id, game_id, points, label):
    db.upsert_user(user.id, user.username or "", user.first_name or "", user.last_name or "")
    db.add_score(user.id, chat_id, game_id, label, points)


def _cleanup(chat_id):
    return SESSIONS.pop(chat_id, None)


def _menu(chat_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Code Breaker", callback_data=f"xgame:code:{chat_id}"),
         InlineKeyboardButton("🔤 Word Scramble", callback_data=f"xgame:scramble:{chat_id}")],
        [InlineKeyboardButton("🧠 Memory Test", callback_data=f"xgame:memory:{chat_id}")],
    ])

async def game_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(
        "❝ <b>VELOCITY GAME CENTER</b> ❞\n\n"
        "🎮 Pick a challenge:\n\n"
        "🔐 <b>Code Breaker</b> — crack a 3–6 digit secret code.\n"
        "🔤 <b>Word Scramble</b> — unscramble the word first.\n"
        "🧠 <b>Memory Test</b> — remember the sequence and type it back.",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_menu(chat.id),
    )

async def start_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, kind, chat_id = q.data.split(":")
    chat_id = int(chat_id)
    if q.message.chat.id != chat_id:
        return
    if q.message.chat.type == "private":
        await q.answer("Ye games groups ke liye hain 😄", show_alert=True)
        return
    if kind == "code":
        await start_code(context.bot, q.message.chat, digits=4)
    elif kind == "scramble":
        await start_scramble(context.bot, q.message.chat)
    else:
        await start_memory(context.bot, q.message.chat)

async def code_cmd(update, context):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("⚠️ Group mein Code Breaker khelo!")
    digits = 4
    if context.args and context.args[0].isdigit() and 3 <= int(context.args[0]) <= 6:
        digits = int(context.args[0])
    await start_code(context.bot, update.effective_chat, digits)

async def start_code(bot, chat, digits=4):
    old = SESSIONS.get(chat.id)
    if old:
        await bot.send_message(chat.id, "⚠️ Ek game already chal raha hai. Pehle usko finish karo!")
        return

    # Wordle-style Code Breaker: repeated digits are allowed and every position
    # receives its own tile-style clue after each guess.
    code = ''.join(random.choice(string.digits) for _ in range(digits))
    sid = "code-" + uuid.uuid4().hex[:10]
    SESSIONS[chat.id] = {
        "type": "code", "id": sid, "code": code, "attempts": 0,
        "max_attempts": 10, "digits": digits, "started": asyncio.get_running_loop().time(),
        "history": []
    }

    await bot.send_message(
        chat.id,
        f"❝ <b>🔐 CODE BREAKER — WORDLE MODE</b> ❞\n\n"
        f"Crack the hidden <b>{digits}-digit</b> code. Digits <b>may repeat</b>.\n\n"
        f"🟩 = correct digit + correct place\n"
        f"🟨 = correct digit + wrong place\n"
        f"⬛ = digit is not in the code\n\n"
        f"🎯 You get <b>10 attempts</b>.\n"
        f"🏆 Solve on the first attempt = <b>50 pts</b>\n\n"
        f"Example: <code>{'1234'[:digits]}</code>",
        parse_mode=constants.ParseMode.HTML
    )


def _code_feedback(guess, code):
    """Return Wordle-style status for each digit, correctly handling repeats."""
    result = ["⬛"] * len(code)
    remaining = Counter()

    # First pass: exact matches.
    for i, (g, c) in enumerate(zip(guess, code)):
        if g == c:
            result[i] = "🟩"
        else:
            remaining[c] += 1

    # Second pass: present elsewhere, with duplicate-safe accounting.
    for i, g in enumerate(guess):
        if result[i] == "🟩":
            continue
        if remaining[g] > 0:
            result[i] = "🟨"
            remaining[g] -= 1

    return result


def _code_board(session):
    lines = []
    for guess, marks in session.get("history", []):
        lines.append(" ".join(f"{m}{d}" for d, m in zip(guess, marks)))
    return "\n".join(lines)


async def code_message(update, context):
    chat = update.effective_chat
    session = SESSIONS.get(chat.id)
    if not session or session["type"] != "code":
        return

    text = update.message.text.strip().replace(" ", "")
    code = session["code"]
    if not text.isdigit() or len(text) != len(code):
        return

    session["attempts"] += 1
    marks = _code_feedback(text, code)
    session["history"].append((text, marks))

    if text == code:
        attempts = session["attempts"]
        # 50, 45, 40 ... 15 points depending on attempts.
        points = max(15, 50 - (attempts - 1) * 5)
        sid = session["id"]
        board = _code_board(session)
        _cleanup(chat.id)
        _score(update.effective_user, chat.id, sid, points, "__code_breaker__")
        await update.message.reply_text(
            f"🔓 <b>CODE CRACKED!</b>\n\n"
            f"<code>{board}</code>\n\n"
            f"🎉 <a href='tg://user?id={update.effective_user.id}'>{_name(update.effective_user)}</a> found the code!\n"
            f"🔢 Attempts: <b>{attempts}/10</b>\n"
            f"🏆 Reward: <b>+{points} pts</b>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    board = _code_board(session)
    remaining = session["max_attempts"] - session["attempts"]
    exact = marks.count("🟩")
    misplaced = marks.count("🟨")
    absent = marks.count("⬛")

    if remaining <= 0:
        sid = session["id"]
        _cleanup(chat.id)
        await update.message.reply_text(
            f"💥 <b>CODE LOCKED!</b>\n\n"
            f"<code>{board}</code>\n\n"
            f"🔐 The code was <code>{code}</code>\n"
            f"No points this round. Try again! 😈",
            parse_mode=constants.ParseMode.HTML
        )
        return

    await update.message.reply_text(
        f"🔐 <b>CODE BREAKER</b>  •  Attempt <b>{session['attempts']}/10</b>\n\n"
        f"<code>{board}</code>\n\n"
        f"🟩 <b>{exact}</b> correct place  •  🟨 <b>{misplaced}</b> wrong place  •  ⬛ <b>{absent}</b> absent\n\n"
        f"💡 <b>Position-by-position clues are shown above.</b>\n"
        f"🎯 <b>{remaining}</b> attempts remaining.",
        parse_mode=constants.ParseMode.HTML
    )

async def scramble_cmd(update, context):
    if update.effective_chat.type != "private":
        await start_scramble(context.bot, update.effective_chat)

async def start_scramble(bot, chat):
    if chat.id in SESSIONS:
        await bot.send_message(chat.id, "⚠️ Ek game already chal raha hai. Finish that round first!")
        return
    lengths = [4,5,6,7,8]
    length = random.choice(lengths)
    word = random.choice(WORDS_BY_LENGTH[length]).upper()
    chars = list(word)
    for _ in range(10):
        random.shuffle(chars)
        scrambled = ''.join(chars)
        if scrambled != word:
            break
    sid = "scramble-" + uuid.uuid4().hex[:10]
    SESSIONS[chat.id] = {"type":"scramble", "id":sid, "word":word}
    await bot.send_message(chat.id,
        f"❝ <b>🔤 WORD SCRAMBLE</b> ❞\n\n"
        f"<blockquote>🧩 Unscramble this:\n\n<b>{' '.join(scrambled)}</b>\n\n"
        f"⚡ First correct answer wins <b>30 pts</b>!</blockquote>", parse_mode=constants.ParseMode.HTML)

async def scramble_message(update, context):
    chat = update.effective_chat
    s = SESSIONS.get(chat.id)
    if not s or s["type"] != "scramble":
        return
    answer = update.message.text.strip().upper().replace(" ", "")
    if answer != s["word"]:
        return
    sid = s["id"]
    word = s["word"]
    _cleanup(chat.id)
    _score(update.effective_user, chat.id, sid, 30, "__word_scramble__")
    await update.message.reply_text(
        f"🎯 <b>SCRAMBLED!</b>\n\n🏆 <a href='tg://user?id={update.effective_user.id}'>{_name(update.effective_user)}</a> got <b>{word}</b> first!\n💰 <b>+30 pts</b>",
        parse_mode=constants.ParseMode.HTML)

async def memory_cmd(update, context):
    if update.effective_chat.type != "private":
        await start_memory(context.bot, update.effective_chat)

async def start_memory(bot, chat):
    """Start a Memory Test without blocking Telegram's update loop.

    IMPORTANT: never await the 5s + 20s timers from the command handler itself.
    The previous implementation did that, which blocked update processing and
    caused users' answers to be delivered only after the round had timed out.
    """
    if chat.id in SESSIONS:
        await bot.send_message(chat.id, "⚠️ Ek game already chal raha hai. Finish that round first!")
        return

    length = random.randint(6, 9)
    seq = ''.join(random.choice(string.digits) for _ in range(length))
    sid = "memory-" + uuid.uuid4().hex[:10]
    SESSIONS[chat.id] = {
        "type": "memory",
        "id": sid,
        "seq": seq,
        "visible": True,
        "finished": False,
        "deadline": None,
    }

    msg = await bot.send_message(
        chat.id,
        f"❝ <b>🧠 MEMORY TEST</b> ❞\n\n"
        f"Remember this sequence for <b>3 seconds</b>:\n\n"
        f"<code>{' '.join(seq)}</code>\n\n"
        f"👀 Focus! Then type it back exactly.",
        parse_mode=constants.ParseMode.HTML,
    )

    # Run timers in the background. Do NOT block the update dispatcher.
    asyncio.create_task(_memory_round_flow(bot, chat.id, sid, seq, msg))


async def _memory_round_flow(bot, chat_id, sid, seq, msg):
    """Hide the sequence after 5s and close the round 60s later."""
    try:
        await asyncio.sleep(3)
        s = SESSIONS.get(chat_id)
        if not s or s.get("id") != sid or s.get("finished"):
            return

        s["visible"] = False
        s["deadline"] = asyncio.get_running_loop().time() + 60.0

        try:
            await msg.edit_text(
                "❝ <b>🧠 MEMORY TEST</b> ❞ ❌\n\n"
                "Sequence hidden!\n\n"
                "⌨️ <b>Type the sequence now.</b>\n"
                "🏆 First correct answer = <b>40 pts</b>\n"
                "⏱️ <b>60 seconds</b>",
                parse_mode=constants.ParseMode.HTML,
            )
        except TelegramError:
            pass

        await asyncio.sleep(60)
        s = SESSIONS.get(chat_id)
        if s and s.get("id") == sid and not s.get("finished"):
            _cleanup(chat_id)
            await bot.send_message(
                chat_id,
                f"⏰ <b>Memory round over!</b>\n"
                f"The sequence was <code>{seq}</code>.",
                parse_mode=constants.ParseMode.HTML,
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        # Never let a background timer crash the bot process.
        import logging
        logging.getLogger(__name__).exception("Memory round task failed for chat %s", chat_id)

async def memory_message(update, context):
    """Process a Memory Test answer immediately and atomically.

    This handler is intentionally separate from the generic game dispatcher.
    Other group-game handlers must never get a chance to consume a valid memory
    answer first. The session is claimed before scoring so two simultaneous
    correct answers cannot both win.
    """
    message = update.message
    chat = update.effective_chat
    if message is None or not message.text or chat is None:
        return False

    s = SESSIONS.get(chat.id)
    if not s or s.get("type") != "memory" or s.get("visible"):
        return False

    # The answer window starts only after the sequence is hidden.
    # Use a monotonic deadline so a late/queued message cannot win.
    deadline = s.get("deadline")
    if deadline is None:
        return False
    if asyncio.get_running_loop().time() > deadline:
        _cleanup(chat.id)
        return False

    raw = message.text.strip()
    # Keep only numeric characters and normalize Unicode digits to ASCII.
    import unicodedata
    digits = []
    for ch in raw:
        if ch.isdigit():
            try:
                digits.append(str(unicodedata.digit(ch)))
            except (TypeError, ValueError):
                pass
    answer = "".join(digits)
    expected = s["seq"]

    if answer != expected:
        # Do not consume unrelated messages. Only answer if this looks like a
        # sequence attempt (contains at least one digit).
        if answer:
            await message.reply_text(
                f"❌ <b>Not quite!</b> You entered <code>{answer}</code>.\n"
                f"🔢 The sequence has <b>{len(expected)}</b> digits. Try again!",
                parse_mode=constants.ParseMode.HTML,
            )
            return True
        return False

    # Claim the round BEFORE doing database work / network I/O.
    # This prevents the timeout task or another simultaneous correct answer
    # from closing/scoring the same round first.
    sid = s["id"]
    seq = s["seq"]
    s["winner_id"] = update.effective_user.id
    s["finished"] = True
    _cleanup(chat.id)

    _score(update.effective_user, chat.id, sid, 40, "__memory_test__")
    await message.reply_text(
        f"🧠 <b>MEMORY MASTER!</b>\n\n"
        f"🎉 <a href='tg://user?id={update.effective_user.id}'>{_name(update.effective_user)}</a> remembered <code>{seq}</code>\n"
        f"🏆 <b>+40 pts</b>",
        parse_mode=constants.ParseMode.HTML,
    )
    return True

async def extra_message(update, context):
    # Each game gets one pass through the same group message handler.
    chat = update.effective_chat
    s = SESSIONS.get(chat.id)
    if not s or update.message is None or not update.message.text:
        return
    if s["type"] == "code":
        await code_message(update, context)
    elif s["type"] == "scramble":
        await scramble_message(update, context)


def register_extra_game_handlers(app):
    app.add_handler(CommandHandler("games", game_menu))
    app.add_handler(CommandHandler("codebreaker", code_cmd))
    app.add_handler(CommandHandler("scramble", scramble_cmd))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CallbackQueryHandler(start_from_callback, pattern=r"^xgame:"))

    # Generic dispatcher remains for Code Breaker and Word Scramble.
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            extra_message,
        ),
        group=-1,
    )
