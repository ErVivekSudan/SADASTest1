"""
Camera API Server — ASDV Edge ADAS (Security-Hardened)
=======================================================
Changes from original:
  - Telemetry endpoints require API key (fixes V8)
  - Rate limiting on all HTTP endpoints (fixes V4 equivalent)
  - Security event logging (fixes V9)
  - CORS restricted (fixes V2 equivalent)
"""

import cv2
import numpy as np
import time
import os
import threading
import json
import logging
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import socket

# Security modules (NEW)
from src.security.config import ALLOWED_ORIGINS, API_KEY, print_security_config
from src.security.auth import verify_api_key, check_rate_limit
from src.security.logger import setup_security_logging, log_security_event

setup_security_logging()
logger = logging.getLogger("asdv.camera_api")


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"
    return ip


from src.inference.tensorrt_engine import InferenceEngine
from src.utils.image import letterbox, unletterbox, scale_boxes
from src.adas.perception.road.segmentation import clean_road_mask
from src.adas.control.mpcv2 import CenterlineMPC
from src.adas.perception.road.road_v2 import RoadPerception
from src.inference.trt_object_engine import TRTObjectInferenceEngine
from src.adas.perception.object.object_brake import ObjectPerception

YOLOP_MODEL_PATH = "src/weights/yolop/yolop.engine"
YOLO_MODEL_PATH = "src/weights/yolo/yolo.engine"
IMG_SIZE = 256
CAMERA_IP = os.getenv("CAMERA_IP", "0")

# Global telemetry state
telemetry = {
    "steer": 0.0,
    "brake": 0.0,
    "fps": 0.0,
    "latency": 0.0,
    # Security metrics added
    "security_status": "NORMAL",
    "ids_alerts": 0,
}
telemetry_lock = threading.Lock()

is_running = False
camera_thread = None
ids_alert_count = 0


def inference_loop():
    global is_running, telemetry, ids_alert_count

    import pycuda.driver as cuda
    cuda.init()
    ctx = cuda.Device(0).make_context()

    cap = cv2.VideoCapture(CAMERA_IP)
    if not cap.isOpened():
        logger.error("[ERROR] Could not open camera at IP: %s", CAMERA_IP)
        return

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    logger.info("[INFO] Camera initialized at %dx%d", w, h)

    engine = InferenceEngine(YOLOP_MODEL_PATH)
    road_engine = RoadPerception()
    object_engine = TRTObjectInferenceEngine(YOLO_MODEL_PATH)
    object_perception = ObjectPerception(w, h)
    mpc = CenterlineMPC(w, h)

    logger.info("[INFO] ADAS Models Initialized.")

    frame_idx = 0
    t_last_inference = time.perf_counter()

    # Anomaly detection state for camera_api mode
    last_steer = None
    last_brake = None
    MAX_STEER_JUMP = 30.0
    MAX_BRAKE_JUMP = 0.8

    while is_running:
        ret = cap.grab()
        if not ret:
            time.sleep(0.01)
            continue

        frame_idx += 1
        if frame_idx % 3 != 0:
            continue

        ret, frame = cap.retrieve()
        if not ret:
            continue

        t_start = time.perf_counter()

        boxed = letterbox(frame)
        drive_logits = engine.infer(boxed)
        object_outputs = object_engine.infer(boxed)

        if drive_logits.shape[0] == 1:
            drive_mask_320 = (drive_logits[0] > 0).astype(np.uint8)
        else:
            drive_mask_320 = (drive_logits[1] > drive_logits[0]).astype(np.uint8)

        drive_mask = unletterbox(drive_mask_320, frame.shape[:2])
        out = road_engine.process(drive_mask)
        center_pts = out["center_points"]

        steer, traj = mpc.compute(
            road_mask=drive_mask,
            center_points=center_pts,
            gps_bias=0
        )

        unletterboxed_objs = scale_boxes(object_outputs, frame.shape[:2])
        brake, dist = object_perception.filter_and_control(unletterboxed_objs, 10)

        # SECURITY: Anomaly detection even in camera_api mode
        security_status = "NORMAL"
        if last_steer is not None and abs(float(steer) - last_steer) > MAX_STEER_JUMP:
            ids_alert_count += 1
            security_status = "IDS_ALERT"
            log_security_event("SENSOR_INJECTION_CAMERA", {
                "steer_jump": abs(float(steer) - last_steer)
            })
            steer = 0.0  # failsafe
            brake = 0.5

        last_steer = float(steer)
        last_brake = float(brake)

        t_end = time.perf_counter()
        latency = (t_end - t_start) * 1000
        fps = 1.0 / (t_end - t_last_inference) if (t_end - t_last_inference) > 0 else 0
        t_last_inference = t_end

        with telemetry_lock:
            telemetry["steer"] = float(steer)
            telemetry["brake"] = float(brake)
            telemetry["fps"] = float(fps)
            telemetry["latency"] = float(latency)
            telemetry["security_status"] = security_status
            telemetry["ids_alerts"] = ids_alert_count

    cap.release()
    logger.info("[INFO] Camera released.")
    ctx.pop()


app = FastAPI(title="ASDV Camera API", version="1.1-secure")

# SECURITY FIX V2: Restrict CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["X-Api-Key"],
)


@app.on_event("startup")
def start_background_thread():
    global is_running, camera_thread
    print_security_config()
    is_running = True
    camera_thread = threading.Thread(target=inference_loop, daemon=True)
    camera_thread.start()


@app.on_event("shutdown")
def stop_background_thread():
    global is_running, camera_thread
    is_running = False
    if camera_thread:
        camera_thread.join()


# SECURITY FIX V8 + V4: Telemetry endpoints require API key + rate limiting
@app.get("/api/telemetry/stream")
def telemetry_stream(
    request: Request,
    _auth=Depends(verify_api_key)   # <-- API key required
):
    # Rate limit check
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        log_security_event("RATE_LIMIT_EXCEEDED", {"ip": client_ip, "endpoint": "/api/telemetry/stream"})
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})

    def event_generator():
        while True:
            with telemetry_lock:
                data = telemetry.copy()
            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(0.05)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/telemetry")
def get_telemetry(
    request: Request,
    _auth=Depends(verify_api_key)   # <-- API key required
):
    # Rate limit check
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        log_security_event("RATE_LIMIT_EXCEEDED", {"ip": client_ip, "endpoint": "/api/telemetry"})
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})

    with telemetry_lock:
        return telemetry


app.mount("/", StaticFiles(directory="src/static", html=True), name="static")

if __name__ == "__main__":
    local_ip = get_local_ip()
    print("\n[ASDV] Camera API Server Started (Security-Hardened)")
    print(f"[ASDV] Local Dashboard   : http://localhost:8000")
    print(f"[ASDV] Network Dashboard : http://{local_ip}:8000")
    print(f"[ASDV] Telemetry API Key : Set X-Api-Key header (see logs for key)")
    uvicorn.run("src.camera_api:app", host="0.0.0.0", port=8000, reload=False)
