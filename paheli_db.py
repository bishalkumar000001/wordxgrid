"""
paheli_db.py — MongoDB operations for the Paheli (Riddle) game.
Uses the same pymongo client pattern as database.py (shared connection).
Database: wordgrid   Collections: paheli_players, paheli_sessions, paheli_scores,
          paheli_challenges, paheli_clans, paheli_riddles, paheli_cooldowns
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from pymongo import MongoClient, DESCENDING, ASCENDING
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)


def _aware(dt) -> datetime:
    """Make a datetime timezone-aware (UTC). PyMongo returns naive datetimes."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

_client: Optional[MongoClient] = None
_mdb = None


def _get_db():
    global _client, _mdb
    if _mdb is None:
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10_000)
        _mdb = _client["wordgrid"]
    return _mdb


# ─── XP / Level / Title thresholds ────────────────────────────────────────────
CLAN_LEVELS = {
    1: 0,
    2: 500,
    3: 1500,
    4: 3000,
    5: 5000,
    6: 8000,
    7: 12000,
    8: 17000,
    9: 23000,
    10: 30000,
}
LEVEL_THRESHOLDS = [
    0, 100, 250, 500, 1000, 2000, 3500, 5000, 7500, 10000,
    15000, 20000, 30000, 50000, 75000, 100000
]

TITLES = [
    "Novice 🌱", "Apprentice 📚", "Scholar 🎓", "Thinker 💭",
    "Solver 🔍", "Mastermind 🧠", "Guru 🌟", "Sage ⚡",
    "Legend 🏆", "Mythic 🔥", "Eternal 💎", "Grandmaster 👑",
    "Champion 🦁", "Titan ⚔️", "Overlord 🌌", "God of Puzzles ✨"
]

SHOP_ITEMS = {
    "hint_single":   {"name": "1 Hint Token",    "cost": 50,  "type": "hint",      "quantity": 1},
    "hint_pack":     {"name": "5 Hint Pack",      "cost": 200, "type": "hint",      "quantity": 5},
    "skip_token":    {"name": "Skip Token",       "cost": 75,  "type": "skip",      "quantity": 1},
    "skip_pack":     {"name": "3 Skip Pack",      "cost": 200, "type": "skip",      "quantity": 3},
    "lucky_wheel":   {"name": "Lucky Wheel Spin", "cost": 100, "type": "lucky",     "quantity": 1},
    "chest_silver":  {"name": "Silver Chest",     "cost": 150, "type": "chest",     "quantity": 1},
    "chest_gold":    {"name": "Gold Chest",       "cost": 400, "type": "chest_gold","quantity": 1},
    "double_xp":     {"name": "2× XP Boost (1h)","cost": 500, "type": "boost",     "quantity": 1},
}

LUCKY_WHEEL_PRIZES = [
    {"type": "coins",  "amount": 50,   "label": "50 Coins 🪙",   "weight": 30},
    {"type": "coins",  "amount": 100,  "label": "100 Coins 🪙",  "weight": 25},
    {"type": "coins",  "amount": 200,  "label": "200 Coins 💰",  "weight": 15},
    {"type": "coins",  "amount": 500,  "label": "500 Coins 💰",  "weight": 8},
    {"type": "gems",   "amount": 1,    "label": "1 Gem 💎",      "weight": 12},
    {"type": "gems",   "amount": 3,    "label": "3 Gems 💎💎💎",  "weight": 6},
    {"type": "gems",   "amount": 10,   "label": "10 Gems 💎",    "weight": 2},
    {"type": "hint",   "amount": 2,    "label": "2 Hint Tokens", "weight": 2},
]

CHEST_PRIZES = {
    "chest": [
        {"type": "coins", "min": 30,  "max": 100},
        {"type": "xp",    "min": 20,  "max": 80},
        {"type": "hint",  "min": 1,   "max": 2},
    ],
    "chest_gold": [
        {"type": "coins", "min": 150, "max": 500},
        {"type": "xp",    "min": 100, "max": 300},
        {"type": "gems",  "min": 1,   "max": 5},
        {"type": "hint",  "min": 2,   "max": 5},
    ],
}


