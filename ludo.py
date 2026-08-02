"""
ludo.py — Ludo Game (Group Mode) for VelocityBots.

Clean rewrite. Uses ludo_engine.py for all game logic — no duplication.
Supports 2–4 players per group.

Bugs fixed vs old version:
  - Three-sixes: now sends most-advanced piece back to home (was just skipping turn)
  - Turn timeout: edits existing board message instead of sending a new one
    (old message's Roll button stays dead; new message is the active one)
  - Piece-finish bonus turn: correctly granted (was missing in some paths)
  - consecutive_sixes always reset to 0 when turn changes player
  - Board display: human-readable positions, full event summary after each move

Commands:
    /ludo   — Create lobby / join existing
    /lend   — Admin: force-end the current game
    /lstats — Your personal stats
"""

import logging
import uuid

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, constants,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from telegram.error import TelegramError

import ludo_db as ldb
import ludo_engine as engine

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

LUDO_JOIN_TIMEOUT = 300    # seconds: lobby auto-closes after this
LUDO_TURN_TIMEOUT = 120    # seconds: turn auto-skips after this

# ── Display constants ─────────────────────────────────────────────────────────

COLOR_EMOJI = {"red": "🔴", "green": "🟢", "yellow": "🟡", "blue": "🔵"}
PIECE_LABEL = ["①", "②", "③", "④"]
DICE_FACE   = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣"}


# ── Piece position formatting ─────────────────────────────────────────────────

def _fmt_piece(pos: int) -> str:
    """Short symbol for one piece: home / ring position / home-col slot / done."""
    if pos == -1:  return "🏠"
    if pos == 58:  return "🏆"
    if pos >= 52:  return f"🏡{pos - 51}"   # home column slots 1-6
    return f"[{pos + 1}]"                    # main ring, 1-indexed


def _piece_move_desc(pos: int, dice: int) -> str:
    """Human-readable description of where a piece will move to."""
    if pos == -1:
        return "🏠 Ghar se bahar aao"
    new_pos = pos + dice
    if pos >= 52:
        if new_pos >= 58:
            return f"🏡Col{pos - 51} → 🏆 Ghar!"
        return f"🏡Col{pos - 51} → 🏡Col{new_pos - 51}"
    if new_pos >= 52:
        return f"Ring {pos + 1} → 🏡 Home Col"
    return f"Ring {pos + 1} → Ring {new_pos + 1}"


# ── Board & lobby rendering ───────────────────────────────────────────────────

def render_lobby(game: dict) -> str:
    players = game["players"]
    n = len(players)
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        "🎲 <b>LUDO — Lobby</b>",
        "━━━━━━━━━━━━━━━━━━━━━\n",
        f"👥 <b>Players:</b> {n}/4\n",
    ]
    for p in players:
        lines.append(f"  {COLOR_EMOJI[p['color']]} {p['name']}")
    lines.append("")
    if n < 2:
        lines.append("⏳ Kam se kam 2 players chahiye.")
    elif n < 4:
        lines.append(f"✅ {n} players ready! Ab start kar sakte ho ya aur ka wait karo (max 4).")
    else:
        lines.append("✅ 4/4 players! Lobby full — game shuru karo!")
    lines += ["", "💡 Join karne ke liye /ludo dabao", "⏰ Lobby 5 minute mein expire hogi"]
    return "\n".join(lines)


def render_board(game: dict, event_lines: list = None) -> str:
    players     = game["players"]
    cur_idx     = game.get("current_player_idx", 0)
    dice_val    = game.get("dice_value")
    dice_rolled = game.get("dice_rolled", False)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        "🎲 <b>LUDO</b>",
        "━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    for i, player in enumerate(players):
        emoji    = COLOR_EMOJI[player["color"]]
        pieces   = player["pieces"]
        finished = player.get("finished_count", 0)
        marker   = "  ◄ <b>BAARI</b>" if i == cur_idx else ""

        piece_strs = "  ".join(
            f"{PIECE_LABEL[j]}{_fmt_piece(p)}" for j, p in enumerate(pieces)
        )
        lines.append(f"{emoji} <b>{player['name']}</b>{marker}")
        lines.append(f"   {piece_strs}")
        if finished:
            lines.append(f"   ✅ {finished}/4 pieces ghar")
        lines.append("")

    if event_lines:
        lines += event_lines
        lines.append("")

    cur_player = players[cur_idx]
    cur_emoji  = COLOR_EMOJI[cur_player["color"]]
    if dice_rolled and dice_val:
        lines.append(
            f"🎲 {cur_emoji} <b>{cur_player['name']}</b> ko "
            f"{DICE_FACE[dice_val]} ({dice_val}) aaya — piece chuno:"
        )
    else:
        lines.append(
            f"🎯 <b>Baari:</b> {cur_emoji} <b>{cur_player['name']}</b> — Dice phenko!"
        )
    return "\n".join(lines)


