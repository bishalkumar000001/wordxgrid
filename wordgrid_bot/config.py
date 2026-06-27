import os
import logging

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
LOG_GROUP_ID = int(os.environ.get("LOG_GROUP_ID", "0"))
SUPPORT_CHANNEL = os.environ.get("SUPPORT_CHANNEL", "")
DB_PATH = os.environ.get("DB_PATH", "wordgrid.db")

GAME_TIMEOUT_SECONDS = 600  # 10 minutes

POINTS_FIRST = 4
POINTS_NORMAL = 3
POINTS_LAST = 5

MAX_HINTS_PER_GAME = 2

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is not set!")
