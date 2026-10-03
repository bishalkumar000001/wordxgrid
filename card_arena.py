"""Velocity Card Arena: collectible cards, confirmed trades, and quick PvP battles."""
import os, random, uuid, logging, io, math
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient, DESCENDING
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from PIL import Image, ImageDraw, ImageFont

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

RARITY_COLORS = {"Common": (104, 133, 155), "Rare": (48, 139, 222), "Epic": (155, 82, 220), "Legendary": (239, 164, 43), "Mythic": (245, 76, 123)}
ELEMENT_SYMBOLS = {"Fire":"FLAME", "Water":"TIDE", "Earth":"STONE", "Air":"WIND", "Ice":"FROST", "Lightning":"VOLT", "Dark":"VOID", "Light":"SOLAR", "Cosmic":"COSMOS"}

def _font(size, bold=False):
    candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"]
    for path in candidates:
        try: return ImageFont.truetype(path, size)
        except OSError: pass
    return ImageFont.load_default()

def _card_image(card):
    """Render a premium, full-art fantasy trading card locally (no image API needed)."""
    from hashlib import sha256

    w, h = 720, 1000
    rarity = card.get("rarity", "Common")
    accent = RARITY_COLORS.get(rarity, RARITY_COLORS["Common"])
    element = card.get("element", "Cosmic")
    name = card.get("name", "Unknown Card")
    seed = int(sha256((name + element).encode()).hexdigest()[:8], 16)
    # Each element gets its own deep atmospheric palette.
    palettes = {
        "Fire": ((38, 9, 19), (170, 39, 16), (255, 172, 39)),
        "Water": ((5, 24, 52), (12, 106, 155), (91, 225, 245)),
        "Earth": ((22, 29, 24), (75, 105, 56), (192, 185, 112)),
        "Air": ((12, 31, 59), (63, 117, 177), (207, 239, 255)),
        "Ice": ((9, 27, 57), (59, 119, 181), (194, 245, 255)),
        "Lightning": ((22, 13, 54), (91, 50, 166), (255, 229, 96)),
        "Dark": ((15, 8, 29), (73, 19, 66), (255, 68, 113)),
        "Light": ((56, 24, 9), (179, 91, 21), (255, 230, 132)),
        "Cosmic": ((9, 8, 39), (65, 34, 126), (117, 226, 255)),
    }
    top, bottom, glow = palettes.get(element, palettes["Cosmic"])
    im = Image.new("RGB", (w, h), top)
    px = im.load()
    # Dramatic vertical gradient with a bright central aura.
    gx, gy = 360, 435
    for y in range(h):
        ty = y / h
        for x in range(w):
            t = ty * 0.62
            dx, dy = (x-gx)/430, (y-gy)/560
            aura = max(0.0, 1.0 - (dx*dx + dy*dy)) ** 2 * 0.48
            px[x, y] = tuple(max(0, min(255, int(top[i]*(1-t-aura) + bottom[i]*t + glow[i]*aura))) for i in range(3))
    d = ImageDraw.Draw(im, "RGBA")

    # Star field and energy streaks behind the creature.
    rng = random.Random(seed)
    for _ in range(115):
        x, y = rng.randint(48, w-48), rng.randint(170, 790)
        r = rng.choice((1, 2, 2, 3, 4))
        d.ellipse((x-r, y-r, x+r, y+r), fill=(*glow, rng.randint(65, 210)))
    for i in range(11):
        x = 65 + i * 59
        d.line((x, 205, x + rng.randint(-95, 95), 690), fill=(*accent, 28), width=rng.choice((2, 3, 5)))

    # Layered magical circles and a sun/moon halo.
    for r, alpha, width in ((245, 45, 4), (215, 65, 3), (180, 80, 2)):
        d.ellipse((360-r, 425-r, 360+r, 425+r), outline=(*glow, alpha), width=width)
    for angle in range(0, 360, 15):
        rad = math.radians(angle)
        r1, r2 = 218, 240 if angle % 30 == 0 else 228
        d.line((360+math.cos(rad)*r1, 425+math.sin(rad)*r1, 360+math.cos(rad)*r2, 425+math.sin(rad)*r2), fill=(*glow, 135), width=3)

    # Original geometric fantasy-creature illustration. Silhouette changes by creature type.
    creature = "dragon"
    if "Fox" in name: creature = "fox"
    elif "Guardian" in name: creature = "turtle"
    elif "Golem" in name or "Titan" in name: creature = "golem"
    elif "Hawk" in name or "Phoenix" in name: creature = "bird"
    elif "Witch" in name: creature = "mage"
    elif "Wolf" in name: creature = "wolf"
    elif "Dragon" in name: creature = "dragon"

    ink = (13, 16, 34, 238)
    shadow = (8, 10, 25, 210)
    plate = (*accent, 235)
    # Glowing underpainting and broad silhouette shapes.
    d.ellipse((205, 255, 515, 620), fill=(*glow, 30))
    if creature in ("dragon", "bird"):
        # Monumental wings, with a luminous inner membrane.
        left_wing = [(340,370),(230,285),(110,270),(165,365),(85,420),(195,425),(230,500),(305,465)]
        right_wing = [(380,370),(490,285),(610,270),(555,365),(635,420),(525,425),(490,500),(415,465)]
        d.polygon(left_wing, fill=shadow, outline=(*accent,235))
        d.polygon(right_wing, fill=shadow, outline=(*accent,235))
        for points in ([(320,390),(235,315),(165,300),(215,375),(145,407),(245,405)], [(400,390),(485,315),(555,300),(505,375),(575,407),(475,405)]):
            d.line(points, fill=(*glow, 200), width=5, joint="curve")
        if creature == "bird":
            d.polygon([(360,305),(420,385),(395,500),(360,555),(325,500),(300,385)], fill=plate, outline=(255,240,190,255))
            d.polygon([(335,390),(285,465),(335,450),(360,510),(385,450),(435,465),(385,390)], fill=ink, outline=(*glow,240))
            d.polygon([(360,505),(342,560),(360,548),(378,560)], fill=(*glow,235))
            d.polygon([(390,345),(440,360),(397,378)], fill=(*glow,245))
        else:
            d.polygon([(300,375),(330,330),(360,350),(390,330),(420,375),(410,475),(360,535),(310,475)], fill=ink, outline=plate)
            d.polygon([(315,385),(265,355),(286,410),(315,430)], fill=(*accent,210))
            d.polygon([(405,385),(455,355),(434,410),(405,430)], fill=(*accent,210))
            d.polygon([(326,345),(315,290),(350,330)], fill=plate, outline=(*glow,255))
            d.polygon([(394,345),(405,290),(370,330)], fill=plate, outline=(*glow,255))
            d.polygon([(338,388),(351,398),(344,406)], fill=(*glow,255))
            d.polygon([(382,388),(369,398),(376,406)], fill=(*glow,255))
            d.line((360,408,360,448), fill=(*glow,230), width=4)
            d.polygon([(360,535),(398,590),(365,575),(345,600),(322,565)], fill=ink, outline=plate)
    elif creature == "wolf":
        d.polygon([(250,390),(205,315),(290,350),(325,300),(360,350),(395,300),(430,350),(515,315),(470,405),(440,490),(360,545),(280,490)], fill=ink, outline=plate)
        d.polygon([(275,400),(325,420),(345,450),(315,465)], fill=(*accent,200))
        d.polygon([(445,400),(395,420),(375,450),(405,465)], fill=(*accent,200))
        d.polygon([(320,390),(345,405),(329,415)], fill=(*glow,255)); d.polygon([(400,390),(375,405),(391,415)], fill=(*glow,255))
        d.polygon([(345,440),(375,440),(360,460)], fill=(*glow,235))
        d.polygon([(280,475),(235,535),(305,510)], fill=plate); d.polygon([(440,475),(485,535),(415,510)], fill=plate)
    elif creature == "fox":
        d.polygon([(265,365),(225,260),(315,335),(360,320),(405,335),(495,260),(455,365),(430,460),(360,520),(290,460)], fill=ink, outline=plate)
        d.polygon([(260,320),(245,278),(300,342)], fill=(*accent,230)); d.polygon([(460,320),(475,278),(420,342)], fill=(*accent,230))
        d.polygon([(300,400),(345,420),(330,440)], fill=(*glow,255)); d.polygon([(420,400),(375,420),(390,440)], fill=(*glow,255))
        d.polygon([(360,435),(390,458),(360,480),(330,458)], fill=plate)
        for off in (-1,0,1): d.arc((255+off*18,440+abs(off)*15,465+off*18,625+abs(off)*15), 200, 330, fill=(*glow,160), width=7)
    elif creature == "turtle":
        d.ellipse((250,350,470,535), fill=ink, outline=plate, width=7)
        d.polygon([(260,395),(205,365),(220,430),(270,450)], fill=(*accent,230), outline=plate)
        d.polygon([(460,395),(515,365),(500,430),(450,450)], fill=(*accent,230), outline=plate)
        d.polygon([(290,390),(360,340),(430,390),(410,475),(360,505),(310,475)], fill=shadow, outline=(*glow,230), width=5)
        for x,y in ((330,405),(390,405)): d.ellipse((x-9,y-9,x+9,y+9), fill=(*glow,255))
        d.polygon([(335,450),(385,450),(360,475)], fill=plate)
        for x in (295,415): d.polygon([(x,505),(x-15,555),(x+15,545)], fill=ink, outline=plate)
    elif creature == "golem":
        d.polygon([(285,350),(315,300),(360,325),(405,300),(435,350),(420,480),(390,535),(330,535),(300,480)], fill=ink, outline=plate, width=6)
        d.polygon([(300,375),(245,400),(270,470),(315,450)], fill=ink, outline=plate, width=5)
        d.polygon([(420,375),(475,400),(450,470),(405,450)], fill=ink, outline=plate, width=5)
        d.polygon([(325,375),(350,390),(335,405)], fill=(*glow,255)); d.polygon([(395,375),(370,390),(385,405)], fill=(*glow,255))
        d.line((360,415,360,485), fill=(*glow,230), width=9)
        for yy in (440,470): d.line((340,yy,380,yy), fill=(*accent,220), width=5)
    else:  # mystical mage
        d.polygon([(360,285),(405,350),(435,465),(470,555),(250,555),(285,465),(315,350)], fill=ink, outline=plate, width=6)
        d.polygon([(300,350),(360,260),(420,350)], fill=shadow, outline=(*glow,245), width=5)
        d.ellipse((325,350,395,420), fill=(*accent,190), outline=(*glow,255), width=4)
        d.ellipse((347,372,373,398), fill=(*glow,255))
        d.line((360,420,360,505), fill=(*glow,240), width=5)
        d.arc((285,375,435,525), 205, 335, fill=(*glow,210), width=6)

    # Foreground energy shards and card-art floor.
    d.polygon([(80,690),(170,615),(230,690),(300,620),(360,700),(425,620),(500,690),(565,610),(650,690),(650,735),(70,735)], fill=(5,8,24,190), outline=(*accent,100))
    d.line((75,720,645,720), fill=(*glow,210), width=3)

    # Premium frame: layered gold edging, corner flourishes, and title plate.
    gold = (247, 207, 112, 245)
    d.rounded_rectangle((15, 15, w-15, h-15), radius=32, outline=gold, width=5)
    d.rounded_rectangle((27, 27, w-27, h-27), radius=27, outline=(*accent,240), width=4)
    d.rounded_rectangle((42, 42, w-42, h-42), radius=21, outline=(255, 230, 164, 125), width=2)
    # Ornate corner diamonds and side runes.
    for x,y in ((58,58),(w-58,58),(58,h-58),(w-58,h-58)):
        d.polygon([(x,y-17),(x+12,y),(x,y+17),(x-12,y)], fill=(*accent,235), outline=gold)
        d.ellipse((x-4,y-4,x+4,y+4), fill=(255,248,211,255))
    for y in range(225, 690, 62):
        d.polygon([(39,y),(48,y-8),(57,y),(48,y+8)], fill=(*accent,175))
        d.polygon([(w-39,y),(w-48,y-8),(w-57,y),(w-48,y+8)], fill=(*accent,175))

    # Top banner and compact element badge.
    d.rounded_rectangle((52, 52, w-52, 164), radius=19, fill=(8, 12, 30, 220), outline=gold, width=3)
    d.text((72, 66), rarity.upper(), font=_font(28, True), fill=(*accent,255))
    d.text((72, 111), element.upper() + " ELEMENT", font=_font(19, True), fill=(235, 237, 249, 255))
    badge_x, badge_y = w-104, 108
    d.ellipse((badge_x-29,badge_y-29,badge_x+29,badge_y+29), fill=(13,17,36,235), outline=gold, width=3)
    d.ellipse((badge_x-20,badge_y-20,badge_x+20,badge_y+20), outline=(*glow,235), width=3)
    d.text((badge_x-10,badge_y-13), str(min(9, max(1, RARITIES.index(rarity)+1)),), font=_font(26, True), fill=(255,255,255,255))

    # Name and stat plate over the lower artwork.
    d.rounded_rectangle((48, 742, w-48, 948), radius=22, fill=(7, 11, 28, 232), outline=gold, width=3)
    title_font = _font(34 if len(name) < 17 else 27, True)
    d.text((72, 761), name.upper(), font=title_font, fill=(255, 248, 226, 255), stroke_width=1, stroke_fill=(35, 20, 31, 255))
    d.line((72, 810, w-72, 810), fill=(*accent,225), width=3)
    d.text((74, 826), "POWER", font=_font(19, True), fill=(190, 203, 231, 255))
    d.text((72, 849), str(card.get("power", 0)), font=_font(58, True), fill=(*glow,255), stroke_width=2, stroke_fill=(20, 15, 30, 255))
    d.text((255, 860), "VELOCITY  /  ARENA", font=_font(17, True), fill=(222, 222, 237, 255))
    d.text((255, 892), "CARD ID  " + str(card.get("card_id", "--------")), font=_font(16), fill=(190, 202, 225, 255))

    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    out.seek(0)
    out.name = "card.png"
    return out



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
        "🎴 /cardpack — Claim ONE card every 12 hours\n"
        "📚 /mycards — View your collection\n"
        "🖼 /cardview CARD_ID — View a card image\n"
        "🏅 /cardtop — Competitive trophy leaderboard\n"
        "🔁 Reply to a player's message with /cardtrade CARD_ID to offer a trade\n"
        "⚔️ Reply to a player's message with /cardbattle to challenge them\n"
        "🏆 Win battles to earn +10 trophies; losses cost 3.\n\n"
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
    card = _new_card()
    _cards.insert_one({"user_id": user.id, **card})
    _players.update_one({"user_id": user.id}, {"$set": {"last_pack": now}}, upsert=True)
    caption = f"🎴 <b>YOUR ARENA DROP</b>\n{card['emoji']} <b>{card['name']}</b>\n{card['rarity']} · ⚔️ Power {card['power']}\nID: <code>{card['card_id']}</code>\n\n⏳ Next card in 12 hours.\n⚔️ Challenge a rival with /cardbattle!"
    await update.effective_message.reply_photo(photo=_card_image(card), caption=caption, parse_mode="HTML")


