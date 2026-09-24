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

# One independent session per game type and group. Different games can run
# simultaneously in the same group (e.g. Code Breaker + Scramble + Memory).
SESSIONS = {}  # (chat_id, game_type) -> session

def _get_session(chat_id, game_type):
    return SESSIONS.get((chat_id, game_type))

def _set_session(chat_id, game_type, session):
    SESSIONS[(chat_id, game_type)] = session

def _cleanup(chat_id, game_type):
    return SESSIONS.pop((chat_id, game_type), None)


def _name(user):
    return user.first_name or user.username or f"User{user.id}"


def _score(user, chat_id, game_id, points, label):
    db.upsert_user(user.id, user.username or "", user.first_name or "", user.last_name or "")
    db.add_score(user.id, chat_id, game_id, label, points)



def _menu(chat_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Code Breaker", callback_data=f"xgame:code:{chat_id}"),
         InlineKeyboardButton("🔤 Word Scramble", callback_data=f"xgame:scramble:{chat_id}")],
        [InlineKeyboardButton("🧠 Memory Test", callback_data=f"xgame:memory:{chat_id}")],
    ])


def _play_again_markup(chat_id, game_type, digits=None):
    """Return the single-tap Play Again button for a finished mini-game."""
    if game_type == "code":
        callback = f"xagain:code:{chat_id}:{digits or 4}"
    else:
        callback = f"xagain:{game_type}:{chat_id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Play Again", callback_data=callback)]
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
        await start_scramble(context, q.message.chat)
    else:
        await start_memory(context.bot, q.message.chat)


