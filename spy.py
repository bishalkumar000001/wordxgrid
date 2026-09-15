"""Find the Spy — Telegram group social deduction game."""
import asyncio
import random
import uuid
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import TelegramError

import config
import database as db
import spy_db
import wordle_db
import paheli_db

MIN_PLAYERS = 4
MAX_PLAYERS = 15
LOBBY_SECONDS = 90
CLUE_SECONDS = 120
VOTE_SECONDS = 60
FINAL_SECONDS = 30

# Word pairs make clues less obvious and keep rounds varied.
WORDS = [
    "PIZZA", "BURGER", "AIRPORT", "RAILWAY STATION", "BEACH", "SWIMMING POOL",
    "DOCTOR", "DENTIST", "HOTEL", "HOSPITAL", "SCHOOL", "UNIVERSITY",
    "WEDDING", "BIRTHDAY", "FOOTBALL", "CRICKET", "COFFEE", "TEA",
    "DOG", "WOLF", "APPLE", "ORANGE", "MOVIE", "THEATRE", "RESTAURANT",
    "KITCHEN", "MOUNTAIN", "FOREST", "CAR", "BUS", "AIRPLANE", "TRAIN",
    "PHONE", "COMPUTER", "MUSIC", "CONCERT", "SUMMER", "WINTER", "CHOCOLATE",
]


def name_of(p):
    return p.get("name") or f"User{p['user_id']}"


def mention(p):
    return f"<a href='tg://user?id={p['user_id']}'>{html.escape(name_of(p))}</a>"


def lobby_kb(game_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Join Game", callback_data=f"spy:join:{game_id}"),
         InlineKeyboardButton("🚪 Leave", callback_data=f"spy:leave:{game_id}")],
        [InlineKeyboardButton("▶️ Start Game", callback_data=f"spy:start:{game_id}")],
        [InlineKeyboardButton("🛑 Stop Game", callback_data=f"spy:stop:{game_id}")],
    ])