async def mycards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _profile(update.effective_user); _init()
    rows = list(_cards.find({"user_id": update.effective_user.id}).sort("obtained_at", DESCENDING).limit(50))
    if not rows:
        await update.effective_message.reply_text("Your collection is empty. Use /cardpack to open your first pack!")
        return
    lines = [f"{i}. {c.get('emoji','🃏')} <b>{c['name']}</b> [{c['rarity']}] · ⚔️ {c['power']} · ID <code>{c['card_id']}</code>" for i,c in enumerate(rows,1)]
    await update.effective_message.reply_text("📚 <b>YOUR CARDS</b>\n\n"+"\n".join(lines)+"\n\nUse /cardview CARD_ID to display a card image.", parse_mode="HTML")


async def cardview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; _profile(user); _init()
    if not context.args:
        await update.effective_message.reply_text("Usage: /cardview CARD_ID\nFind IDs with /mycards."); return
    card = _cards.find_one({"user_id": user.id, "card_id": context.args[0].upper()})
    if not card:
        await update.effective_message.reply_text("Card not found in your collection. Check /mycards."); return
    await update.effective_message.reply_photo(photo=_card_image(card), caption=f"{card['emoji']} {card['name']} · {card['rarity']} · Power {card['power']}\nID: {card['card_id']}")