def get_level(xp: int) -> int:
    level = 0
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if xp >= threshold:
            level = i
        else:
            break
    return min(level, len(LEVEL_THRESHOLDS) - 1)


def get_title(level: int) -> str:
    idx = min(level, len(TITLES) - 1)
    return TITLES[idx]


def xp_for_next_level(xp: int) -> tuple[int, int]:
    """Returns (xp_needed, next_level_threshold)."""
    level = get_level(xp)
    if level + 1 >= len(LEVEL_THRESHOLDS):
        return 0, LEVEL_THRESHOLDS[-1]
    next_threshold = LEVEL_THRESHOLDS[level + 1]
    return max(0, next_threshold - xp), next_threshold


# ─── Init ─────────────────────────────────────────────────────────────────────

def init_paheli_db():
    db = _get_db()
    db.paheli_players.create_index([("user_id", 1)], unique=True)
    db.paheli_sessions.create_index([("group_id", 1), ("active", 1)])
    db.paheli_sessions.create_index([("user_id", 1), ("active", 1)])
    db.paheli_sessions.create_index([("session_id", 1)], unique=True)
    db.paheli_scores.create_index([("user_id", 1)])
    db.paheli_scores.create_index([("group_id", 1)])
    db.paheli_scores.create_index([("scored_at", 1)])
    db.paheli_challenges.create_index([("challenger_id", 1)])
    db.paheli_challenges.create_index([("challenged_id", 1)])
    db.paheli_challenges.create_index([("status", 1)])
    db.paheli_clans.create_index([("clan_tag", 1)], unique=True)
    db.paheli_riddles.create_index([("riddle_id", 1)], unique=True)
    db.paheli_cooldowns.create_index([("user_id", 1), ("action", 1)], unique=True)
    db.paheli_cooldowns.create_index([("expires_at", 1)], expireAfterSeconds=0)
    db.paheli_banned.create_index([("user_id", 1)], unique=True)
    logger.info("Paheli DB initialised")


# ─── Player CRUD ──────────────────────────────────────────────────────────────

def get_player(user_id: int) -> Optional[dict]:
    doc = _get_db().paheli_players.find_one({"user_id": user_id}, {"_id": 0})
    return doc


def ensure_player(user_id: int, first_name: str = "", username: str = "") -> dict:
    db = _get_db()
    doc = db.paheli_players.find_one_and_update(
        {"user_id": user_id},
        {"$setOnInsert": {
            "user_id":       user_id,
            "first_name":    first_name,
            "username":      username,
            "xp":            0,
            "coins":         50,
            "gems":          0,
            "level":         0,
            "title":         TITLES[0],
            "daily_streak":  0,
            "last_daily":    None,
            "last_weekly":   None,
            "achievements":  [],
            "badges":        [],
            "inventory":     {"hints": 3, "skips": 1},
            "settings":      {"language": "both", "notifications": True, "difficulty": "all"},
            "riddles_solved": 0,
            "riddles_skipped": 0,
            "total_score":   0,
            "games_played":  0,
            "clan_id":       None,
            "banned":        False,
            "created_at":    datetime.now(timezone.utc),
            "xp_boost_until": None,
        }},
        upsert=True,
        return_document=True,
    )
    return {k: v for k, v in doc.items() if k != "_id"}


def update_player(user_id: int, update: dict):
    _get_db().paheli_players.update_one({"user_id": user_id}, update)