# ── Keyboards ──────────────────────────────────────────────────────────────────

def lobby_keyboard(game_id: str, player_count: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🎯 Join Karo", callback_data=f"ludo:join:{game_id}")]]
    if player_count >= 2:
        rows.append([InlineKeyboardButton(
            "▶️ Game Shuru Karo!", callback_data=f"ludo:start:{game_id}")])
    else:
        rows.append([InlineKeyboardButton(
            "▶️ Shuru karo (2+ chahiye)", callback_data=f"ludo:need_more:{game_id}")])
    return InlineKeyboardMarkup(rows)


def roll_keyboard(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎲 Dice Phenko!", callback_data=f"ludo:roll:{game_id}"),
    ]])


def piece_select_keyboard(
    game_id: str, valid_pieces: list, pieces: list, dice: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{PIECE_LABEL[i]} Piece {i + 1}: {_piece_move_desc(pieces[i], dice)}",
            callback_data=f"ludo:move:{game_id}:{i}",
        )]
        for i in valid_pieces
    ])


# ── Internal helpers ──────────────────────────────────────────────────────────

def _display_name(user) -> str:
    name = (user.first_name or "").strip()
    if user.last_name:
        name = f"{name} {user.last_name}".strip()
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


async def _send_board(query, game_id: str, text: str, markup=None):
    """
    Try to edit the current message. If that fails (message too old, not modified,
    etc.) send a new message. Always returns the live message so we can track its ID.
    """
    msg = None
    try:
        msg = await query.edit_message_text(
            text, parse_mode=constants.ParseMode.HTML, reply_markup=markup)
    except TelegramError:
        try:
            msg = await query.message.reply_text(
                text, parse_mode=constants.ParseMode.HTML, reply_markup=markup)
        except TelegramError as e:
            logger.error("_send_board: both edit and reply failed: %s", e)
    if msg:
        ldb.update_ludo_message_id(game_id, msg.message_id)
    return msg


