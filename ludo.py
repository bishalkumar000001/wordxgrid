"""
ludo.py — Ludo Game Module for VelocityBots Telegram Bot.

Supports 2–4 players per group. Plug-and-play:
    call register_ludo_handlers(app) in bot.py

Commands:
    /ludo      — Create lobby / join existing lobby
    /lend      — Admin: end ludo game forcibly
    /lstats    — Your ludo stats
"""

import copy
import logging
import random
import uuid
from datetime import datetime, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, constants,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from telegram.error import TelegramError

import ludo_db as ldb

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

LUDO_JOIN_TIMEOUT = 300    # 5 min for lobby before auto-close
LUDO_TURN_TIMEOUT = 120    # 2 min per turn before auto-skip

# ── Color definitions ─────────────────────────────────────────────────────────

COLOR_EMOJI   = {"red": "🔴", "green": "🟢", "yellow": "🟡", "blue": "🔵"}
COLOR_NAME    = {"red": "Laal", "green": "Hara", "yellow": "Peela", "blue": "Neela"}

# Absolute track offsets: where each color enters the main ring (0-indexed, 0–51)
COLOR_OFFSETS = {"red": 0, "green": 13, "yellow": 26, "blue": 39}

# Absolute safe squares (star squares + entry squares, 0-indexed 0–51)
SAFE_ABS = {0, 8, 13, 21, 26, 34, 39, 47}

# Piece position display symbols (①②③④)
PIECE_LABEL = ["①", "②", "③", "④"]

# Dice faces
DICE_FACE = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣"}

# ── Board position helpers ─────────────────────────────────────────────────────
#
# Piece position encoding (relative to that player's colour):
#   -1      : at home base (not yet on board)
#   0–51    : on the main ring
#   52–57   : in home column (colour-specific, always safe)
#   58      : finished (home centre)
#
# To convert relative → absolute for collision detection:
#   abs = (rel + offset) % 52      (only meaningful when rel in 0–51)

def _rel_to_abs(rel: int, color: str) -> int:
    return (rel + COLOR_OFFSETS[color]) % 52


def _is_safe_abs(abs_pos: int) -> bool:
    return abs_pos in SAFE_ABS


# ── Game logic ────────────────────────────────────────────────────────────────

def get_valid_moves(player: dict, dice: int) -> list[int]:
    """Return piece indices that can legally move with the given dice roll."""
    valid = []
    for i, pos in enumerate(player["pieces"]):
        if pos == 58:            # already finished
            continue
        if pos == -1:            # at home base – needs a 6
            if dice == 6:
                valid.append(i)
        else:                    # on ring or home column
            if pos + dice <= 58:
                valid.append(i)
    return valid


def apply_move(
    game_state: dict,
    player_idx: int,
    piece_idx: int,
    dice: int,
) -> tuple[dict, list[dict], bool]:
    """
    Apply one move to a deep copy of game_state.
    Returns (new_state, captures, piece_finished).
      captures      : list of {player_name, color, piece_idx} that were sent home
      piece_finished: True if this piece just reached position 58
    """
    state = copy.deepcopy(game_state)
    player = state["players"][player_idx]
    pieces = player["pieces"]
    pos    = pieces[piece_idx]
    color  = player["color"]

    captures: list[dict] = []
    piece_finished = False

    # Move piece
    new_pos = 0 if pos == -1 else pos + dice
    pieces[piece_idx] = new_pos

    if new_pos == 58:
        player["finished_count"] = player.get("finished_count", 0) + 1
        piece_finished = True

    elif new_pos < 52:          # still on main ring – check captures
        my_abs = _rel_to_abs(new_pos, color)
        if not _is_safe_abs(my_abs):
            for opp_idx, opp in enumerate(state["players"]):
                if opp_idx == player_idx:
                    continue
                for j, opp_pos in enumerate(opp["pieces"]):
                    if opp_pos < 0 or opp_pos >= 52:
                        continue
                    if _rel_to_abs(opp_pos, opp["color"]) == my_abs:
                        opp["pieces"][j] = -1
                        captures.append({
                            "player_name": opp["name"],
                            "color":       opp["color"],
                            "piece_idx":   j,
                        })

    # Write updated pieces back
    state["players"][player_idx]["pieces"] = pieces
    return state, captures, piece_finished


