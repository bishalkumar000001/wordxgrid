"""
ludo_engine.py — Server-authoritative Ludo game logic for the Mini App.
Pure functions only — no Telegram, no DB, no side effects.
Position encoding:
  -1      : home base (not on board)
  0–51    : main ring (relative to color offset)
  52–57   : home column (color-specific, always safe)
  58      : finished (home centre)
"""
import copy
import random

# ── Constants ──────────────────────────────────────────────────────────────────

COLOR_ORDER   = ["red", "green", "yellow", "blue"]

# Absolute track offsets: where each color enters the main ring
COLOR_OFFSETS = {"red": 0, "green": 13, "yellow": 26, "blue": 39}

# Absolute safe squares on the main ring (star cells + entry cells)
SAFE_ABS = {0, 8, 13, 21, 26, 34, 39, 47}


# ── Board coordinate maps (for frontend use) ───────────────────────────────────
# Main track: 52 cells, [row, col] in 15x15 grid

TRACK = [
    # Segment 1: Row 6, cols 1-5 (Red entry at 0)
    [6,1],[6,2],[6,3],[6,4],[6,5],
    # Segment 2: Col 5, rows 5-0 (going up)
    [5,5],[4,5],[3,5],[2,5],[1,5],[0,5],
    # Segment 3: Row 0, cols 6-7
    [0,6],[0,7],
    # Segment 4: Col 8, rows 1-6 (Green entry at abs 13)
    [1,8],[2,8],[3,8],[4,8],[5,8],[6,8],
    # Segment 5: Row 6, cols 9-14
    [6,9],[6,10],[6,11],[6,12],[6,13],[6,14],
    # Segment 6: Col 14, rows 7-8 (Yellow entry at abs 26)
    [7,14],[8,14],
    # Segment 7: Row 8, cols 13-7
    [8,13],[8,12],[8,11],[8,10],[8,9],[8,8],[8,7],
    # Segment 8: Col 7, rows 9-13
    [9,7],[10,7],[11,7],[12,7],[13,7],
    # Segment 9: Row 14, cols 7-6 (Blue entry at abs 39)
    [14,7],[14,6],
    # Segment 10: Col 6, rows 13-8
    [13,6],[12,6],[11,6],[10,6],[9,6],[8,6],
    # Segment 11: Row 8, cols 5-1
    [8,5],[8,4],[8,3],[8,2],[8,1],
]

# Home columns (relative positions 52-57 for each color)
HOME_COLS = {
    "red":    [[7,1],[7,2],[7,3],[7,4],[7,5],[7,6]],
    "green":  [[1,7],[2,7],[3,7],[4,7],[5,7],[6,7]],
    "yellow": [[7,13],[7,12],[7,11],[7,10],[7,9],[7,8]],
    "blue":   [[13,7],[12,7],[11,7],[10,7],[9,7],[8,7]],
}

# Home base piece slots (position -1) for each color
HOME_BASES = {
    "red":    [[1,1],[1,3],[3,1],[3,3]],
    "green":  [[1,11],[1,13],[3,11],[3,13]],
    "yellow": [[11,11],[11,13],[13,11],[13,13]],
    "blue":   [[11,1],[11,3],[13,1],[13,3]],
}

# Finishing center cell
CENTER = [7, 7]


# ── Position helpers ───────────────────────────────────────────────────────────

def rel_to_abs(rel: int, color: str) -> int:
    """Convert relative position to absolute track index."""
    return (rel + COLOR_OFFSETS[color]) % 52


def is_safe_abs(abs_pos: int) -> bool:
    return abs_pos in SAFE_ABS


def get_cell_coords(rel_pos: int, color: str) -> list:
    """
    Return [row, col] for a piece at relative position rel_pos with given color.
    rel_pos: -1 (home base), 0-51 (main ring), 52-57 (home col), 58 (finished)
    piece_idx: 0-3 (for home base slot selection)
    """
    if rel_pos == 58:
        return CENTER
    if rel_pos >= 52:
        return HOME_COLS[color][rel_pos - 52]
    if rel_pos >= 0:
        return TRACK[rel_to_abs(rel_pos, color)]
    return None  # -1: use home base, caller provides piece_idx


def get_home_base_coords(color: str, piece_idx: int) -> list:
    return HOME_BASES[color][piece_idx]


# ── Dice ───────────────────────────────────────────────────────────────────────

def roll_dice() -> int:
    return random.randint(1, 6)


# ── Move validation ────────────────────────────────────────────────────────────

def get_valid_moves(player: dict, dice: int) -> list:
    """Return list of piece indices that can legally move."""
    valid = []
    for i, pos in enumerate(player["pieces"]):
        if pos == 58:
            continue
        if pos == -1:
            if dice == 6:
                valid.append(i)
        else:
            if pos + dice <= 58:
                valid.append(i)
    return valid


def has_any_move(game_state: dict, player_idx: int, dice: int) -> bool:
    player = game_state["players"][player_idx]
    return len(get_valid_moves(player, dice)) > 0


# ── Apply move ─────────────────────────────────────────────────────────────────

