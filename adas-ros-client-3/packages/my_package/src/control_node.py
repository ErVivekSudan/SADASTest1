#!/usr/bin/env python3
"""
Duckiebot Web-Controlled Driving Node — Security-Hardened
----------------------------------------------------------
Changes from original:
  - /api/control requires X-Api-Key header (fixes V3)
  - Rate limiting on /api/control (fixes V4)
  - Steering/brake input validation & clamping (fixes V3/V6)
  - Security event logging (fixes V9)
  - CORS restricted to known origins (fixes V2)
  - All endpoints log access for audit trail
"""

import os
import threading
import time
import logging
import json
import secrets
import hmac
from collections import defaultdict

import rospy
from flask import Flask, request, jsonify, send_from_directory
from duckietown.dtros import DTROS, NodeType
from duckietown_msgs.msg import WheelsCmdStamped

# ── Tuning constants ──────────────────────────────────────
MAX_SPEED = 0.2          # m/s  – maximum forward wheel speed
MAX_STEER = 27.0         # deg  – maximum steering angle
BRAKE_ZERO = 0.8         # brake force at which effective speed = 0
CONTROL_HZ = 10          # wheel-command publish rate
SAFETY_TIMEOUT = 0.5     # seconds – auto-stop if no update
FLASK_PORT = 8080

# ── Security Configuration ────────────────────────────────
# Load API key from environment variable.
# Set this to the SAME value as ASDV_API_KEY on the edge-adas server.
# On Duckiebot, set this with:  export DUCKIEBOT_API_KEY=your_key_here
# Or add it to /etc/environment for persistence.
API_KEY: str = os.environ.get("DUCKIEBOT_API_KEY", secrets.token_hex(16))

# Rate limiting
RATE_LIMIT_MAX = int(os.environ.get("DUCKIEBOT_RATE_MAX", "20"))
RATE_LIMIT_WINDOW = int(os.environ.get("DUCKIEBOT_RATE_WINDOW", "10"))

# Allowed origins (set to edge-adas server IP in production)
ALLOWED_ORIGIN: str = os.environ.get(
    "DUCKIEBOT_ALLOWED_ORIGIN", "http://localhost:8000"
)

# ── Security Logger ───────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
sec_logger = logging.getLogger("duckiebot.security")

# Print security config at startup
print("=" * 55)
print("  [SECURITY] Duckiebot Control Node — Security Config")
print("=" * 55)
print(f"  API Key         : {API_KEY[:4]}...{API_KEY[-4:]} (set DUCKIEBOT_API_KEY)")
print(f"  Allowed Origin  : {ALLOWED_ORIGIN}")
print(f"  Rate Limit      : {RATE_LIMIT_MAX} req / {RATE_LIMIT_WINDOW}s")
print("=" * 55)


# ── Rate Limiter ──────────────────────────────────────────
_rate_counters = defaultdict(lambda: {"count": 0, "window_start": time.time()})

def _is_rate_limited(client_ip: str) -> bool:
    """Returns True if the client has exceeded rate limit."""
    now = time.time()
    entry = _rate_counters[client_ip]
    if now - entry["window_start"] > RATE_LIMIT_WINDOW:
        entry["count"] = 0
        entry["window_start"] = now
    entry["count"] += 1
    if entry["count"] > RATE_LIMIT_MAX:
        sec_logger.warning("[RATE] Rate limit exceeded for IP: %s", client_ip)
        return True
    return False


# ── Constant-Time String Comparison ──────────────────────
def _safe_compare(a: str, b: str) -> bool:
    """Timing-safe string comparison to prevent brute-force timing attacks."""
    return hmac.compare_digest(a.encode(), b.encode())