def all_finished(player: dict) -> bool:
    return player.get("finished_count", 0) >= 4


def next_active_player(game_state: dict, from_idx: int) -> int:
    """Return the index of the next player who hasn't finished all pieces."""
    n = len(game_state["players"])
    for step in range(1, n + 1):
        idx = (from_idx + step) % n
        if not all_finished(game_state["players"][idx]):
            return idx
    return from_idx  # fallback (shouldn't happen)


# ── Display helpers ───────────────────────────────────────────────────────────

def _fmt_piece(pos: int) -> str:
    if pos == -1:
        return "🏠"
    if pos == 58:
        return "🏆"
    if pos >= 52:
        return f"🏡"          # in home column
    return f"·{pos}·"


def render_lobby(game_state: dict) -> str:
    players = game_state["players"]
    n = len(players)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        "🎲 <b>LUDO — Lobby</b>",
        "━━━━━━━━━━━━━━━━━━━━━\n",
        f"👥 <b>Players:</b> {n}/4\n",
    ]
    for p in players:
        emoji = COLOR_EMOJI[p["color"]]
        lines.append(f"  {emoji} {p['name']}")

    lines.append("")
    if n < 2:
        lines.append("⏳ Aur players ka wait kar rahe hain… (minimum 2 chahiye)")
    elif n < 4:
        lines.append(
            f"✅ {n} players ready! Abhi start kar sakte ho ya aur ka wait karo (max 4)."
        )
    else:
        lines.append("✅ 4/4 players! Game shuru karo!")

    lines.append("\n💡 /ludo type karo join karne ke liye")
    lines.append("⏰ Lobby 5 minute mein expire hogi")
    return "\n".join(lines)


def render_board(game_state: dict) -> str:
    players    = game_state["players"]
    cur_idx    = game_state.get("current_player_idx", 0)
    dice_val   = game_state.get("dice_value")
    dice_rolled = game_state.get("dice_rolled", False)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        "🎲 <b>LUDO — VelocityBots</b>",
        "━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    for i, player in enumerate(players):
        emoji     = COLOR_EMOJI[player["color"]]
        name      = player["name"]
        pieces    = player["pieces"]
        finished  = player.get("finished_count", 0)
        marker    = "  ◄ <b>BAARI</b>" if i == cur_idx else ""

        piece_strs = " ".join(_fmt_piece(p) for p in pieces)
        lines.append(f"{emoji} <b>{name}</b>{marker}")
        lines.append(f"   {piece_strs}")
        if finished:
            lines.append(f"   ✅ {finished}/4 ghar pahunche")
        lines.append("")

    cur_player = players[cur_idx]
    cur_emoji  = COLOR_EMOJI[cur_player["color"]]

    if dice_rolled and dice_val:
        lines.append(f"🎲 Aaya: <b>{DICE_FACE.get(dice_val, str(dice_val))}</b>  ({dice_val})")
    else:
        lines.append(f"🎯 <b>Baari:</b> {cur_emoji} {cur_player['name']} — Dice phenko!")

    return "\n".join(lines)


# ── Keyboards ─────────────────────────────────────────────────────────────────

def lobby_keyboard(game_id: str, player_count: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🎯 Join Karo", callback_data=f"ludo:join:{game_id}")],
    ]
    if player_count >= 2:
        rows.append([InlineKeyboardButton(
            "▶️ Game Shuru Karo!",
            callback_data=f"ludo:start:{game_id}",
        )])
    else:
        rows.append([InlineKeyboardButton(
            "▶️ Shuru karo (2+ chahiye)",
            callback_data=f"ludo:need_more:{game_id}",
        )])
    return InlineKeyboardMarkup(rows)


