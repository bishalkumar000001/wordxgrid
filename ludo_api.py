"""Flask blueprint for Ludo rooms in the existing WordXGrid web process.

Register this blueprint from web.py so the deployed Telegram Web App and the
bot share one Render service:

    from ludo_api import ludo_api
    app.register_blueprint(ludo_api)

The room state is kept in MongoDB through ludo_db.py, using the existing
MONGO_URL and wordgrid database.
"""

from __future__ import annotations

import random
import secrets
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify, request

from ludo_db import create_room, delete_room, get_room, init_ludo_db, list_open_rooms, save_room

ludo_api = Blueprint("ludo_api", __name__, url_prefix="/api/ludo")
COLORS = ("red", "blue", "green", "yellow")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _state(room: dict[str, Any]) -> dict[str, Any]:
    result = dict(room)
    for key in ("_id", "updated_at"):
        result.pop(key, None)
    if hasattr(result.get("created_at"), "isoformat"):
        result["createdAt"] = result.pop("created_at").isoformat()
    else:
        result["createdAt"] = result.pop("created_at", _now())
    result["id"] = result.pop("room_id")
    return result


def _input(required: tuple[str, ...]) -> dict[str, Any] | None:
    body = request.get_json(silent=True) or {}
    if any(not isinstance(body.get(key), str) or not body[key].strip() for key in required):
        return None
    return body


def _room_or_error(room_id: str):
    room = get_room(room_id)
    return room if room else _error("Room not found", 404)


@ludo_api.get("/rooms")
def rooms():
    return jsonify([_state(room) for room in list_open_rooms()])


@ludo_api.post("/rooms")
def create():
    body = _input(("name", "playerName", "playerId"))
    if not body:
        return _error("name, playerName, and playerId are required")
    if len(body["name"]) > 24 or len(body["playerName"]) > 24 or len(body["playerId"]) > 64:
        return _error("One or more values are too long")
    player = {
        "id": body["playerId"],
        "name": body["playerName"],
        "color": COLORS[0],
        "pieces": [-1, -1, -1, -1],
        "connected": True,
    }
    room = create_room({
        "room_id": secrets.token_hex(3),
        "name": body["name"],
        "status": "waiting",
        "players": [player],
        "maxPlayers": 4,
        "currentPlayerId": player["id"],
        "currentPlayerColor": player["color"],
        "dice": None,
        "turnNumber": 1,
        "lastAction": f"{player['name']} created the room",
        "winnerId": None,
    })
    return jsonify(_state(room)), 201


@ludo_api.get("/rooms/<room_id>")
def get(room_id: str):
    room = _room_or_error(room_id)
    if isinstance(room, tuple):
        return room
    return jsonify(_state(room))


@ludo_api.post("/rooms/<room_id>/join")
def join(room_id: str):
    body = _input(("playerName", "playerId"))
    if not body:
        return _error("playerName and playerId are required")
    room = _room_or_error(room_id)
    if isinstance(room, tuple):
        return room
    existing = next((player for player in room["players"] if player["id"] == body["playerId"]), None)
    if existing:
        existing["connected"] = True
    elif room["status"] != "waiting":
        return _error("This room has already started")
    elif len(room["players"]) >= room["maxPlayers"]:
        return _error("This room is full")
    else:
        player = {
            "id": body["playerId"],
            "name": body["playerName"],
            "color": COLORS[len(room["players"])],
            "pieces": [-1, -1, -1, -1],
            "connected": True,
        }
        room["players"].append(player)
        room["lastAction"] = f"{player['name']} joined the room"
        if len(room["players"]) >= 2:
            room["status"] = "playing"
    save_room(room)
    return jsonify(_state(room))


@ludo_api.post("/rooms/<room_id>/roll")
def roll(room_id: str):
    body = _input(("playerName", "playerId"))
    if not body:
        return _error("playerName and playerId are required")
    room = _room_or_error(room_id)
    if isinstance(room, tuple):
        return room
    if room["status"] != "playing":
        return _error("The game needs at least two players")
    if room["currentPlayerId"] != body["playerId"]:
        return _error("It is not your turn")
    if room["dice"] is not None:
        return _error("Choose a piece before rolling again")
    room["dice"] = random.randint(1, 6)
    room["lastAction"] = f"{body['playerName']} rolled a {room['dice']}"
    save_room(room)
    return jsonify(_state(room))


@ludo_api.post("/rooms/<room_id>/move")
def move(room_id: str):
    body = _input(("playerName", "playerId"))
    if not body or not isinstance(body.get("pieceIndex"), int) or not 0 <= body["pieceIndex"] <= 3:
        return _error("playerName, playerId, and pieceIndex are required")
    room = _room_or_error(room_id)
    if isinstance(room, tuple):
        return room
    if room["currentPlayerId"] != body["playerId"]:
        return _error("It is not your turn")
    if room["dice"] is None:
        return _error("Roll the dice first")
    player = next((item for item in room["players"] if item["id"] == body["playerId"]), None)
    if not player:
        return _error("Player not found")
    roll_value = room["dice"]
    current_position = player["pieces"][body["pieceIndex"]]
    if current_position == 56:
        return _error("That piece is already home")
    if current_position == -1 and roll_value != 6:
        return _error("A piece needs a six to leave home")
    player["pieces"][body["pieceIndex"]] = 0 if current_position == -1 else min(current_position + roll_value, 56)
    room["lastAction"] = f"{body['playerName']} moved piece {body['pieceIndex'] + 1}"
    room["dice"] = None
    if all(piece == 56 for piece in player["pieces"]):
        room["status"] = "finished"
        room["winnerId"] = player["id"]
        room["lastAction"] = f"{body['playerName']} won the game"
    elif roll_value != 6:
        index = next(i for i, item in enumerate(room["players"]) if item["id"] == room["currentPlayerId"])
        next_player = room["players"][(index + 1) % len(room["players"])]
        room["currentPlayerId"] = next_player["id"]
        room["currentPlayerColor"] = next_player["color"]
        room["turnNumber"] += 1
    else:
        room["lastAction"] = f"{body['playerName']} gets another turn"
    save_room(room)
    return jsonify(_state(room))


@ludo_api.post("/rooms/<room_id>/leave")
def leave(room_id: str):
    body = _input(("playerName", "playerId"))
    if not body:
        return _error("playerName and playerId are required")
    room = _room_or_error(room_id)
    if isinstance(room, tuple):
        return room
    player = next((item for item in room["players"] if item["id"] == body["playerId"]), None)
    if player:
        player["connected"] = False
    room["lastAction"] = f"{body['playerName']} left the room"
    if all(not item["connected"] for item in room["players"]):
        delete_room(room_id)
    else:
        save_room(room)
    return jsonify(_state(room))


def init_ludo_api() -> None:
    init_ludo_db()