async def carddaily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("🎴 Card Arena uses one fair card drop every 12 hours. Use /cardpack to claim your next card—there are no bonus-card shortcuts.")


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
    # Each player selects their own card from the public battle message.
    if len(parts) == 4 and parts[1] == "pick":
        _, _, battle_id, card_id = parts
        battle = _battles.find_one({"battle_id": battle_id, "status": "selecting"})
        if not battle:
            await q.answer("This battle is no longer active.", show_alert=True); return
        uid = q.from_user.id
        if uid not in (battle["challenger"], battle["opponent"]):
            await q.answer("Only the two battling players can select cards.", show_alert=True); return
        card = _cards.find_one({"user_id": uid, "card_id": card_id})
        if not card:
            await q.answer("You can only select a card from your own collection.", show_alert=True); return
        field = "pick_challenger" if uid == battle["challenger"] else "pick_opponent"
        if battle.get(field):
            await q.answer("You already locked in your card for this battle.", show_alert=True); return
        _battles.update_one({"battle_id": battle_id, "status": "selecting", field: {"$exists": False}}, {"$set": {field: card_id}})
        battle = _battles.find_one({"battle_id": battle_id})
        if not battle.get("pick_challenger") or not battle.get("pick_opponent"):
            await q.answer("Card locked in privately! Waiting for your opponent.")
            locked_text = "🔒 <b>YOUR CARD IS LOCKED IN</b>\n\nYour choice is private. The battle result will be posted in the group once both players have selected."
            if q.message.photo:
                await q.edit_message_caption(caption=locked_text, parse_mode="HTML", reply_markup=None)
            else:
                await q.edit_message_text(locked_text, parse_mode="HTML", reply_markup=None)
            return
        ca = _cards.find_one({"user_id": battle["challenger"], "card_id": battle["pick_challenger"]})
        cb = _cards.find_one({"user_id": battle["opponent"], "card_id": battle["pick_opponent"]})
        if not ca or not cb:
            _battles.update_one({"battle_id": battle_id}, {"$set": {"status": "cancelled"}})
            await q.edit_message_text("Battle cancelled because a selected card is no longer available."); await q.answer(); return
        score_a = ca["power"] + random.randint(0, 10)
        score_b = cb["power"] + random.randint(0, 10)
        winner_id = battle["challenger"] if score_a > score_b else battle["opponent"] if score_b > score_a else None
        result = "🤝 It's a draw! No cards change hands." if winner_id is None else (f"🏆 Challenger wins with {ca['name']}!" if winner_id == battle["challenger"] else f"🏆 Opponent wins with {cb['name']}!")
        if winner_id is not None:
            loser_id = battle["opponent"] if winner_id == battle["challenger"] else battle["challenger"]
            losing_card = cb if loser_id == battle["opponent"] else ca
            moved = _cards.delete_one({"user_id": loser_id, "card_id": losing_card["card_id"]})
            if moved.deleted_count:
                losing_card.pop("_id", None); losing_card["user_id"] = winner_id; losing_card["obtained_at"] = datetime.now(timezone.utc)
                _cards.insert_one(losing_card)
                result += f"\n🎴 {losing_card['name']} was transferred to the winner!"
            _players.update_one({"user_id": winner_id}, {"$inc": {"trophies": 10, "wins": 1}, "$setOnInsert": {"user_id": winner_id}}, upsert=True)
            _players.update_one({"user_id": loser_id}, {"$inc": {"trophies": -3, "losses": 1}, "$setOnInsert": {"user_id": loser_id}}, upsert=True)
        _battles.update_one({"battle_id": battle_id, "status": "selecting"}, {"$set": {"status": "complete", "score_challenger": score_a, "score_opponent": score_b, "winner": winner_id}})
        result_text = f"⚔️ <b>CARD DUEL RESULTS</b>\n\n{ca.get('emoji','🃏')} <b>{ca['name']}</b>: {score_a} power\nvs\n{cb.get('emoji','🃏')} <b>{cb['name']}</b>: {score_b} power\n\n{result}"
        locked_text = "✅ Both cards are locked. The result has been posted in the group."
        if q.message.photo:
            await q.edit_message_caption(caption=locked_text, parse_mode="HTML", reply_markup=None)
        else:
            await q.edit_message_text(locked_text, parse_mode="HTML", reply_markup=None)
        try:
            await context.bot.send_message(chat_id=battle["group_chat_id"], text=result_text, parse_mode="HTML")
        except Exception:
            pass
        await q.answer(); return

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
        _battles.update_one({"battle_id":item_id,"status":"pending"},{"$set":{"status":"selecting", "group_chat_id": q.message.chat_id}})
        # Acknowledge the button click before sending multiple private card photos.
        # Telegram callback queries expire quickly, so never answer this query again below.
        await q.answer("Battle accepted! Sending private card choices…")
        await q.edit_message_text("⚔️ Battle accepted! Both players must open a private chat with the bot and press Start if they haven't already. Card choices are sent privately and revealed only after both are locked in.")
        for uid, cards in ((battle["challenger"], a), (battle["opponent"], b)):
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text="⚔️ <b>CARD BATTLE — PRIVATE PICK</b>\n\nChoose one card below. Your opponent cannot see your options or selection. Once locked, your choice cannot be changed. The winner takes the loser's selected card.",
                    parse_mode="HTML",
                )
                for c in cards[:8]:
                    caption = f"{c.get('emoji','🃏')} <b>{c['name']}</b>\n{c['rarity']} · {c.get('element','Cosmic')} · ⚔️ Power {c['power']}\nID: <code>{c['card_id']}</code>"
                    pick_button = InlineKeyboardMarkup([[InlineKeyboardButton("🔒 Choose this card", callback_data=f"arena:pick:{item_id}:{c['card_id']}")]])
                    await context.bot.send_photo(
                        chat_id=uid,
                        photo=_card_image(c),
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=pick_button,
                    )
            except Exception:
                _battles.update_one({"battle_id":item_id,"status":"selecting"},{"$set":{"status":"cancelled"}})
                await context.bot.send_message(chat_id=q.message.chat_id, text="Battle cancelled: both players must start the bot in private chat before choosing cards.")
                return
        return
    await q.answer("Unknown action", show_alert=True)


