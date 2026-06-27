import sqlite3
import os
import json
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "wordgrid.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            first_name  TEXT,
            last_name   TEXT,
            updated_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS scores (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            group_id    INTEGER NOT NULL,
            game_id     TEXT NOT NULL,
            points      INTEGER NOT NULL DEFAULT 0,
            word        TEXT NOT NULL,
            scored_at   TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS games (
            game_id      TEXT PRIMARY KEY,
            group_id     INTEGER NOT NULL,
            mode         TEXT NOT NULL DEFAULT 'easy',
            words        TEXT NOT NULL,
            found_words  TEXT NOT NULL DEFAULT '',
            started_at   TEXT DEFAULT (datetime('now')),
            ended_at     TEXT,
            message_id   INTEGER,
            pin_msg_id   INTEGER,
            active       INTEGER NOT NULL DEFAULT 1,
            grid_data    TEXT,
            placed_words TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_scores_user   ON scores(user_id);
        CREATE INDEX IF NOT EXISTS idx_scores_group  ON scores(group_id);
        CREATE INDEX IF NOT EXISTS idx_scores_game   ON scores(game_id);
        CREATE INDEX IF NOT EXISTS idx_scores_dated  ON scores(scored_at);
        CREATE INDEX IF NOT EXISTS idx_games_group   ON games(group_id);
    """)

    # Migrate existing tables that lack the new columns
    for col, definition in [
        ("grid_data",    "TEXT"),
        ("placed_words", "TEXT"),
    ]:
        try:
            c.execute(f"ALTER TABLE games ADD COLUMN {col} {definition}")
        except Exception:
            pass

    conn.commit()
    conn.close()
    logger.info("Database initialised at %s", DB_PATH)


def upsert_user(user_id: int, username: str, first_name: str, last_name: str = ""):
    conn = get_conn()
    conn.execute("""
        INSERT INTO users (user_id, username, first_name, last_name, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            username   = excluded.username,
            first_name = excluded.first_name,
            last_name  = excluded.last_name,
            updated_at = excluded.updated_at
    """, (user_id, username or "", first_name or "", last_name or ""))
    conn.commit()
    conn.close()


def create_game(
    game_id: str,
    group_id: int,
    mode: str,
    words: list,
    grid: list = None,
    placed: dict = None,
) -> None:
    grid_json = json.dumps(grid) if grid is not None else None
    # placed values are tuples; convert to lists for JSON
    placed_json = json.dumps({k: list(v) for k, v in placed.items()}) if placed else None
    conn = get_conn()
    conn.execute("""
        INSERT INTO games
            (game_id, group_id, mode, words, found_words, active, grid_data, placed_words)
        VALUES (?, ?, ?, ?, '', 1, ?, ?)
    """, (game_id, group_id, mode, ",".join(words), grid_json, placed_json))
    conn.commit()
    conn.close()


def get_active_game(group_id: int):
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM games WHERE group_id = ? AND active = 1
        ORDER BY started_at DESC LIMIT 1
    """, (group_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_game(game_id: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM games WHERE game_id = ?", (game_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_game_message(game_id: str, message_id: int):
    conn = get_conn()
    conn.execute("UPDATE games SET message_id = ? WHERE game_id = ?", (message_id, game_id))
    conn.commit()
    conn.close()


def update_game_pin(game_id: str, pin_msg_id: int):
    conn = get_conn()
    conn.execute("UPDATE games SET pin_msg_id = ? WHERE game_id = ?", (pin_msg_id, game_id))
    conn.commit()
    conn.close()


def mark_word_found(game_id: str, word: str) -> bool:
    """Mark a word as found. Returns False if already found."""
    conn = get_conn()
    row = conn.execute(
        "SELECT found_words FROM games WHERE game_id = ? AND active = 1",
        (game_id,),
    ).fetchone()
    if not row:
        conn.close()
        return False
    found = [w for w in row["found_words"].split(",") if w]
    if word in found:
        conn.close()
        return False
    found.append(word)
    conn.execute(
        "UPDATE games SET found_words = ? WHERE game_id = ?",
        (",".join(found), game_id),
    )
    conn.commit()
    conn.close()
    return True


def end_game(game_id: str):
    conn = get_conn()
    conn.execute("""
        UPDATE games SET active = 0, ended_at = datetime('now')
        WHERE game_id = ?
    """, (game_id,))
    conn.commit()
    conn.close()


def add_score(user_id: int, group_id: int, game_id: str, word: str, points: int):
    conn = get_conn()
    conn.execute("""
        INSERT INTO scores (user_id, group_id, game_id, word, points)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, group_id, game_id, word, points))
    conn.commit()
    conn.close()


def get_global_leaderboard(limit: int = 20):
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.user_id, u.username, u.first_name, u.last_name,
               SUM(s.points) as total_points,
               COUNT(DISTINCT s.game_id) as games_played
        FROM scores s
        JOIN users u ON s.user_id = u.user_id
        GROUP BY s.user_id
        ORDER BY total_points DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_period_leaderboard(period: str, group_id: int = None, limit: int = 20):
    conn = get_conn()

    if period == "day":
        since = "datetime('now', '-1 day')"
    elif period == "week":
        since = "datetime('now', '-7 days')"
    elif period == "month":
        since = "datetime('now', '-30 days')"
    elif period == "year":
        since = "datetime('now', '-365 days')"
    else:
        since = None

    group_filter = "AND s.group_id = ?" if group_id else ""
    params_base = (limit,) if not group_id else (group_id, limit)

    if since:
        query = f"""
            SELECT u.user_id, u.username, u.first_name, u.last_name,
                   SUM(s.points) as total_points,
                   COUNT(DISTINCT s.game_id) as games_played
            FROM scores s
            JOIN users u ON s.user_id = u.user_id
            WHERE s.scored_at >= {since} {group_filter}
            GROUP BY s.user_id
            ORDER BY total_points DESC
            LIMIT ?
        """
    else:
        query = f"""
            SELECT u.user_id, u.username, u.first_name, u.last_name,
                   SUM(s.points) as total_points,
                   COUNT(DISTINCT s.game_id) as games_played
            FROM scores s
            JOIN users u ON s.user_id = u.user_id
            WHERE 1=1 {group_filter}
            GROUP BY s.user_id
            ORDER BY total_points DESC
            LIMIT ?
        """

    rows = conn.execute(query, params_base).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_game_scores(game_id: str):
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.user_id, u.username, u.first_name,
               SUM(s.points) as total_points
        FROM scores s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.game_id = ?
        GROUP BY s.user_id
        ORDER BY total_points DESC
    """, (game_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_hint_count(game_id: str) -> int:
    conn = get_conn()
    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM scores
        WHERE game_id = ? AND word = '__hint__'
    """, (game_id,)).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def record_hint_used(game_id: str, user_id: int, group_id: int):
    conn = get_conn()
    conn.execute("""
        INSERT INTO scores (user_id, group_id, game_id, word, points)
        VALUES (?, ?, ?, '__hint__', 0)
    """, (user_id, group_id, game_id))
    conn.commit()
    conn.close()


# ── helpers to decode stored JSON ────────────────────────────────────────────

def decode_grid(game: dict) -> list:
    raw = game.get("grid_data")
    if not raw:
        return []
    return json.loads(raw)


def decode_placed(game: dict) -> dict:
    raw = game.get("placed_words")
    if not raw:
        return {}
    data = json.loads(raw)
    return {k: tuple(v) for k, v in data.items()}