def roll_keyboard(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎲 Dice Phenko!", callback_data=f"ludo:roll:{game_id}"),
    ]])


def piece_select_keyboard(
    game_id: str,
    valid_pieces: list[int],
    pieces: list[int],
    dice: int,
) -> InlineKeyboardMarkup:
    rows = []
    for i in valid_pieces:
        pos = pieces[i]
        if pos == -1:
            pos_str = "🏠 Ghar se bahar aao"
        elif pos >= 52:
            pos_str = f"🏡 Col {pos - 51} → {min(pos + dice - 51, 6)}"
        else:
            pos_str = f"·{pos}· → ·{pos + dice}·"
        rows.append([InlineKeyboardButton(
            f"{PIECE_LABEL[i]} Piece {i+1}: {pos_str}",
            callback_data=f"ludo:move:{game_id}:{i}",
        )])
    return InlineKeyboardMarkup(rows)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _display_name(user) -> str:
    name = (user.first_name or "").strip()
    if user.last_name:
        name = (name + " " + user.last_name).strip()
    return name or f"User{user.id}"


def _cancel_turn_timeout(context, game_id: str):
    for job in context.job_queue.get_jobs_by_name(f"ludo_turn_{game_id}"):
        job.schedule_removal()


def _schedule_turn_timeout(context, game_id: str, group_id: int, player_idx: int):
    _cancel_turn_timeout(context, game_id)
    context.job_queue.run_once(
        _ludo_turn_timeout,
        when=LUDO_TURN_TIMEOUT,
        data={"game_id": game_id, "group_id": group_id, "player_idx": player_idx},
        name=f"ludo_turn_{game_id}",
    )


# ── /ludo command ─────────────────────────────────────────────────────────────

async def cmd_ludo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if chat.type == "private":
        await update.message.reply_text(
            "⚠️ Ludo sirf groups mein khela ja sakta hai!\n"
            "Kisi group mein /ludo type karo. 🎲"
        )
        return

    existing = ldb.get_active_ludo(chat.id)

    # ── Existing waiting lobby ────────────────────────────────────────────────
    if existing and existing["status"] == "waiting":
        game_id = existing["game_id"]

        if ldb.is_player_in_game(game_id, user.id):
            await update.message.reply_text(
                "⚠️ Tum pehle se lobby mein ho!\n\n"
                + render_lobby(existing),
                parse_mode=constants.ParseMode.HTML,
                reply_markup=lobby_keyboard(game_id, len(existing["players"])),
            )
            return

        if len(existing["players"]) >= 4:
            await update.message.reply_text("❌ Lobby full hai! (4/4 players)")
            return

        success, color = ldb.join_ludo_game(game_id, user.id, _display_name(user))
        if not success:
            await update.message.reply_text("❌ Join nahi ho saka. Dobara try karo.")
            return

        updated = ldb.get_ludo_game(game_id)
        c_emoji = COLOR_EMOJI[color]
        await update.message.reply_text(
            f"✅ {c_emoji} <b>{_display_name(user)}</b> game mein join ho gaye!\n\n"
            + render_lobby(updated),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=lobby_keyboard(game_id, len(updated["players"])),
        )

        # Update original lobby message if we have its id
        lobby_msg_id = existing.get("lobby_message_id")
        if lobby_msg_id:
            try:
                await context.bot.edit_message_text(
                    render_lobby(updated),
                    chat_id=chat.id,
                    message_id=lobby_msg_id,
                    parse_mode=constants.ParseMode.HTML,
                    reply_markup=lobby_keyboard(game_id, len(updated["players"])),
                )
            except TelegramError:
                pass
        return

    # ── Active playing game ───────────────────────────────────────────────────
    if existing and existing["status"] == "playing":
        await update.message.reply_text(
            "⚠️ Group mein ek ludo game pehle se chal rahi hai!\n"
            "Uske khatam hone ka wait karo ya admin /lend kar sakta hai. 🎲"
        )
        return

    # ── Create new lobby ──────────────────────────────────────────────────────
    game_id = str(uuid.uuid4())
    ldb.create_ludo_game(game_id, chat.id, user.id, _display_name(user))
    game = ldb.get_ludo_game(game_id)

    sent = await update.message.reply_text(
        render_lobby(game),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=lobby_keyboard(game_id, 1),
    )
    ldb.update_lobby_message_id(game_id, sent.message_id)

    # Schedule lobby expiry
    context.job_queue.run_once(
        _ludo_lobby_timeout,
        when=LUDO_JOIN_TIMEOUT,
        data={"game_id": game_id, "group_id": chat.id},
        name=f"ludo_lobby_{game_id}",
    )


