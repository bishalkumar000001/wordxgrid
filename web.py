"""
web.py — Flask + Flask-SocketIO web server for WordXGrid.
Serves the Ludo Mini App and provides real-time WebSocket support.

When imported by bot.py: Flask app is available as `app`.
When run directly:       Full SocketIO server starts (WebSocket-capable).
"""
import logging
import os

from flask import Flask
from flask_socketio import SocketIO

import ludo_rooms_db as rdb
from ludo_webapp import ludo_bp, register_socketio_events

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Flask app ──────────────────────────────────────────────────────────────────

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("WEBAPP_SECRET_KEY", "ludo-webapp-secret-key-change-me")

# ── Flask-SocketIO ─────────────────────────────────────────────────────────────
# async_mode='threading' — safe to import from bot.py without monkey-patching.
# For higher concurrency on Render, switch Procfile to:
#   web: gunicorn --worker-class eventlet -w 1 web:app
# and change async_mode to 'eventlet' (requires: pip install eventlet).

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

# ── Blueprints + SocketIO events ───────────────────────────────────────────────

app.register_blueprint(ludo_bp)
register_socketio_events(socketio)


# ── Health check ───────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return "WordXGrid Bot is Running! 🎮"


@app.route("/health")
def health():
    return {"status": "ok", "service": "wordxgrid-web"}


# ── Startup ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        rdb.init_webapp_indexes()
    except Exception as e:
        logger.warning("Could not init DB indexes: %s", e)

    port = int(os.environ.get("PORT", 10000))
    logger.info("Starting WordXGrid web server on port %d", port)
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        allow_unsafe_werkzeug=True,
    )