def vote_kb(game):
    players = game["players"]
    rows = []
    row = []
    for p in players:
        row.append(InlineKeyboardButton(f"👤 {name_of(p)[:18]}", callback_data=f"spy:vote:{game['game_id']}:{p['user_id']}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def word_kb(game_id):
    # The final word buttons are generated from the actual answer plus decoys,
    # but only the Spy can use them.
    return None


async def cmd_spy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        await update.effective_message.reply_text("🕵️ Find the Spy is played in groups. Add me to a group and use /spy.")
        return
    if (spy_db.get_active_game(chat.id) or db.get_active_game(chat.id) or
            wordle_db.get_active_wordle(chat.id) or paheli_db.get_active_paheli(chat.id)):
        await update.effective_message.reply_text("⚠️ Another game is already running in this group.")
        return
    game_id = str(uuid.uuid4())
    spy_db.create_game(game_id, chat.id, user.id, random.choice(WORDS))
    player = {"user_id": user.id, "name": user.first_name or user.username or f"User{user.id}", "username": user.username or ""}
    spy_db.add_player(game_id, player)
    msg = await update.effective_message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n🕵️ <b>FIND THE SPY</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Players: <b>1/{MAX_PLAYERS}</b>\n"
        f"Minimum: <b>{MIN_PLAYERS}</b> players\n\n"
        "Join using the buttons below.\n"
        "The host can start once enough players have joined.",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=lobby_kb(game_id),
    )
    spy_db.set_message(game_id, msg.message_id)
    context.job_queue.run_once(spy_lobby_timeout, LOBBY_SECONDS, data={"game_id": game_id, "chat_id": chat.id}, name=f"spy_lobby_{game_id}")


async def spy_lobby_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Cancel a lobby that did not reach the minimum player count in time."""
    data = context.job.data or {}
    game = spy_db.get_game(data.get("game_id"))
    if not game or not game.get("active") or game.get("phase") != "lobby":
        return

    if len(game.get("players", [])) < MIN_PLAYERS:
        await _cancel_spy_game(
            context,
            game,
            "⏰ <b>Find the Spy lobby expired.</b>\n\n"
            f"At least {MIN_PLAYERS} players were required. No points were awarded.",
        )


async def cmd_stopgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop whichever game is currently blocking this group."""
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    if not chat or chat.type == "private":
        await message.reply_text("🛑 Use /stopgame inside the group where the game is running.")
        return

    # Only group admins can use the universal stop command.
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status not in ("administrator", "creator"):
            await message.reply_text("❌ Only group admins can use /stopgame.")
            return
    except TelegramError:
        await message.reply_text("❌ I couldn't verify your admin status.")
        return

    # Prefer Find the Spy, then the other games checked by /spy.
    spy_game = spy_db.get_active_game(chat.id)
    if spy_game:
        await _cancel_spy_game(context, spy_game, "🛑 <b>Find the Spy game stopped by an admin.</b>")
        await message.reply_text("✅ Find the Spy has been stopped. No points were awarded.", parse_mode=constants.ParseMode.HTML)
        return

    grid_game = db.get_active_game(chat.id)
    if grid_game:
        # Reuse the existing WordGrid /end implementation so its timers/messages
        # are cleaned up exactly as they are for /end.
        await cmd_end(update, context)
        return

    wordle_game = wordle_db.get_active_wordle(chat.id)
    if wordle_game:
        wordle_db.end_wordle_game(wordle_game["game_id"])
        for job in context.job_queue.get_jobs_by_name(f"wordle_timeout_{wordle_game['game_id']}"):
            job.schedule_removal()
        await message.reply_text("🛑 Wordle has been stopped. No points were awarded.")
        return

    paheli_game = paheli_db.get_active_paheli(chat.id)
    if paheli_game:
        paheli_db.skip_paheli(paheli_game["session_id"])
        for job in context.job_queue.get_jobs_by_name(f"ph_timeout_{paheli_game['session_id']}"):
            job.schedule_removal()
        await message.reply_text("🛑 Paheli has been stopped. No points were awarded.")
        return

    await message.reply_text("ℹ️ No active game was found in this group.")


async def cmd_stopspy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    if not chat or chat.type == "private":
        await message.reply_text("🛑 Use /stopspy inside the group where the Find the Spy game is running.")
        return

    game = spy_db.get_active_game(chat.id)
    if not game:
        await message.reply_text("ℹ️ There is no active Find the Spy game in this group.")
        return

    is_host = user.id == game.get("host_id")
    is_admin = False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        is_admin = member.status in ("administrator", "creator")
    except TelegramError:
        pass

    if not (is_host or is_admin):
        await message.reply_text("❌ Only the game host or a group admin can stop the game.")
        return

    await _cancel_spy_game(context, game, "🛑 <b>Find the Spy game stopped.</b>")
    await message.reply_text("✅ The Find the Spy game has been stopped. No points were awarded.", parse_mode=constants.ParseMode.HTML)


async def cb_spy_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    game = spy_db.get_game(q.data.split(":")[2])
    if not game or not game.get("active"):
        await q.answer("This game is already over.", show_alert=True)
        return

    if q.from_user.id != game.get("host_id"):
        try:
            member = await context.bot.get_chat_member(game["group_id"], q.from_user.id)
            is_admin = member.status in ("administrator", "creator")
        except TelegramError:
            is_admin = False
        if not is_admin:
            await q.answer("Only the host or a group admin can stop the game.", show_alert=True)
            return

    await q.answer("🛑 Stopping game...")
    await _cancel_spy_game(context, game, "🛑 <b>Find the Spy game was stopped by the host/admin.</b>")


async def _cancel_spy_game(context, game, group_message=None):
    if not game or not game.get("active"):
        return
    spy_db.end_game(game["game_id"])
    for prefix in ("spy_lobby_", "spy_clue_", "spy_vote_", "spy_final_"):
        for job in context.job_queue.get_jobs_by_name(prefix + game["game_id"]):
            job.schedule_removal()
    # Remove any temporary final-answer callback tokens for this game.
    for key, value in list(context.application.bot_data.items()):
        if key.startswith("spy_final_") and isinstance(value, dict) and value.get("game_id") == game["game_id"]:
            context.application.bot_data.pop(key, None)
    if group_message:
        try:
            await context.bot.send_message(game["group_id"], group_message, parse_mode=constants.ParseMode.HTML)
        except TelegramError:
            pass


async def cb_spy_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    game = spy_db.get_game(q.data.split(":")[2])
    if not game or not game.get("active") or game.get("phase") != "lobby":
        await q.answer("⏰ This lobby has ended.", show_alert=True); return
    if any(p["user_id"] == q.from_user.id for p in game["players"]):
        await q.answer("You're already in the game!"); return
    if len(game["players"]) >= MAX_PLAYERS:
        await q.answer("The game is full.", show_alert=True); return
    p = {"user_id": q.from_user.id, "name": q.from_user.first_name or q.from_user.username or f"User{q.from_user.id}", "username": q.from_user.username or ""}
    spy_db.add_player(game["game_id"], p)
    await q.answer("✅ You joined!")
    game = spy_db.get_game(game["game_id"])
    names = "\n".join(f"{i}. {mention(x)}" for i, x in enumerate(game["players"], 1))
    try:
        await q.message.edit_text(
            "━━━━━━━━━━━━━━━━━━\n🕵️ <b>FIND THE SPY</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Players: <b>{len(game['players'])}/{MAX_PLAYERS}</b>\n\n{names}\n\n"
            f"{'✅ Enough players — host can start!' if len(game['players']) >= MIN_PLAYERS else f'Need {MIN_PLAYERS - len(game["players"])} more player(s).'}",
            parse_mode=constants.ParseMode.HTML, reply_markup=lobby_kb(game["game_id"]),
        )
    except TelegramError:
        pass


async def cb_spy_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    game = spy_db.get_game(q.data.split(":")[2])
    if not game or game.get("phase") != "lobby":
        await q.answer("This lobby is closed.", show_alert=True); return
    spy_db.remove_player(game["game_id"], q.from_user.id)
    await q.answer("🚪 You left the game.")
    game = spy_db.get_game(game["game_id"])
    names = "\n".join(f"{i}. {mention(x)}" for i, x in enumerate(game["players"], 1)) or "Nobody yet."
    try:
        await q.message.edit_text(f"🕵️ <b>FIND THE SPY</b>\n\n👥 <b>{len(game['players'])}/{MAX_PLAYERS}</b>\n\n{names}", parse_mode=constants.ParseMode.HTML, reply_markup=lobby_kb(game["game_id"]))
    except TelegramError:
        pass


async def cb_spy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    game = spy_db.get_game(q.data.split(":")[2])
    if not game or game.get("phase") != "lobby":
        await q.answer("This game has already started.", show_alert=True); return
    if q.from_user.id != game["host_id"]:
        await q.answer("Only the host can start the game.", show_alert=True); return
    if len(game["players"]) < MIN_PLAYERS:
        await q.answer(f"Need at least {MIN_PLAYERS} players.", show_alert=True); return
    await q.answer("🕵️ Roles are being assigned...")
    await _begin_game(context, game)


async def _begin_game(context, game):
    players = game["players"]
    spy = random.choice(players)
    spy_db.start_game(game["game_id"], spy["user_id"])
    # Try to deliver roles privately. Users must have opened the bot once in Telegram.
    failures = []
    for p in players:
        try:
            if p["user_id"] == spy["user_id"]:
                text = "🕵️ <b>YOU ARE THE SPY!</b>\n\nYou do <b>not</b> know the secret word.\nListen to the clues and blend in.\n\nIf you're voted out, you'll get one final chance to guess the word."
            else:
                text = f"👨‍👩‍👧 <b>YOU ARE A CIVILIAN!</b>\n\n🔑 Secret word: <b>{html.escape(game['word'])}</b>\n\nGive a clue that helps other civilians, but don't make the word too obvious!"
            await context.bot.send_message(p["user_id"], text, parse_mode=constants.ParseMode.HTML)
        except TelegramError:
            failures.append(p)
    if failures:
        # Do not expose who failed to receive which role.
        spy_db.end_game(game["game_id"])
        await context.bot.send_message(game["group_id"], "⚠️ <b>Game cancelled.</b>\n\nEvery player must open the bot in private (tap Start or send /start) so I can send the secret role. Then start /spy again.", parse_mode=constants.ParseMode.HTML)
        return
    names = "\n".join(f"{i}. {mention(p)}" for i, p in enumerate(players, 1))
    await context.bot.send_message(
        game["group_id"],
        "━━━━━━━━━━━━━━━━━━\n🎤 <b>CLUE ROUND</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        "Everyone must give <b>one</b> clue. Don't say the secret word.\n\n" + names +
        "\n\n🗣️ Send your clue with <code>/clue &lt;your clue&gt;</code>.\n"
        f"⏱️ You have {CLUE_SECONDS} seconds.", parse_mode=constants.ParseMode.HTML)
    context.job_queue.run_once(spy_clue_timeout, CLUE_SECONDS, data={"game_id": game["game_id"], "chat_id": game["group_id"]}, name=f"spy_clue_{game['game_id']}")


async def cmd_clue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private": return
    game = spy_db.get_active_game(chat.id)
    if not game or game.get("phase") != "clues": return
    if not context.args:
        await update.effective_message.reply_text("Use: /clue <your one-word or short clue>"); return
    user = update.effective_user
    if not any(p["user_id"] == user.id for p in game["players"]): return
    clue = " ".join(context.args).strip()
    if len(clue) > 80:
        await update.effective_message.reply_text("❌ Keep your clue under 80 characters."); return
    if not spy_db.add_clue(game["game_id"], user.id, user.first_name or user.username or f"User{user.id}", clue):
        await update.effective_message.reply_text("⚠️ You already gave your clue."); return
    game = spy_db.get_game(game["game_id"])
    await update.effective_message.reply_text(f"💬 {html.escape(user.first_name or 'Player')}: <b>{html.escape(clue)}</b>", parse_mode=constants.ParseMode.HTML)
    if len(game["clues"]) >= len(game["players"]):
        await _start_voting(context, game)


async def spy_clue_timeout(context):
    game = spy_db.get_game(context.job.data["game_id"])
    if game and game.get("active") and game.get("phase") == "clues":
        await _start_voting(context, game)


async def _start_voting(context, game):
    # prevent duplicate transition from timeout + last clue
    if game.get("phase") != "clues": return
    spy_db.set_voting(game["game_id"])
    game = spy_db.get_game(game["game_id"])
    lines = ["━━━━━━━━━━━━━━━━━━", "🗳️ <b>VOTING TIME</b>", "━━━━━━━━━━━━━━━━━━", "", "Who do you think is the 🕵️ SPY?", "", "Tap one player below. Your vote is private."]
    if game["clues"]:
        lines += ["", "💬 <b>Clues:</b>"] + [f"• <b>{html.escape(c['name'])}</b>: {html.escape(c['clue'])}" for c in game["clues"]]
    await context.bot.send_message(game["group_id"], "\n".join(lines), parse_mode=constants.ParseMode.HTML, reply_markup=vote_kb(game))
    context.job_queue.run_once(spy_vote_timeout, VOTE_SECONDS, data={"game_id": game["game_id"], "chat_id": game["group_id"]}, name=f"spy_vote_{game['game_id']}")


async def cb_spy_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    game = spy_db.get_game(parts[2])
    if not game or game.get("phase") != "voting":
        await q.answer("Voting is closed.", show_alert=True); return
    voter = q.from_user.id
    if not any(p["user_id"] == voter for p in game["players"]):
        await q.answer("You are not playing.", show_alert=True); return
    target = int(parts[3])
    if target == voter:
        await q.answer("You can't vote for yourself.", show_alert=True); return
    if not any(p["user_id"] == target for p in game["players"]):
        await q.answer("Invalid player.", show_alert=True); return
    if not spy_db.add_vote(game["game_id"], voter, target):
        await q.answer("You already voted!", show_alert=True); return
    await q.answer("✅ Vote recorded")
    game = spy_db.get_game(game["game_id"])
    if len(game.get("votes", {})) >= len(game["players"]):
        await _finish_voting(context, game)


async def spy_vote_timeout(context):
    game = spy_db.get_game(context.job.data["game_id"])
    if game and game.get("active") and game.get("phase") == "voting":
        await _finish_voting(context, game)


async def _finish_voting(context, game):
    if game.get("phase") != "voting": return
    votes = {int(k): int(v) for k, v in game.get("votes", {}).items()}
    counts = {p["user_id"]: 0 for p in game["players"]}
    for target in votes.values(): counts[target] = counts.get(target, 0) + 1
    max_votes = max(counts.values()) if counts else 0
    top = [uid for uid, n in counts.items() if n == max_votes]
    eliminated_id = random.choice(top) if top else None
    eliminated = next((p for p in game["players"] if p["user_id"] == eliminated_id), None)
    spy_id = game["spy_id"]
    result = "\n".join(f"• {mention(p)} — <b>{counts[p['user_id']]}</b> vote(s)" for p in game["players"])
    await context.bot.send_message(game["group_id"], f"🗳️ <b>VOTE RESULTS</b>\n\n{result}\n\n🚨 {mention(eliminated) if eliminated else 'Nobody'} was eliminated!", parse_mode=constants.ParseMode.HTML)
    if eliminated_id == spy_id:
        await _spy_final_chance(context, game)
    else:
        await _end_round(context, game, spy_won=True, reason="The Spy survived the vote!")


async def _spy_final_chance(context, game):
    spy = next(p for p in game["players"] if p["user_id"] == game["spy_id"])
    decoys = random.sample([w for w in WORDS if w != game["word"]], 3)
    options = decoys + [game["word"]]
    random.shuffle(options)
    kb = []
    row = []
    for w in options:
        token = uuid.uuid4().hex[:8]
        row.append(InlineKeyboardButton(w.title(), callback_data=f"spy:final:{game['game_id']}:{token}"))
        context.application.bot_data[f"spy_final_{token}"] = {"game_id": game["game_id"], "word": w, "spy_id": spy["user_id"]}
        if len(row) == 2:
            kb.append(row); row = []
    if row:
        kb.append(row)
    spy_db.set_final_phase(game["game_id"])
    # Never expose the answer choices in the group: only the Spy receives them privately.
    await context.bot.send_message(
        game["group_id"],
        "🧠 <b>FINAL CHANCE!</b>\n\nThe Spy was caught! 🕵️\n"
        "The Spy has one chance to identify the secret word.\n\n"
        "🔐 <b>The Spy has received the answer buttons privately.</b>",
        parse_mode=constants.ParseMode.HTML,
    )
    try:
        await context.bot.send_message(
            spy["user_id"],
            "🧠 <b>FINAL CHANCE</b>\n\nChoose the secret word:",
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )
    except TelegramError:
        await _end_round(context, game, spy_won=False, reason="The Spy could not submit the final guess in time.")
        return
    context.job_queue.run_once(spy_final_timeout, FINAL_SECONDS, data={"game_id": game["game_id"], "chat_id": game["group_id"]}, name=f"spy_final_{game['game_id']}")


async def cb_spy_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    token = q.data.split(":")[-1]
    data = context.application.bot_data.get(f"spy_final_{token}")
    if not data:
        await q.answer("⏰ Final chance expired.", show_alert=True); return
    game = spy_db.get_game(data["game_id"])
    if not game or not game.get("active"):
        await q.answer("Game is over.", show_alert=True); return
    if q.from_user.id != data["spy_id"]:
        await q.answer("❌ Only the Spy can choose.", show_alert=True); return
    spy_db.set_final_guess(game["game_id"], q.from_user.id, data["word"])
    if data["word"] == game["word"]:
        await q.answer("🎯 Correct!", show_alert=True)
        await _end_round(context, spy_db.get_game(game["game_id"]), spy_won=True, reason="The Spy guessed the secret word!")
    else:
        await q.answer("❌ Wrong!", show_alert=True)
        await _end_round(context, spy_db.get_game(game["game_id"]), spy_won=False, reason="The Spy was caught and missed the final guess.")
    context.application.bot_data.pop(f"spy_final_{token}", None)


async def spy_final_timeout(context):
    game = spy_db.get_game(context.job.data["game_id"])
    if game and game.get("active") and game.get("phase") == "final":
        await _end_round(context, game, spy_won=False, reason="The Spy ran out of time.")


async def _end_round(context, game, spy_won: bool, reason: str):
    # Idempotence guard.
    if not game or not game.get("active"): return
    spy_db.end_game(game["game_id"])
    for prefix in ("spy_lobby_", "spy_clue_", "spy_vote_", "spy_final_"):
        for job in context.job_queue.get_jobs_by_name(prefix + game["game_id"]): job.schedule_removal()
    spy_id = game["spy_id"]
    players = game["players"]
    spy_points = 500 if spy_won else 0
    civilian_points = 0 if spy_won else 100
    for p in players:
        won = (p["user_id"] == spy_id and spy_won) or (p["user_id"] != spy_id and not spy_won)
        spy_db.update_stats(p["user_id"], won, p["user_id"] == spy_id, spy_points if p["user_id"] == spy_id else civilian_points)
    outcome = "🕵️ <b>SPY WINS!</b>" if spy_won else "👨‍👩‍👧 <b>CIVILIANS WIN!</b>"
    await context.bot.send_message(game["group_id"], f"━━━━━━━━━━━━━━━━━━\n{outcome}\n━━━━━━━━━━━━━━━━━━\n\n{html.escape(reason)}\n\n🕵️ Spy: {mention(next(p for p in players if p['user_id'] == spy_id))}\n🔑 Secret word: <b>{html.escape(game['word'])}</b>\n\n💰 {'Spy +500 points' if spy_won else 'Each civilian +100 points'}\n\nUse /spy to play again.\n📊 Use /spystats to view your personal stats.", parse_mode=constants.ParseMode.HTML)


async def cmd_spystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = spy_db.get_stats(update.effective_user.id)
    await update.effective_message.reply_text(
        "🕵️ <b>Your Find the Spy Stats</b>\n\n"
        f"🎮 Games Played: <b>{s.get('games_played',0)}</b>\n"
        f"🏆 Games Won: <b>{s.get('wins',0)}</b>\n"
        f"❌ Games Lost: <b>{s.get('losses',0)}</b>\n\n"
        f"🕵️ Times as Spy: <b>{s.get('spy_games',0)}</b>\n"
        f"😈 Spy Wins: <b>{s.get('spy_wins',0)}</b>\n"
        f"👨‍👩‍👧 Civilian Games: <b>{s.get('civilian_games',0)}</b>\n"
        f"✅ Civilian Wins: <b>{s.get('civilian_wins',0)}</b>\n\n"
        f"💰 Total Points: <b>{s.get('total_points',0)}</b>\n"
        f"🔥 Current Win Streak: <b>{s.get('current_streak',0)}</b>\n"
        f"🏅 Best Win Streak: <b>{s.get('best_streak',0)}</b>", parse_mode=constants.ParseMode.HTML)


def register_spy_handlers(app: Application):
    spy_db.init_spy_db()
    app.add_handler(CommandHandler("spy", cmd_spy))
    app.add_handler(CommandHandler("clue", cmd_clue))
    app.add_handler(CommandHandler("spystats", cmd_spystats))
    app.add_handler(CommandHandler("stopspy", cmd_stopspy))
    app.add_handler(CommandHandler("stopgame", cmd_stopgame))
    app.add_handler(CallbackQueryHandler(cb_spy_join, pattern=r"^spy:join:"))
    app.add_handler(CallbackQueryHandler(cb_spy_leave, pattern=r"^spy:leave:"))
    app.add_handler(CallbackQueryHandler(cb_spy_start, pattern=r"^spy:start:"))
    app.add_handler(CallbackQueryHandler(cb_spy_stop, pattern=r"^spy:stop:"))
    app.add_handler(CallbackQueryHandler(cb_spy_vote, pattern=r"^spy:vote:"))
    app.add_handler(CallbackQueryHandler(cb_spy_final, pattern=r"^spy:final:"))
