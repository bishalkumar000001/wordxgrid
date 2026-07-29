"""MongoDB persistence for the Telegram Ludo room service.

This module follows the same MongoDB connection convention as WordXGrid.
The web app's API can use these helpers when deployed alongside the bot.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, DESCENDING

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

_client: MongoClient | None = None
_db = None


def _get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10_000)
        _db = _client["wordgrid"]
    return _db


def init_ludo_db() -> None:
    db = _get_db()
    db.ludo_rooms.create_index([("room_id", 1)], unique=True)
    db.ludo_rooms.create_index([("status", 1), ("created_at", DESCENDING)])
    db.ludo_rooms.create_index([("updated_at", DESCENDING)])


def create_room(room: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    document = {**room, "created_at": now, "updated_at": now}
    _get_db().ludo_rooms.insert_one(document)
    return document


def get_room(room_id: str) -> dict[str, Any] | None:
    document = _get_db().ludo_rooms.find_one({"room_id": room_id})
    if document:
        document.pop("_id", None)
    return document


def save_room(room: dict[str, Any]) -> None:
    room["updated_at"] = datetime.now(timezone.utc)
    room_id = room["room_id"]
    _get_db().ludo_rooms.replace_one({"room_id": room_id}, room, upsert=True)


def list_open_rooms(limit: int = 20) -> list[dict[str, Any]]:
    rooms = _get_db().ludo_rooms.find(
        {"status": "waiting"},
        {"_id": 0},
    ).sort("created_at", DESCENDING).limit(limit)
    return list(rooms)


def delete_room(room_id: str) -> None:
    _get_db().ludo_rooms.delete_one({"room_id": room_id})