# ── /ludo command ──────────────────────────────────────────────────────────────

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

    # ── Existing waiting lobby ─────────────────────────────────────────────────
    if existing and existing["status"] == "waiting":
        game_id = existing["game_id"]

        if ldb.is_player_in_game(game_id, user.id):
            await update.message.reply_text(
                "⚠️ Tum pehle se lobby mein ho!\n\n" + render_lobby(existing),
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
        await update.message.reply_text(
            f"✅ {COLOR_EMOJI[color]} <b>{_display_name(user)}</b> game mein join ho gaye!\n\n"
            + render_lobby(updated),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=lobby_keyboard(game_id, len(updated["players"])),
        )
        # Also refresh the original lobby message
        lobby_msg_id = existing.get("lobby_message_id")
        if lobby_msg_id:
            try:
                await context.bot.edit_message_text(
                    render_lobby(updated),
                    chat_id=chat.id, message_id=lobby_msg_id,
                    parse_mode=constants.ParseMode.HTML,
                    reply_markup=lobby_keyboard(game_id, len(updated["players"])),
                )
            except TelegramError:
                pass
        return

    # ── Active game in progress ────────────────────────────────────────────────
    if existing and existing["status"] == "playing":
        await update.message.reply_text(
            "⚠️ Ek ludo game pehle se chal rahi hai!\n"
            "Uske khatam hone ka wait karo, ya admin /lend kar sakta hai. 🎲"
        )
        return

    # ── Create new lobby ───────────────────────────────────────────────────────
    game_id = uuid.uuid4().hex[:8]
    ldb.create_ludo_game(game_id, chat.id, user.id, _display_name(user))
    game = ldb.get_ludo_game(game_id)

    sent = await update.message.reply_text(
        render_lobby(game),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=lobby_keyboard(game_id, 1),
    )
    ldb.update_lobby_message_id(game_id, sent.message_id)

    context.job_queue.run_once(
        _ludo_lobby_timeout,
        when=LUDO_JOIN_TIMEOUT,
        data={"game_id": game_id, "group_id": chat.id},
        name=f"ludo_lobby_{game_id}",
    )


# ── Callback dispatcher ────────────────────────────────────────────────────────

async def cb_ludo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    parts   = query.data.split(":")
    action  = parts[1] if len(parts) > 1 else ""
    game_id = parts[2] if len(parts) > 2 else None

    if   action == "join":      await _cb_join(query, context, game_id)
    elif action == "start":     await _cb_start(query, context, game_id)
    elif action == "need_more":
        await query.answer("❌ Kam se kam 2 players chahiye!", show_alert=True)
    elif action == "roll":      await _cb_roll(query, context, game_id)
    elif action == "move":
        piece_idx = int(parts[3]) if len(parts) > 3 else 0
        await _cb_move(query, context, game_id, piece_idx)
    else:
        await query.answer()


# ── Join callback ──────────────────────────────────────────────────────────────

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
        await query.answer("Lobby full hai! (4/4) 😢", show_alert=True)
        return

    success, color = ldb.join_ludo_game(game_id, user.id, _display_name(user))
    if not success:
        await query.answer("Join nahi ho saka!", show_alert=True)
        return

    updated = ldb.get_ludo_game(game_id)
    await query.answer(f"{COLOR_EMOJI[color]} Tum join ho gaye!")
    try:
        await query.edit_message_text(
            render_lobby(updated),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=lobby_keyboard(game_id, len(updated["players"])),
        )
    except TelegramError:
        pass


# ── Start callback ─────────────────────────────────────────────────────────────

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

    for job in context.job_queue.get_jobs_by_name(f"ludo_lobby_{game_id}"):
        job.schedule_removal()

    ldb.start_ludo_game(game_id)
    game = ldb.get_ludo_game(game_id)
    await query.answer("🎲 Ludo shuru ho raha hai!")

    first = game["players"][0]
    board_text = (
        render_board(game)
        + f"\n\n🎯 {COLOR_EMOJI[first['color']]} <b>{first['name']}</b> pehle khelenga!\n"
          "Niche button daba ke dice phenko! 🎲"
    )

    msg = None
    try:
        msg = await query.edit_message_text(
            board_text, parse_mode=constants.ParseMode.HTML,
            reply_markup=roll_keyboard(game_id),
        )
    except TelegramError:
        try:
            msg = await query.message.reply_text(
                board_text, parse_mode=constants.ParseMode.HTML,
                reply_markup=roll_keyboard(game_id),
            )
        except TelegramError:
            pass
    if msg:
        ldb.update_ludo_message_id(game_id, msg.message_id)

    _schedule_turn_timeout(context, game_id, game["group_id"], 0)


# ── Roll callback ──────────────────────────────────────────────────────────────

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
            f"⏳ Abhi {cur_player['name']} ki baari hai!", show_alert=True)
        return

    # Prevent double-roll: if dice already rolled, remind them to pick a piece
    if game.get("dice_rolled", False):
        await query.answer("Tum pehle hi dice phenko chuke ho! Piece chuno.", show_alert=True)
        return

    dice   = engine.roll_dice()
    consec = game.get("consecutive_sixes", 0)
    consec = consec + 1 if dice == 6 else 0

    # ── Three consecutive sixes: penalty + forfeit ────────────────────────────
    if consec >= 3:
        # FIX: apply penalty — send most-advanced on-board piece back to home
        new_gs   = engine.apply_three_sixes_penalty(game, cur_idx)
        next_idx = engine.next_active_player(new_gs, cur_idx)
        ldb.update_ludo_game_state(game_id, {
            "players":            new_gs["players"],
            "current_player_idx": next_idx,
            "dice_value":         None,
            "dice_rolled":        False,
            "consecutive_sixes":  0,
        })
        game       = ldb.get_ludo_game(game_id)
        nxt_player = game["players"][next_idx]

        _cancel_turn_timeout(context, game_id)
        _schedule_turn_timeout(context, game_id, game["group_id"], next_idx)

        await query.answer(f"🎲 {dice}! Teen 6 — piece wapas ghar! 😅", show_alert=True)
        await _send_board(
            query, game_id,
            render_board(game, [
                f"⚠️ <b>{cur_player['name']}</b> ne teesri baar 6 daala!",
                "Sabse aage wala piece wapas ghar gaya! 🏠",
                f"Ab {COLOR_EMOJI[nxt_player['color']]} <b>{nxt_player['name']}</b> ki baari.",
            ]),
            markup=roll_keyboard(game_id),
        )
        return

    # ── Normal roll: save dice, check valid moves ─────────────────────────────
    ldb.update_ludo_game_state(game_id, {
        "dice_value":        dice,
        "dice_rolled":       True,
        "consecutive_sixes": consec,
    })
    game       = ldb.get_ludo_game(game_id)
    cur_player = game["players"][cur_idx]   # refresh after DB save

    valid = engine.get_valid_moves(cur_player, dice)

    # ── No valid moves: auto-skip ─────────────────────────────────────────────
    if not valid:
        next_idx = engine.next_active_player(game, cur_idx)
        ldb.update_ludo_game_state(game_id, {
            "current_player_idx": next_idx,
            "dice_value":         None,
            "dice_rolled":        False,
            "consecutive_sixes":  0,
        })
        game       = ldb.get_ludo_game(game_id)
        nxt_player = game["players"][next_idx]

        _cancel_turn_timeout(context, game_id)
        _schedule_turn_timeout(context, game_id, game["group_id"], next_idx)

        await query.answer(f"🎲 {dice} aaya! Koi move nahi — baari gayi!")
        await _send_board(
            query, game_id,
            render_board(game, [
                f"{DICE_FACE[dice]} <b>{cur_player['name']}</b> ko {dice} aaya — koi move nahi, baari gayi!",
                f"Ab {COLOR_EMOJI[nxt_player['color']]} <b>{nxt_player['name']}</b> ki baari.",
            ]),
            markup=roll_keyboard(game_id),
        )
        return

    await query.answer(f"🎲 {dice} aaya!")

    # ── Single valid move: auto-move without asking ───────────────────────────
    if len(valid) == 1:
        _cancel_turn_timeout(context, game_id)
        await _do_move(query, context, game_id, game, cur_idx, valid[0], dice, consec)
        return

    # ── Multiple valid moves: show piece selection ────────────────────────────
    try:
        await query.edit_message_text(
            render_board(game, [f"{DICE_FACE[dice]} <b>{dice} aaya!</b> Kaun sa piece hilao?"]),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=piece_select_keyboard(game_id, valid, cur_player["pieces"], dice),
        )
    except TelegramError:
        pass