# ── Callback dispatcher ───────────────────────────────────────────────────────

async def cb_ludo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    parts  = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    game_id = parts[2] if len(parts) > 2 else None

    if action == "join":
        await _cb_join(query, context, game_id)
    elif action == "start":
        await _cb_start(query, context, game_id)
    elif action == "need_more":
        await query.answer(
            "❌ Kam se kam 2 players chahiye game start karne ke liye!",
            show_alert=True,
        )
    elif action == "roll":
        await _cb_roll(query, context, game_id)
    elif action == "move":
        piece_idx = int(parts[3]) if len(parts) > 3 else 0
        await _cb_move(query, context, game_id, piece_idx)
    else:
        await query.answer()


# ── Join callback ─────────────────────────────────────────────────────────────

async def _cb_join(query, context, game_id: str):
    user = query.from_user

    game = ldb.get_ludo_game(game_id)
    if not game or game["status"] != "waiting":
        await query.answer("Lobby abhi available nahi hai!", show_alert=True)
        return

    if ldb.is_player_in_game(game_id, user.id):
        await query.answer("Tum pehle se is lobby mein ho! 🎮", show_alert=True)
        return

    if len(game["players"]) >= 4:
        await query.answer("Lobby full hai! (4/4 players) 😢", show_alert=True)
        return

    success, color = ldb.join_ludo_game(game_id, user.id, _display_name(user))
    if not success:
        await query.answer("Join nahi ho saka!", show_alert=True)
        return

    updated = ldb.get_ludo_game(game_id)
    c_emoji = COLOR_EMOJI[color]
    await query.answer(f"{c_emoji} Tum join ho gaye!")

    try:
        await query.edit_message_text(
            render_lobby(updated),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=lobby_keyboard(game_id, len(updated["players"])),
        )
    except TelegramError:
        pass


# ── Start callback ────────────────────────────────────────────────────────────

async def _cb_start(query, context, game_id: str):
    user = query.from_user

    game = ldb.get_ludo_game(game_id)
    if not game or game["status"] != "waiting":
        await query.answer("Game already shuru ya khatam ho gaya!", show_alert=True)
        return

    if not ldb.is_player_in_game(game_id, user.id):
        await query.answer("❌ Tum is game mein nahi ho!", show_alert=True)
        return

    if len(game["players"]) < 2:
        await query.answer("❌ Kam se kam 2 players chahiye!", show_alert=True)
        return

    # Cancel lobby timeout
    for job in context.job_queue.get_jobs_by_name(f"ludo_lobby_{game_id}"):
        job.schedule_removal()

    ldb.start_ludo_game(game_id)
    game = ldb.get_ludo_game(game_id)

    await query.answer("🎲 Ludo shuru ho raha hai!")

    first_player = game["players"][0]
    f_emoji = COLOR_EMOJI[first_player["color"]]

    board_text = (
        render_board(game)
        + f"\n\n🎯 {f_emoji} <b>{first_player['name']}</b> pehle khelenga!\n"
        + "Niche button daba ke dice phenko! 🎲"
    )

    try:
        msg = await query.edit_message_text(
            board_text,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=roll_keyboard(game_id),
        )
        ldb.update_ludo_message_id(game_id, msg.message_id)
    except TelegramError:
        sent = await query.message.reply_text(
            board_text,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=roll_keyboard(game_id),
        )
        ldb.update_ludo_message_id(game_id, sent.message_id)

    _schedule_turn_timeout(context, game_id, game["group_id"], 0)