async def play_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the same mini-game again from its finished-round button."""
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    if len(parts) < 3:
        return

    kind = parts[1]
    chat_id = int(parts[2])
    if q.message is None or q.message.chat.id != chat_id:
        return
    if q.message.chat.type == "private":
        await q.answer("Ye games groups ke liye hain 😄", show_alert=True)
        return

    if kind == "code":
        digits = 4
        if len(parts) >= 4 and parts[3].isdigit():
            digits = max(3, min(6, int(parts[3])))
        await start_code(context.bot, q.message.chat, digits=digits)
    elif kind == "scramble":
        await start_scramble(context, q.message.chat)
    elif kind == "memory":
        await start_memory(context.bot, q.message.chat)

async def code_cmd(update, context):
    if update.effective_chat.type == "private":
        return await update.message.reply_text("⚠️ Group mein Code Breaker khelo!")
    digits = 4
    if context.args and context.args[0].isdigit() and 3 <= int(context.args[0]) <= 6:
        digits = int(context.args[0])
    await start_code(context.bot, update.effective_chat, digits)

async def start_code(bot, chat, digits=4):
    old = _get_session(chat.id, "code")
    if old:
        await bot.send_message(chat.id, "⚠️ A Code Breaker round is already running here. Finish it before starting another Code Breaker round!")
        return

    # Wordle-style Code Breaker: repeated digits are allowed and every position
    # receives its own tile-style clue after each guess.
    code = ''.join(random.choice(string.digits) for _ in range(digits))
    sid = "code-" + uuid.uuid4().hex[:10]
    _set_session(chat.id, "code", {
        "type": "code", "id": sid, "code": code, "attempts": 0,
        "max_attempts": 10, "digits": digits, "started": asyncio.get_running_loop().time(),
        "history": []
    })

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
    session = _get_session(chat.id, "code")
    if not session:
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
        _cleanup(chat.id, "code")
        _score(update.effective_user, chat.id, sid, points, "__code_breaker__")
        await update.message.reply_text(
            f"🔓 <b>CODE CRACKED!</b>\n\n"
            f"<code>{board}</code>\n\n"
            f"🎉 <a href='tg://user?id={update.effective_user.id}'>{_name(update.effective_user)}</a> found the code!\n"
            f"🔢 Attempts: <b>{attempts}/10</b>\n"
            f"🏆 Reward: <b>+{points} pts</b>",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_play_again_markup(chat.id, "code", session["digits"]),
        )
        return

    board = _code_board(session)
    remaining = session["max_attempts"] - session["attempts"]
    exact = marks.count("🟩")
    misplaced = marks.count("🟨")
    absent = marks.count("⬛")

    if remaining <= 0:
        sid = session["id"]
        _cleanup(chat.id, "code")
        await update.message.reply_text(
            f"💥 <b>CODE LOCKED!</b>\n\n"
            f"<code>{board}</code>\n\n"
            f"🔐 The code was <code>{code}</code>\n"
            f"No points this round. Try again! 😈",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_play_again_markup(chat.id, "code", session["digits"]),
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
        await start_scramble(context, update.effective_chat)

async def start_scramble(context, chat):
    bot = context.bot
    if _get_session(chat.id, "scramble"):
        await bot.send_message(chat.id, "⚠️ A Word Scramble round is already running here. Finish it before starting another Scramble round!")
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
    _set_session(chat.id, "scramble", {
        "type": "scramble",
        "id": sid,
        "word": word,
        "task": None,
    })
    await bot.send_message(
        chat.id,
        f"❝ <b>🔤 WORD SCRAMBLE</b> ❞\n\n"
        f"<blockquote>🧩 Unscramble this:\n\n<b>{' '.join(scrambled)}</b>\n\n"
        f"⚡ First correct answer wins <b>30 pts</b>!\n"
        f"⏱️ Time limit: <b>2 minutes</b></blockquote>",
        parse_mode=constants.ParseMode.HTML,
    )

    # Schedule the timeout through PTB's JobQueue.
    session = _get_session(chat.id, "scramble")
    if session and context.job_queue is not None:
        session["job"] = context.job_queue.run_once(
            _scramble_timeout_job,
            120,
            data={"chat_id": chat.id, "sid": sid, "word": word},
            name=f"scramble:{chat.id}:{sid}",
        )

async def _scramble_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    sid = data["sid"]
    word = data["word"]

    session = _get_session(chat_id, "scramble")
    if not session or session.get("id") != sid:
        return

    _cleanup(chat_id, "scramble")
    try:
        await context.bot.send_message(
            chat_id,
            f"⏰ <b>SCRAMBLE ENDED!</b>\n\n"
            f"😔 Time's up! Nobody solved the scramble.\n"
            f"✅ The correct word was <b>{word}</b>.\n\n"
            f"🎮 Tap below or use <code>/scramble</code> to play again.",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_play_again_markup(chat_id, "scramble"),
        )
    except TelegramError:
        pass

async def scramble_message(update, context):
    chat = update.effective_chat
    s = _get_session(chat.id, "scramble")
    if not s:
        return
    answer = update.message.text.strip().upper().replace(" ", "")
    if answer != s["word"]:
        return
    sid = s["id"]
    word = s["word"]
    job = s.get("job")
    if job:
        try:
            job.schedule_removal()
        except Exception:
            pass
    _cleanup(chat.id, "scramble")
    _score(update.effective_user, chat.id, sid, 30, "__word_scramble__")
    await update.message.reply_text(
        f"🎯 <b>SCRAMBLED!</b>\n\n🏆 <a href='tg://user?id={update.effective_user.id}'>{_name(update.effective_user)}</a> got <b>{word}</b> first!\n💰 <b>+30 pts</b>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_play_again_markup(chat.id, "scramble"),
    )

async def memory_cmd(update, context):
    if update.effective_chat.type != "private":
        await start_memory(context.bot, update.effective_chat)

async def start_memory(bot, chat):
    """Start a Memory Test without blocking Telegram's update loop.

    IMPORTANT: never await the 5s + 20s timers from the command handler itself.
    The previous implementation did that, which blocked update processing and
    caused users' answers to be delivered only after the round had timed out.
    """
    if _get_session(chat.id, "memory"):
        await bot.send_message(chat.id, "⚠️ A Memory Test round is already running here. Finish it before starting another Memory Test!")
        return

    length = random.randint(6, 9)
    seq = ''.join(random.choice(string.digits) for _ in range(length))
    sid = "memory-" + uuid.uuid4().hex[:10]
    _set_session(chat.id, "memory", {
        "type": "memory",
        "id": sid,
        "seq": seq,
        "visible": True,
        "finished": False,
        "deadline": None,
    })

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
        s = _get_session(chat_id, "memory")
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
        s = _get_session(chat_id, "memory")
        if s and s.get("id") == sid and not s.get("finished"):
            _cleanup(chat_id, "memory")
            await bot.send_message(
                chat_id,
                f"⏰ <b>Memory round over!</b>\n"
                f"The sequence was <code>{seq}</code>.\n\n"
                f"🎮 Tap below or use <code>/memory</code> to play again.",
                parse_mode=constants.ParseMode.HTML,
                reply_markup=_play_again_markup(chat_id, "memory"),
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

    s = _get_session(chat.id, "memory")
    if not s or s.get("visible"):
        return False

    # The answer window starts only after the sequence is hidden.
    # Use a monotonic deadline so a late/queued message cannot win.
    deadline = s.get("deadline")
    if deadline is None:
        return False
    if asyncio.get_running_loop().time() > deadline:
        _cleanup(chat.id, "memory")
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
    _cleanup(chat.id, "memory")

    _score(update.effective_user, chat.id, sid, 40, "__memory_test__")
    await message.reply_text(
        f"🧠 <b>MEMORY MASTER!</b>\n\n"
        f"🎉 <a href='tg://user?id={update.effective_user.id}'>{_name(update.effective_user)}</a> remembered <code>{seq}</code>\n"
        f"🏆 <b>+40 pts</b>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_play_again_markup(chat.id, "memory"),
    )
    return True

async def extra_message(update, context):
    # Each game gets one pass through the same group message handler.
    chat = update.effective_chat
    if update.message is None or not update.message.text:
        return

    # Each game has its own session. A group can therefore run Code Breaker,
    # Word Scramble and Memory Test at the same time.
    if _get_session(chat.id, "code"):
        await code_message(update, context)
    if _get_session(chat.id, "scramble"):
        await scramble_message(update, context)


def register_extra_game_handlers(app):
    app.add_handler(CommandHandler("games", game_menu))
    app.add_handler(CommandHandler("codebreaker", code_cmd))
    app.add_handler(CommandHandler("scramble", scramble_cmd))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CallbackQueryHandler(start_from_callback, pattern=r"^xgame:"))
    app.add_handler(CallbackQueryHandler(play_again_callback, pattern=r"^xagain:"))

    # Generic dispatcher remains for Code Breaker and Word Scramble.
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            extra_message,
        ),
        group=-1,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Word Chain
# ─────────────────────────────────────────────────────────────────────────────

async def chain_cmd(update, context):
    if update.effective_chat.type != "private":
        await start_chain(context, update.effective_chat)

async def start_chain(context, chat):
    if _get_session(chat.id, "chain"):
        await context.bot.send_message(chat.id, "⚠️ A Word Chain round is already running here. Finish it before starting another round!")
        return

    # Prefer 4–8 letter words so the game stays fast and readable.
    pool = []
    for length in (4, 5, 6, 7, 8):
        pool.extend(WORDS_BY_LENGTH.get(length, []))
    word = random.choice(pool).upper()
    sid = "chain-" + uuid.uuid4().hex[:10]
    _set_session(chat.id, "chain", {
        "type": "chain", "id": sid, "word": word,
        "used": {word}, "last_user": None, "streak": 0,
    })
    await context.bot.send_message(
        chat.id,
        f"❝ <b>🔤 WORD CHAIN</b> ❞\n\n"
        f"Start with: <b>{word}</b>\n\n"
        f"🔗 Next word must start with <b>{word[-1]}</b>.\n"
        f"⚡ First valid answer wins <b>10 pts</b>!\n"
        f"🚫 No repeated words.\n\n"
        f"Example: <b>{word}</b> → <b>{word[-1]}...</b>",
        parse_mode=constants.ParseMode.HTML,
    )

async def chain_message(update, context):
    chat = update.effective_chat
    s = _get_session(chat.id, "chain")
    if not s or not update.message or not update.message.text:
        return False

    answer = update.message.text.strip().upper()
    # Telegram messages can contain spaces/punctuation; only accept a clean word.
    if not answer.isalpha() or len(answer) < 3 or len(answer) > 20:
        return False
    if answer in s["used"]:
        return False
    if answer[0] != s["word"][-1]:
        return False

    # Our bundled word list is the game's dictionary.
    valid_words = None
    for length in range(3, 21):
        if length in WORDS_BY_LENGTH:
            valid_words = valid_words or set()
            valid_words.update(WORDS_BY_LENGTH[length])
    if answer not in valid_words:
        return False

    previous = s["word"]
    s["word"] = answer
    s["used"].add(answer)
    s["last_user"] = update.effective_user.id
    s["streak"] += 1
    points = 10 + min(20, (s["streak"] - 1) * 2)
    _score(update.effective_user, chat.id, s["id"], points, "__word_chain__")

    await update.message.reply_text(
        f"🔗 <b>{previous}</b> → <b>{answer}</b> ✅\n\n"
        f"🎯 Next letter: <b>{answer[-1]}</b>\n"
        f"🔥 Chain: <b>{s['streak']}</b>\n"
        f"🏆 <a href='tg://user?id={update.effective_user.id}'>{_name(update.effective_user)}</a> +<b>{points} pts</b>",
        parse_mode=constants.ParseMode.HTML,
    )
    return True

# ─────────────────────────────────────────────────────────────────────────────
# Higher or Lower — playing-card version
# ─────────────────────────────────────────────────────────────────────────────

_CARD_RANKS = list(range(2, 15))  # 11=J, 12=Q, 13=K, 14=A
_CARD_LABELS = {11: "J", 12: "Q", 13: "K", 14: "A"}
_SUITS = ["♠️", "♥️", "♦️", "♣️"]


def _card_text(card):
    rank, suit = card
    return f"{_CARD_LABELS.get(rank, str(rank))}{suit}"


def _new_card(previous=None):
    choices = [(r, s) for r in _CARD_RANKS for s in _SUITS]
    if previous:
        choices = [c for c in choices if c != previous]
    return random.choice(choices)


def _higher_lower_markup(chat_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬆️ HIGHER", callback_data=f"hl:high:{chat_id}"),
        InlineKeyboardButton("⬇️ LOWER", callback_data=f"hl:low:{chat_id}"),
    ], [
        InlineKeyboardButton("🛑 End", callback_data=f"hl:end:{chat_id}"),
    ]])

async def higherlower_cmd(update, context):
    if update.effective_chat.type != "private":
        await start_higherlower(context.bot, update.effective_chat)

async def start_higherlower(bot, chat):
    if _get_session(chat.id, "higherlower"):
        await bot.send_message(chat.id, "⚠️ A Higher or Lower round is already running here.")
        return
    card = _new_card()
    sid = "higherlower-" + uuid.uuid4().hex[:10]
    _set_session(chat.id, "higherlower", {
        "type": "higherlower", "id": sid, "card": card,
        "streak": 0, "round": 1,
    })
    await bot.send_message(
        chat.id,
        f"❝ <b>🃏 HIGHER OR LOWER</b> ❞\n\n"
        f"Current card: <b>{_card_text(card)}</b>\n\n"
        f"Will the next card be higher or lower?\n"
        f"🔥 Streak: <b>0</b>\n\n"
        f"Choose a button below:",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=_higher_lower_markup(chat.id),
    )

async def higherlower_callback(update, context):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    if len(parts) != 3:
        return
    choice, chat_id = parts[1], int(parts[2])
    if q.message is None or q.message.chat.id != chat_id:
        return
    s = _get_session(chat_id, "higherlower")
    if not s:
        await q.answer("This round has ended.", show_alert=True)
        return

    if choice == "end":
        streak = s["streak"]
        _cleanup(chat_id, "higherlower")
        await q.edit_message_text(
            f"🛑 <b>HIGHER OR LOWER ENDED</b>\n\n"
            f"🔥 Final streak: <b>{streak}</b>\n\n"
            f"Play another round below!",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_play_again_markup(chat_id, "higherlower"),
        )
        return

    old = s["card"]
    new = _new_card(old)
    old_rank, new_rank = old[0], new[0]
    correct = (choice == "high" and new_rank > old_rank) or (choice == "low" and new_rank < old_rank)
    # With a deck, equal rank is neither higher nor lower and counts as a miss.
    if correct:
        s["streak"] += 1
        points = 10 + min(90, (s["streak"] - 1) * 5)
        _score(q.from_user, chat_id, s["id"], points, "__higher_lower__")
        s["card"] = new
        s["round"] += 1
        await q.edit_message_text(
            f"🃏 <b>HIGHER OR LOWER</b>\n\n"
            f"Previous: <b>{_card_text(old)}</b>\n"
            f"Next: <b>{_card_text(new)}</b>\n\n"
            f"✅ <b>Correct!</b>\n"
            f"🔥 Streak: <b>{s['streak']}</b>\n"
            f"🏆 <a href='tg://user?id={q.from_user.id}'>{_name(q.from_user)}</a> +<b>{points} pts</b>\n\n"
            f"Will the next card be higher or lower?",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_higher_lower_markup(chat_id),
        )
    else:
        streak = s["streak"]
        _cleanup(chat_id, "higherlower")
        await q.edit_message_text(
            f"💥 <b>WRONG!</b>\n\n"
            f"Previous: <b>{_card_text(old)}</b>\n"
            f"Next: <b>{_card_text(new)}</b>\n\n"
            f"❌ You chose <b>{'HIGHER' if choice == 'high' else 'LOWER'}</b>.\n"
            f"🔥 Final streak: <b>{streak}</b>\n\n"
            f"Try again!",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=_play_again_markup(chat_id, "higherlower"),
        )

# Extend the existing dispatcher for the new games.
_original_extra_message = extra_message
async def extra_message(update, context):
    chat = update.effective_chat
    if update.message is None or not update.message.text:
        return
    if _get_session(chat.id, "chain"):
        handled = await chain_message(update, context)
        if handled:
            return
    await _original_extra_message(update, context)

# Extend the existing registration function while preserving all old handlers.
_original_register_extra_game_handlers = register_extra_game_handlers
def register_extra_game_handlers(app):
    _original_register_extra_game_handlers(app)
    app.add_handler(CommandHandler("chain", chain_cmd))
    app.add_handler(CommandHandler("higherlower", higherlower_cmd))
    app.add_handler(CommandHandler("hl", higherlower_cmd))
    app.add_handler(CallbackQueryHandler(higherlower_callback, pattern=r"^hl:"))
    app.add_handler(CallbackQueryHandler(play_again_callback, pattern=r"^xagain:higherlower:"))

# Add Higher/Lower support to the existing Play Again callback.
_original_play_again_callback = play_again_callback
async def play_again_callback(update, context):
    q = update.callback_query
    if q.data.startswith("xagain:higherlower:"):
        await q.answer()
        parts = q.data.split(":")
        if len(parts) >= 3:
            chat_id = int(parts[2])
            if q.message and q.message.chat.id == chat_id:
                await start_higherlower(context.bot, q.message.chat)
        return
    await _original_play_again_callback(update, context)

# Re-registering below uses the wrapper above through the original function's
# global lookup. The specific callback is intentionally kept for clarity.