def grant_xp_coins(user_id: int, xp: int, coins: int, reason: str = "") -> dict:
    """Award XP + coins; handle level-up. Returns updated player doc."""
    db = _get_db()
    doc = db.paheli_players.find_one({"user_id": user_id})
    if not doc:
        return {}

    # Check XP boost
    boost = 1
    if doc.get("xp_boost_until") and _aware(doc["xp_boost_until"]) > datetime.now(timezone.utc):
        boost = 2

    actual_xp = xp * boost
    old_level  = get_level(doc.get("xp", 0))
    new_xp     = doc.get("xp", 0) + actual_xp
    new_coins  = doc.get("coins", 0) + coins
    new_level  = get_level(new_xp)
    new_title  = get_title(new_level)

    db.paheli_players.update_one(
        {"user_id": user_id},
        {"$inc": {"xp": actual_xp, "coins": coins, "total_score": coins},
         "$set": {"level": new_level, "title": new_title}},
    )
    grant_clan_xp(user_id, actual_xp)
          
    return {
        "xp_gained": actual_xp,
        "coins_gained": coins,
        "boosted": boost > 1,
        "leveled_up": new_level > old_level,
        "old_level": old_level,
        "new_level": new_level,
        "new_title": new_title,
    }


def spend_coins(user_id: int, amount: int) -> bool:
    """Returns True if successful."""
    result = _get_db().paheli_players.update_one(
        {"user_id": user_id, "coins": {"$gte": amount}},
        {"$inc": {"coins": -amount}},
    )
    return result.modified_count > 0


def spend_gems(user_id: int, amount: int) -> bool:
    result = _get_db().paheli_players.update_one(
        {"user_id": user_id, "gems": {"$gte": amount}},
        {"$inc": {"gems": -amount}},
    )
    return result.modified_count > 0


def add_inventory(user_id: int, item_type: str, qty: int):
    key = f"inventory.{item_type}s" if not item_type.endswith("s") else f"inventory.{item_type}"
    _get_db().paheli_players.update_one(
        {"user_id": user_id},
        {"$inc": {f"inventory.{item_type}s": qty}},
    )


def use_inventory_item(user_id: int, item_type: str) -> bool:
    key = f"inventory.{item_type}s"
    result = _get_db().paheli_players.update_one(
        {"user_id": user_id, key: {"$gte": 1}},
        {"$inc": {key: -1}},
    )
    return result.modified_count > 0


# ─── Daily / Weekly rewards ───────────────────────────────────────────────────

def claim_daily(user_id: int) -> Optional[dict]:
    """Returns reward dict or None if already claimed today."""
    db = _get_db()
    doc = db.paheli_players.find_one({"user_id": user_id})
    if not doc:
        return None

    now = datetime.now(timezone.utc)
    last = doc.get("last_daily")

    if last:
        last = _aware(last)
              
        if (now - last).total_seconds() < 86400:
            return None  # already claimed

    # Streak logic
    streak = doc.get("daily_streak", 0)
    if last and (now - last).total_seconds() < 172800:  # within 2 days
        streak += 1
    else:
        streak = 1

    # Reward scales with streak
    coins = min(50 + (streak - 1) * 25, 500)
    xp    = min(30 + (streak - 1) * 10, 200)
    gems  = 1 if streak % 7 == 0 else 0   # gem every 7 days

    db.paheli_players.update_one(
        {"user_id": user_id},
        {"$set":  {"last_daily": now, "daily_streak": streak},
         "$inc":  {"coins": coins, "xp": xp, "gems": gems}},
    )
    return {"coins": coins, "xp": xp, "gems": gems, "streak": streak}


def claim_weekly(user_id: int) -> Optional[dict]:
    """Returns reward dict or None if already claimed this week."""
    db = _get_db()
    doc = db.paheli_players.find_one({"user_id": user_id})
    if not doc:
        return None

    now = datetime.now(timezone.utc)
    last = doc.get("last_weekly")
          
    if last:
        last = _aware(last)
        if (now - last).total_seconds() < 604800:
            return None

    coins = 500
    xp    = 300
    gems  = 3

    db.paheli_players.update_one(
        {"user_id": user_id},
        {"$set": {"last_weekly": now},
         "$inc": {"coins": coins, "xp": xp, "gems": gems}},
    )
    return {"coins": coins, "xp": xp, "gems": gems}


# ─── Sessions (active riddle game per group/DM) ────────────────────────────────

