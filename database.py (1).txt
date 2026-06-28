import os
import json
import logging
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient, DESCENDING, UpdateOne
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

_client = None
_mdb = None


def _get_db():
    global _client, _mdb
    if _mdb is None:
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10_000)
        _mdb = _client["wordgrid"]
    return _mdb


def init_db():
    db = _get_db()
    db.games.create_index([("group_id", 1), ("active", 1)])
    db.games.create_index([("game_id", 1)], unique=True)
    db.scores.create_index([("user_id", 1)])
    db.scores.create_index([("group_id", 1)])
    db.scores.create_index([("game_id", 1)])
    db.scores.create_index([("scored_at", 1)])
    db.users.create_index([("user_id", 1)], unique=True)
    logger.info("MongoDB connected (db=wordgrid)")


# ── Users ─────────────────────────────────────────────────────────────────────

def upsert_user(user_id: int, username: str, first_name: str, last_name: str = ""):
    db = _get_db()
    db.users.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "username": username or "",
            "first_name": first_name or "",
            "last_name": last_name or "",
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


# ── Games ─────────────────────────────────────────────────────────────────────

def create_game(
    game_id: str,
    group_id: int,
    mode: str,
    words: list,
    grid: list = None,
    placed: dict = None,
) -> None:
    db = _get_db()
    grid_json    = json.dumps(grid) if grid is not None else None
    placed_json  = json.dumps({k: list(v) for k, v in placed.items()}) if placed else None
    db.games.insert_one({
        "game_id":      game_id,
        "group_id":     group_id,
        "mode":         mode,
        "words":        ",".join(words),
        "found_words":  "",
        "active":       1,
        "grid_data":    grid_json,
        "placed_words": placed_json,
        "message_id":   None,
        "pin_msg_id":   None,
        "started_at":   datetime.now(timezone.utc),
        "ended_at":     None,
    })


def _game_doc_to_dict(doc) -> dict:
    if doc is None:
        return None
    d = dict(doc)
    d.pop("_id", None)
    return d


def get_active_game(group_id: int):
    db = _get_db()
    doc = db.games.find_one(
        {"group_id": group_id, "active": 1},
        sort=[("started_at", DESCENDING)],
    )
    return _game_doc_to_dict(doc)


def get_game(game_id: str):
    db = _get_db()
    doc = db.games.find_one({"game_id": game_id})
    return _game_doc_to_dict(doc)


def update_game_message(game_id: str, message_id: int):
    _get_db().games.update_one({"game_id": game_id}, {"$set": {"message_id": message_id}})


def update_game_pin(game_id: str, pin_msg_id: int):
    _get_db().games.update_one({"game_id": game_id}, {"$set": {"pin_msg_id": pin_msg_id}})


def mark_word_found(game_id: str, word: str) -> bool:
    db = _get_db()
    doc = db.games.find_one({"game_id": game_id, "active": 1}, {"found_words": 1})
    if not doc:
        return False
    found = [w for w in doc.get("found_words", "").split(",") if w]
    if word in found:
        return False
    found.append(word)
    db.games.update_one(
        {"game_id": game_id},
        {"$set": {"found_words": ",".join(found)}},
    )
    return True


def end_game(game_id: str):
    _get_db().games.update_one(
        {"game_id": game_id},
        {"$set": {"active": 0, "ended_at": datetime.now(timezone.utc)}},
    )


# ── Scores ────────────────────────────────────────────────────────────────────

def add_score(user_id: int, group_id: int, game_id: str, word: str, points: int):
    _get_db().scores.insert_one({
        "user_id":   user_id,
        "group_id":  group_id,
        "game_id":   game_id,
        "word":      word,
        "points":    points,
        "scored_at": datetime.now(timezone.utc),
    })


def get_game_scores(game_id: str):
    db = _get_db()
    pipeline = [
        {"$match": {"game_id": game_id, "word": {"$ne": "__hint__"}}},
        {"$group": {
            "_id":          "$user_id",
            "total_points": {"$sum": "$points"},
        }},
        {"$sort": {"total_points": DESCENDING}},
        {"$lookup": {
            "from":         "users",
            "localField":   "_id",
            "foreignField": "user_id",
            "as":           "u",
        }},
        {"$unwind": {"path": "$u", "preserveNullAndEmptyArrays": True}},
        {"$project": {
            "user_id":      "$_id",
            "first_name":   {"$ifNull": ["$u.first_name", "Unknown"]},
            "last_name":    {"$ifNull": ["$u.last_name",  ""]},
            "username":     {"$ifNull": ["$u.username",   ""]},
            "total_points": 1,
        }},
    ]
    return list(db.scores.aggregate(pipeline))


def get_period_leaderboard(period: str, group_id: int = None, limit: int = 20):
    db = _get_db()

    match: dict = {"word": {"$ne": "__hint__"}}

    period_delta = {
        "day":   timedelta(days=1),
        "week":  timedelta(days=7),
        "month": timedelta(days=30),
        "year":  timedelta(days=365),
    }
    if period in period_delta:
        match["scored_at"] = {"$gte": datetime.now(timezone.utc) - period_delta[period]}

    if group_id:
        match["group_id"] = group_id

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id":          "$user_id",
            "total_points": {"$sum": "$points"},
            "games_set":    {"$addToSet": "$game_id"},
            "words_found":  {"$sum": 1},
        }},
        {"$addFields": {"games_played": {"$size": "$games_set"}}},
        {"$sort": {"total_points": DESCENDING}},
        {"$limit": limit},
        {"$lookup": {
            "from":         "users",
            "localField":   "_id",
            "foreignField": "user_id",
            "as":           "u",
        }},
        {"$unwind": {"path": "$u", "preserveNullAndEmptyArrays": True}},
        {"$project": {
            "user_id":      "$_id",
            "first_name":   {"$ifNull": ["$u.first_name", "Unknown"]},
            "last_name":    {"$ifNull": ["$u.last_name",  ""]},
            "username":     {"$ifNull": ["$u.username",   ""]},
            "total_points": 1,
            "games_played": 1,
            "words_found":  1,
        }},
    ]
    return list(db.scores.aggregate(pipeline))


def get_global_leaderboard(limit: int = 20):
    return get_period_leaderboard("all", limit=limit)


# ── Hints ─────────────────────────────────────────────────────────────────────

def get_hint_count(game_id: str) -> int:
    return _get_db().scores.count_documents({"game_id": game_id, "word": "__hint__"})


def record_hint_used(game_id: str, user_id: int, group_id: int):
    _get_db().scores.insert_one({
        "user_id":   user_id,
        "group_id":  group_id,
        "game_id":   game_id,
        "word":      "__hint__",
        "points":    0,
        "scored_at": datetime.now(timezone.utc),
    })


# ── Decode helpers ────────────────────────────────────────────────────────────

def decode_grid(game: dict) -> list:
    raw = game.get("grid_data")
    if not raw:
        return []
    return json.loads(raw)


def decode_placed(game: dict) -> dict:
    raw = game.get("placed_words")
    if not raw:
        return {}
    data = json.loads(raw)
    return {k: tuple(v) for k, v in data.items()}
