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
    # Food & drinks
    "PIZZA", "BURGER", "PASTA", "SANDWICH", "TACO", "NOODLES", "BREAD", "CHEESE", "CAKE", "ICE CREAM",
    "CHOCOLATE", "COOKIE", "BISCUIT", "DONUT", "FRIES", "NUGGETS", "STEAK", "CHICKEN", "RICE", "CURRY",
    "SOUP", "SALAD", "POTATO", "TOMATO", "APPLE", "ORANGE", "BANANA", "MANGO", "GRAPE", "WATERMELON",
    "COFFEE", "TEA", "MILK", "JUICE", "LEMONADE", "CAPPUCCINO", "LATTE", "SMOOTHIE", "Pancake".upper(), "WAFFLE",
    "HOT DOG", "WRAP", "BIRYANI", "SUSHI", "DUMPLINGS", "POPCORN", "PIZZA SHOP", "RESTAURANT", "CAFE", "BAKERY",

    # Places & travel
    "AIRPORT", "RAILWAY STATION", "BUS STATION", "BEACH", "SWIMMING POOL", "HOTEL", "HOSPITAL", "SCHOOL", "UNIVERSITY",
    "MOUNTAIN", "FOREST", "PARK", "LAKE", "RIVER", "ISLAND", "DESERT", "JUNGLE", "MUSEUM", "LIBRARY", "CINEMA",
    "THEATRE", "STADIUM", "GYM", "MARKET", "MALL", "SUPERMARKET", "BANK", "OFFICE", "FACTORY", "FARM",
    "TEMPLE", "CHURCH", "MOSQUE", "CASTLE", "BRIDGE", "TOWER", "VILLAGE", "CITY", "COUNTRYSIDE", "CAMPING",
    "RESORT", "HOSTEL", "METRO", "SUBWAY", "TRAIN", "AIRPLANE", "BUS", "CAR", "TAXI", "BOAT",

    # People, jobs & places of work
    "DOCTOR", "DENTIST", "NURSE", "TEACHER", "STUDENT", "POLICE OFFICER", "FIREFIGHTER", "CHEF", "FARMER", "PILOT",
    "DRIVER", "LAWYER", "ENGINEER", "ARTIST", "SINGER", "ACTOR", "PHOTOGRAPHER", "JOURNALIST", "SCIENTIST", "PROGRAMMER",
    "MECHANIC", "BARBER", "DESIGNER", "ATHLETE", "COACH", "MANAGER", "CASHIER", "WAITER", "SECURITY GUARD", "VET",

    # Animals
    "DOG", "WOLF", "CAT", "FOX", "HORSE", "LION", "TIGER", "BEAR", "ELEPHANT", "MONKEY", "GIRAFFE", "ZEBRA",
    "RABBIT", "PANDA", "SNAKE", "CROCODILE", "DOLPHIN", "WHALE", "SHARK", "EAGLE", "PARROT", "PENGUIN", "OWL",
    "BUTTERFLY", "BEE", "ANT", "SPIDER", "FROG", "TURTLE", "GOAT", "SHEEP", "COW", "CHICKEN",

    # Sports & games
    "FOOTBALL", "CRICKET", "BASKETBALL", "TENNIS", "BADMINTON", "VOLLEYBALL", "BASEBALL", "GOLF", "BOXING", "WRESTLING",
    "SWIMMING", "CYCLING", "RUNNING", "RACING", "SKATING", "SURFING", "CHESS", "CARDS", "DARTS", "BOWLING",

    # Technology & objects
    "PHONE", "COMPUTER", "LAPTOP", "TABLET", "CAMERA", "TELEVISION", "RADIO", "HEADPHONES", "SPEAKER", "KEYBOARD",
    "MOUSE", "SCREEN", "CHARGER", "BATTERY", "INTERNET", "ROBOT", "DRONE", "CLOCK", "WATCH", "CALCULATOR",
    "BOOK", "PEN", "PENCIL", "NOTEBOOK", "BACKPACK", "UMBRELLA", "KEY", "WALLET", "MIRROR", "CHAIR", "TABLE",
    "BED", "DOOR", "WINDOW", "LAMP", "BOTTLE", "CUP", "PLATE", "SPOON", "FORK", "KNIFE",

    # Events & entertainment
    "WEDDING", "BIRTHDAY", "PARTY", "CONCERT", "FESTIVAL", "HOLIDAY", "VACATION", "MOVIE", "MUSIC", "SONG",
    "DANCE", "DRAMA", "COMEDY", "MAGIC SHOW", "CIRCUS", "GAME", "MATCH", "CEREMONY", "ANNIVERSARY", "PICNIC",

    # Nature, weather & time
    "SUMMER", "WINTER", "SPRING", "AUTUMN", "SUN", "MOON", "STAR", "RAIN", "SNOW", "WIND", "CLOUD", "STORM",
    "THUNDER", "LIGHTNING", "FIRE", "ICE", "FLOWER", "TREE", "GRASS", "GARDEN", "SUNSET", "SUNRISE", "OCEAN", "WATERFALL",

    # Everyday concepts
    "MORNING", "NIGHT", "WEEKEND", "MONEY", "GIFT", "SHOPPING", "TRAVEL", "ADVENTURE", "SCHOOL BAG", "HOMEWORK",
    "EXAM", "CLASSROOM", "KITCHEN", "BEDROOM", "BATHROOM", "GARDEN", "PLAYGROUND", "HOSPITALITY", "MEDICINE", "AMBULANCE",
]