def create_paheli_session(session_id: str, riddle: dict, group_id: int,
                          started_by: int, mode: str = "group") -> None:
    _get_db().paheli_sessions.insert_one({
        "session_id":   session_id,
        "group_id":     group_id,
        "user_id":      started_by,
        "riddle_id":    riddle["id"],
        "riddle":       riddle,
        "hints_used":   0,
        "skipped":      False,
        "solved":       False,
        "solver_id":    None,
        "mode":         mode,
        "active":       1,
        "started_at":   datetime.now(timezone.utc),
        "ended_at":     None,
    })


def get_active_paheli(group_id: int) -> Optional[dict]:
    doc = _get_db().paheli_sessions.find_one(
        {"group_id": group_id, "active": 1},
        sort=[("started_at", DESCENDING)],
    )
    return {k: v for k, v in doc.items() if k != "_id"} if doc else None


def get_active_paheli_by_session(session_id: str) -> Optional[dict]:
    doc = _get_db().paheli_sessions.find_one({"session_id": session_id})
    return {k: v for k, v in doc.items() if k != "_id"} if doc else None


def increment_hints(session_id: str) -> int:
    result = _get_db().paheli_sessions.find_one_and_update(
        {"session_id": session_id},
        {"$inc": {"hints_used": 1}},
        return_document=True,
    )
    return result.get("hints_used", 0) if result else 0


def solve_paheli(session_id: str, solver_id: int) -> bool:
    result = _get_db().paheli_sessions.update_one(
        {"session_id": session_id, "active": 1},
        {"$set": {"active": 0, "solved": True,
                  "solver_id": solver_id,
                  "ended_at": datetime.now(timezone.utc)}},
    )
    return result.modified_count > 0


def skip_paheli(session_id: str) -> bool:
    result = _get_db().paheli_sessions.update_one(
        {"session_id": session_id, "active": 1},
        {"$set": {"active": 0, "skipped": True,
                  "ended_at": datetime.now(timezone.utc)}},
    )
    return result.modified_count > 0


def timeout_paheli(session_id: str) -> bool:
    result = _get_db().paheli_sessions.update_one(
        {"session_id": session_id, "active": 1},
        {"$set": {"active": 0, "ended_at": datetime.now(timezone.utc)}},
    )
    return result.modified_count > 0


# ─── Scores ───────────────────────────────────────────────────────────────────

def record_paheli_score(user_id: int, group_id: int, session_id: str,
                        riddle_id: int, points: int, difficulty: str):
    _get_db().paheli_scores.insert_one({
        "user_id":    user_id,
        "group_id":   group_id,
        "session_id": session_id,
        "riddle_id":  riddle_id,
        "points":     points,
        "difficulty": difficulty,
        "scored_at":  datetime.now(timezone.utc),
    })
    _get_db().paheli_players.update_one(
        {"user_id": user_id},
        {"$inc": {"riddles_solved": 1, "games_played": 1}},
    )


def get_paheli_leaderboard(period: str = "all", group_id: int = None,
                           limit: int = 20) -> list:
    db = _get_db()
    match: dict = {}

    deltas = {"day": 1, "week": 7, "month": 30, "year": 365}
    if period in deltas:
        match["scored_at"] = {"$gte": datetime.now(timezone.utc) - timedelta(days=deltas[period])}

    if group_id:
        match["group_id"] = group_id

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id":           "$user_id",
            "total_points":  {"$sum": "$points"},
            "riddles_solved": {"$sum": 1},
        }},
        {"$sort": {"total_points": DESCENDING}},
        {"$limit": limit},
        {"$lookup": {
            "from":         "paheli_players",
            "localField":   "_id",
            "foreignField": "user_id",
            "as":           "p",
        }},
        {"$unwind": {"path": "$p", "preserveNullAndEmptyArrays": True}},
        {"$project": {
            "user_id":       "$_id",
            "first_name":    {"$ifNull": ["$p.first_name", "Unknown"]},
            "username":      {"$ifNull": ["$p.username",   ""]},
            "level":         {"$ifNull": ["$p.level",      0]},
            "title":         {"$ifNull": ["$p.title",      "Novice"]},
            "total_points":  1,
            "riddles_solved": 1,
        }},
    ]
    return list(db.paheli_scores.aggregate(pipeline))


