"""
Intrusion Detection System (IDS) — ASDV Edge ADAS
===================================================
Detects:
  1. Replay attacks        — old/duplicate messages
  2. Data injection        — abnormal sensor value jumps
  3. GPS spoofing          — physically impossible GPS movement
  4. Oversized payloads    — buffer overflow / resource exhaustion

Each check returns (is_safe: bool, reason: str).
All alerts are logged and counted for the session report.
"""

import time
import math
import logging
from typing import Optional, Tuple

from src.security.config import (
    MAX_MESSAGE_AGE_SECONDS,
    MAX_STEERING_JUMP_DEG,
    MAX_BRAKE_JUMP,
    MAX_IMAGE_SIZE_BYTES,
    MAX_GPS_SPEED_MPS,
)

logger = logging.getLogger("asdv.security.ids")


class IntrusionDetector:
    """
    Stateful IDS that tracks previous values to detect anomalies.
    One instance is created per WebSocket session.

    Think of this like a security guard who remembers what the last
    message said and gets suspicious if the next one is too different.
    """

    def __init__(self):
        # Replay protection state
        self._last_timestamp: Optional[float] = None
        self._seen_timestamps: set = set()

        # Anomaly detection state
        self._last_steering: Optional[float] = None
        self._last_brake: Optional[float] = None

        # GPS spoofing detection state
        self._last_lat: Optional[float] = None
        self._last_lon: Optional[float] = None
        self._last_gps_time: Optional[float] = None

        # Alert counters (for the security report)
        self.alerts: dict = {
            "replay_rejected": 0,
            "stale_message": 0,
            "injection_steer": 0,
            "injection_brake": 0,
            "gps_spoof": 0,
            "oversized_payload": 0,
        }

    # ===========================================================
    # CHECK 1: Replay Attack Protection
    # ===========================================================
    def check_replay(self, msg_timestamp: float) -> Tuple[bool, str]:
        """
        Rejects messages that are:
          - Too old (older than MAX_MESSAGE_AGE_SECONDS)
          - Already seen (exact duplicate timestamp = replay)

        WHY: A replay attack records a legitimate message and resends
        it later. For example, an attacker could replay a "go straight"
        frame to keep the vehicle moving when it should have stopped.
        """
        now = time.time()
        age = now - msg_timestamp

        # Reject stale messages
        if age > MAX_MESSAGE_AGE_SECONDS:
            self.alerts["stale_message"] += 1
            reason = f"Message too old: {age:.2f}s > {MAX_MESSAGE_AGE_SECONDS}s"
            logger.warning("[IDS-REPLAY] %s", reason)
            return False, reason

        # Reject future timestamps (clock skew attack)
        if msg_timestamp > now + 2.0:
            self.alerts["replay_rejected"] += 1
            reason = f"Future timestamp detected: {msg_timestamp:.2f} > {now:.2f}"
            logger.warning("[IDS-REPLAY] %s", reason)
            return False, reason

        # Reject exact duplicate timestamp
        if msg_timestamp in self._seen_timestamps:
            self.alerts["replay_rejected"] += 1
            reason = f"Duplicate timestamp rejected: {msg_timestamp}"
            logger.warning("[IDS-REPLAY] %s", reason)
            return False, reason

        # Keep only last 100 timestamps to avoid memory leak
        if len(self._seen_timestamps) > 100:
            self._seen_timestamps.pop()

        self._seen_timestamps.add(msg_timestamp)
        self._last_timestamp = msg_timestamp
        return True, "ok"

    # ===========================================================
    # CHECK 2: Image Payload Size
    # ===========================================================
    def check_payload_size(self, image_bytes: bytes) -> Tuple[bool, str]:
        """
        Rejects suspiciously large image payloads.

        WHY: An attacker could send a 100MB image to exhaust Jetson Nano
        RAM and crash the inference pipeline (resource exhaustion attack).
        """
        size = len(image_bytes)
        if size > MAX_IMAGE_SIZE_BYTES:
            self.alerts["oversized_payload"] += 1
            reason = f"Oversized image: {size} bytes > {MAX_IMAGE_SIZE_BYTES}"
            logger.warning("[IDS-PAYLOAD] %s", reason)
            return False, reason
        return True, "ok"

    # ===========================================================
    # CHECK 3: Control Output Anomaly Detection
    # ===========================================================
    def check_control_output(
        self, steering: float, brake: float
    ) -> Tuple[bool, str]:
        """
        Detects sudden impossible jumps in steering/brake output.

        This runs AFTER inference — if the AI suddenly outputs a
        wildly different steering angle than it did one frame ago,
        something is wrong (injected frame, model anomaly, etc.)

        WHY: If an attacker injected a fake camera frame showing a
        sharp turn, the steering angle would jump dramatically. We
        detect this and trigger a safe stop instead.
        """
        if self._last_steering is not None:
            steer_jump = abs(steering - self._last_steering)
            if steer_jump > MAX_STEERING_JUMP_DEG:
                self.alerts["injection_steer"] += 1
                reason = (
                    f"Steering jump too large: {steer_jump:.1f}° "
                    f"(max {MAX_STEERING_JUMP_DEG}°)"
                )
                logger.warning("[IDS-ANOMALY] %s", reason)
                # Return safe values, not rejection — keep vehicle moving safely
                return False, reason

        if self._last_brake is not None:
            brake_jump = abs(brake - self._last_brake)
            if brake_jump > MAX_BRAKE_JUMP:
                self.alerts["injection_brake"] += 1
                reason = (
                    f"Brake jump too large: {brake_jump:.3f} "
                    f"(max {MAX_BRAKE_JUMP})"
                )
                logger.warning("[IDS-ANOMALY] %s", reason)
                return False, reason

        self._last_steering = steering
        self._last_brake = brake
        return True, "ok"

    # ===========================================================
    # CHECK 4: GPS Spoofing Detection
    # ===========================================================
    def check_gps(
        self, lat: float, lon: float
    ) -> Tuple[bool, str]:
        """
        Detects GPS spoofing by checking if the vehicle would need
        to be moving at physically impossible speeds between GPS updates.

        A campus vehicle cannot teleport 500 meters between two GPS
        readings 100ms apart. If it appears to, the GPS data is fake.

        WHY: GPS spoofing is a known attack where an attacker broadcasts
        fake GPS signals to make the vehicle think it's somewhere else,
        causing it to navigate incorrectly.
        """
        now = time.time()

        if (
            self._last_lat is not None
            and self._last_lon is not None
            and self._last_gps_time is not None
        ):
            dt = now - self._last_gps_time
            if dt > 0:
                dist = _haversine(self._last_lat, self._last_lon, lat, lon)
                implied_speed = dist / dt

                if implied_speed > MAX_GPS_SPEED_MPS:
                    self.alerts["gps_spoof"] += 1
                    reason = (
                        f"GPS spoof detected: implied speed {implied_speed:.1f} m/s "
                        f"(max {MAX_GPS_SPEED_MPS} m/s)"
                    )
                    logger.warning("[IDS-GPS] %s", reason)
                    return False, reason

        self._last_lat = lat
        self._last_lon = lon
        self._last_gps_time = now
        return True, "ok"

    def get_alert_summary(self) -> dict:
        """Returns current alert counts for telemetry/logging."""
        return dict(self.alerts)


# ---------------------------------------------------------------
# Helper: Haversine distance in meters
# ---------------------------------------------------------------
def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in meters between two GPS coordinates."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))
