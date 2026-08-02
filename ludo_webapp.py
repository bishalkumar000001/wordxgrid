"""
ludo_webapp.py — Flask Blueprint + SocketIO handlers for the Ludo Mini App.
Registers HTTP routes and all real-time WebSocket events.
"""
import logging
import os
import uuid
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, jsonify

import ludo_auth as auth
import ludo_engine as engine
import ludo_rooms_db as rdb
from flask_socketio import SocketIO, emit, join_room, leave_room

logger = logging.getLogger(__name__)

ludo_bp = Blueprint("ludo", __name__)

# Map: sid → {user_id, room_id}
_sessions: dict = {}

TURN_TIMEOUT = int(os.environ.get("LUDO_TURN_TIMEOUT", "60"))  # seconds

# Will be set by register_socketio_events()
_sio = None


# ── HTTP Routes ────────────────────────────────────────────────────────────────

@ludo_bp.route("/ludo")
def ludo_index():
    return render_template("ludo/index.html")


@ludo_bp.route("/api/ludo/rooms")
def api_list_rooms():
    rooms = rdb.list_public_rooms()
    # Strip game_state for listing
    safe = []
    for r in rooms:
        safe.append({
            "room_id":     r["room_id"],
            "invite_code": r["invite_code"],
            "host_id":     r["host_id"],
            "is_private":  r["is_private"],
            "max_players": r["max_players"],
            "player_count": len(r.get("players", [])),
            "players": [
                {"name": p["name"], "color": p["color"], "ready": p["ready"]}
                for p in r.get("players", [])
            ],
        })
    return jsonify(safe)


@ludo_bp.route("/api/ludo/health")
def api_health():
    return jsonify({"status": "ok"})


# ── SocketIO event registration ────────────────────────────────────────────────

