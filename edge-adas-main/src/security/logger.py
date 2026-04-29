"""
Security Event Logger — ASDV Edge ADAS
========================================
Structured JSON security log for evaluation and audit purposes.
All security events (auth failures, IDS alerts, anomalies) are
written to both console AND a rotating log file.

WHY: Non-repudiation — we must be able to prove what happened.
In a security evaluation, being able to show "here is the log of
every attack we detected and responded to" is extremely valuable.
"""

import logging
import json
import time
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.getenv("ASDV_LOG_DIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "asdv_security.log")


def setup_security_logging():
    """
    Call this once at application startup.
    Creates logs/ directory and sets up rotating file + console logging.
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    # Root logger for asdv.security namespace
    logger = logging.getLogger("asdv.security")
    logger.setLevel(logging.DEBUG)

    # Formatter: structured JSON-like for easy parsing
    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # Rotating file handler: 5 MB per file, keep 3 files
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger


def log_security_event(event_type: str, details: dict):
    """
    Log a structured security event.

    event_type examples:
        "AUTH_FAIL", "REPLAY_ATTACK", "GPS_SPOOF",
        "SENSOR_INJECTION", "RATE_LIMIT_EXCEEDED", "FAILSAFE_TRIGGERED"
    """
    logger = logging.getLogger("asdv.security.events")
    entry = {
        "timestamp": time.time(),
        "event": event_type,
        **details
    }
    logger.warning("[SECURITY EVENT] %s", json.dumps(entry))
