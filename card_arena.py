"""Velocity Card Arena: collectible cards, confirmed trades, and quick PvP battles."""
import os, random, uuid, logging
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient, DESCENDING
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

log = logging.getLogger(__name__)
_client = None
_cards = None
_players = None
_trades = None
_battles = None

CARD_POOL = [
    {"name":"Ember Fox","rarity":"Common","power":18,"element":"Fire","emoji":"🦊"},
    {"name":"Tide Guardian","rarity":"Common","power":19,"element":"Water","emoji":"🐢"},
    {"name":"Stone Golem","rarity":"Common","power":20,"element":"Earth","emoji":"🗿"},
    {"name":"Storm Hawk","rarity":"Rare","power":28,"element":"Air","emoji":"🦅"},
    {"name":"Frost Witch","rarity":"Rare","power":30,"element":"Ice","emoji":"🧙"},
    {"name":"Thunder Wolf","rarity":"Epic","power":42,"element":"Lightning","emoji":"🐺"},
    {"name":"Abyss Dragon","rarity":"Epic","power":46,"element":"Dark","emoji":"🐉"},
    {"name":"Solar Phoenix","rarity":"Legendary","power":58,"element":"Light","emoji":"🔥"},
    {"name":"Celestial Titan","rarity":"Mythic","power":75,"element":"Cosmic","emoji":"🌌"},
]
RARITY_WEIGHTS = [55, 25, 13, 6, 1]
RARITIES = ["Common", "Rare", "Epic", "Legendary", "Mythic"]


def _init():
    global _client, _cards, _players, _trades, _battles
    if _cards is not None:
        return
    uri = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    database = _client["wordgrid"]
    _cards, _players = database["arena_cards"], database["arena_players"]
    _trades, _battles = database["arena_trades"], database["arena_battles"]
    _cards.create_index([("user_id", 1), ("card_id", 1)], unique=True)
    _players.create_index("user_id", unique=True)
    _trades.create_index("trade_id", unique=True)
    _battles.create_index("battle_id", unique=True)


def _profile(user):
    _init()
    _players.update_one({"user_id": user.id}, {"$set": {"user_id": user.id, "name": user.full_name, "username": user.username or "", "updated_at": datetime.now(timezone.utc)}}, upsert=True)


def _new_card():
    rarity = random.choices(RARITIES, weights=RARITY_WEIGHTS, k=1)[0]
    eligible = [c for c in CARD_POOL if c["rarity"] == rarity]
    if not eligible:
        eligible = [c for c in CARD_POOL if c["rarity"] == "Common"]
    base = random.choice(eligible).copy()
    base.update({"card_id": uuid.uuid4().hex[:8].upper(), "level": 1, "obtained_at": datetime.now(timezone.utc)})
    return base