def register_socketio_events(socketio):
    global _sio
    _sio = socketio

    @socketio.on("connect")
    def on_connect():
        sid = request.sid
        _sessions[sid] = {"user_id": None, "room_id": None}
        logger.debug("Client connected: %s", sid)

    @socketio.on("disconnect")
    def on_disconnect():
        sid = request.sid
        session = _sessions.pop(sid, {})
        user_id = session.get("user_id")
        room_id = session.get("room_id")

        if user_id and room_id:
            rdb.set_player_connected(room_id, user_id, False)
            room = rdb.get_room(room_id)
            if room:
                _sio.emit("room_update", _room_payload(room), room=room_id, skip_sid=sid)
        logger.debug("Client disconnected: %s", sid)

    @socketio.on("authenticate")
    def on_authenticate(data):
        sid = request.sid
        init_data = (data or {}).get("initData", "")
        user = auth.validate_init_data(init_data)

        if not user or not user.get("id"):
            _emit_error(sid, "auth_failed", "Invalid Telegram initData")
            return

        user_id = user["id"]
        _sessions[sid] = {
            "user_id":  user_id,
            "user":     user,
            "room_id":  None,
        }

        # Check for existing active room (reconnect support)
        existing_room = rdb.find_player_room(user_id)
        if existing_room:
            room_id = existing_room["room_id"]
            _sessions[sid]["room_id"] = room_id
            rdb.set_player_connected(room_id, user_id, True)
            join_room(room_id, sid=sid)
            # Use full game payload (includes game_state) so frontend can restore
            # the board when reconnecting to an in-progress game
            reconnect_payload = (
                _game_payload(existing_room)
                if existing_room.get("status") == "playing"
                else _room_payload(existing_room)
            )
            _sio.emit("authenticated", {
                "user": user,
                "reconnected_room": reconnect_payload,
            }, room=sid)
            _sio.emit("room_update", _room_payload(rdb.get_room(room_id)), room=room_id)
        else:
            _sio.emit("authenticated", {"user": user, "reconnected_room": None}, room=sid)

    @socketio.on("create_room")
    def on_create_room(data):
        sid = request.sid
        session = _sessions.get(sid, {})
        user = session.get("user")
        if not user:
            _emit_error(sid, "not_authenticated", "Please authenticate first")
            return

        if session.get("room_id"):
            _emit_error(sid, "already_in_room", "Leave your current room first")
            return

        is_private = bool((data or {}).get("is_private", False))
        max_players = int((data or {}).get("max_players", 4))
        max_players = max(2, min(4, max_players))

        room_id = str(uuid.uuid4())
        room = rdb.create_room(
            room_id=room_id,
            host_id=user["id"],
            host_name=auth.get_user_display_name(user),
            host_username=user.get("username", ""),
            host_photo=user.get("photo_url", ""),
            is_private=is_private,
            max_players=max_players,
        )

        _sessions[sid]["room_id"] = room_id
        _sio.enter_room(sid, room_id)
        _sio.emit("room_joined", _room_payload(room), room=sid)

    @socketio.on("join_room_by_code")
    def on_join_by_code(data):
        sid = request.sid
        session = _sessions.get(sid, {})
        user = session.get("user")
        if not user:
            _emit_error(sid, "not_authenticated", "Please authenticate first")
            return
        if session.get("room_id"):
            _emit_error(sid, "already_in_room", "Leave your current room first")
            return

        invite_code = (data or {}).get("invite_code", "").strip().upper()
        room = rdb.get_room_by_invite(invite_code)
        if not room:
            _emit_error(sid, "room_not_found", "Invalid invite code or room not found")
            return

        _do_join_room(sid, user, room["room_id"])

    @socketio.on("join_room_by_id")
    def on_join_by_id(data):
        sid = request.sid
        session = _sessions.get(sid, {})
        user = session.get("user")
        if not user:
            _emit_error(sid, "not_authenticated", "Please authenticate first")
            return
        if session.get("room_id"):
            _emit_error(sid, "already_in_room", "Leave your current room first")
            return

        room_id = (data or {}).get("room_id", "")
        if not rdb.get_room(room_id):
            _emit_error(sid, "room_not_found", "Room not found")
            return

        _do_join_room(sid, user, room_id)

    @socketio.on("leave_room")
    def on_leave_room():
        sid = request.sid
        session = _sessions.get(sid, {})
        user = session.get("user")
        room_id = session.get("room_id")

        if not user or not room_id:
            return

        room = rdb.get_room(room_id)
        if not room or room["status"] == "playing":
            # Disconnect but keep in game state
            rdb.set_player_connected(room_id, user["id"], False)
            leave_room(room_id, sid=sid)
            _sessions[sid]["room_id"] = None
            _sio.emit("room_update", _room_payload(rdb.get_room(room_id)), room=room_id)
            return

        # Remove player from waiting room
        updated = rdb.remove_player(room_id, user["id"])
        _sio.leave_room(sid, room_id)
        _sessions[sid]["room_id"] = None
        _sio.emit("left_room", {}, room=sid)

        if updated and len(updated.get("players", [])) == 0:
            # Room is empty, let it expire naturally
            return

        if updated:
            # If host left, assign new host
            if updated["host_id"] == user["id"] and updated.get("players"):
                new_host = updated["players"][0]["user_id"]
                _get_db_update_host(room_id, new_host)
                updated = rdb.get_room(room_id)

            _sio.emit("room_update", _room_payload(updated), room=room_id)

    @socketio.on("player_ready")
    def on_player_ready(data):
        sid = request.sid
        session = _sessions.get(sid, {})
        user = session.get("user")
        room_id = session.get("room_id")

        if not user or not room_id:
            return

        ready = bool((data or {}).get("ready", True))
        rdb.set_player_ready(room_id, user["id"], ready)
        room = rdb.get_room(room_id)
        if room:
            _sio.emit("room_update", _room_payload(room), room=room_id)

    @socketio.on("kick_player")
    def on_kick_player(data):
        sid = request.sid
        session = _sessions.get(sid, {})
        user = session.get("user")
        room_id = session.get("room_id")

        if not user or not room_id:
            return

        room = rdb.get_room(room_id)
        if not room or room["host_id"] != user["id"]:
            _emit_error(sid, "not_host", "Only the host can kick players")
            return

        target_id = int((data or {}).get("user_id", 0))
        if target_id == user["id"]:
            _emit_error(sid, "cannot_kick_self", "You can't kick yourself")
            return

        # Notify the kicked player
        for osid, osession in list(_sessions.items()):
            if osession.get("user_id") == target_id and osession.get("room_id") == room_id:
                _sio.emit("kicked", {"reason": "Kicked by host"}, room=osid)
                _sio.leave_room(osid, room_id)
                osession["room_id"] = None

        updated = rdb.kick_player(room_id, target_id)
        if updated:
            _sio.emit("room_update", _room_payload(updated), room=room_id)

    @socketio.on("start_game")
    def on_start_game():
        sid = request.sid
        session = _sessions.get(sid, {})
        user = session.get("user")
        room_id = session.get("room_id")

        if not user or not room_id:
            return

        room = rdb.get_room(room_id)
        if not room:
            _emit_error(sid, "room_not_found", "Room not found")
            return
        if room["host_id"] != user["id"]:
            _emit_error(sid, "not_host", "Only the host can start the game")
            return
        if room["status"] != "waiting":
            _emit_error(sid, "invalid_state", "Game already started")
            return

        players = room["players"]
        if len(players) < 2:
            _emit_error(sid, "not_enough_players", "Need at least 2 players to start")
            return

        all_ready = all(p["ready"] or p["user_id"] == room["host_id"] for p in players)
        if not all_ready:
            _emit_error(sid, "not_all_ready", "Wait for all players to be ready")
            return

        # Build game state
        game_players = [
            {"user_id": p["user_id"], "name": p["name"], "color": p["color"]}
            for p in players
        ]
        game_state = engine.make_initial_game_state(game_players)

        ok = rdb.start_game(room_id, game_state)
        if not ok:
            _emit_error(sid, "start_failed", "Failed to start game")
            return

        room = rdb.get_room(room_id)
        _sio.emit("game_started", _game_payload(room), room=room_id)

    @socketio.on("roll_dice")
    def on_roll_dice():
        sid = request.sid
        session = _sessions.get(sid, {})
        user = session.get("user")
        room_id = session.get("room_id")

        if not user or not room_id:
            return

        room = rdb.get_room(room_id)
        if not room or room["status"] != "playing":
            _emit_error(sid, "invalid_state", "Game is not active")
            return

        gs = room.get("game_state")
        if not gs:
            return

        current_idx = gs["current_player_idx"]
        current_player = gs["players"][current_idx]

        if current_player["user_id"] != user["id"]:
            _emit_error(sid, "not_your_turn", "It's not your turn")
            return

        if gs.get("dice_rolled"):
            _emit_error(sid, "already_rolled", "Dice already rolled this turn")
            return

        dice = engine.roll_dice()
        consecutive = gs.get("consecutive_sixes", 0)

        if dice == 6:
            consecutive += 1
        else:
            consecutive = 0

        gs["dice_value"] = dice
        gs["dice_rolled"] = True
        gs["consecutive_sixes"] = consecutive

        # Three consecutive sixes penalty
        if consecutive >= 3:
            gs = engine.apply_three_sixes_penalty(gs, current_idx)
            rdb.update_game_state(room_id, gs)
            _sio.emit("dice_rolled", {
                "player_idx": current_idx,
                "dice":       dice,
                "penalty":    True,
                "game_state": gs,
            }, room=room_id)
            # Advance turn
            _advance_turn(room_id, current_idx, dice, got_extra=False)
            return

        valid_moves = engine.get_valid_moves(current_player, dice)

        if not valid_moves:
            # No moves available — advance turn; always reset consecutive_sixes
            gs["consecutive_sixes"] = 0
            rdb.update_game_state(room_id, gs)
            _sio.emit("dice_rolled", {
                "player_idx":  current_idx,
                "dice":        dice,
                "valid_moves": [],
                "game_state":  gs,
            }, room=room_id)
            _advance_turn(room_id, current_idx, dice, got_extra=False)
            return

        rdb.update_game_state(room_id, gs)
        _sio.emit("dice_rolled", {
            "player_idx":  current_idx,
            "dice":        dice,
            "valid_moves": valid_moves,
            "game_state":  gs,
        }, room=room_id)

    @socketio.on("move_piece")
    def on_move_piece(data):
        sid = request.sid
        session = _sessions.get(sid, {})
        user = session.get("user")
        room_id = session.get("room_id")

        if not user or not room_id:
            return

        room = rdb.get_room(room_id)
        if not room or room["status"] != "playing":
            _emit_error(sid, "invalid_state", "Game is not active")
            return

        gs = room.get("game_state")
        if not gs:
            return

        current_idx = gs["current_player_idx"]
        if gs["players"][current_idx]["user_id"] != user["id"]:
            _emit_error(sid, "not_your_turn", "It's not your turn")
            return

        if not gs.get("dice_rolled"):
            _emit_error(sid, "must_roll_first", "Roll dice first")
            return

        piece_idx = int((data or {}).get("piece_idx", -1))
        if piece_idx < 0 or piece_idx > 3:
            _emit_error(sid, "invalid_piece", "Invalid piece index")
            return

        dice = gs["dice_value"]
        valid_moves = engine.get_valid_moves(gs["players"][current_idx], dice)
        if piece_idx not in valid_moves:
            _emit_error(sid, "invalid_move", "That piece cannot move")
            return

        # Apply move
        new_gs, captures, piece_finished = engine.apply_move(gs, current_idx, piece_idx, dice)

        # Check rankings
        new_gs = engine.check_rankings(new_gs)

        # Check game over
        game_over = engine.is_game_over(new_gs)

        # Extra turn on 6, capture, or piece finishing (unless 3 sixes)
        got_extra = (dice == 6 or len(captures) > 0 or piece_finished) and new_gs.get("consecutive_sixes", 0) < 3

        rdb.update_game_state(room_id, new_gs)

        _sio.emit("piece_moved", {
            "player_idx":    current_idx,
            "piece_idx":     piece_idx,
            "dice":          dice,
            "captures":      captures,
            "piece_finished": piece_finished,
            "game_state":    new_gs,
            "game_over":     game_over,
        }, room=room_id)

        if game_over:
            final_rankings = engine.get_final_rankings(new_gs)
            rdb.end_game(room_id, final_rankings)
            room = rdb.get_room(room_id)
            _sio.emit("game_over", {
                "rankings":  final_rankings,
                "players":   new_gs["players"],
                "game_state": new_gs,
            }, room=room_id)
            return

        if got_extra:
            # Give same player another turn
            new_gs["dice_rolled"] = False
            new_gs["dice_value"] = None
            rdb.update_game_state(room_id, new_gs)
            _sio.emit("extra_turn", {
                "player_idx": current_idx,
                "reason":     "6" if dice == 6 else "capture",
                "game_state": new_gs,
            }, room=room_id)
        else:
            _advance_turn(room_id, current_idx, dice, got_extra=False)

    @socketio.on("request_room_state")
    def on_request_room_state():
        sid = request.sid
        session = _sessions.get(sid, {})
        room_id = session.get("room_id")
        if not room_id:
            _emit_error(sid, "not_in_room", "Not in a room")
            return
        room = rdb.get_room(room_id)
        if room:
            if room["status"] == "playing":
                _sio.emit("game_state_sync", _game_payload(room), room=sid)
            else:
                _sio.emit("room_update", _room_payload(room), room=sid)

    @socketio.on("list_rooms")
    def on_list_rooms():
        sid = request.sid
        rooms = rdb.list_public_rooms()
        safe = []
        for r in rooms:
            safe.append({
                "room_id":      r["room_id"],
                "invite_code":  r["invite_code"],
                "is_private":   r["is_private"],
                "max_players":  r["max_players"],
                "player_count": len(r.get("players", [])),
                "players": [
                    {"name": p["name"], "color": p["color"]}
                    for p in r.get("players", [])
                ],
            })
        _sio.emit("rooms_list", {"rooms": safe}, room=sid)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _advance_turn(room_id: str, from_idx: int, dice: int, got_extra: bool):
    room = rdb.get_room(room_id)
    if not room:
        return
    gs = room.get("game_state")
    if not gs:
        return

    next_idx = engine.next_active_player(gs, from_idx)
    gs["current_player_idx"] = next_idx
    gs["dice_rolled"] = False
    gs["dice_value"] = None
    # Always reset consecutive_sixes when passing to another player
    gs["consecutive_sixes"] = 0

    rdb.update_game_state(room_id, gs)
    _sio.emit("turn_changed", {
        "player_idx": next_idx,
        "game_state": gs,
    }, room=room_id)