# ── Roll callback ─────────────────────────────────────────────────────────────

async def _cb_roll(query, context, game_id: str):
    user = query.from_user

    game = ldb.get_ludo_game(game_id)
    if not game or game["status"] != "playing":
        await query.answer("Game active nahi hai!", show_alert=True)
        return

    cur_idx    = game["current_player_idx"]
    cur_player = game["players"][cur_idx]

    if cur_player["user_id"] != user.id:
        await query.answer(
            f"⏳ Abhi {cur_player['name']} ki baari hai!",
            show_alert=True,
        )
        return

    if game.get("dice_rolled", False):
        await query.answer("Tum pehle hi dice phenko chuke ho!", show_alert=True)
        return

    dice  = random.randint(1, 6)
    consec = game.get("consecutive_sixes", 0)
    consec = consec + 1 if dice == 6 else 0

    # Three consecutive sixes → forfeit
    if consec >= 3:
        await query.answer(
            f"🎲 {dice}! Teesri baar 6 — baari gayi! 😅",
            show_alert=True,
        )
        next_idx = next_active_player(game, cur_idx)
        ldb.update_ludo_game_state(game_id, {
            "current_player_idx": next_idx,
            "dice_value":         None,
            "dice_rolled":        False,
            "consecutive_sixes":  0,
        })
        game = ldb.get_ludo_game(game_id)
        _cancel_turn_timeout(context, game_id)
        _schedule_turn_timeout(context, game_id, game["group_id"], next_idx)
        try:
            await query.edit_message_text(
                render_board(game)
                + f"\n\n⚠️ {cur_player['name']} ne teesri baar 6 daala! Baari gayi!",
                parse_mode=constants.ParseMode.HTML,
                reply_markup=roll_keyboard(game_id),
            )
        except TelegramError:
            pass
        return

    ldb.update_ludo_game_state(game_id, {
        "dice_value":        dice,
        "dice_rolled":       True,
        "consecutive_sixes": consec,
    })
    game = ldb.get_ludo_game(game_id)

    valid = get_valid_moves(cur_player, dice)

    if not valid:
        # No move possible — auto-advance
        await query.answer(f"🎲 {dice} aaya! Koi move nahi. Baari gayi.", show_alert=False)
        next_idx = next_active_player(game, cur_idx)
        ldb.update_ludo_game_state(game_id, {
            "current_player_idx": next_idx,
            "dice_value":         None,
            "dice_rolled":        False,
            "consecutive_sixes":  0,
        })
        game = ldb.get_ludo_game(game_id)
        _cancel_turn_timeout(context, game_id)
        _schedule_turn_timeout(context, game_id, game["group_id"], next_idx)
        try:
            await query.edit_message_text(
                render_board(game)
                + f"\n\n{DICE_FACE[dice]} {cur_player['name']} ko {dice} aaya — koi move nahi, baari gayi!",
                parse_mode=constants.ParseMode.HTML,
                reply_markup=roll_keyboard(game_id),
            )
        except TelegramError:
            pass
        return

    await query.answer(f"🎲 {dice} aaya!")

    # Auto-move if only one choice
    if len(valid) == 1:
        _cancel_turn_timeout(context, game_id)
        await _do_move(query, context, game_id, game, cur_idx, valid[0], dice, consec)
        return

    # Ask player to choose piece
    pieces    = cur_player["pieces"]
    board_txt = (
        render_board(game)
        + f"\n\n{DICE_FACE[dice]} <b>{dice} aaya!</b> Kaun sa piece hilao?"
    )
    try:
        await query.edit_message_text(
            board_txt,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=piece_select_keyboard(game_id, valid, pieces, dice),
        )
    except TelegramError:
        pass


# ── Move piece callback ───────────────────────────────────────────────────────

