"""Per-update handler de-duplication for Telegram games.

Telegram may occasionally deliver/retry an update, and duplicate handler
registration can also cause the same callback to run twice.  This module wraps
Application.add_handler so every registered handler gets an idempotency guard.
The guard is scoped to (update_id, handler signature), so different game
handlers can still process the same normal group message when appropriate.
"""

import asyncio
import time
from collections import OrderedDict
from functools import wraps

# update_id -> {handler_key: timestamp}
_SEEN = OrderedDict()
_LOCK = asyncio.Lock()
_TTL = 300.0
_MAX = 10000
_INSTALLED = set()


def _handler_key(handler, group: int) -> str:
    callback = getattr(handler, "callback", None)
    callback_name = getattr(callback, "__module__", "") + ":" + getattr(callback, "__qualname__", repr(callback))

    # Include useful handler-specific routing data so two intentionally
    # different handlers using the same callback are not collapsed together.
    parts = [type(handler).__name__, str(group), callback_name]
    for attr in ("commands", "pattern", "filters"):
        value = getattr(handler, attr, None)
        if value is not None:
            parts.append(repr(value))
    return "|".join(parts)


async def _claim(update_id: int, key: str) -> bool:
    now = time.monotonic()
    async with _LOCK:
        # Cheap TTL cleanup from the oldest entries.
        while _SEEN:
            _, ts = next(iter(_SEEN.items()))
            if now - ts <= _TTL:
                break
            _SEEN.popitem(last=False)

        compound = (update_id, key)
        if compound in _SEEN:
            return False

        _SEEN[compound] = now
        if len(_SEEN) > _MAX:
            _SEEN.popitem(last=False)
        return True


def install_handler_deduplication(app) -> None:
    """Install a transparent guard around app.add_handler once."""
    app_id = id(app)
    if app_id in _INSTALLED:
        return

    original_add_handler = app.add_handler

    def add_handler_once(handler, group=0):
        original_callback = getattr(handler, "callback", None)
        if original_callback is None:
            return original_add_handler(handler, group=group)

        key = _handler_key(handler, group)

        @wraps(original_callback)
        async def guarded(update, context):
            update_id = getattr(update, "update_id", None)
            # Synthetic/non-Telegram updates have no stable ID; don't block them.
            if update_id is None:
                return await original_callback(update, context)
            if not await _claim(update_id, key):
                return None
            return await original_callback(update, context)

        handler.callback = guarded
        return original_add_handler(handler, group=group)

    app.add_handler = add_handler_once
    _INSTALLED.add(app_id)
