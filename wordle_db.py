"""
wordle_db.py — MongoDB persistence for the Wordle game.
Uses the same connection pattern as database.py / paheli_db.py.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient, DESCENDING
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


def init_wordle_indexes():
    db = _get_db()
    db.wordle_games.create_index([("group_id", 1), ("active", 1)])
    db.wordle_games.create_index([("game_id", 1)], unique=True)
    db.wordle_scores.create_index([("user_id", 1)])
    db.wordle_scores.create_index([("group_id", 1)])
    db.wordle_scores.create_index([("scored_at", 1)])
    db.wordle_users.create_index([("user_id", 1)], unique=True)
    logger.info("Wordle indexes initialized")


# ── Game CRUD ─────────────────────────────────────────────────────────────────

def create_wordle_game(game_id: str, group_id: int, word: str, length: int) -> None:
    db = _get_db()
    db.wordle_games.insert_one({
        "game_id":        game_id,
        "group_id":       group_id,
        "word":           word.upper(),
        "length":         length,
        "attempts":       0,
        "active":         1,
        "guesses":        [],
        "status_msg_id":  None,
        "started_at":     datetime.now(timezone.utc),
        "ended_at":       None,
    })


def get_active_wordle(group_id: int) -> Optional[dict]:
    db = _get_db()
    return db.wordle_games.find_one({"group_id": group_id, "active": 1})


def record_guess(game_id: str, user_id: int, user_name: str, guess: str) -> Optional[dict]:
    """Atomically increment attempts and record the guess.
    Returns the *updated* game document (with new attempt count), or None."""
    db = _get_db()
    doc = db.wordle_games.find_one_and_update(
        {"game_id": game_id, "active": 1},
        {
            "$inc": {"attempts": 1},
            "$push": {
                "guesses": {
                    "user_id":   user_id,
                    "user_name": user_name,
                    "guess":     guess.upper(),
                }
            },
        },
        return_document=True,   # return the document AFTER the update
    )
    return doc


def update_wordle_status_message(game_id: str, message_id: int) -> None:
    db = _get_db()
    db.wordle_games.update_one(
        {"game_id": game_id},
        {"$set": {"status_msg_id": message_id}},
    )


def end_wordle_game(game_id: str) -> None:
    db = _get_db()
    db.wordle_games.update_one(
        {"game_id": game_id},
        {"$set": {"active": 0, "ended_at": datetime.now(timezone.utc)}},
    )


# ── Scoring ───────────────────────────────────────────────────────────────────

def add_wordle_score(
    user_id: int,
    group_id: int,
    game_id: str,
    points: int,
    first_name: str,
    username: str,
) -> None:
    db = _get_db()
    db.wordle_scores.insert_one({
        "user_id":    user_id,
        "group_id":   group_id,
        "game_id":    game_id,
        "points":     points,
        "first_name": first_name,
        "username":   username,
        "scored_at":  datetime.now(timezone.utc),
    })
    # Cumulative per-user stats
    db.wordle_users.update_one(
        {"user_id": user_id},
        {
            "$inc": {"total_points": points, "games_won": 1},
            "$set": {
                "first_name": first_name,
                "username":   username,
                "updated_at": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )


def get_wordle_leaderboard(group_id: int = None, limit: int = 10) -> list:
    db = _get_db()
    match = {"group_id": group_id} if group_id else {}
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id":          "$user_id",
            "first_name":   {"$last": "$first_name"},
            "total_points": {"$sum": "$points"},
            "games_won":    {"$sum": 1},
        }},
        {"$sort": {"total_points": -1}},
        {"$limit": limit},
    ]
    return list(db.wordle_scores.aggregate(pipeline))


def get_wordle_stats(user_id: int) -> dict:
    db = _get_db()
    doc = db.wordle_users.find_one({"user_id": user_id})
    return doc or {"total_points": 0, "games_won": 0}