# ─── Used riddles (per group, avoid repetition) ────────────────────────────────

def get_used_riddle_ids(group_id: int, limit: int = 500) -> set:
    docs = _get_db().paheli_sessions.find(
        {"group_id": group_id},
        {"riddle_id": 1},
        sort=[("started_at", DESCENDING)],
        limit=limit,
    )
    return {d["riddle_id"] for d in docs}


# ─── PvP Challenges ───────────────────────────────────────────────────────────

def create_challenge(challenge_id: str, challenger_id: int, challenged_id: int,
                     group_id: int, riddle: dict) -> None:
    _get_db().paheli_challenges.insert_one({
        "challenge_id":  challenge_id,
        "challenger_id": challenger_id,
        "challenged_id": challenged_id,
        "group_id":      group_id,
        "riddle":        riddle,
        "riddle_id":     riddle["id"],
        "challenger_time": None,
        "challenged_time": None,
        "winner_id":     None,
        "status":        "pending",
        "created_at":    datetime.now(timezone.utc),
        "expires_at":    datetime.now(timezone.utc) + timedelta(minutes=10),
    })


def accept_challenge(challenge_id: str) -> bool:
    result = _get_db().paheli_challenges.update_one(
        {"challenge_id": challenge_id, "status": "pending"},
        {"$set": {"status": "active"}},
    )
    return result.modified_count > 0


def get_challenge(challenge_id: str) -> Optional[dict]:
    doc = _get_db().paheli_challenges.find_one({"challenge_id": challenge_id})
    return {k: v for k, v in doc.items() if k != "_id"} if doc else None


def get_pending_challenge(challenged_id: int, group_id: int) -> Optional[dict]:
    doc = _get_db().paheli_challenges.find_one({
        "challenged_id": challenged_id,
        "group_id": group_id,
        "status": "pending",
        "expires_at": {"$gt": datetime.now(timezone.utc)},
    })
    return {k: v for k, v in doc.items() if k != "_id"} if doc else None


def resolve_challenge(challenge_id: str, winner_id: int):
    _get_db().paheli_challenges.update_one(
        {"challenge_id": challenge_id},
        {"$set": {"status": "completed", "winner_id": winner_id}},
    )


# ─── Clan / Guild system ──────────────────────────────────────────────────────

def create_clan(clan_tag: str, clan_name: str, owner_id: int) -> bool:
    try:
        _get_db().paheli_clans.insert_one({
            "clan_tag":    clan_tag.upper(),
            "clan_name":   clan_name,
            "owner_id":    owner_id,
            "members":     [owner_id],

            "level": 1,
            "xp": 0,
            "coins": 0,
            "wins": 0,
            "losses": 0,
            "max_members": 10,
                  
            "total_xp":    0,
            "created_at":  datetime.now(timezone.utc),
        })
        _get_db().paheli_players.update_one(
            {"user_id": owner_id},
            {"$set": {"clan_id": clan_tag.upper()}},
        )
        return True
    except DuplicateKeyError:
        return False

          
def get_clan_level(xp):
    level = 1

    for lvl, need in CLAN_LEVELS.items():
        if xp >= need:
            level = lvl

    return level


def grant_clan_xp(user_id: int, xp: int):
    db = _get_db()

    clan = db.paheli_clans.find_one({"members": user_id})

    if not clan:
        return

    new_xp = clan.get("xp", 0) + xp

    new_level = get_clan_level(new_xp)

    member_limit = {
        1:10,
        2:15,
        3:20,
        4:25,
        5:30,
        6:35,
        7:40,
        8:45,
        9:50,
        10:60,
    }

    db.paheli_clans.update_one(
        {"_id": clan["_id"]},
        {
            "$set": {
                "xp": new_xp,
                "level": new_level,
                "max_members": member_limit[new_level],
            }
        }
    )

