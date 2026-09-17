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
    code = ''.join(random.sample(string.digits, digits))
    sid = "code-" + uuid.uuid4().hex[:10]
    SESSIONS[chat.id] = {"type":"code", "id":sid, "code":code, "attempts":0}
    await bot.send_message(chat.id,
        f"❝ <b>🔐 CODE BREAKER</b> ❞\n\n"
        f"Secret code is <b>{digits} digits</b>. Digits repeat nahi honge.\n"
        f"<blockquote>🎯 Guess type karo: <code>{'•' * digits}</code>\n"
        f"💡 Har wrong guess ke baad clue milega.\n"
        f"🏆 First crack = <b>50 pts</b></blockquote>\n\n"
        f"<i>Example: {'1234'[:digits]}</i>", parse_mode=constants.ParseMode.HTML)
    # 3 minutes
    context = None

async def code_message(update, context):
    chat = update.effective_chat
    session = SESSIONS.get(chat.id)
    if not session or session["type"] != "code":
        return
    text = update.message.text.strip()
    code = session["code"]
    if not text.isdigit() or len(text) != len(code) or len(set(text)) != len(text):
        return
    session["attempts"] += 1
    if text == code:
        attempts = session["attempts"]
        points = max(15, 50 - (attempts - 1) * 5)
        sid = session["id"]
        _cleanup(chat.id)
        _score(update.effective_user, chat.id, sid, points, "__code_breaker__")
        await update.message.reply_text(
            f"🔓 <b>CODE CRACKED!</b>\n\n"
            f"🎉 <a href='tg://user?id={update.effective_user.id}'>{_name(update.effective_user)}</a> found <code>{code}</code>\n"
            f"🔢 Attempts: <b>{attempts}</b>\n🏆 Reward: <b>+{points} pts</b>", parse_mode=constants.ParseMode.HTML)
        return
    exact = sum(a == b for a,b in zip(text, code))
    common = sum((Counter(text) & Counter(code)).values())
    misplaced = common - exact
    absent = len(code) - common
    await update.message.reply_text(
        f"🔐 <b>Clue</b> for <code>{text}</code>\n"
        f"🟢 Right place: <b>{exact}</b>\n"
        f"🟡 Right digit, wrong place: <b>{misplaced}</b>\n"
        f"⚫ Not in code: <b>{absent}</b>", parse_mode=constants.ParseMode.HTML)

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
    if chat.id in SESSIONS:
        await bot.send_message(chat.id, "⚠️ Ek game already chal raha hai. Finish that round first!")
        return
    length = random.randint(6, 9)
    seq = ''.join(random.choice(string.digits) for _ in range(length))
    sid = "memory-" + uuid.uuid4().hex[:10]
    SESSIONS[chat.id] = {"type":"memory", "id":sid, "seq":seq, "visible":True}
    msg = await bot.send_message(chat.id,
        f"❝ <b>🧠 MEMORY TEST</b> ❞\n\n"
        f"Remember this sequence for <b>5 seconds</b>:\n\n"
        f"<code>{' '.join(seq)}</code>\n\n"
        f"👀 Focus! Then type it back exactly.", parse_mode=constants.ParseMode.HTML)
    await asyncio.sleep(5)
    s = SESSIONS.get(chat.id)
    if not s or s.get("id") != sid:
        return
    s["visible"] = False
    try:
        await msg.edit_text(
            "❝ <b>🧠 MEMORY TEST</b> ❞ ❌\n\n"
            "Sequence hidden!\n\n⌨️ <b>Type the sequence now.</b>\n🏆 First correct answer = <b>40 pts</b>",
            parse_mode=constants.ParseMode.HTML)
    except TelegramError:
        pass
    # timeout task
    await asyncio.sleep(45)
    s = SESSIONS.get(chat.id)
    if s and s.get("id") == sid:
        _cleanup(chat.id)
        await bot.send_message(chat.id, f"⏰ <b>Memory round over!</b>\nThe sequence was <code>{seq}</code>.", parse_mode=constants.ParseMode.HTML)

async def memory_message(update, context):
    chat = update.effective_chat
    s = SESSIONS.get(chat.id)
    if not s or s["type"] != "memory" or s.get("visible"):
        return
    answer = ''.join(update.message.text.split())
    if not answer.isdigit() or answer != s["seq"]:
        return
    sid = s["id"]
    seq = s["seq"]
    _cleanup(chat.id)
    _score(update.effective_user, chat.id, sid, 40, "__memory_test__")
    await update.message.reply_text(
        f"🧠 <b>MEMORY MASTER!</b>\n\n"
        f"🎉 <a href='tg://user?id={update.effective_user.id}'>{_name(update.effective_user)}</a> remembered <code>{seq}</code>\n"
        f"🏆 <b>+40 pts</b>", parse_mode=constants.ParseMode.HTML)

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
    elif s["type"] == "memory":
        await memory_message(update, context)


def register_extra_game_handlers(app):
    app.add_handler(CommandHandler("games", game_menu))
    app.add_handler(CommandHandler("codebreaker", code_cmd))
    app.add_handler(CommandHandler("scramble", scramble_cmd))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CallbackQueryHandler(start_from_callback, pattern=r"^xgame:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, extra_message), group=3)