def _do_join_room(sid: str, user: dict, room_id: str):
    user_id = user["id"]
    name = auth.get_user_display_name(user)
    username = user.get("username", "")
    photo = user.get("photo_url", "")

    success, color, error = rdb.add_player(room_id, user_id, name, username, photo)
    if not success:
        _emit_error(sid, "join_failed", error or "Cannot join room")
        return

    _sessions[sid]["room_id"] = room_id
    _sessions[sid]["user_id"] = user_id
    _sio.enter_room(sid, room_id)

    room = rdb.get_room(room_id)
    _sio.emit("room_joined", _room_payload(room), room=sid)
    _sio.emit("room_update", _room_payload(room), room=room_id, skip_sid=sid)


def _emit_error(sid: str, code: str, message: str):
    if _sio:
        _sio.emit("error", {"code": code, "message": message}, room=sid)


def _room_payload(room: dict) -> dict:
    if not room:
        return {}
    return {
        "room_id":      room["room_id"],
        "invite_code":  room.get("invite_code", ""),
        "host_id":      room["host_id"],
        "is_private":   room.get("is_private", False),
        "max_players":  room.get("max_players", 4),
        "status":       room["status"],
        "players": [
            {
                "user_id":   p["user_id"],
                "name":      p["name"],
                "color":     p["color"],
                "ready":     p.get("ready", False),
                "connected": p.get("connected", True),
            }
            for p in room.get("players", [])
        ],
    }


def _game_payload(room: dict) -> dict:
    base = _room_payload(room)
    base["game_state"] = room.get("game_state")
    base["rankings"]   = room.get("rankings", [])
    return base


def _get_db_update_host(room_id: str, new_host_id: int):
    from pymongo import MongoClient
    import os
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db = client["wordgrid"]
    db.ludo_webapp_rooms.update_one({"room_id": room_id}, {"$set": {"host_id": new_host_id}})
