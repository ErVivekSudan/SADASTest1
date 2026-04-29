# 🔐 ASDV Security Setup Guide

## Secure Navigation and Network Architecture  
### Campus Autonomous Vehicle System — NVIDIA Jetson Nano  

> **Project:** Autonomous Self-Driving Vehicle (ASDV)  
> **Security Lead:** Vivek  
> **Version:** 2.1 — Security Hardened  
> **Date:** April 2026  

---

## 📋 Table of Contents

1. [What Changed from the Original](#1-what-changed-from-the-original)
2. [How the Two Repos Work Together](#2-how-the-two-repos-work-together)
3. [Understanding the New Security Keys](#3-understanding-the-new-security-keys)
4. [New Files You Need to Create](#4-new-files-you-need-to-create)
5. [MACHINE 1 — Jetson Nano Setup (edge-adas)](#5-machine-1--jetson-nano-setup-edge-adas)
6. [MACHINE 2 — Duckiebot Setup (adas-ros-client)](#6-machine-2--duckiebot-setup-adas-ros-client)
7. [Start Everything Up](#7-start-everything-up)
8. [Test Step by Step](#8-test-step-by-step)
9. [What Each Security Check Does in Plain English](#9-what-each-security-check-does-in-plain-english)
10. [Reading the Security Logs](#10-reading-the-security-logs)
11. [Common Problems and Fixes](#11-common-problems-and-fixes)
12. [Quick Reference Cheat Sheet](#12-quick-reference-cheat-sheet)

---

## 1. What Changed from the Original

Before the security hardening, the system worked like this:

```
OLD (INSECURE) FLOW:
─────────────────────────────────────────────────────
Duckiebot Camera  ──► ws://JETSON_IP:8000/ws     (NO password, anyone can connect)
Browser/Script    ──► POST /api/control          (NO key, anyone can steer the bot)
Anyone on WiFi    ──► GET  /api/telemetry        (NO auth, live speed/steer visible)
Any Website       ──► cross-origin requests      (NO restriction, CORS = "*")
─────────────────────────────────────────────────────
```

After the security hardening, the system works like this:

```
NEW (SECURE) FLOW:
─────────────────────────────────────────────────────
Duckiebot Camera  ──► ws://JETSON_IP:8000/ws?token=SECRET  (TOKEN required ✅)
Browser/Script    ──► POST /api/control + X-Api-Key header  (API KEY required ✅)
Anyone on WiFi    ──► GET  /api/telemetry + X-Api-Key       (API KEY required ✅)
Only known origin ──► cross-origin requests allowed          (CORS restricted ✅)
─────────────────────────────────────────────────────
```

### Summary Table of All 9 Security Fixes

| # | What was broken | What was fixed | Where |
|---|---|---|---|
| V1 | WebSocket had no authentication | Token auth via `?token=` query param | `edge-adas/src/main.py` |
| V2 | CORS was wide open (`*`) | Restricted to approved origins only | Both repos |
| V3 | `/api/control` had no auth | API Key required via `X-Api-Key` header | `adas-ros-client` |
| V4 | No rate limiting — DoS possible | Per-IP rate limiter (20 req / 10 sec) | `adas-ros-client` |
| V5 | No replay protection | Timestamp freshness + duplicate rejection | `edge-adas` |
| V6 | No sensor anomaly detection | Steering/brake jump IDS check + failsafe | `edge-adas` |
| V7 | GPS bias hardcoded to 0 | GPS spoofing detected via speed check | `edge-adas` |
| V8 | Telemetry API fully open | API key required on all telemetry endpoints | `edge-adas` |
| V9 | Zero security logging | Full structured audit log to file | Both repos |

---

## 2. How the Two Repos Work Together

Think of the system as a **brain and a body**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ASDV System — Big Picture                               │
│                                                                             │
│   ┌──────────────────────────────────┐                                      │
│   │    REPO 1: edge-adas             │                                      │
│   │    Runs ON: NVIDIA Jetson Nano   │                                      │
│   │    Role: THE BRAIN               │                                      │
│   │                                  │                                      │
│   │  Receives camera frames          │                                      │
│   │  Runs AI (YOLOPv2 + YOLO)       │                                      │
│   │  Detects lanes + obstacles       │                                      │
│   │  Runs MPC control                │                                      │
│   │  Sends steering + brake commands │                                      │
│   │  PORT: 8000                      │                                      │
│   └──────────────┬───────────────────┘                                      │
│                  │   WebSocket (ws://)                                       │
│                  │   NOW REQUIRES TOKEN                                      │
│                  │                                                           │
│   ┌──────────────▼───────────────────┐                                      │
│   │    REPO 2: adas-ros-client       │                                      │
│   │    Runs ON: Duckiebot            │                                      │
│   │    Role: THE BODY                │                                      │
│   │                                  │                                      │
│   │  Sends camera frames to brain    │                                      │
│   │  Receives steering + brake       │                                      │
│   │  Converts to wheel velocities    │                                      │
│   │  Publishes ROS WheelsCmdStamped  │                                      │
│   │  Hosts browser control panel     │                                      │
│   │  PORT: 8080                      │                                      │
│   └──────────────────────────────────┘                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Understanding the New Security Keys

You need to create **2 secret values**. Think of them like passwords.

| Key Name | What it does | Who needs it |
|---|---|---|
| `ASDV_WS_TOKEN` | Password to connect the Duckiebot to the Jetson AI server via WebSocket | Jetson Nano (as server) AND Duckiebot (as client) |
| `ASDV_API_KEY` / `DUCKIEBOT_API_KEY` | Password to call any HTTP control or telemetry endpoint | Browser, scripts, Duckiebot forwarding logic |

> ⚠️ **IMPORTANT RULE:** Both machines must use the EXACT same key values.  
> If they don't match, the connection will be rejected.

---

## 4. New Files You Need to Create

Here is every new file you must create and exactly where it goes:

### In `edge-adas` repo:

```
edge-adas/
│
├── .env                         ← YOU MUST CREATE THIS (your secrets)
├── .env.example                 ← Already created (template to copy from)
├── pyproject.toml               ← REPLACE with new version (adds python-dotenv)
│
└── src/
    ├── main.py                  ← REPLACE with hardened version
    ├── camera_api.py            ← REPLACE with hardened version
    │
    └── security/                ← CREATE this entire folder
        ├── __init__.py          ← CREATE (1 line comment file)
        ├── config.py            ← CREATE (reads .env and sets limits)
        ├── auth.py              ← CREATE (WebSocket + HTTP auth logic)
        ├── ids.py               ← CREATE (Intrusion Detection System)
        └── logger.py            ← CREATE (security event logging)
```

### In `adas-ros-client` repo:

```
adas-ros-client/
│
└── packages/
    └── my_package/
        └── src/
            └── control_node.py  ← REPLACE with hardened version
```

---

## 5. MACHINE 1 — Jetson Nano Setup (edge-adas)

> 📌 Do all of these steps while physically on the Jetson Nano  
> (or SSH'd into it). The project folder should be something like  
> `~/edge-adas` or wherever you cloned the repo.

---

### Step 1 — Go to the edge-adas folder

```bash
cd ~/edge-adas
```

If your folder has a different name, use that instead. You can check what is there with:

```bash
ls
```

You should see `pyproject.toml`, `README.md`, and a `src/` folder. That means you are in the right place.

---

### Step 2 — Generate your two secret keys

Copy-paste these two commands one at a time. Each one will print a random secret:

```bash
# This generates your WebSocket token (64 characters long):
python3 -c "import secrets; print(secrets.token_hex(32))"

# This generates your API key (32 characters long):
python3 -c "import secrets; print(secrets.token_hex(16))"
```

You will see output like this:

```
a3f8d2e1c0b9...   ← this is your ASDV_WS_TOKEN (copy this whole line)
e5c4b3a2d1f0...   ← this is your ASDV_API_KEY  (copy this whole line)
```

**Write both values down or copy them to a notes file — you will need them in a later step.**

---

### Step 3 — Create the .env file

The `.env` file is where all your secrets and security settings live.  
It looks like a plain text file with `KEY=VALUE` lines.

```bash
nano .env
```

This opens the text editor. Now paste the following and fill in your real values:

```env
# ============================================================
# ASDV Security Configuration — edge-adas
# NEVER share this file. NEVER commit this to GitHub.
# ============================================================

# WebSocket token — Duckiebot uses this to authenticate to the AI server
# Replace the value below with YOUR generated ASDV_WS_TOKEN:
ASDV_WS_TOKEN=PASTE_YOUR_64_CHAR_TOKEN_HERE

# API key — browser or scripts use this to access control and telemetry
# Replace the value below with YOUR generated ASDV_API_KEY:
ASDV_API_KEY=PASTE_YOUR_32_CHAR_KEY_HERE

# Allowed browser origins (add any IP:port where your browser opens the UI)
ASDV_ALLOWED_ORIGINS=http://localhost:8000,http://localhost:8080

# How old a sensor message can be before it is rejected (seconds)
ASDV_MAX_MSG_AGE=5.0

# Maximum image size allowed (in bytes) — 5MB = 5242880
ASDV_MAX_IMG_BYTES=5242880

# Maximum believable GPS speed for a campus vehicle (meters per second)
# 15 m/s = 54 km/h — if GPS says faster than this, it is flagged as spoofed
ASDV_MAX_GPS_SPEED=15.0

# Maximum steering change per frame (degrees)
# If steering jumps more than this in one frame, IDS flags it as injection
ASDV_MAX_STEER_JUMP=30.0

# Rate limiting — maximum HTTP requests allowed per IP per time window
ASDV_RATE_WINDOW=10
ASDV_RATE_MAX=30

# Where to write security log files
ASDV_LOG_DIR=logs
```

Save and exit: press `Ctrl+X`, then `Y`, then `Enter`.

---

### Step 4 — Make sure .env is not committed to GitHub

```bash
# Check if .env is already in gitignore:
cat .gitignore | grep .env
```

If you see `.env` in the output, you are safe. If you do NOT see it, add it:

```bash
echo ".env" >> .gitignore
```

> ⚠️ Never commit your `.env` file. It contains passwords.  
> The `.env.example` file (no real secrets) is fine to commit.

---

### Step 5 — Create the security module folder

```bash
mkdir -p src/security
```

Then create the empty `__init__.py` marker file:

```bash
echo "# ASDV Security Module" > src/security/__init__.py
```

Now create all the other security files (`config.py`, `auth.py`, `ids.py`, `logger.py`) by copy-pasting the full contents from the security implementation provided in the previous message.

> 📁 Reminder of where each file goes:
> - `src/security/config.py`
> - `src/security/auth.py`
> - `src/security/ids.py`
> - `src/security/logger.py`

---

### Step 6 — Replace main.py and camera_api.py

Replace these two files with the hardened versions from the previous message:

```bash
# Check the files are there before replacing:
ls src/main.py
ls src/camera_api.py
```

Then open each one and replace the contents:

```bash
nano src/main.py
# Select all → Delete → Paste hardened version → Save (Ctrl+X, Y, Enter)

nano src/camera_api.py
# Select all → Delete → Paste hardened version → Save (Ctrl+X, Y, Enter)
```

---

### Step 7 — Install python-dotenv

The hardened code needs `python-dotenv` to load the `.env` file automatically.  
The original code did not need this because it had no `.env` file.

```bash
pip install python-dotenv
```

If you use `uv` (the package manager shown in the repo's `uv.lock`):

```bash
uv add python-dotenv
uv sync
```

---

### Step 8 — Create the logs folder

```bash
mkdir -p logs
```

This is where all security event logs will be written when the server runs.

---

### Step 9 — Find your Jetson Nano IP address

```bash
hostname -I
```

You will see something like `192.168.1.105`. Write this down.  
This is your **JETSON_IP** and you will need it for the Duckiebot setup.

---

## 6. MACHINE 2 — Duckiebot Setup (adas-ros-client)

> 📌 Do all of these steps while on the Duckiebot (or SSH'd into it).  
> The project folder should be something like `~/adas-ros-client` or  
> wherever you cloned that repo.

---

### Step 1 — Go to the adas-ros-client folder

```bash
cd ~/adas-ros-client
```

Check you are in the right place:

```bash
ls packages/my_package/src/
```

You should see `control_node.py` there.

---

### Step 2 — Set the security environment variables

The hardened `control_node.py` reads these three environment variables at startup.  
Set them with the same values you generated on the Jetson Nano.

```bash
# Replace the values below with your REAL values from the Jetson Nano .env file:
export DUCKIEBOT_API_KEY=PASTE_YOUR_SAME_32_CHAR_KEY_HERE
export DUCKIEBOT_ALLOWED_ORIGIN=http://JETSON_IP:8000
export DUCKIEBOT_RATE_MAX=20
export DUCKIEBOT_RATE_WINDOW=10
```

> ⚠️ The `DUCKIEBOT_API_KEY` value MUST be the same as `ASDV_API_KEY` from the Jetson `.env` file.  
> They must match **exactly**, including uppercase/lowercase.

---

### Step 3 — Make the variables permanent (optional but recommended)

Every time you open a new terminal, environment variables set with `export` disappear.  
To make them permanent, add them to your shell profile:

```bash
nano ~/.bashrc
```

Scroll to the very bottom of the file and add these lines:

```bash
# ASDV Security Variables
export DUCKIEBOT_API_KEY=PASTE_YOUR_SAME_32_CHAR_KEY_HERE
export DUCKIEBOT_ALLOWED_ORIGIN=http://JETSON_IP:8000
export DUCKIEBOT_RATE_MAX=20
export DUCKIEBOT_RATE_WINDOW=10
```

Save and exit (`Ctrl+X`, `Y`, `Enter`), then apply immediately:

```bash
source ~/.bashrc
```

---

### Step 4 — Replace control_node.py

Replace the existing file with the hardened version from the previous message:

```bash
nano packages/my_package/src/control_node.py
# Select all → Delete → Paste hardened version → Save (Ctrl+X, Y, Enter)
```

---

### Step 5 — Update the WebSocket connection URL

This is the most important change to make manually.

Somewhere in your project — either in the JavaScript frontend, a Python forwarding script, or a ROS node — you have a WebSocket connection URL that points to the Jetson Nano AI server.

**Find it** (it looks like one of these):

```
ws://192.168.1.105:8000/ws
ws://JETSON_IP:8000/ws
```

**Change it to this format** (add `?token=` at the end):

```
ws://JETSON_IP:8000/ws?token=PASTE_YOUR_ASDV_WS_TOKEN_HERE
```

#### Where to look for the WebSocket URL:

- In the browser control panel: `packages/my_package/src/static/index.html`
- In any Python relay or forwarding script in the project
- In any ROS node that forwards camera frames to the Jetson

If it is in `index.html`, open it and search for `ws://`:

```bash
grep -n "ws://" packages/my_package/src/static/index.html
```

This will show you the exact line number where the WebSocket URL is written.  
Edit that line and add `?token=YOUR_TOKEN` to the end of the URL.

---

## 7. Start Everything Up

### Start the AI Server (Jetson Nano)

Open a terminal on the Jetson Nano in the `edge-adas` folder:

```bash
cd ~/edge-adas

# Option A — if you normally run as a Python module:
python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8000

# Option B — if you run camera_api directly:
python3 src/camera_api.py

# Option C — if you have a run script or Makefile:
# Use whatever you used before
```

When it starts successfully, you will see something like this:

```
==============================================================
  [SECURITY] ASDV Security Configuration Loaded
==============================================================
  WS Token      : a3f8d2e1...f0b9 (set ASDV_WS_TOKEN)
  API Key       : e5c4...b3a2 (set ASDV_API_KEY)
  Max Msg Age   : 5.0s
  Max Img Size  : 5120 KB
  Max GPS Speed : 15.0 m/s
  Allowed CORS  : ['http://localhost:8000', 'http://localhost:8080']
==============================================================
🚀 Initializing pipeline...
✅ Pipeline ready
```

> 💡 If you see the security config block printed, the `.env` was loaded correctly.  
> If you do NOT see it, the `.env` file was not found — double-check you are in the right folder.

---

### Start the Duckiebot Control Node

Open a terminal on the Duckiebot in the `adas-ros-client` folder:

```bash
cd ~/adas-ros-client

# Use the exact same command you used before to launch the Duckietown node
# (this does not change — the startup command is the same)
dts devel run   # OR
rosrun my_package control_node.py   # OR  
python3 packages/my_package/src/control_node.py
```

When it starts successfully, you will see something like this:

```
=======================================================
  [SECURITY] Duckiebot Control Node — Security Config
=======================================================
  API Key         : e5c4...b3a2 (set DUCKIEBOT_API_KEY)
  Allowed Origin  : http://192.168.1.105:8000
  Rate Limit      : 20 req / 10s
=======================================================
[ControlNode] Ready — UI at http://0.0.0.0:8080
[STARTUP] Control node started securely.
```

> 💡 If the API key shows the right prefix from your `.env`, the variable was loaded correctly.

---

## 8. Test Step by Step

### Test 1 — Verify the Jetson Server Responds

From any machine on the same network:

```bash
curl http://JETSON_IP:8000/api/telemetry
```

Expected response WITHOUT the key:
```json
{"detail": "Invalid or missing API key. Use X-Api-Key header."}
```
This means authentication is working correctly — access is blocked.

Now try WITH the API key:
```bash
curl http://JETSON_IP:8000/api/telemetry \
  -H "X-Api-Key: YOUR_ASDV_API_KEY"
```

Expected response:
```json
{"steer": 0.0, "brake": 0.0, "fps": 0.0, "latency": 0.0, ...}
```

---

### Test 2 — Verify the Duckiebot Control Rejects Bad Requests

```bash
# Try WITHOUT the API key — should get 401 Unauthorized:
curl -X POST http://DUCKIEBOT_IP:8080/api/control \
  -H "Content-Type: application/json" \
  -d '{"steer": 5.0, "brake": 0.2}'
```

Expected output:
```json
{"error": "Unauthorized"}
```

Now try WITH the key:
```bash
curl -X POST http://DUCKIEBOT_IP:8080/api/control \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: YOUR_DUCKIEBOT_API_KEY" \
  -d '{"steer": 5.0, "brake": 0.2}'
```

Expected output:
```json
{"status": "ok"}
```

---

### Test 3 — Verify Rate Limiting Blocks Flooding

Send 25 rapid requests (exceeds the 20/10s limit):

```bash
for i in $(seq 1 25); do
  curl -s -X POST http://DUCKIEBOT_IP:8080/api/control \
    -H "Content-Type: application/json" \
    -H "X-Api-Key: YOUR_DUCKIEBOT_API_KEY" \
    -d '{"steer": 0.0, "brake": 1.0}' &
done
wait
```

You should see some responses return `429 Too Many Requests`.  
This proves rate limiting is working.

---

### Test 4 — Test Mode Switching

```bash
# Switch to auto mode:
curl -X POST http://DUCKIEBOT_IP:8080/api/mode \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: YOUR_DUCKIEBOT_API_KEY" \
  -d '{"mode": "auto"}'

# Switch back to manual:
curl -X POST http://DUCKIEBOT_IP:8080/api/mode \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: YOUR_DUCKIEBOT_API_KEY" \
  -d '{"mode": "manual"}'
```

---

### Test 5 — Test Emergency Stop (No Key Required)

```bash
# Emergency stop works WITHOUT any API key — safety by design:
curl -X POST http://DUCKIEBOT_IP:8080/api/emergency_stop
```

Expected output:
```json
{"status": "stopped"}
```

This proves the safety-over-security design decision works: in a real emergency,  
anyone can stop the vehicle immediately without needing a password.

---

### Test 6 — Manual Drive Test

1. Open your browser and go to `http://DUCKIEBOT_IP:8080`
2. The control panel should load
3. Try manual driving controls
4. Confirm the safety timeout (500 ms with no input) causes the bot to stop

---

### Test 7 — Auto Mode End-to-End Test

1. Confirm `edge-adas` server is running on Jetson Nano
2. Confirm WebSocket connection URL in browser/client uses `?token=YOUR_TOKEN`
3. Switch to auto mode
4. Feed a camera video or live camera to the system
5. Confirm steering and brake values change based on the lane detection output

---

## 9. What Each Security Check Does in Plain English

### WebSocket Token Authentication

> Like a password for the Duckiebot's door to the AI brain.

The Duckiebot connects to the Jetson using a WebSocket. Before, any device on the WiFi could connect. Now, the connection URL must contain `?token=SECRET`. If the secret is wrong or missing, the connection is closed immediately with code 4401 (custom: Unauthorized).

---

### CORS Restriction

> Like a guest list for the browser.

When a browser page at one web address (origin) tries to talk to a server at a different address, the browser first asks: "Am I allowed?" Before, the server said yes to everyone (`*`). Now, it only says yes to addresses you have explicitly listed.

---

### API Key on Control Endpoints

> Like a second password specifically for the steering wheel.

Any HTTP request to `/api/control` or `/api/mode` must carry the header `X-Api-Key: YOUR_KEY`. Without it, the server returns 401 Unauthorized. The comparison is done in constant time (same time whether the key is completely wrong or one character off) to prevent timing attacks.

---

### Rate Limiting

> Like a bouncer who kicks you out if you knock too fast.

Each IP address can only make 20 requests per 10-second window. After that, it gets blocked with a 429 response until the window resets. This prevents DoS attacks that try to flood the control server.

---

### Replay Attack Protection

> Like marking a letter as "already opened" so someone can't deliver the same letter twice.

Every `SensorMessage` the Duckiebot sends already has a `timestamp` field. The AI server now checks: is this timestamp within the last 5 seconds? Has this exact timestamp been seen before? If either check fails, the message is discarded.

---

### Sensor/Control Anomaly Detection (IDS)

> Like noticing if someone suddenly grabs the steering wheel and yanks it.

After the AI inference runs and produces a steering angle, the IDS compares it to the previous frame's steering angle. A real lane-following system on a smooth campus road cannot jump 30+ degrees in one frame. If it does, it is flagged as a potential injected fake frame, and the output is overridden with a safe straight-ahead command.

---

### GPS Spoofing Detection

> Like noticing if your phone's map suddenly says you are in another country.

Between consecutive GPS readings, the system calculates how fast the vehicle would have to be moving to cover that distance in that time. A campus vehicle cannot teleport. If the implied speed exceeds 15 m/s (54 km/h), the GPS reading is flagged as spoofed and ignored — the vehicle falls back to visual lane following only.

---

### Telemetry Authentication

> Like putting a lock on the window so no one can watch your controls.

The `/api/telemetry` and `/api/telemetry/stream` endpoints previously exposed live speed, steering angle, and inference latency to anyone on the network. Now they require the same API key as the control endpoints.

---

### Security Event Logging

> Like keeping a diary of every suspicious thing that happened.

Every security event — failed authentication, rate limit hit, replay attack, GPS spoof, anomaly detection, failsafe trigger — is written to `logs/asdv_security.log` in structured format with timestamps. This file is your evidence during evaluation.

---

## 10. Reading the Security Logs

### Watch logs live on Jetson Nano:

```bash
tail -f logs/asdv_security.log
```

### What each log event means:

| Log Message | What Happened |
|---|---|
| `[AUTH] WebSocket authenticated from 192.168.x.x` | Duckiebot connected successfully ✅ |
| `[AUTH] WebSocket rejected — invalid token` | Wrong or missing token — check URL ❌ |
| `[AUTH] HTTP API key rejected` | Wrong or missing `X-Api-Key` header ❌ |
| `[IDS-REPLAY] Message too old: 8.2s > 5.0s` | Stale message received — possible replay attack ⚠️ |
| `[IDS-REPLAY] Duplicate timestamp rejected` | Exact replay attack detected 🚨 |
| `[IDS-GPS] GPS spoof detected: implied speed 120 m/s` | GPS spoofing detected 🚨 |
| `[IDS-ANOMALY] Steering jump too large: 45.2°` | Frame injection suspected 🚨 |
| `[RATE] Rate limit exceeded for IP: 192.168.x.x` | DoS/flood attempt blocked ⚠️ |
| `[SECURITY EVENT] FAILSAFE_TRIGGERED` | Vehicle went to safe defaults after anomaly 🛡️ |
| `[SECURITY EVENT] SESSION_SUMMARY` | End of WebSocket session alert count |

### Sample log output:

```
2026-04-26 13:05:23 | INFO     | asdv.security.auth   | [AUTH] WebSocket authenticated from 192.168.1.42
2026-04-26 13:05:24 | WARNING  | asdv.security.ids    | [IDS-REPLAY] Message too old: 6.1s > 5.0s
2026-04-26 13:05:31 | WARNING  | asdv.security.events | [SECURITY EVENT] {"timestamp": 1745652331, "event": "GPS_SPOOF", "reason": "implied speed 130.2 m/s (max 15.0 m/s)"}
2026-04-26 13:05:31 | WARNING  | asdv.security.events | [SECURITY EVENT] {"timestamp": 1745652331, "event": "FAILSAFE_TRIGGERED", "safe_steer": 0.0, "safe_brake": 0.5}
```

---

## 11. Common Problems and Fixes

### Problem: WebSocket connects and immediately disconnects

**Cause:** The token in the connection URL is wrong or missing.

**Fix:**
1. Check the connection URL — it must end with `?token=YOUR_ASDV_WS_TOKEN`
2. Compare the token in the URL exactly to the value in `.env` — they must match character for character
3. Check the Jetson log for: `[AUTH] WebSocket rejected — invalid token`

---

### Problem: `/api/control` returns 401 Unauthorized

**Cause:** The `X-Api-Key` header is missing or has the wrong value.

**Fix:**
1. Make sure every request includes `-H "X-Api-Key: YOUR_KEY"`
2. Verify `DUCKIEBOT_API_KEY` on the bot matches `ASDV_API_KEY` on the Jetson exactly
3. Check for accidental spaces or newline characters in your key values

---

### Problem: Browser shows CORS error

**Cause:** Your browser's origin is not in the allowed list.

**Fix:**
1. Find out what origin your browser is using — look at the address bar: `http://HOST:PORT`
2. Add that exact value to `ASDV_ALLOWED_ORIGINS` in the Jetson `.env` file
3. Restart the Jetson server
4. Also add it to `DUCKIEBOT_ALLOWED_ORIGIN` on the bot

Example: if your browser is at `http://192.168.1.50:8080`, add that to:
```env
ASDV_ALLOWED_ORIGINS=http://localhost:8000,http://localhost:8080,http://192.168.1.50:8080
```

---

### Problem: Requests randomly fail with 429 Too Many Requests

**Cause:** Rate limiting is triggering — too many requests per second from your IP.

**Fix:** Either slow down your request rate, or raise the limit in `.env`:
```env
ASDV_RATE_MAX=50
ASDV_RATE_WINDOW=10
```

---

### Problem: Bot keeps stopping in auto mode unexpectedly

**Cause:** Either the safety timeout fired (no control update within 500 ms) or the IDS anomaly detector triggered a failsafe.

**Fix:**
1. Check the Jetson log for `[IDS-ANOMALY]` or `[FAILSAFE_TRIGGERED]` messages
2. If anomaly triggered: check if your camera feed has sharp scene changes — this causes big steering jumps
3. If timeout triggered: check the round-trip latency between Jetson and Duckiebot — if it exceeds 500 ms the timeout fires
4. Increase the timeout in `control_node.py` if needed: `SAFETY_TIMEOUT = 1.0`

---

### Problem: `.env` values not loading (security config shows random-looking tokens)

**Cause:** `python-dotenv` is not installed, OR you are running from the wrong directory.

**Fix:**
```bash
pip install python-dotenv
cd ~/edge-adas   # MUST be in the same folder as .env
python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8000
```

---

### Problem: `logs/asdv_security.log` does not exist

**Cause:** The `logs/` directory was not created.

**Fix:**
```bash
mkdir -p ~/edge-adas/logs
```

---

## 12. Quick Reference Cheat Sheet

### Machine 1 — Jetson Nano commands

```bash
# Go to project
cd ~/edge-adas

# Generate tokens (do this once):
python3 -c "import secrets; print('ASDV_WS_TOKEN=' + secrets.token_hex(32))"
python3 -c "import secrets; print('ASDV_API_KEY=' + secrets.token_hex(16))"

# Create logs folder:
mkdir -p logs

# Install new dependency:
pip install python-dotenv

# Start server (Option A):
python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8000

# Watch security logs live:
tail -f logs/asdv_security.log

# Test telemetry without key (should return 401):
curl http://localhost:8000/api/telemetry

# Test telemetry with key:
curl http://localhost:8000/api/telemetry -H "X-Api-Key: YOUR_API_KEY"
```

---

### Machine 2 — Duckiebot commands

```bash
# Go to project
cd ~/adas-ros-client

# Set environment variables (use the SAME key as ASDV_API_KEY):
export DUCKIEBOT_API_KEY=YOUR_SAME_API_KEY
export DUCKIEBOT_ALLOWED_ORIGIN=http://JETSON_IP:8000

# Start control node (use whatever command you normally use):
rosrun my_package control_node.py

# Test control WITHOUT key (should return 401):
curl -X POST http://localhost:8080/api/control \
  -H "Content-Type: application/json" \
  -d '{"steer": 5.0, "brake": 0.2}'

# Test control WITH key (should return {"status":"ok"}):
curl -X POST http://localhost:8080/api/control \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: YOUR_SAME_API_KEY" \
  -d '{"steer": 5.0, "brake": 0.2}'

# Emergency stop (no key needed — safety design):
curl -X POST http://localhost:8080/api/emergency_stop
```

---

### WebSocket URL (the one change you MUST make in your frontend/client code)

```
# OLD (stop using this):
ws://JETSON_IP:8000/ws

# NEW (use this):
ws://JETSON_IP:8000/ws?token=YOUR_ASDV_WS_TOKEN
```

---

### Security Vulnerabilities vs Fixes — One Line Each

```
V1 WebSocket auth  → add ?token=SECRET to connection URL
V2 CORS open       → ASDV_ALLOWED_ORIGINS in .env lists approved origins
V3 Control no auth → X-Api-Key header required on /api/control
V4 No rate limit   → 20 requests per 10 seconds per IP enforced
V5 No replay guard → message timestamp checked for freshness + deduplication
V6 No IDS          → steering/brake jump detection in ids.py, failsafe on trigger
V7 GPS unguarded   → haversine speed check, >15 m/s flagged as spoof
V8 Telemetry open  → X-Api-Key required on /api/telemetry endpoints
V9 No logging      → logs/asdv_security.log written with structured events
```

---

## Security Architecture Summary

```
  Attacker                   ASDV System
     │                            │
     ├──── Fake WS connect ──────►│  BLOCKED: Invalid token → close code 4401
     │                            │
     ├──── Replay old frame ─────►│  BLOCKED: Timestamp > 5s old → discarded
     │                            │
     ├──── Inject large image ───►│  BLOCKED: >5MB payload → rejected
     │                            │
     ├──── Spoof GPS coords ─────►│  BLOCKED: Speed >15 m/s → GPS ignored,
     │                            │           fallback to visual nav
     ├──── Inject steer cmd ─────►│  BLOCKED: Jump >30° → failsafe override
     │                            │           (steer=0, brake=0.5)
     ├──── Flood /api/control ───►│  BLOCKED: >20 req/10s → 429 Too Many Requests
     │                            │
     ├──── Spy on telemetry ─────►│  BLOCKED: No X-Api-Key → 401 Unauthorized
     │                            │
     └──── Cross-origin attack ──►│  BLOCKED: Origin not in allowed list → CORS error
                                  │
                              All events logged
                           to logs/asdv_security.log
```

---

*Document prepared by Vivek (Security Lead) — ASDV Minor Project, April 2026*