def apply_move(
    game_state: dict,
    player_idx: int,
    piece_idx: int,
    dice: int,
) -> tuple:
    """
    Apply move to a deep copy of game_state.
    Returns (new_state, captures, piece_finished).
      captures       : list of {player_name, color, piece_idx}
      piece_finished : True if this piece just reached position 58
    """
    state = copy.deepcopy(game_state)
    player = state["players"][player_idx]
    pieces = player["pieces"]
    pos    = pieces[piece_idx]
    color  = player["color"]

    captures = []
    piece_finished = False

    # Compute new position
    new_pos = 0 if pos == -1 else pos + dice
    pieces[piece_idx] = new_pos

    if new_pos == 58:
        player["finished_count"] = player.get("finished_count", 0) + 1
        piece_finished = True

    elif new_pos < 52:
        # Check captures (only on non-safe main-ring cells)
        my_abs = rel_to_abs(new_pos, color)
        if not is_safe_abs(my_abs):
            for opp_idx, opp in enumerate(state["players"]):
                if opp_idx == player_idx:
                    continue
                for j, opp_pos in enumerate(opp["pieces"]):
                    if opp_pos < 0 or opp_pos >= 52:
                        continue
                    if rel_to_abs(opp_pos, opp["color"]) == my_abs:
                        opp["pieces"][j] = -1
                        captures.append({
                            "player_name": opp["name"],
                            "color":       opp["color"],
                            "piece_idx":   j,
                        })

    state["players"][player_idx]["pieces"] = pieces
    return state, captures, piece_finished


# ── Game state checks ──────────────────────────────────────────────────────────

def all_finished(player: dict) -> bool:
    return player.get("finished_count", 0) >= 4


def count_finished(player: dict) -> int:
    return player.get("finished_count", 0)


def next_active_player(game_state: dict, from_idx: int) -> int:
    """Return index of next player who hasn't finished all pieces."""
    n = len(game_state["players"])
    for step in range(1, n + 1):
        idx = (from_idx + step) % n
        if not all_finished(game_state["players"][idx]):
            return idx
    return from_idx


def get_winner(game_state: dict):
    """Return index of first player with all 4 pieces finished, or None."""
    for i, p in enumerate(game_state["players"]):
        if all_finished(p) and i not in game_state.get("rankings", []):
            return i
    return None


def check_rankings(game_state: dict) -> dict:
    """
    Check if any new player has finished all pieces.
    Returns updated game_state with rankings list.
    rankings: list of player indices in finish order.
    """
    state = copy.deepcopy(game_state)
    if "rankings" not in state:
        state["rankings"] = []

    for i, p in enumerate(state["players"]):
        if all_finished(p) and i not in state["rankings"]:
            state["rankings"].append(i)

    return state


def is_game_over(game_state: dict) -> bool:
    """Game ends when all-but-one players have finished (or all finished)."""
    n = len(game_state["players"])
    rankings = game_state.get("rankings", [])
    return len(rankings) >= n - 1


def get_final_rankings(game_state: dict) -> list:
    """
    Return full final rankings (all player indices in finish order).
    Unfinished players get last place based on how many pieces they finished.
    """
    n = len(game_state["players"])
    rankings = list(game_state.get("rankings", []))
    # Add remaining players sorted by finished_count desc
    remaining = [
        (i, game_state["players"][i].get("finished_count", 0))
        for i in range(n) if i not in rankings
    ]
    remaining.sort(key=lambda x: -x[1])
    rankings += [i for i, _ in remaining]
    return rankings


# ── Three-sixes rule ───────────────────────────────────────────────────────────

def apply_three_sixes_penalty(game_state: dict, player_idx: int) -> dict:
    """
    If a player rolls three consecutive sixes, send their furthest-advanced piece
    back to home base. Considers both main ring (0-51) and home column (52-57).
    Resets consecutive_sixes counter.
    """
    state = copy.deepcopy(game_state)
    player = state["players"][player_idx]
    pieces = player["pieces"]
    # Find the piece that is furthest along (any on-board piece, i.e. pos 0–57)
    # pos == 58 (finished) cannot be sent back
    best_idx = -1
    best_pos = -2
    for i, pos in enumerate(pieces):
        if 0 <= pos < 58 and pos > best_pos:
            best_pos = pos
            best_idx = i
    if best_idx >= 0:
        pieces[best_idx] = -1
    state["consecutive_sixes"] = 0
    return state


# ── Initial game state factory ─────────────────────────────────────────────────

def make_initial_game_state(players: list) -> dict:
    """
    Build starting game state from a list of player dicts.
    Each player dict must have: user_id, name, color.
    """
    game_players = []
    for p in players:
        game_players.append({
            "user_id":        p["user_id"],
            "name":           p["name"],
            "color":          p["color"],
            "pieces":         [-1, -1, -1, -1],
            "finished_count": 0,
        })
    return {
        "players":              game_players,
        "current_player_idx":   0,
        "dice_value":           None,
        "dice_rolled":          False,
        "consecutive_sixes":    0,
        "rankings":             [],
        "status":               "playing",
    }