FINAL_SIMILAR_WORDS = {
    "PIZZA": ["BURGER", "PASTA", "SANDWICH", "LASAGNA", "TACO", "NOODLES", "BREAD", "CHEESE", "CAKE"],
    "BURGER": ["PIZZA", "SANDWICH", "HOT DOG", "TACO", "PASTA", "WRAP", "FRIES", "NUGGETS", "STEAK"],
    "AIRPORT": ["RAILWAY STATION", "BUS STATION", "TRAIN", "AIRPLANE", "HOTEL", "TAXI", "TERMINAL", "TRAVEL", "LUGGAGE"],
    "RAILWAY STATION": ["AIRPORT", "BUS STATION", "TRAIN", "PLATFORM", "METRO", "TAXI", "HOTEL", "TRAVEL", "LUGGAGE"],
    "BEACH": ["SWIMMING POOL", "SEA", "ISLAND", "RESORT", "MOUNTAIN", "FOREST", "PARK", "LAKE", "RIVER"],
    "SWIMMING POOL": ["BEACH", "SEA", "LAKE", "WATER PARK", "RESORT", "RIVER", "HOTEL", "GYM", "PARK"],
    "DOCTOR": ["DENTIST", "NURSE", "HOSPITAL", "PATIENT", "MEDICINE", "SURGEON", "CLINIC", "THERAPIST", "PHARMACY"],
    "DENTIST": ["DOCTOR", "NURSE", "HOSPITAL", "CLINIC", "TOOTH", "MEDICINE", "SURGEON", "PHARMACY", "PATIENT"],
    "HOTEL": ["HOSPITAL", "RESTAURANT", "RESORT", "HOSTEL", "AIRPORT", "ROOM", "LOBBY", "MOTEL", "APARTMENT"],
    "HOSPITAL": ["DOCTOR", "DENTIST", "CLINIC", "NURSE", "PHARMACY", "PATIENT", "MEDICINE", "AMBULANCE", "HOTEL"],
    "SCHOOL": ["UNIVERSITY", "COLLEGE", "CLASSROOM", "TEACHER", "STUDENT", "LIBRARY", "EXAM", "HOMEWORK", "CAMPUS"],
    "UNIVERSITY": ["SCHOOL", "COLLEGE", "CAMPUS", "STUDENT", "TEACHER", "LIBRARY", "LECTURE", "EXAM", "CLASSROOM"],
    "WEDDING": ["BIRTHDAY", "PARTY", "MARRIAGE", "CEREMONY", "BRIDE", "GROOM", "RING", "DANCE", "CELEBRATION"],
    "BIRTHDAY": ["WEDDING", "PARTY", "CAKE", "GIFT", "CANDLE", "CELEBRATION", "ANNIVERSARY", "INVITATION", "BALLOON"],
    "FOOTBALL": ["CRICKET", "SOCCER", "BASKETBALL", "TENNIS", "STADIUM", "PLAYER", "GOAL", "MATCH", "REFEREE"],
    "CRICKET": ["FOOTBALL", "BASEBALL", "TENNIS", "BASKETBALL", "STADIUM", "PLAYER", "BAT", "BALL", "MATCH"],
    "COFFEE": ["TEA", "MILK", "CAPPUCCINO", "LATTE", "JUICE", "DRINK", "CAFE", "SUGAR", "CHOCOLATE"],
    "TEA": ["COFFEE", "MILK", "JUICE", "DRINK", "CAFE", "SUGAR", "LEMON", "CHOCOLATE", "HERBAL TEA"],
    "DOG": ["WOLF", "CAT", "FOX", "HORSE", "PET", "PUPPY", "LION", "TIGER", "BEAR"],
    "WOLF": ["DOG", "FOX", "LION", "TIGER", "BEAR", "CAT", "HORSE", "ANIMAL", "JACKAL"],
    "APPLE": ["ORANGE", "BANANA", "MANGO", "GRAPE", "PEAR", "PEACH", "FRUIT", "CHERRY", "WATERMELON"],
    "ORANGE": ["APPLE", "BANANA", "MANGO", "LEMON", "GRAPE", "PEACH", "FRUIT", "TANGERINE", "WATERMELON"],
    "MOVIE": ["THEATRE", "CONCERT", "MUSIC", "ACTOR", "FILM", "CINEMA", "SHOW", "SERIES", "TELEVISION"],
    "THEATRE": ["MOVIE", "CONCERT", "CINEMA", "STAGE", "ACTOR", "MUSIC", "SHOW", "OPERA", "DRAMA"],
    "RESTAURANT": ["KITCHEN", "HOTEL", "CAFE", "FOOD", "CHEF", "MENU", "DINNER", "PIZZA", "RESTAURANT"],
    "KITCHEN": ["RESTAURANT", "DINING ROOM", "COOKING", "CHEF", "OVEN", "FRIDGE", "FOOD", "CAFE", "HOTEL"],
    "MOUNTAIN": ["FOREST", "BEACH", "HILL", "VALLEY", "RIVER", "LAKE", "CLIMBING", "SNOW", "ISLAND"],
    "FOREST": ["MOUNTAIN", "JUNGLE", "PARK", "TREE", "RIVER", "LAKE", "ANIMAL", "BEACH", "VALLEY"],
    "CAR": ["BUS", "AIRPLANE", "TRAIN", "TAXI", "TRUCK", "MOTORCYCLE", "VEHICLE", "ROAD", "DRIVER"],
    "BUS": ["CAR", "TRAIN", "AIRPLANE", "TAXI", "TRUCK", "METRO", "VEHICLE", "ROAD", "DRIVER"],
    "AIRPLANE": ["CAR", "TRAIN", "BUS", "AIRPORT", "HELICOPTER", "JET", "FLIGHT", "TRAVEL", "PILOT"],
    "TRAIN": ["AIRPLANE", "CAR", "BUS", "RAILWAY STATION", "METRO", "TRAM", "TRACK", "TRAVEL", "TAXI"],
    "PHONE": ["COMPUTER", "TABLET", "LAPTOP", "CAMERA", "INTERNET", "SMARTPHONE", "CHARGER", "SCREEN", "KEYBOARD"],
    "COMPUTER": ["PHONE", "LAPTOP", "TABLET", "KEYBOARD", "MOUSE", "INTERNET", "SCREEN", "SOFTWARE", "CAMERA"],
    "MUSIC": ["CONCERT", "MOVIE", "SONG", "SINGER", "GUITAR", "DANCE", "RADIO", "PIANO", "THEATRE"],
    "CONCERT": ["MUSIC", "MOVIE", "THEATRE", "SINGER", "BAND", "STAGE", "SONG", "DANCE", "FESTIVAL"],
    "SUMMER": ["WINTER", "SPRING", "AUTUMN", "SUN", "HEAT", "HOLIDAY", "BEACH", "RAIN", "WEATHER"],
    "WINTER": ["SUMMER", "SPRING", "AUTUMN", "SNOW", "COLD", "ICE", "HOLIDAY", "MOUNTAIN", "WEATHER"],
    "CHOCOLATE": ["CAKE", "CANDY", "ICE CREAM", "COFFEE", "COOKIE", "DESSERT", "SUGAR", "BISCUIT", "PIZZA"],
}

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
        [InlineKeyboardButton("❓ How to Play", callback_data=f"spy:help:{game_id}")],
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


