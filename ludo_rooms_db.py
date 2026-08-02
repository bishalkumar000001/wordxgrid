"""
ludo_rooms_db.py — MongoDB CRUD for the Ludo Mini App (WebApp) rooms.
Separate from ludo_db.py (which handles the text-based bot Ludo game).
Collection: ludo_webapp_rooms
"""
import os
import logging
import random
import string
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient, DESCENDING

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

_client: Optional[MongoClient] = None
_mdb = None

COLOR_ORDER = ["red", "green", "yellow", "blue"]


def _get_db():
    global _client, _mdb
    if _mdb is None:
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10_000)
        _mdb = _client["wordgrid"]
    return _mdb


def init_webapp_indexes():
    db = _get_db()
    db.ludo_webapp_rooms.create_index([("room_id", 1)], unique=True)
    db.ludo_webapp_rooms.create_index([("invite_code", 1)], unique=True, sparse=True)
    db.ludo_webapp_rooms.create_index([("status", 1), ("is_private", 1)])
    db.ludo_webapp_rooms.create_index([("created_at", 1)], expireAfterSeconds=3600)
    logger.info("Ludo WebApp indexes created")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _gen_invite_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _clean(doc) -> Optional[dict]:
    if doc is None:
        return None
    d = dict(doc)
    d.pop("_id", None)
    return d


# ── Room lifecycle ─────────────────────────────────────────────────────────────

def create_room(
    room_id: str,
    host_id: int,
    host_name: str,
    host_username: str,
    host_photo: str,
    is_private: bool,
    max_players: int,
) -> dict:
    """Create a new waiting room. Returns the created room dict."""
    db = _get_db()
    invite_code = _gen_invite_code()
    # Ensure uniqueness
    while db.ludo_webapp_rooms.find_one({"invite_code": invite_code}):
        invite_code = _gen_invite_code()

    # Pick colors in order; host gets red
    available_colors = list(COLOR_ORDER)
    host_color = available_colors[0]

    room = {
        "room_id":      room_id,
        "invite_code":  invite_code,
        "host_id":      host_id,
        "is_private":   is_private,
        "max_players":  max_players,
        "status":       "waiting",   # waiting | playing | finished
        "players": [{
            "user_id":        host_id,
            "name":           host_name,
            "username":       host_username or "",
            "photo_url":      host_photo or "",
            "color":          host_color,
            "ready":          False,
            "connected":      True,
            "pieces":         [-1, -1, -1, -1],
            "finished_count": 0,
        }],
        "game_state":   None,
        "rankings":     [],
        "created_at":   datetime.now(timezone.utc),
        "started_at":   None,
        "ended_at":     None,
    }
    db.ludo_webapp_rooms.insert_one(room)
    return _clean(db.ludo_webapp_rooms.find_one({"room_id": room_id}))


def get_room(room_id: str) -> Optional[dict]:
    return _clean(_get_db().ludo_webapp_rooms.find_one({"room_id": room_id}))


def get_room_by_invite(invite_code: str) -> Optional[dict]:
    return _clean(_get_db().ludo_webapp_rooms.find_one({
        "invite_code": invite_code.upper().strip(),
        "status": "waiting",
    }))


def list_public_rooms() -> list:
    """List public waiting rooms (max 20, newest first)."""
    cursor = _get_db().ludo_webapp_rooms.find(
        {"status": "waiting", "is_private": False},
        sort=[("created_at", DESCENDING)],
        limit=20,
    )
    rooms = []
    for doc in cursor:
        doc.pop("_id", None)
        doc.pop("game_state", None)  # Don't send full game state
        rooms.append(doc)
    return rooms


