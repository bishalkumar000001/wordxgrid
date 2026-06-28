import os
import logging

logger = logging.getLogger(__name__)

BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
OWNER_ID        = int(os.environ.get("OWNER_ID", "0"))
LOG_GROUP_ID    = int(os.environ.get("LOG_GROUP_ID", "0"))
SUPPORT_CHANNEL = os.environ.get("SUPPORT_CHANNEL", "")

# Comma-separated Telegram user IDs that can use /broadcast
# e.g. SUDO_USERS=123456789,987654321
_sudo_raw  = os.environ.get("SUDO_USERS", "")
SUDO_USERS = set(
    int(x.strip()) for x in _sudo_raw.split(",") if x.strip().isdigit()
)
if OWNER_ID:
    SUDO_USERS.add(OWNER_ID)   # owner is always sudo

GAME_TIMEOUT_SECONDS = 600   # 10 minutes

POINTS_FIRST  = 4
POINTS_NORMAL = 3
POINTS_LAST   = 5

MAX_HINTS_PER_GAME = 5

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is not set!")