async def arena(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _profile(update.effective_user)
    await update.effective_message.reply_text(
        "🃏 <b>VELOCITY CARD ARENA</b>\n\n"
        "🎁 /cardpack — Open a pack every 12 hours (3 cards)\n"
        "📚 /mycards — View your collection\n"
        "🔁 Reply to a player's message with /cardtrade CARD_ID to offer a trade\n"
        "⚔️ Reply to a player's message with /cardbattle to challenge them\n"
        "🏆 /cardtop — Collection leaderboard\n\n"
        "Rarity: Common · Rare · Epic · Legendary · Mythic\nBattles are friendly, using card power; no real-money wagers.", parse_mode="HTML")


async def cardpack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    _profile(user)
    _init()
    player = _players.find_one({"user_id": user.id}) or {}
    now = datetime.now(timezone.utc)
    last = player.get("last_pack")
    if last and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last and now - last < timedelta(hours=12):
        remaining = timedelta(hours=12) - (now-last)
        hours, rem = divmod(int(remaining.total_seconds()), 3600)
        await update.effective_message.reply_text(f"⏳ Your next pack is available in {hours}h {rem//60}m.")
        return
    drawn = []
    for _ in range(3):
        card = _new_card()
        _cards.insert_one({"user_id": user.id, **card})
        drawn.append(card)
    _players.update_one({"user_id": user.id}, {"$set": {"last_pack": now}}, upsert=True)
    lines = [f"{c['emoji']} <b>{c['name']}</b> — {c['rarity']} · Power {c['power']} · ID <code>{c['card_id']}</code>" for c in drawn]
    await update.effective_message.reply_text("🎁 <b>PACK OPENED!</b>\n\n"+"\n".join(lines), parse_mode="HTML")


async def mycards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _profile(update.effective_user); _init()
    rows = list(_cards.find({"user_id": update.effective_user.id}).sort("obtained_at", DESCENDING).limit(50))
    if not rows:
        await update.effective_message.reply_text("Your collection is empty. Use /cardpack to open your first pack!")
        return
    lines = [f"{i}. {c.get('emoji','🃏')} <b>{c['name']}</b> [{c['rarity']}] · ⚔️ {c['power']} · ID <code>{c['card_id']}</code>" for i,c in enumerate(rows,1)]
    await update.effective_message.reply_text("📚 <b>YOUR CARDS</b>\n\n"+"\n".join(lines), parse_mode="HTML")


async def cardtrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; user = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("Reply to the player you want to trade with: /cardtrade CARD_ID")
        return
    if not context.args:
        await msg.reply_text("Usage: reply to a player's message with /cardtrade CARD_ID")
        return
    target = msg.reply_to_message.from_user
    if target.id == user.id or target.is_bot:
        await msg.reply_text("Choose another human player to trade with."); return
    _profile(user); _profile(target); _init()
    card = _cards.find_one({"user_id": user.id, "card_id": context.args[0].upper()})
    if not card:
        await msg.reply_text("That card ID isn't in your collection. Check /mycards."); return
    trade_id = uuid.uuid4().hex[:10]
    _trades.insert_one({"trade_id":trade_id,"from_id":user.id,"to_id":target.id,"card_id":card["card_id"],"status":"pending","created_at":datetime.now(timezone.utc)})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Accept trade", callback_data=f"arena:trade:yes:{trade_id}"), InlineKeyboardButton("❌ Decline", callback_data=f"arena:trade:no:{trade_id}")]])
    await msg.reply_text(f"🔁 <b>TRADE OFFER</b>\n{user.mention_html()} offers {card.get('emoji','🃏')} <b>{card['name']}</b> [{card['rarity']}] to {target.mention_html()}.\nOnly the recipient can accept.", reply_markup=kb, parse_mode="HTML")


async def cardbattle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; user = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("Reply to another player's message with /cardbattle to challenge them."); return
    target = msg.reply_to_message.from_user
    if target.id == user.id or target.is_bot:
        await msg.reply_text("Choose another human player."); return
    _profile(user); _profile(target); _init()
    mine = list(_cards.find({"user_id":user.id})); theirs = list(_cards.find({"user_id":target.id}))
    if not mine or not theirs:
        await msg.reply_text("Both players need at least one card. Use /cardpack first!"); return
    battle_id = uuid.uuid4().hex[:10]
    _battles.insert_one({"battle_id":battle_id,"challenger":user.id,"opponent":target.id,"status":"pending","created_at":datetime.now(timezone.utc)})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚔️ Accept battle", callback_data=f"arena:battle:yes:{battle_id}"), InlineKeyboardButton("No thanks", callback_data=f"arena:battle:no:{battle_id}")]])
    await msg.reply_text(f"⚔️ {user.mention_html()} challenges {target.mention_html()} to a card battle!", reply_markup=kb, parse_mode="HTML")


async def arena_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _init()
    parts = q.data.split(":")
    if len(parts) != 4:
        await q.answer("Invalid action", show_alert=True); return
    _, kind, decision, item_id = parts
    if kind == "trade":
        trade = _trades.find_one({"trade_id":item_id,"status":"pending"})
        if not trade:
            await q.answer("This trade has expired or was already handled.", show_alert=True); return
        if q.from_user.id != trade["to_id"]:
            await q.answer("Only the recipient can respond to this trade.", show_alert=True); return
        if decision == "no":
            _trades.update_one({"trade_id":item_id,"status":"pending"},{"$set":{"status":"declined"}})
            await q.edit_message_text("❌ Trade declined."); await q.answer(); return
        card = _cards.find_one({"user_id":trade["from_id"],"card_id":trade["card_id"]})
        if not card:
            _trades.update_one({"trade_id":item_id},{"$set":{"status":"failed"}})
            await q.edit_message_text("Trade cancelled: offered card no longer exists."); await q.answer(); return
        moved = _cards.delete_one({"user_id":trade["from_id"],"card_id":trade["card_id"]})
        if moved.deleted_count:
            card.pop("_id",None); card["user_id"] = trade["to_id"]; card["obtained_at"] = datetime.now(timezone.utc)
            _cards.insert_one(card); _trades.update_one({"trade_id":item_id},{"$set":{"status":"accepted"}})
            await q.edit_message_text(f"✅ Trade complete! {card.get('emoji','🃏')} {card['name']} now belongs to the recipient.")
        else:
            await q.edit_message_text("Trade cancelled; card was already moved.")
        await q.answer(); return
    if kind == "battle":
        battle = _battles.find_one({"battle_id":item_id,"status":"pending"})
        if not battle:
            await q.answer("This battle has expired or was already handled.", show_alert=True); return
        if q.from_user.id != battle["opponent"]:
            await q.answer("Only the challenged player can respond.", show_alert=True); return
        if decision == "no":
            _battles.update_one({"battle_id":item_id,"status":"pending"},{"$set":{"status":"declined"}})
            await q.edit_message_text("Battle declined. No cards or points were lost."); await q.answer(); return
        a = list(_cards.find({"user_id":battle["challenger"]})); b = list(_cards.find({"user_id":battle["opponent"]}))
        if not a or not b:
            await q.edit_message_text("Battle cancelled: both players need at least one card."); await q.answer(); return
        ca, cb = random.choice(a), random.choice(b)
        score_a = ca["power"] + random.randint(0, 10); score_b = cb["power"] + random.randint(0, 10)
        result = "It's a draw!" if score_a == score_b else (f"🏆 Challenger wins with {ca['name']}!" if score_a > score_b else f"🏆 Defender wins with {cb['name']}!")
        _battles.update_one({"battle_id":item_id,"status":"pending"},{"$set":{"status":"complete","score_challenger":score_a,"score_opponent":score_b,"winner":battle['challenger'] if score_a>score_b else battle['opponent'] if score_b>score_a else None}})
        await q.edit_message_text(f"⚔️ <b>CARD BATTLE</b>\n\n{ca.get('emoji','🃏')} {ca['name']}: {score_a} power\nvs\n{cb.get('emoji','🃏')} {cb['name']}: {score_b} power\n\n{result}", parse_mode="HTML")
        await q.answer(); return
    await q.answer("Unknown action", show_alert=True)


async def cardtop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _init()
    pipeline = [{"$group":{"_id":"$user_id","count":{"$sum":1},"power":{"$sum":"$power"}}},{"$sort":{"power":-1,"count":-1}},{"$limit":10}]
    rows = list(_cards.aggregate(pipeline))
    if not rows:
        await update.effective_message.reply_text("No card collections yet. Be the first to use /cardpack!"); return
    lines=[]
    for i,row in enumerate(rows,1):
        p=_players.find_one({"user_id":row["_id"]}) or {}
        lines.append(f"{i}. {p.get('name','Player')} — {row['count']} cards · {row['power']} total power")
    await update.effective_message.reply_text("🏆 <b>CARD ARENA LEADERBOARD</b>\n\n"+"\n".join(lines), parse_mode="HTML")


def register_card_arena_handlers(app):
    app.add_handler(CommandHandler(["cardarena","cardgame"], arena))
    app.add_handler(CommandHandler("cardpack", cardpack))
    app.add_handler(CommandHandler("mycards", mycards))
    app.add_handler(CommandHandler("cardtrade", cardtrade))
    app.add_handler(CommandHandler("cardbattle", cardbattle))
    app.add_handler(CommandHandler("cardtop", cardtop))
    app.add_handler(CallbackQueryHandler(arena_callback, pattern=r"^arena:"))
    log.info("Velocity Card Arena handlers registered")
