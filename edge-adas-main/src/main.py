"""
Autonomy Server — ASDV Edge ADAS (Security-Hardened)
======================================================
Changes from original:
  - WebSocket requires token authentication (fixes V1)
  - CORS restricted to allowed origins only (fixes V2)
  - Replay attack protection via message timestamp (fixes V5)
  - Payload size validation (fixes V6 partial)
  - Control output anomaly detection (fixes V6)
  - GPS spoofing detection via speed plausibility (fixes V7)
  - All security events logged (fixes V9)
"""

import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import numpy as np

# Security modules (NEW)
from src.security.config import ALLOWED_ORIGINS, print_security_config
from src.security.auth import authenticate_websocket
from src.security.ids import IntrusionDetector
from src.security.logger import setup_security_logging, log_security_event

# API Models & Codecs
from src.api.models import (
    SensorMessage, AutonomyMessage, AutonomyState, Control,
    encode_msgpack, decode_msgpack, decode_jpeg_bytes
)

# Inference Engines
from src.inference.openvino_engine import InferenceEngine
from src.inference.object_engine import ObjectInferenceEngine, ObjectPerception

# Perception Pipeline
from src.adas.perception.road.segmentation import clean_road_mask
from src.adas.perception.road.road_v2 import RoadPerception

# Control
from src.adas.control.mpcv2 import CenterlineMPC

# Utils
from src.utils.image import letterbox, unletterbox, scale_boxes

# =============================================================================
# Security Setup — runs first, before anything else
# =============================================================================
setup_security_logging()
print_security_config()

# =============================================================================
# Configuration
# =============================================================================
YOLOP_MODEL_PATH = "src/weights/yolop/yolopv2fp16.xml"
YOLO_MODEL_PATH = "src/weights/yolo/yolo26n.xml"
DEVICE = "GPU"

# =============================================================================
# FastAPI Application
# =============================================================================
app = FastAPI(title="ASDV Autonomy Server", version="2.1-secure")

# SECURITY FIX V2: Restrict CORS to known origins only
# Before: allow_origins=["*"] — any website could make requests
# After:  only origins in ALLOWED_ORIGINS list are permitted
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],        # Only GET needed for HTTP; WS uses its own auth
    allow_headers=["X-Api-Key"],
)

# =============================================================================
# Initialize Pipeline Components (Global Singletons)
# =============================================================================
print("🚀 Initializing pipeline...")
engine = InferenceEngine(YOLOP_MODEL_PATH, device=DEVICE)
object_engine = ObjectInferenceEngine(YOLO_MODEL_PATH, device="CPU")
road_perception = RoadPerception()
mpc = CenterlineMPC()
print("✅ Pipeline ready")

# =============================================================================
# WebSocket Flow Control
# =============================================================================
latest_packet: bytes | None = None