def join_clan(user_id: int, clan_tag: str) -> bool:
    clan = _get_db().paheli_clans.find_one({"clan_tag": clan_tag.upper()})

    if not clan:
        return False

    if len(clan.get("members", [])) >= clan.get("max_members", 10):
        return False

    result = _get_db().paheli_clans.update_one(
        {"clan_tag": clan_tag.upper()},
        {"$addToSet": {"members": user_id}},
    )
    if result.modified_count > 0:
        _get_db().paheli_players.update_one(
            {"user_id": user_id},
            {"$set": {"clan_id": clan_tag.upper()}},
        )
        return True
    return False


def leave_clan(user_id: int) -> bool:
    doc = _get_db().paheli_players.find_one({"user_id": user_id})
    if not doc or not doc.get("clan_id"):
        return False
    clan_tag = doc["clan_id"]
    _get_db().paheli_clans.update_one(
        {"clan_tag": clan_tag},
        {"$pull": {"members": user_id}},
    )
    _get_db().paheli_players.update_one(
        {"user_id": user_id},
        {"$set": {"clan_id": None}},
    )
    return True


def get_clan(clan_tag: str) -> Optional[dict]:
    doc = _get_db().paheli_clans.find_one({"clan_tag": clan_tag.upper()})
    return {k: v for k, v in doc.items() if k != "_id"} if doc else None


def get_clan_leaderboard(limit: int = 10) -> list:
    return list(_get_db().paheli_clans.find(
        {},
        {"_id": 0, "clan_tag": 1, "clan_name": 1, "total_xp": 1, "members": 1},
        sort=[("total_xp", DESCENDING)],
        limit=limit,
    ))


# ─── Admin: Custom riddles ─────────────────────────────────────────────────────

def add_custom_riddle(riddle_data: dict) -> bool:
    try:
        _get_db().paheli_riddles.insert_one(riddle_data)
        return True
    except DuplicateKeyError:
        return False


def get_custom_riddles() -> list:
    return list(_get_db().paheli_riddles.find({}, {"_id": 0}))


def delete_custom_riddle(riddle_id: int) -> bool:
    result = _get_db().paheli_riddles.delete_one({"riddle_id": riddle_id})
    return result.deleted_count > 0


# ─── Anti-spam / Cooldowns ────────────────────────────────────────────────────

def check_cooldown(user_id: int, action: str, seconds: int) -> int:
    """Returns 0 if ok, else seconds remaining."""
    db = _get_db()
    doc = db.paheli_cooldowns.find_one({"user_id": user_id, "action": action})
    if not doc:
        return 0
    remaining = (_aware(doc["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(remaining))


def set_cooldown(user_id: int, action: str, seconds: int):
    expires = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    _get_db().paheli_cooldowns.update_one(
        {"user_id": user_id, "action": action},
        {"$set": {"expires_at": expires}},
        upsert=True,
    )


# ─── Ban system ───────────────────────────────────────────────────────────────

def ban_user(user_id: int, reason: str = "", banned_by: int = 0):
    _get_db().paheli_banned.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "reason": reason,
                  "banned_by": banned_by,
                  "banned_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    _get_db().paheli_players.update_one(
        {"user_id": user_id}, {"$set": {"banned": True}}
    )


def unban_user(user_id: int):
    _get_db().paheli_banned.delete_one({"user_id": user_id})
    _get_db().paheli_players.update_one(
        {"user_id": user_id}, {"$set": {"banned": False}}
    )


def is_banned(user_id: int) -> bool:
    return _get_db().paheli_banned.count_documents({"user_id": user_id}) > 0


# ─── Stats ────────────────────────────────────────────────────────────────────

def get_global_paheli_stats() -> dict:
    db = _get_db()
    return {
        "total_players":  db.paheli_players.count_documents({}),
        "total_sessions": db.paheli_sessions.count_documents({}),
        "total_solved":   db.paheli_sessions.count_documents({"solved": True}),
        "total_skipped":  db.paheli_sessions.count_documents({"skipped": True}),
        "total_clans":    db.paheli_clans.count_documents({}),
    }