async def spy_help_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❝ <b>HOW TO PLAY · FIND THE SPY</b> ❞\n\n<blockquote>"
        "1️⃣ Join with <b>➕ Join Game</b> (you must /start the bot in DM first).\n"
        "2️⃣ Host presses <b>▶️ Start Game</b>.\n"
        "3️⃣ Civilians get the secret word privately; the Spy does not.\n"
        "4️⃣ Everyone must give <b>ONE clue</b> with <code>/clue your clue</code>.\n"
        "   Players who miss the deadline are removed from that game automatically.\n"
        "5️⃣ Vote using the private inline buttons — one vote, no self-vote.\n"
        "6️⃣ Spy has the final guess <b>only if tied for the highest votes</b>.\n"
        "7️⃣ Final guess has <b>20 buttons</b>: 1 correct + 19 decoys.\n\n"
        "🏆 Spy wins if not caught, or if the final guess is correct.\n"
        "👨‍👩‍👧 Civilians win if the Spy is caught and fails the final guess.\n\n"
        "💰 Spy win: +500 | Civilian win: +100 each\n"
        "📊 Stats: /spystats\n</blockquote>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.reply_text(text, parse_mode=constants.ParseMode.HTML)
        except TelegramError:
            pass
    else:
        await update.effective_message.reply_text(text, parse_mode=constants.ParseMode.HTML)


