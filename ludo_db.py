"""
ludo_db.py — MongoDB operations for the Ludo game module.
Uses the same pymongo connection pattern as database.py / paheli_db.py.
Database: wordgrid   Collection: ludo_games
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient, DESCENDING
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

_client: Optional[MongoClient] = None
_mdb = None


def _get_db():
    global _client, _mdb
    if _mdb is None:
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10_000)
        _mdb = _client["wordgrid"]
    return _mdb


def init_ludo_indexes():
    """Create indexes for ludo_games collection."""
    db = _get_db()
    db.ludo_games.create_index([("group_id", 1), ("status", 1)])
    db.ludo_games.create_index([("game_id", 1)], unique=True)
    logger.info("Ludo indexes created")


# ── Game lifecycle ────────────────────────────────────────────────────────────

def create_ludo_game(game_id: str, group_id: int, creator_id: int, creator_name: str):
    """Create a new waiting lobby."""
    _get_db().ludo_games.insert_one({
        "game_id":          game_id,
        "group_id":         group_id,
        "status":           "waiting",   # waiting | playing | finished
        "creator_id":       creator_id,
        "players": [{
            "user_id":        creator_id,
            "name":           creator_name,
            "color":          "red",
            "pieces":         [-1, -1, -1, -1],  # -1=home, 0-51=track, 52-57=home col, 58=done
            "finished_count": 0,
        }],
        "current_player_idx":  0,
        "dice_value":          None,
        "dice_rolled":         False,
        "consecutive_sixes":   0,
        "message_id":          None,   # active board message
        "lobby_message_id":    None,   # lobby waiting message
        "created_at":          datetime.now(timezone.utc),
        "started_at":          None,
        "ended_at":            None,
        "winner":              None,
    })


def get_active_ludo(group_id: int) -> Optional[dict]:
    """Return the active (waiting or playing) ludo game for a group."""
    doc = _get_db().ludo_games.find_one(
        {"group_id": group_id, "status": {"$in": ["waiting", "playing"]}},
        sort=[("created_at", DESCENDING)],
    )
    if doc:
        doc.pop("_id", None)
    return doc


def get_ludo_game(game_id: str) -> Optional[dict]:
    doc = _get_db().ludo_games.find_one({"game_id": game_id})
    if doc:
        doc.pop("_id", None)
    return doc


def is_player_in_game(game_id: str, user_id: int) -> bool:
    game = get_ludo_game(game_id)
    if not game:
        return False
    return any(p["user_id"] == user_id for p in game["players"])


def join_ludo_game(game_id: str, user_id: int, name: str):
    """Add a player to a waiting lobby. Returns (success, color_assigned)."""
    game = get_ludo_game(game_id)
    if not game or game["status"] != "waiting":
        return False, None
    players = game["players"]
    if len(players) >= 4:
        return False, None
    if any(p["user_id"] == user_id for p in players):
        return False, None

    color_order = ["red", "green", "yellow", "blue"]
    used = {p["color"] for p in players}
    available = [c for c in color_order if c not in used]
    if not available:
        return False, None

    color = available[0]
    _get_db().ludo_games.update_one(
        {"game_id": game_id},
        {"$push": {"players": {
            "user_id":        user_id,
            "name":           name,
            "color":          color,
            "pieces":         [-1, -1, -1, -1],
            "finished_count": 0,
        }}},
    )
    return True, color


def start_ludo_game(game_id: str):
    _get_db().ludo_games.update_one(
        {"game_id": game_id},
        {"$set": {
            "status":     "playing",
            "started_at": datetime.now(timezone.utc),
        }},
    )


def update_ludo_game_state(game_id: str, update_dict: dict):
    _get_db().ludo_games.update_one(
        {"game_id": game_id},
        {"$set": update_dict},
    )


def update_ludo_message_id(game_id: str, message_id: int):
    _get_db().ludo_games.update_one(
        {"game_id": game_id},
        {"$set": {"message_id": message_id}},
    )


def update_lobby_message_id(game_id: str, message_id: int):
    _get_db().ludo_games.update_one(
        {"game_id": game_id},
        {"$set": {"lobby_message_id": message_id}},
    )


def end_ludo_game(game_id: str, winner_id: int = None, winner_name: str = None):
    _get_db().ludo_games.update_one(
        {"game_id": game_id},
        {"$set": {
            "status":   "finished",
            "ended_at": datetime.now(timezone.utc),
            "winner":   {"user_id": winner_id, "name": winner_name} if winner_id else None,
        }},
    )


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_ludo_stats(user_id: int) -> dict:
    db = _get_db()
    played = db.ludo_games.count_documents({
        "players.user_id": user_id,
        "status": "finished",
    })
    wins = db.ludo_games.count_documents({"winner.user_id": user_id})
    return {"games_played": played, "wins": wins}
