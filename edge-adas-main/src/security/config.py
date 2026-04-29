"""
Security Configuration — ASDV Edge ADAS
=========================================
All security tokens and limits are loaded from environment variables.
To set them, create a file called .env in the edge-adas root folder
with the following content:

    ASDV_WS_TOKEN=your_secret_token_here
    ASDV_API_KEY=your_api_key_here

IMPORTANT: Never commit .env to GitHub. It is already in .gitignore.
"""

import os
import secrets

# ------------------------------------------------------------
# WebSocket Authentication Token
# Used by the Duckiebot ROS client to authenticate to this server.
# If not set in environment, a random one is generated on startup.
# ------------------------------------------------------------
WS_AUTH_TOKEN: str = os.getenv("ASDV_WS_TOKEN", secrets.token_hex(32))

# ------------------------------------------------------------
# API Key for the Telemetry HTTP endpoint
# ------------------------------------------------------------
API_KEY: str = os.getenv("ASDV_API_KEY", secrets.token_hex(16))

# ------------------------------------------------------------
# Message Replay Protection
# Messages older than this many seconds are rejected.
# ------------------------------------------------------------
MAX_MESSAGE_AGE_SECONDS: float = float(os.getenv("ASDV_MAX_MSG_AGE", "5.0"))

# ------------------------------------------------------------
# Sensor Anomaly Detection Thresholds
# ------------------------------------------------------------
MAX_STEERING_JUMP_DEG: float = float(os.getenv("ASDV_MAX_STEER_JUMP", "30.0"))
MAX_BRAKE_JUMP: float = float(os.getenv("ASDV_MAX_BRAKE_JUMP", "0.8"))
MAX_IMAGE_SIZE_BYTES: int = int(os.getenv("ASDV_MAX_IMG_BYTES", str(5 * 1024 * 1024)))  # 5 MB

# ------------------------------------------------------------
# GPS Spoofing Detection
# ------------------------------------------------------------
MAX_GPS_SPEED_MPS: float = float(os.getenv("ASDV_MAX_GPS_SPEED", "15.0"))  # ~54 km/h campus max
GPS_SPOOF_BIAS_CLAMP: float = 0.5  # Max bias from GPS when anomaly detected

# ------------------------------------------------------------
# Rate Limiting (for HTTP endpoints on camera_api)
# ------------------------------------------------------------
RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("ASDV_RATE_WINDOW", "10"))
RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("ASDV_RATE_MAX", "30"))

# ------------------------------------------------------------
# Allowed CORS Origins
# Replace with your actual frontend origin if needed.
# ------------------------------------------------------------
ALLOWED_ORIGINS: list = os.getenv(
    "ASDV_ALLOWED_ORIGINS", "http://localhost:8000,http://localhost:8080"
).split(",")

# ------------------------------------------------------------
# Print config on startup (tokens are partially masked)
# ------------------------------------------------------------
def print_security_config():
    print("=" * 60)
    print("  [SECURITY] ASDV Security Configuration Loaded")
    print("=" * 60)
    print(f"  WS Token      : {WS_AUTH_TOKEN[:8]}...{WS_AUTH_TOKEN[-4:]} (set ASDV_WS_TOKEN)")
    print(f"  API Key       : {API_KEY[:4]}...{API_KEY[-4:]} (set ASDV_API_KEY)")
    print(f"  Max Msg Age   : {MAX_MESSAGE_AGE_SECONDS}s")
    print(f"  Max Img Size  : {MAX_IMAGE_SIZE_BYTES // 1024} KB")
    print(f"  Max GPS Speed : {MAX_GPS_SPEED_MPS} m/s")
    print(f"  Allowed CORS  : {ALLOWED_ORIGINS}")
    print("=" * 60)
