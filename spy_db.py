"""Persistence for the Find the Spy group game."""
import os
import logging
from datetime import datetime, timezone
from typing import Optional
from pymongo import MongoClient, DESCENDING

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


def init_spy_db():
    db = _get_db()
    db.spy_games.create_index([("group_id", 1), ("active", 1)])
    db.spy_games.create_index([("game_id", 1)], unique=True)
    db.spy_stats.create_index([("user_id", 1)], unique=True)
    logger.info("Spy game indexes initialized")


def create_game(game_id: str, group_id: int, host_id: int, word: str):
    _get_db().spy_games.insert_one({
        "game_id": game_id,
        "group_id": group_id,
        "host_id": host_id,
        "word": word,
        "active": True,
        "phase": "lobby",
        "players": [],
        "spy_id": None,
        "clues": [],
        "votes": {},
        "round": 1,
        "message_id": None,
        "started_at": datetime.now(timezone.utc),
        "ended_at": None,
    })


def get_game(game_id: str):
    return _get_db().spy_games.find_one({"game_id": game_id})


def get_active_game(group_id: int):
    return _get_db().spy_games.find_one({"group_id": group_id, "active": True})


def set_message(game_id: str, message_id: int):
    _get_db().spy_games.update_one({"game_id": game_id}, {"$set": {"message_id": message_id}})


def add_player(game_id: str, player: dict) -> bool:
    result = _get_db().spy_games.update_one(
        {"game_id": game_id, "active": True, "phase": "lobby",
         "players.user_id": {"$ne": player["user_id"]}},
        {"$push": {"players": player}},
    )
    return result.modified_count == 1


def remove_player(game_id: str, user_id: int):
    _get_db().spy_games.update_one(
        {"game_id": game_id, "active": True, "phase": "lobby"},
        {"$pull": {"players": {"user_id": user_id}}},
    )


def start_game(game_id: str, spy_id: int):
    _get_db().spy_games.update_one(
        {"game_id": game_id, "active": True, "phase": "lobby"},
        {"$set": {"phase": "clues", "spy_id": spy_id}},
    )


def add_clue(game_id: str, user_id: int, name: str, clue: str) -> bool:
    result = _get_db().spy_games.update_one(
        {"game_id": game_id, "active": True, "phase": "clues",
         "clues.user_id": {"$ne": user_id}},
        {"$push": {"clues": {"user_id": user_id, "name": name, "clue": clue}}},
    )
    return result.modified_count == 1


def set_voting(game_id: str):
    _get_db().spy_games.update_one({"game_id": game_id}, {"$set": {"phase": "voting"}})


def add_vote(game_id: str, voter_id: int, target_id: int) -> bool:
    result = _get_db().spy_games.update_one(
        {"game_id": game_id, "active": True, "phase": "voting", f"votes.{voter_id}": {"$exists": False}},
        {"$set": {f"votes.{voter_id}": target_id}},
    )
    return result.modified_count == 1


def set_final_phase(game_id: str):
    _get_db().spy_games.update_one({"game_id": game_id}, {"$set": {"phase": "final"}})


def set_final_guess(game_id: str, guesser_id: int, guess: str):
    _get_db().spy_games.update_one(
        {"game_id": game_id},
        {"$set": {"phase": "final", "final_guesser": guesser_id, "final_guess": guess}},
    )


def end_game(game_id: str):
    _get_db().spy_games.update_one(
        {"game_id": game_id},
        {"$set": {"active": False, "phase": "ended", "ended_at": datetime.now(timezone.utc)}},
    )


def update_stats(user_id: int, won: bool, is_spy: bool, points: int):
    db = _get_db()
    old = db.spy_stats.find_one({"user_id": user_id}) or {}
    current = int(old.get("current_streak", 0)) + 1 if won else 0
    best = max(int(old.get("best_streak", 0)), current)
    db.spy_stats.update_one(
        {"user_id": user_id},
        {
            "$inc": {
                "games_played": 1,
                "wins": 1 if won else 0,
                "losses": 0 if won else 1,
                "spy_games": 1 if is_spy else 0,
                "spy_wins": 1 if is_spy and won else 0,
                "civilian_games": 0 if is_spy else 1,
                "civilian_wins": 0 if is_spy or not won else 1,
                "total_points": points,
            },
            "$set": {
                "current_streak": current,
                "best_streak": best,
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {"user_id": user_id},
        },
        upsert=True,
    )
    return db.spy_stats.find_one({"user_id": user_id})


def get_stats(user_id: int) -> dict:
    return _get_db().spy_stats.find_one({"user_id": user_id}) or {
        "games_played": 0, "wins": 0, "losses": 0, "spy_games": 0,
        "spy_wins": 0, "civilian_games": 0, "civilian_wins": 0,
        "total_points": 0, "current_streak": 0, "best_streak": 0,
    }