# ── Move piece callback ────────────────────────────────────────────────────────

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

    valid = engine.get_valid_moves(cur_player, dice)
    if piece_idx not in valid:
        await query.answer("❌ Yeh piece is dice se move nahi ho sakta!", show_alert=True)
        return

    await query.answer("✅ Move hua!")
    _cancel_turn_timeout(context, game_id)
    consec = game.get("consecutive_sixes", 0)
    await _do_move(query, context, game_id, game, cur_idx, piece_idx, dice, consec)


# ── Execute move ───────────────────────────────────────────────────────────────

async def _do_move(query, context, game_id, game, player_idx, piece_idx, dice, consec_sixes):
    """Apply a move, handle win condition, advance turn, update the board message."""
    new_state, captures, piece_finished = engine.apply_move(
        game, player_idx, piece_idx, dice)
    cur_player = new_state["players"][player_idx]

    # ── Win condition ──────────────────────────────────────────────────────────
    if engine.all_finished(cur_player):
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
            try:
                await query.message.reply_text(win_text, parse_mode=constants.ParseMode.HTML)
            except TelegramError:
                pass
        return

    # ── Determine next turn ────────────────────────────────────────────────────
    # Bonus turn: rolled 6, captured an opponent, or finished a piece
    got_bonus = dice == 6 or bool(captures) or piece_finished

    if got_bonus:
        next_idx    = player_idx
        # Keep six-counter only when the bonus came from a 6; captures & finishes reset it
        next_consec = consec_sixes if dice == 6 else 0
    else:
        next_idx    = engine.next_active_player(new_state, player_idx)
        next_consec = 0

    ldb.update_ludo_game_state(game_id, {
        "players":            new_state["players"],
        "current_player_idx": next_idx,
        "dice_value":         None,
        "dice_rolled":        False,
        "consecutive_sixes":  next_consec,
    })
    game = ldb.get_ludo_game(game_id)

    # ── Build event summary ────────────────────────────────────────────────────
    event_lines = []
    if piece_finished:
        event_lines.append(
            f"🏆 <b>Piece {piece_idx + 1}</b> ghar pahunch gaya! Bonus baari mili!")
    for cap in captures:
        c = COLOR_EMOJI[cap["color"]]
        event_lines.append(
            f"💥 {c} <b>{cap['player_name']}</b> ka Piece {cap['piece_idx'] + 1} pakad liya! Wapas ghar gaya!")
    if dice == 6 and not piece_finished and not captures:
        event_lines.append("🎲 6 aaya — ek aur baari mili!")

    nxt_player = game["players"][next_idx]
    if next_idx != player_idx:
        event_lines.append(
            f"🎯 Ab {COLOR_EMOJI[nxt_player['color']]} <b>{nxt_player['name']}</b> ki baari.")

    _schedule_turn_timeout(context, game_id, game["group_id"], next_idx)
    await _send_board(
        query, game_id,
        render_board(game, event_lines),
        markup=roll_keyboard(game_id),
    )