async def _cb_move(query, context, game_id: str, piece_idx: int):
    user = query.from_user

    game = ldb.get_ludo_game(game_id)
    if not game or game["status"] != "playing":
        await query.answer("Game active nahi hai!", show_alert=True)
        return

    cur_idx    = game["current_player_idx"]
    cur_player = game["players"][cur_idx]

    if cur_player["user_id"] != user.id:
        await query.answer("Tumhari baari nahi hai!", show_alert=True)
        return

    if not game.get("dice_rolled", False):
        await query.answer("Pehle dice phenko!", show_alert=True)
        return

    dice = game.get("dice_value")
    if dice is None:
        await query.answer("Dice value nahi mili!", show_alert=True)
        return

    valid = get_valid_moves(cur_player, dice)
    if piece_idx not in valid:
        await query.answer("❌ Yeh piece is dice se move nahi ho sakta!", show_alert=True)
        return

    await query.answer("✅ Move hua!")
    _cancel_turn_timeout(context, game_id)
    consec = game.get("consecutive_sixes", 0)
    await _do_move(query, context, game_id, game, cur_idx, piece_idx, dice, consec)


# ── Execute move ──────────────────────────────────────────────────────────────

async def _do_move(query, context, game_id, game, player_idx, piece_idx, dice, consec_sixes):
    """Apply move, check win, advance turn, update Telegram message."""
    new_state, captures, piece_finished = apply_move(game, player_idx, piece_idx, dice)
    cur_player = new_state["players"][player_idx]

    # ── Win condition ────────────────────────────────────────────────────────
    if all_finished(cur_player):
        ldb.update_ludo_game_state(game_id, {
            "players": new_state["players"],
            "status":  "finished",
        })
        ldb.end_ludo_game(game_id, cur_player["user_id"], cur_player["name"])

        c_emoji  = COLOR_EMOJI[cur_player["color"]]
        win_text = (
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "🏆 <b>LUDO KHATAM!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎉 {c_emoji} <b>{cur_player['name']}</b> ne jeeta! 🥇\n\n"
            "Sabhi 4 pieces ghar pahunch gayi!\n\n"
            "Naya game ke liye /ludo type karo! 🎲"
        )
        try:
            await query.edit_message_text(win_text, parse_mode=constants.ParseMode.HTML)
        except TelegramError:
            await query.message.reply_text(win_text, parse_mode=constants.ParseMode.HTML)
        return

    # ── Determine next turn ──────────────────────────────────────────────────
    # Bonus turn: rolled 6, captured opponent, or sent piece home
    got_bonus = dice == 6 or bool(captures) or piece_finished

    if got_bonus:
        next_idx    = player_idx
        next_consec = consec_sixes if dice == 6 else 0
    else:
        next_idx    = next_active_player(new_state, player_idx)
        next_consec = 0

    ldb.update_ludo_game_state(game_id, {
        "players":             new_state["players"],
        "current_player_idx":  next_idx,
        "dice_value":          None,
        "dice_rolled":         False,
        "consecutive_sixes":   next_consec,
    })
    game = ldb.get_ludo_game(game_id)

    # Build event summary
    event_lines = []
    if piece_finished:
        event_lines.append(
            f"🏆 <b>Piece {piece_idx+1}</b> ghar pahunch gaya! Bonus baari mili!"
        )
    for cap in captures:
        c_emoji = COLOR_EMOJI[cap["color"]]
        event_lines.append(
            f"💥 {c_emoji} {cap['player_name']} ka Piece {cap['piece_idx']+1} pakad liya! Wapas ghar gaya!"
        )
    if dice == 6 and not piece_finished:
        event_lines.append("🎲 6 aaya — ek aur baari milegi!")

    board_text = render_board(game)
    if event_lines:
        board_text += "\n\n" + "\n".join(event_lines)

    _schedule_turn_timeout(context, game_id, game["group_id"], next_idx)

    try:
        await query.edit_message_text(
            board_text,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=roll_keyboard(game_id),
        )
    except TelegramError:
        try:
            sent = await query.message.reply_text(
                board_text,
                parse_mode=constants.ParseMode.HTML,
                reply_markup=roll_keyboard(game_id),
            )
            ldb.update_ludo_message_id(game_id, sent.message_id)
        except TelegramError:
            pass