def add_player(room_id: str, user_id: int, name: str, username: str, photo_url: str) -> tuple:
    """
    Add a player to a waiting room.
    Returns (success: bool, color: str|None, error: str|None)
    """
    room = get_room(room_id)
    if not room:
        return False, None, "Room not found"
    if room["status"] != "waiting":
        return False, None, "Room is not in waiting state"

    players = room["players"]
    if len(players) >= room["max_players"]:
        return False, None, "Room is full"

    # Already in room (reconnect)
    for p in players:
        if p["user_id"] == user_id:
            _get_db().ludo_webapp_rooms.update_one(
                {"room_id": room_id, "players.user_id": user_id},
                {"$set": {"players.$.connected": True}}
            )
            return True, p["color"], None

    # Assign next available color
    used_colors = {p["color"] for p in players}
    available = [c for c in COLOR_ORDER if c not in used_colors]
    if not available:
        return False, None, "Room is full"

    color = available[0]
    new_player = {
        "user_id":        user_id,
        "name":           name,
        "username":       username or "",
        "photo_url":      photo_url or "",
        "color":          color,
        "ready":          False,
        "connected":      True,
        "pieces":         [-1, -1, -1, -1],
        "finished_count": 0,
    }
    _get_db().ludo_webapp_rooms.update_one(
        {"room_id": room_id},
        {"$push": {"players": new_player}}
    )
    return True, color, None


def remove_player(room_id: str, user_id: int) -> Optional[dict]:
    """Remove player from room. Returns updated room or None."""
    db = _get_db()
    db.ludo_webapp_rooms.update_one(
        {"room_id": room_id},
        {"$pull": {"players": {"user_id": user_id}}}
    )
    return get_room(room_id)


def set_player_ready(room_id: str, user_id: int, ready: bool):
    _get_db().ludo_webapp_rooms.update_one(
        {"room_id": room_id, "players.user_id": user_id},
        {"$set": {"players.$.ready": ready}}
    )


def set_player_connected(room_id: str, user_id: int, connected: bool):
    _get_db().ludo_webapp_rooms.update_one(
        {"room_id": room_id, "players.user_id": user_id},
        {"$set": {"players.$.connected": connected}}
    )


def kick_player(room_id: str, target_user_id: int) -> Optional[dict]:
    return remove_player(room_id, target_user_id)


def start_game(room_id: str, game_state: dict) -> bool:
    result = _get_db().ludo_webapp_rooms.update_one(
        {"room_id": room_id, "status": "waiting"},
        {"$set": {
            "status":      "playing",
            "game_state":  game_state,
            "started_at":  datetime.now(timezone.utc),
        }}
    )
    return result.modified_count > 0


def update_game_state(room_id: str, game_state: dict):
    _get_db().ludo_webapp_rooms.update_one(
        {"room_id": room_id},
        {"$set": {"game_state": game_state}}
    )


def end_game(room_id: str, rankings: list):
    _get_db().ludo_webapp_rooms.update_one(
        {"room_id": room_id},
        {"$set": {
            "status":   "finished",
            "rankings": rankings,
            "ended_at": datetime.now(timezone.utc),
        }}
    )


def is_player_in_room(room_id: str, user_id: int) -> bool:
    room = get_room(room_id)
    if not room:
        return False
    return any(p["user_id"] == user_id for p in room["players"])


def find_player_room(user_id: int) -> Optional[dict]:
    """Find an active room a user is in (for reconnect support)."""
    doc = _get_db().ludo_webapp_rooms.find_one({
        "players.user_id": user_id,
        "status": {"$in": ["waiting", "playing"]},
    }, sort=[("created_at", DESCENDING)])
    return _clean(doc)


def update_player_pieces(room_id: str, color: str, pieces: list):
    """Update piece positions for a player by color in the embedded game_state."""
    room = get_room(room_id)
    if not room or not room.get("game_state"):
        return
    gs = room["game_state"]
    for player in gs.get("players", []):
        if player["color"] == color:
            player["pieces"] = pieces
            break
    _get_db().ludo_webapp_rooms.update_one(
        {"room_id": room_id},
        {"$set": {"game_state": gs}}
    )