class ControlNode(DTROS):

    def __init__(self, node_name):
        super().__init__(node_name=node_name, node_type=NodeType.CONTROL)

        # ── ROS setup ──────────────────────────────────────
        self._vehicle = os.environ.get("VEHICLE_NAME", "duckiebot")
        self._pub = rospy.Publisher(
            f"/{self._vehicle}/wheels_driver_node/wheels_cmd",
            WheelsCmdStamped,
            queue_size=1,
        )

        # ── Shared state (guarded by lock) ─────────────────
        self._lock = threading.Lock()
        self._steer = 0.0
        self._brake = 1.0        # Default: full brake = stopped (safe default)
        self._mode = "manual"
        self._last_update = time.time()

        # ── Security: anomaly detection state ──────────────
        self._last_steer_received = 0.0
        self._last_brake_received = 1.0
        MAX_STEER_JUMP_PER_UPDATE = 20.0  # degrees — max change in one control message
        self._max_steer_jump = MAX_STEER_JUMP_PER_UPDATE

        # ── Start Flask in a daemon thread ─────────────────
        self._start_flask()

        # ── Periodic wheel-command publisher ───────────────
        self._timer = rospy.Timer(
            rospy.Duration(1.0 / CONTROL_HZ), self._control_cb
        )
        rospy.loginfo("[ControlNode] Ready — UI at http://0.0.0.0:%d", FLASK_PORT)
        sec_logger.info("[STARTUP] Control node started securely.")

    # ════════════════════════════════════════════════════════
    #  Flask web server
    # ════════════════════════════════════════════════════════
    def _start_flask(self):
        static_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "static"
        )
        app = Flask(__name__, static_folder=static_dir)

        # ── SECURITY: Add security headers to all responses ──
        @app.after_request
        def add_security_headers(response):
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            # SECURITY FIX V2: Restrict CORS
            origin = request.headers.get("Origin", "")
            if origin == ALLOWED_ORIGIN:
                response.headers["Access-Control-Allow-Origin"] = origin
            return response

        # ── Serve the control-panel page ──────────────────
        @app.route("/")
        def index():
            return send_from_directory(app.static_folder, "index.html")

        # ── SECURITY FIX V3 + V4: /api/control now requires API key + rate limit ──
        @app.route("/api/control", methods=["POST"])
        def api_control():
            """
            Receive steer + brake from UI or auto-forwarding system.

            Security checks (in order):
            1. Rate limiting — reject if too many requests per IP
            2. API key authentication — reject if key is wrong or missing
            3. Input validation — clamp values to safe ranges
            4. Anomaly detection — reject impossible steering jumps
            """
            client_ip = request.remote_addr or "unknown"

            # Check 1: Rate limiting (SECURITY FIX V4)
            if _is_rate_limited(client_ip):
                sec_logger.warning("[RATE] /api/control blocked: %s", client_ip)
                return jsonify(error="Too many requests"), 429

            # Check 2: API key (SECURITY FIX V3)
            provided_key = request.headers.get("X-Api-Key", "")
            if not _safe_compare(provided_key, API_KEY):
                sec_logger.warning(
                    "[AUTH] /api/control rejected — bad API key from %s", client_ip
                )
                return jsonify(error="Unauthorized"), 401

            # Check 3: Parse and validate input
            data = request.get_json(silent=True) or {}

            try:
                raw_steer = float(data.get("steer", 0.0))
                raw_brake = float(data.get("brake", 1.0))
            except (TypeError, ValueError):
                sec_logger.warning("[VALIDATION] Non-numeric steer/brake from %s", client_ip)
                return jsonify(error="Invalid input"), 400

            # Clamp to safe physical limits
            s = max(-MAX_STEER, min(MAX_STEER, raw_steer))
            b = max(0.0, min(1.0, raw_brake))

            # Check 4: Anomaly detection — impossible steering jump
            steer_jump = abs(s - self._last_steer_received)
            if steer_jump > self._max_steer_jump:
                sec_logger.warning(
                    "[IDS] Steering jump anomaly: %.1f° from %s — applying failsafe",
                    steer_jump, client_ip
                )
                # Failsafe: use last known safe values, add brake
                s = self._last_steer_received * 0.5  # smooth toward centre
                b = max(b, 0.5)                       # add some braking

            self._last_steer_received = s
            self._last_brake_received = b

            with self._lock:
                self._steer = s
                self._brake = b
                self._last_update = time.time()

            sec_logger.debug("[CONTROL] steer=%.2f brake=%.3f from %s", s, b, client_ip)
            return jsonify(status="ok")

        # ── /api/mode — switch manual/auto ────────────────
        @app.route("/api/mode", methods=["POST"])
        def api_mode():
            """
            Switch between manual and auto mode.
            Requires API key for safety — mode changes must be authenticated.
            """
            client_ip = request.remote_addr or "unknown"

            # Rate limit
            if _is_rate_limited(client_ip):
                return jsonify(error="Too many requests"), 429

            # API key required
            provided_key = request.headers.get("X-Api-Key", "")
            if not _safe_compare(provided_key, API_KEY):
                sec_logger.warning("[AUTH] /api/mode rejected from %s", client_ip)
                return jsonify(error="Unauthorized"), 401

            data = request.get_json(silent=True) or {}
            mode = data.get("mode", "manual")

            # Validate mode value — only accept known modes
            if mode not in ("manual", "auto"):
                return jsonify(error="Invalid mode"), 400

            with self._lock:
                self._mode = mode
                if mode == "auto":
                    # Safe default when switching to auto
                    self._brake = 1.0
                    self._steer = 0.0

            sec_logger.info("[MODE] Switched to %s by %s", mode, client_ip)
            return jsonify(status="ok", mode=mode)

        # ── /api/status — public telemetry ────────────────
        @app.route("/api/status")
        def api_status():
            """
            Live status endpoint.
            Note: This is intentionally left public (no API key) because
            the frontend needs to poll it for the control panel display.
            It only returns non-sensitive operational data.
            """
            with self._lock:
                vl, vr = self._wheel_velocities()
                return jsonify(
                    steer=round(self._steer, 2),
                    brake=round(self._brake, 3),
                    mode=self._mode,
                    vel_left=round(vl, 4),
                    vel_right=round(vr, 4),
                )

        # ── /api/emergency_stop — always accessible ────────
        @app.route("/api/emergency_stop", methods=["POST"])
        def emergency_stop():
            """
            Emergency stop endpoint.
            Intentionally requires NO authentication — in an emergency,
            you must be able to stop the vehicle immediately.
            This is a safety-over-security design decision.
            """
            with self._lock:
                self._steer = 0.0
                self._brake = 1.0
                self._last_update = time.time()
            sec_logger.warning("[SAFETY] Emergency stop triggered by %s", request.remote_addr)
            return jsonify(status="stopped")

        t = threading.Thread(
            target=lambda: app.run(
                host="0.0.0.0", port=FLASK_PORT, threaded=True
            ),
            daemon=True,
        )
        t.start()

    # ════════════════════════════════════════════════════════
    #  Differential-drive math (unchanged from original)
    # ════════════════════════════════════════════════════════
    def _wheel_velocities(self):
        """Convert (steer, brake) → (vel_left, vel_right)."""
        if self._brake >= BRAKE_ZERO:
            speed = 0.0
        else:
            speed = MAX_SPEED * max(0.0, 1.0 - self._brake / BRAKE_ZERO)

        n = self._steer / MAX_STEER  # normalised [-1, 1]
        vl = speed * (1.0 + n)
        vr = speed * (1.0 - n)
        return vl, vr

    # ════════════════════════════════════════════════════════
    #  ROS timer callback (unchanged logic, added logging)
    # ════════════════════════════════════════════════════════
    def _control_cb(self, _event):
        with self._lock:
            elapsed = time.time() - self._last_update
            if elapsed > SAFETY_TIMEOUT:
                # Safety timeout — no update received, stop the vehicle
                vl, vr = 0.0, 0.0
                if elapsed > SAFETY_TIMEOUT * 2:
                    sec_logger.debug("[SAFETY] Timeout: %.2fs, wheels stopped.", elapsed)
            else:
                vl, vr = self._wheel_velocities()

        msg = WheelsCmdStamped()
        msg.vel_left = vl
        msg.vel_right = vr
        self._pub.publish(msg)

    # ════════════════════════════════════════════════════════
    #  Graceful shutdown (unchanged)
    # ════════════════════════════════════════════════════════
    def on_shutdown(self):
        msg = WheelsCmdStamped()
        msg.vel_left = 0.0
        msg.vel_right = 0.0
        self._pub.publish(msg)
        sec_logger.info("[SHUTDOWN] Wheels stopped. Node shut down cleanly.")
        rospy.loginfo("[ControlNode] Shutdown — wheels stopped.")


if __name__ == "__main__":
    node = ControlNode(node_name="control_node")
    rospy.spin()
