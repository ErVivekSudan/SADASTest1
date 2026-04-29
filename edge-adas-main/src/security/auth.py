"""
Authentication & Authorization Utilities — ASDV Edge ADAS
===========================================================
Provides:
  - WebSocket token validation
  - HTTP API key validation
  - Rate limiter for HTTP endpoints
"""

import time
import logging
from collections import defaultdict
from fastapi import WebSocket, HTTPException, Header, status
from typing import Optional

from src.security.config import (
    WS_AUTH_TOKEN,
    API_KEY,
    RATE_LIMIT_WINDOW_SECONDS,
    RATE_LIMIT_MAX_REQUESTS,
)

logger = logging.getLogger("asdv.security.auth")

# ---------------------------------------------------------------
# WebSocket Authentication
# ---------------------------------------------------------------

async def authenticate_websocket(ws: WebSocket) -> bool:
    """
    Validates the WebSocket connection using a token passed as a
    query parameter: ws://host/ws?token=YOUR_TOKEN

    Returns True if valid, closes connection and returns False if not.

    WHY: Without this, ANY device on the same WiFi network can connect
    to the autonomy server and feed it fake camera frames or receive
    real-time steering telemetry.
    """
    token = ws.query_params.get("token", "")
    if not _constant_time_compare(token, WS_AUTH_TOKEN):
        logger.warning(
            "[AUTH] WebSocket rejected — invalid token from %s",
            ws.client.host if ws.client else "unknown"
        )
        await ws.close(code=4401)  # 4401 = custom: Unauthorized
        return False

    logger.info(
        "[AUTH] WebSocket authenticated from %s",
        ws.client.host if ws.client else "unknown"
    )
    return True


def _constant_time_compare(a: str, b: str) -> bool:
    """
    Compare two strings in constant time to prevent timing attacks.

    WHY: A normal `a == b` comparison exits early when it finds a
    mismatch, which leaks information about how close a guess is.
    Constant-time comparison always takes the same time regardless
    of how many characters match.
    """
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode(), b.encode()):
        result |= x ^ y
    return result == 0


# ---------------------------------------------------------------
# HTTP API Key Authentication (for telemetry endpoints)
# ---------------------------------------------------------------

def verify_api_key(x_api_key: Optional[str] = Header(default=None)):
    """
    FastAPI dependency: validates X-Api-Key header on HTTP endpoints.

    Usage:
        @app.get("/api/telemetry", dependencies=[Depends(verify_api_key)])
        def get_telemetry(): ...

    WHY: Without this, anyone who finds the server IP can read live
    telemetry data (speed, steering, GPS coordinates).
    """
    if not x_api_key or not _constant_time_compare(x_api_key, API_KEY):
        logger.warning("[AUTH] HTTP API key rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Use X-Api-Key header.",
        )


# ---------------------------------------------------------------
# Rate Limiter (simple in-memory, per IP)
# ---------------------------------------------------------------

_rate_counters: dict = defaultdict(lambda: {"count": 0, "window_start": time.time()})
_rate_lock_imported = False  # use simple dict — Jetson Nano friendly

def check_rate_limit(client_ip: str) -> bool:
    """
    Returns True if request is allowed, False if rate limit exceeded.

    Allows RATE_LIMIT_MAX_REQUESTS per RATE_LIMIT_WINDOW_SECONDS per IP.

    WHY: A simple loop sending thousands of HTTP requests per second
    (DoS attack) can overwhelm the Jetson Nano and crash the inference
    pipeline, causing the vehicle to lose control.
    """
    now = time.time()
    entry = _rate_counters[client_ip]

    if now - entry["window_start"] > RATE_LIMIT_WINDOW_SECONDS:
        entry["count"] = 0
        entry["window_start"] = now

    entry["count"] += 1

    if entry["count"] > RATE_LIMIT_MAX_REQUESTS:
        logger.warning("[RATE] Rate limit exceeded for IP: %s", client_ip)
        return False

    return True