# ── Turn & lobby timeouts ──────────────────────────────────────────────────────

async def _ludo_turn_timeout(context: ContextTypes.DEFAULT_TYPE):
    data     = context.job.data
    game_id  = data["game_id"]
    group_id = data["group_id"]

    game = ldb.get_ludo_game(game_id)
    if not game or game["status"] != "playing":
        return

    cur_idx    = game["current_player_idx"]
    cur_player = game["players"][cur_idx]
    next_idx   = engine.next_active_player(game, cur_idx)

    ldb.update_ludo_game_state(game_id, {
        "current_player_idx": next_idx,
        "dice_value":         None,
        "dice_rolled":        False,
        "consecutive_sixes":  0,
    })
    game       = ldb.get_ludo_game(game_id)
    nxt_player = game["players"][next_idx]

    board_text = render_board(game, [
        f"⏰ {COLOR_EMOJI[cur_player['color']]} <b>{cur_player['name']}</b> "
        f"ne 2 minute mein move nahi kiya — baari skip.",
        f"Ab {COLOR_EMOJI[nxt_player['color']]} <b>{nxt_player['name']}</b> ki baari.",
    ])

    # FIX: try to edit the existing board message first so old Roll button dies
    msg_id = game.get("message_id")
    msg    = None
    if msg_id:
        try:
            msg = await context.bot.edit_message_text(
                board_text,
                chat_id=group_id, message_id=msg_id,
                parse_mode=constants.ParseMode.HTML,
                reply_markup=roll_keyboard(game_id),
            )
        except TelegramError:
            pass

    if not msg:
        try:
            msg = await context.bot.send_message(
                group_id, board_text,
                parse_mode=constants.ParseMode.HTML,
                reply_markup=roll_keyboard(game_id),
            )
        except TelegramError as e:
            logger.warning("Ludo turn timeout send error: %s", e)

    if msg:
        ldb.update_ludo_message_id(game_id, msg.message_id)

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
            "⏰ Ludo lobby expire ho gayi (5 min)!\n"
            "Naya game ke liye /ludo type karo. 🎲",
        )
    except TelegramError:
        pass


# ── /lend command ──────────────────────────────────────────────────────────────

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


# ── /lstats command ────────────────────────────────────────────────────────────

async def cmd_lstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    stats  = ldb.get_ludo_stats(user.id)
    name   = _display_name(user)
    played = stats["games_played"]
    wins   = stats["wins"]
    rate   = f"{wins / played * 100:.0f}%" if played else "N/A"

    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎲 <b>{name} ke Ludo Stats</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎮 <b>Games Played:</b> {played}\n"
        f"🏆 <b>Wins:</b>        {wins}\n"
        f"📊 <b>Win Rate:</b>     {rate}",
        parse_mode=constants.ParseMode.HTML,
    )


# ── Handler registration ───────────────────────────────────────────────────────

def register_ludo_handlers(app: Application):
    app.add_handler(CommandHandler("ludo",   cmd_ludo))
    app.add_handler(CommandHandler("lend",   cmd_lend))
    app.add_handler(CommandHandler("lstats", cmd_lstats))
    app.add_handler(CallbackQueryHandler(cb_ludo, pattern=r"^ludo:"))