async def cb_spy_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await spy_help_text(update, context)


async def cmd_spy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        await update.effective_message.reply_text("❝ <b>FIND THE SPY</b> ❞\n\n🕵️ This game is designed for groups. Add me to a group and use /spy.")
        return
    # Find the Spy is independent from the other games. Multiple different
    # games may run simultaneously in the same group. Only one Spy round is
    # allowed at a time.
    if spy_db.get_active_game(chat.id):
        await update.effective_message.reply_text(
            "❝ <b>FIND THE SPY</b> ❞\n\n⚠️ A Find the Spy round is already running in this group. Finish it before starting another one.",
            parse_mode=constants.ParseMode.HTML,
        )
        return
    game_id = str(uuid.uuid4())
    spy_db.create_game(game_id, chat.id, user.id, random.choice(WORDS))
    player = {"user_id": user.id, "name": user.first_name or user.username or f"User{user.id}", "username": user.username or ""}
    spy_db.add_player(game_id, player)
    msg = await update.effective_message.reply_text(
        "❝ <b>FIND THE SPY</b> ❞\n\n"
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

    # Require an explicit /start in the bot's private chat.
    # get_chat() is not a valid registration check because Telegram can resolve
    # a user's private chat even if they never started the bot.
    if not spy_db.has_private_started(q.from_user.id):
        await q.answer("⚠️ Start the bot in DM first by sending /start, then join the game.", show_alert=True)
        return

    p = {"user_id": q.from_user.id, "name": q.from_user.first_name or q.from_user.username or f"User{q.from_user.id}", "username": q.from_user.username or ""}
    spy_db.add_player(game["game_id"], p)
    await q.answer("✅ You joined!")
    game = spy_db.get_game(game["game_id"])
    names = "\n".join(f"{i}. {mention(x)}" for i, x in enumerate(game["players"], 1))
    try:
        await q.message.edit_text(
            "❝ <b>FIND THE SPY</b> ❞\n\n"
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
                text = "❝ <b>YOUR SECRET ROLE</b> ❞\n\n<blockquote>🕵️ <b>YOU ARE THE SPY</b>\n\nYou do <b>not</b> know the secret word.\nStudy the clues, stay convincing, and blend in.\n\n✦ If you are tied for the highest votes, you unlock the final guess.</blockquote>"
            else:
                text = f"❝ <b>YOUR SECRET ROLE</b> ❞\n\n<blockquote>👨‍👩‍👧 <b>YOU ARE A CIVILIAN</b>\n\n🔑 <b>Secret word:</b> {html.escape(game['word'])}\n\nGive a clever clue that helps the civilians without making the word too obvious.</blockquote>"
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
        "❝ <b>FIND THE SPY · CLUE ROUND</b> ❞\n\n<blockquote>"
        "Everyone must give <b>one</b> clue or be removed from this game. Don't say the secret word.\n\n" + names +
        "\n\n🗣️ Send your clue with <code>/clue &lt;your clue&gt;</code>.\n"
        f"⏱️ You have {CLUE_SECONDS} seconds.</blockquote>", parse_mode=constants.ParseMode.HTML)
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
    if not game or not game.get("active") or game.get("phase") != "clues":
        return

    clue_user_ids = {clue["user_id"] for clue in game.get("clues", [])}
    missing = [
        player for player in game.get("players", [])
        if player["user_id"] not in clue_user_ids
    ]

    if missing:
        spy_db.remove_players(game["game_id"], missing)
        game = spy_db.get_game(game["game_id"])
        removed_names = ", ".join(mention(player) for player in missing)
        await context.bot.send_message(
            game["group_id"],
            "⚠️ <b>Clue deadline reached.</b>\n\n"
            f"🚪 Removed from this game for not giving a clue: {removed_names}",
            parse_mode=constants.ParseMode.HTML,
        )

        # Missing a required clue also removes the Spy. In that case there is
        # no reason to continue to a vote: the civilians win immediately.
        if game.get("spy_id") in {player["user_id"] for player in missing}:
            await _end_round(
                context,
                game,
                spy_won=False,
                reason="The Spy did not give a clue and was removed from the game.",
            )
            return

        # A single remaining player cannot cast a valid vote. The surviving
        # Spy wins if the missed clues left nobody to challenge them.
        if len(game.get("players", [])) < 2:
            await _end_round(
                context,
                game,
                spy_won=True,
                reason="Too few players remained after the missed-clue removals.",
            )
            return

    await _start_voting(context, game)


async def _start_voting(context, game):
    # prevent duplicate transition from timeout + last clue
    if game.get("phase") != "clues": return
    spy_db.set_voting(game["game_id"])
    game = spy_db.get_game(game["game_id"])
    lines = ["❝ <b>FIND THE SPY · VOTING</b> ❞", "", "<blockquote>", "Who do you think is the 🕵️ <b>SPY</b>?", "", "🔐 Your vote is private.", "✦ Choose one player below.", "</blockquote>"]
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
    spy_id = game["spy_id"]
    spy_votes = counts.get(spy_id, 0)
    spy_is_unique_highest = spy_votes == max_votes and len(top) == 1
    spy_is_tied_highest = spy_votes == max_votes and len(top) > 1

    # If the Spy is uniquely the highest-voted player, the Spy loses immediately.
    # The special final guess is available ONLY when the Spy is tied for the highest votes.
    eliminated_id = random.choice(top) if top else None
    eliminated = next((p for p in game["players"] if p["user_id"] == eliminated_id), None)
    result = "\n".join(f"• {mention(p)} — <b>{counts[p['user_id']]}</b> vote(s)" for p in game["players"])

    if spy_is_unique_highest:
        await context.bot.send_message(
            game["group_id"],
            f"❝ <b>FIND THE SPY · VOTE RESULTS</b> ❞\n\n<blockquote>{result}\n\n🚨 {mention(eliminated)} was eliminated.\n\n❌ The Spy received the highest vote count alone. <b>No final chance.</b></blockquote>",
            parse_mode=constants.ParseMode.HTML,
        )
        await _end_round(context, game, spy_won=False, reason="The Spy received more votes than every other player.")
    elif spy_is_tied_highest:
        await context.bot.send_message(
            game["group_id"],
            f"❝ <b>FIND THE SPY · VOTE RESULTS</b> ❞\n\n<blockquote>{result}\n\n⚖️ The Spy is tied for the highest votes.\n\n🎯 <b>Special final chance unlocked.</b></blockquote>",
            parse_mode=constants.ParseMode.HTML,
        )
        await _spy_final_chance(context, game)
    else:
        await context.bot.send_message(
            game["group_id"],
            f"❝ <b>FIND THE SPY · VOTE RESULTS</b> ❞\n\n<blockquote>{result}\n\n🚨 {mention(eliminated) if eliminated else 'Nobody'} was eliminated.</blockquote>",
            parse_mode=constants.ParseMode.HTML,
        )
        await _end_round(context, game, spy_won=True, reason="The Spy was not the highest-voted player and survived the vote!")


async def _spy_final_chance(context, game):
    spy = next(p for p in game["players"] if p["user_id"] == game["spy_id"])
    answer = game["word"]
    decoy_pool = list(FINAL_SIMILAR_WORDS.get(answer, []))
    if len(decoy_pool) < 19:
        fallback = [w for w in WORDS if w != answer and w not in decoy_pool]
        decoy_pool.extend(fallback)
    # Exactly 20 choices: the real word + 19 decoys.
    # Prefer related words, then fill from the game word pool if needed.
    decoys = random.sample(decoy_pool, 19)
    options = decoys + [answer]
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
            "🧠 <b>FINAL CHANCE</b>\n\nChoose the secret word from 20 similar options:",
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
    all_players = players + game.get("removed_players", [])
    spy_player = next((p for p in all_players if p["user_id"] == spy_id), None)
    spy_points = 500 if spy_won else 0
    civilian_points = 0 if spy_won else 100
    for p in players:
        won = (p["user_id"] == spy_id and spy_won) or (p["user_id"] != spy_id and not spy_won)
        spy_db.update_stats(p["user_id"], won, p["user_id"] == spy_id, spy_points if p["user_id"] == spy_id else civilian_points)
    outcome = "🕵️ <b>SPY WINS!</b>" if spy_won else "👨‍👩‍👧 <b>CIVILIANS WIN!</b>"
    spy_name = mention(spy_player) if spy_player else f"<code>{spy_id}</code>"
    await context.bot.send_message(game["group_id"], f"━━━━━━━━━━━━━━━━━━\n{outcome}\n━━━━━━━━━━━━━━━━━━\n\n{html.escape(reason)}\n\n🕵️ Spy: {spy_name}\n🔑 Secret word: <b>{html.escape(game['word'])}</b>\n\n💰 {'Spy +500 points' if spy_won else 'Each civilian +100 points'}\n\nUse /spy to play again.\n📊 Use /spystats to view your personal stats.", parse_mode=constants.ParseMode.HTML)


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
    app.add_handler(CommandHandler("spyhelp", spy_help_text))
    app.add_handler(CommandHandler("clue", cmd_clue))
    app.add_handler(CommandHandler("spystats", cmd_spystats))
    app.add_handler(CommandHandler("stopspy", cmd_stopspy))
    app.add_handler(CommandHandler("stopgame", cmd_stopgame))
    app.add_handler(CallbackQueryHandler(cb_spy_join, pattern=r"^spy:join:"))
    app.add_handler(CallbackQueryHandler(cb_spy_leave, pattern=r"^spy:leave:"))
    app.add_handler(CallbackQueryHandler(cb_spy_start, pattern=r"^spy:start:"))
    app.add_handler(CallbackQueryHandler(cb_spy_stop, pattern=r"^spy:stop:"))
    app.add_handler(CallbackQueryHandler(cb_spy_help, pattern=r"^spy:help:"))
    app.add_handler(CallbackQueryHandler(cb_spy_vote, pattern=r"^spy:vote:"))
    app.add_handler(CallbackQueryHandler(cb_spy_final, pattern=r"^spy:final:"))