# ── Turn / lobby timeouts ─────────────────────────────────────────────────────

async def _ludo_turn_timeout(context: ContextTypes.DEFAULT_TYPE):
    data       = context.job.data
    game_id    = data["game_id"]
    group_id   = data["group_id"]

    game = ldb.get_ludo_game(game_id)
    if not game or game["status"] != "playing":
        return

    cur_idx    = game["current_player_idx"]
    cur_player = game["players"][cur_idx]
    next_idx   = next_active_player(game, cur_idx)

    ldb.update_ludo_game_state(game_id, {
        "current_player_idx": next_idx,
        "dice_value":         None,
        "dice_rolled":        False,
        "consecutive_sixes":  0,
    })
    game = ldb.get_ludo_game(game_id)

    nxt_player  = game["players"][next_idx]
    c_emoji     = COLOR_EMOJI[cur_player["color"]]
    n_emoji     = COLOR_EMOJI[nxt_player["color"]]

    try:
        await context.bot.send_message(
            group_id,
            f"⏰ {c_emoji} <b>{cur_player['name']}</b> ne 2 minute mein move nahi kiya! Baari skip.\n"
            f"Ab {n_emoji} <b>{nxt_player['name']}</b> ki baari hai.\n\n"
            + render_board(game),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=roll_keyboard(game_id),
        )
    except TelegramError as e:
        logger.warning("Ludo turn timeout send error: %s", e)

    _schedule_turn_timeout(context, game_id, group_id, next_idx)


async def _ludo_lobby_timeout(context: ContextTypes.DEFAULT_TYPE):
    data     = context.job.data
    game_id  = data["game_id"]
    group_id = data["group_id"]

    game = ldb.get_ludo_game(game_id)
    if not game or game["status"] != "waiting":
        return

    ldb.end_ludo_game(game_id)
    try:
        await context.bot.send_message(
            group_id,
            "⏰ Ludo lobby 5 minute mein expire ho gaya!\n"
            "Naya game ke liye /ludo type karo. 🎲",
        )
    except TelegramError:
        pass


# ── /lend command ─────────────────────────────────────────────────────────────

async def cmd_lend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if chat.type == "private":
        await update.message.reply_text("⚠️ Groups only!")
        return

    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ Sirf group admins /lend kar sakte hain.")
        return

    game = ldb.get_active_ludo(chat.id)
    if not game:
        await update.message.reply_text("❌ Koi active ludo game nahi hai.")
        return

    game_id = game["game_id"]
    for job_name in [f"ludo_lobby_{game_id}", f"ludo_turn_{game_id}"]:
        for job in context.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()

    ldb.end_ludo_game(game_id)
    await update.message.reply_text(
        f"🛑 <b>{user.first_name}</b> ne ludo game band kar diya.\n"
        "Naya game ke liye /ludo type karo! 🎲",
        parse_mode=constants.ParseMode.HTML,
    )


# ── /lstats command ───────────────────────────────────────────────────────────

async def cmd_lstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    stats = ldb.get_ludo_stats(user.id)

    name = _display_name(user)
    wins = stats["wins"]
    played = stats["games_played"]
    win_rate = f"{(wins/played*100):.0f}%" if played > 0 else "N/A"

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎲 <b>{name} ke Ludo Stats</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎮 <b>Games Played:</b> {played}\n"
        f"🏆 <b>Wins:</b> {wins}\n"
        f"📊 <b>Win Rate:</b> {win_rate}",
        parse_mode=constants.ParseMode.HTML,
    )


# ── Handler registration ──────────────────────────────────────────────────────

def register_ludo_handlers(app: Application):
    app.add_handler(CommandHandler("ludo",   cmd_ludo))
    app.add_handler(CommandHandler("lend",   cmd_lend))
    app.add_handler(CommandHandler("lstats", cmd_lstats))
    app.add_handler(CallbackQueryHandler(cb_ludo, pattern=r"^ludo:"))
