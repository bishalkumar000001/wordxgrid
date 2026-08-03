"""
web.py — Flask web server for WordXGrid.
Provides a health endpoint and is imported by bot.py.
"""
import logging
import os

from flask import Flask

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Flask app ──────────────────────────────────────────────────────────────────

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("WEBAPP_SECRET_KEY", "wordxgrid-secret-key-change-me")


# ── Health check ───────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return "WordXGrid Bot is Running! 🎮"


@app.route("/health")
def health():
    return {"status": "ok", "service": "wordxgrid-web"}


# ── Startup ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    logger.info("Starting WordXGrid web server on port %d", port)
    app.run(host="0.0.0.0", port=port)