async def receiver_task(ws: WebSocket):
    """Constantly drains socket to keep 'latest_packet' fresh."""
    global latest_packet
    try:
        while True:
            latest_packet = await ws.receive_bytes()
    except Exception:
        pass


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Secure WebSocket endpoint.

    SECURITY FLOW:
    1. Accept TCP connection (needed before we can read the token)
    2. Authenticate via token query parameter
    3. Create per-session IDS instance
    4. For each message: replay check → payload check → inference → anomaly check → respond
    5. If any check fails → send WARNING status or close connection
    """
    global latest_packet

    # Step 1: Accept at TCP level (required before auth check)
    await ws.accept()

    # Step 2: SECURITY FIX V1 — Authenticate WebSocket
    # Before: await ws.accept() with no checks
    # After:  token must be passed as ?token=XXX query parameter
    if not await authenticate_websocket(ws):
        log_security_event("AUTH_FAIL", {
            "source": "websocket",
            "ip": ws.client.host if ws.client else "unknown"
        })
        return  # Connection already closed inside authenticate_websocket

    print(f"✅ Secure WebSocket connected from {ws.client.host if ws.client else 'unknown'}")

    # Step 3: Create per-session IDS
    ids = IntrusionDetector()

    # Start background receiver
    asyncio.create_task(receiver_task(ws))

    try:
        while True:
            # Wait for a frame
            if latest_packet is None:
                await asyncio.sleep(0.005)
                continue

            # Grab & clear
            raw_data = latest_packet
            latest_packet = None

            # Decode incoming message
            try:
                msg = decode_msgpack(raw_data, SensorMessage)
            except Exception as e:
                log_security_event("MALFORMED_MESSAGE", {"error": str(e)})
                continue

            # SECURITY FIX V5 — Replay Attack Protection
            # Check message timestamp is fresh and not seen before
            replay_ok, replay_reason = ids.check_replay(msg.payload.timestamp)
            if not replay_ok:
                log_security_event("REPLAY_ATTACK", {"reason": replay_reason})
                # Send WARNING but keep connection alive — log and continue
                await _send_warning(ws, "REPLAY_DETECTED")
                continue

            # SECURITY FIX V6 (payload size) — Reject oversized images
            payload_ok, payload_reason = ids.check_payload_size(msg.payload.image)
            if not payload_ok:
                log_security_event("OVERSIZED_PAYLOAD", {"reason": payload_reason})
                await _send_warning(ws, "PAYLOAD_REJECTED")
                continue

            # SECURITY FIX V7 — GPS Spoofing Detection
            gps_ok, gps_reason = ids.check_gps(
                msg.payload.gps.lat,
                msg.payload.gps.lon
            )
            if not gps_ok:
                log_security_event("GPS_SPOOF", {"reason": gps_reason})
                # Use 0.0 bias (go straight) instead of spoofed GPS data
                gps_bias = 0.0
            else:
                # Normal path: GPS is valid, no bias correction needed in demo
                gps_bias = 0.0  # TODO: integrate CheckpointManager here

            # --- AI Inference Pipeline (unchanged from original) ---
            frame = decode_jpeg_bytes(msg.payload.image)
            frame_shape = frame.shape[:2]
            boxed = letterbox(frame)

            outputs = engine.infer(boxed)
            object_outputs = object_engine.get_perception(boxed)

            drive_logits = outputs["drive"][0]
            lane_logits = outputs["lane"][0]

            if drive_logits.shape[0] == 1:
                drive_mask_640 = (drive_logits[0] > 0).astype(np.uint8)
            else:
                drive_mask_640 = (drive_logits[1] > drive_logits[0]).astype(np.uint8)

            if lane_logits.shape[0] == 1:
                lane_mask_640 = (lane_logits[0] > 0).astype(np.uint8)
            else:
                lane_mask_640 = (lane_logits[1] > lane_logits[0]).astype(np.uint8)

            drive_mask = unletterbox(drive_mask_640, frame_shape)
            lane_mask = unletterbox(lane_mask_640, frame_shape)
            drive_mask = clean_road_mask(drive_mask)

            road_out = road_perception.process(drive_mask)
            center_pts = road_out["center_points"]

            h, w = frame_shape
            object_perception = ObjectPerception(w, h)
            scaled_objs = scale_boxes(object_outputs, frame_shape)
            brake_force, closest_dist = object_perception.filter_and_control(scaled_objs, 10)

            steering, trajectory = mpc.compute(
                road_mask=drive_mask,
                center_points=center_pts,
                gps_bias=gps_bias
            )
            # --- End AI Pipeline ---

            # SECURITY FIX V6 — Control Output Anomaly Detection
            # Check if steering/brake changed by an impossible amount in one frame
            control_ok, control_reason = ids.check_control_output(
                float(steering), float(brake_force)
            )
            if not control_ok:
                log_security_event("SENSOR_INJECTION", {"reason": control_reason})
                # FAILSAFE: Override with safe values (go straight, mild brake)
                steering = 0.0
                brake_force = 0.5
                status_msg = "WARNING"
                log_security_event("FAILSAFE_TRIGGERED", {
                    "reason": "anomaly_detected",
                    "safe_steer": steering,
                    "safe_brake": brake_force
                })
            else:
                if brake_force > 0.5:
                    status_msg = "WARNING"
                else:
                    status_msg = "NORMAL"

            # Build and send response
            response = AutonomyMessage(
                type="autonomy",
                payload=AutonomyState(
                    laneLines=[],
                    trajectory=trajectory,
                    control=Control(
                        steeringAngle=float(steering),
                        confidence=road_out["confidence"]
                    ),
                    status=status_msg
                )
            )
            await ws.send_bytes(encode_msgpack(response))

    except Exception as e:
        print(f"❌ Connection error: {e}")
        log_security_event("CONNECTION_ERROR", {"error": str(e)})
    finally:
        # Log session summary
        summary = ids.get_alert_summary()
        if any(v > 0 for v in summary.values()):
            log_security_event("SESSION_SUMMARY", summary)


async def _send_warning(ws: WebSocket, reason: str):
    """Send a WARNING autonomy message to the client."""
    try:
        response = AutonomyMessage(
            type="autonomy",
            payload=AutonomyState(
                laneLines=[],
                trajectory=[],
                control=Control(steeringAngle=0.0, confidence=0.0),
                status="WARNING"
            )
        )
        await ws.send_bytes(encode_msgpack(response))
    except Exception:
        pass