async def cardtop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _init()
    rows = list(_players.find({"wins":{"$gt":0}}).sort([("trophies",DESCENDING),("wins",DESCENDING)]).limit(10))
    if not rows:
        await update.effective_message.reply_text("🏆 No ranked battles yet. Challenge someone with /cardbattle to start earning trophies!"); return
    lines=[]
    for i,p in enumerate(rows,1):
        lines.append(f"{i}. {p.get('name','Player')} — 🏆 {p.get('trophies',0)} trophies · ⚔️ {p.get('wins',0)} wins · 💥 {p.get('losses',0)} losses")
    await update.effective_message.reply_text("🏆 <b>VELOCITY ARENA RANKINGS</b>\n\n"+"\n".join(lines)+"\n\nWin: +10 trophies · Loss: −3 trophies", parse_mode="HTML")


def register_card_arena_handlers(app):
    app.add_handler(CommandHandler(["cardarena","cardgame"], arena))
    app.add_handler(CommandHandler("cardpack", cardpack))
    app.add_handler(CommandHandler("mycards", mycards))
    app.add_handler(CommandHandler("cardview", cardview))
    app.add_handler(CommandHandler("carddaily", carddaily))
    app.add_handler(CommandHandler("cardtrade", cardtrade))
    app.add_handler(CommandHandler("cardbattle", cardbattle))
    app.add_handler(CommandHandler("cardtop", cardtop))
    app.add_handler(CallbackQueryHandler(arena_callback, pattern=r"^arena:"))
    log.info("Velocity Card Arena handlers registered")
