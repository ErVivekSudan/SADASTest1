# 🦆 Duckiebot Web-Controlled Driving

A lightweight, web-based remote-control system for [Duckietown](https://www.duckietown.org/) robots. Drive your Duckiebot from any browser on the same network — manually with an on-screen steering wheel, or autonomously via an external SSE telemetry stream.

Built on the **Duckietown `template-ros` (v3)** scaffold with **ROS** (Noetic) and **Flask**.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup & Build](#setup--build)
- [Running](#running)
- [Usage Guide](#usage-guide)
  - [Manual Mode](#manual-mode)
  - [Auto Mode](#auto-mode)
  - [Emergency Stop](#emergency-stop)
- [API Reference](#api-reference)
- [Configuration & Tuning](#configuration--tuning)
- [How It Works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Features

| Feature            | Description                                                            |
| ------------------ | ---------------------------------------------------------------------- |
| **Manual Driving** | Interactive canvas steering wheel (±27°) and speed slider (0–0.2 m/s)  |
| **Auto Mode**      | Connect to any external SSE stream sending `{steer, brake}` telemetry  |
| **Live Telemetry** | Real-time display of steering angle, brake force, and wheel velocities |
| **Emergency Stop** | One-tap button that immediately zeroes all wheel commands              |
| **Safety Timeout** | Wheels auto-stop if no control update is received within 500 ms        |
| **Lightweight**    | Single-process design — Flask runs in a thread inside the ROS node     |
| **Cross-Device**   | Responsive UI works on desktops, tablets, and phones                   |

---

## Architecture

```
┌───────────────────────── Duckiebot (Docker) ───────────────────────────┐
│                                                                        │
│   control_node.py  (single process)                                    │
│   ┌────────────────────────────┬──────────────────────────────────┐    │
│   │  Flask Thread (port 8080)  │  ROS Timer (10 Hz)               │    │
│   │  ──────────────────────    │  ─────────────────────────────   │    │
│   │  GET  /         → UI       │  Reads shared state              │    │
│   │  POST /api/control         │  Converts steer,brake → wheels   │    │
│   │  POST /api/mode            │  Publishes WheelsCmdStamped      │    │
│   │  GET  /api/status          │  Safety timeout (500 ms)         │    │
│   └────────────────────────────┴──────────────────────────────────┘    │
│                                       ↓ publishes                      │
│                     /{VEHICLE_NAME}/wheels_driver_node/wheels_cmd      │
└────────────────────────────────────────────────────────────────────────┘
         ↑ HTTP (port 8080)
┌────────────────────┐        ┌────────────────────────┐
│  Browser (Laptop)  │ ──────→│  External SSE Source   │
│  Manual Controls   │        │  (Auto Mode, optional) │
└────────────────────┘        └────────────────────────┘
```

**Why single-process?** Running Flask in a daemon thread inside the ROS node eliminates inter-process communication overhead. Steer/brake values are shared via a `threading.Lock`-protected state, keeping latency minimal on the Duckiebot's constrained hardware.

---

## Project Structure

```
ros-test/
├── .dtproject                    # Duckietown project metadata (template-ros v3)
├── Dockerfile                    # Multi-stage Docker build for Duckiebot
├── configurations.yaml           # Duckietown configurations (empty)
├── dependencies-apt.txt          # System-level APT packages (none needed)
├── dependencies-py3.txt          # Python packages → flask
├── dependencies-py3.dt.txt       # Duckietown-specific Python packages (none)
│
├── launchers/
│   └── default.sh                # Entry point — runs control_node.py
│
├── packages/
│   └── my_package/               # ROS catkin package
│       ├── CMakeLists.txt        # Catkin build file (rospy)
│       ├── package.xml           # Package manifest
│       ├── __init__.py
│       └── src/
│           ├── __init__.py
│           ├── control_node.py   # ⭐ Main node (Flask + ROS publisher)
│           └── static/
│               └── index.html    # ⭐ Web control panel UI
│
├── assets/                       # Module assets (placeholder)
├── docs/                         # Sphinx documentation scaffold
├── html/                         # Generated docs output (gitignored)
├── .github/workflows/            # CI — Jira PR title enforcement
├── LICENSE.pdf                   # Duckietown license
└── README.md                     # ← You are here
```

> **Key files to know:** The entire application is just two files:
> [`control_node.py`](packages/my_package/src/control_node.py) and [`index.html`](packages/my_package/src/static/index.html).

---

## Prerequisites

Before you begin, make sure you have:

1. **A Duckiebot** — Powered on and connected to your local network
2. **Duckietown Shell (`dts`)** — Installed on your development machine
   ```bash
   pip3 install duckietown-shell
   dts update
   ```
3. **Docker** — The Duckiebot runs Docker natively; `dts` handles the build
4. **Network access** — Your laptop/phone must be on the same network as the Duckiebot

> **Verify connectivity:**
>
> ```bash
> ping ROBOT_NAME.local
> ```
>
> Replace `ROBOT_NAME` with your Duckiebot's hostname throughout this guide.

---

## Setup & Build

### 1. Clone the repository

```bash
git clone https://github.com/SrabanMondal/ros-test.git
cd ros-test
```

### 2. Build the Docker image on the Duckiebot

```bash
dts devel build -f -H ROBOT_NAME
```

| Flag            | Purpose                                        |
| --------------- | ---------------------------------------------- |
| `-f`            | Force rebuild (ignores cache)                  |
| `-H ROBOT_NAME` | Build directly on the Duckiebot hardware (ARM) |

> **Note:** The first build may take several minutes as it pulls the base image (`dt-ros-commons:daffy`) and installs Flask. Subsequent builds are faster due to Docker layer caching.

### 3. Verify the build

A successful build ends with:

```
Successfully tagged duckietown/ros-test:v1-...
```

---

## Running

### Start the container

```bash
dts devel run -H ROBOT_NAME
```

On startup you should see the log message:

```
[ControlNode] Ready  —  UI at http://0.0.0.0:8080
```

### Open the control panel

From any device on the same network, open a browser and navigate to:

```
http://ROBOT_NAME.local:8080
```

> **Alternative:** If `.local` mDNS doesn't work, use the Duckiebot's IP address directly:
>
> ```
> http://192.168.x.x:8080
> ```

### Stop the container

Press `Ctrl+C` in the terminal where `dts devel run` is active. The node will publish zero-velocity wheel commands on shutdown.

---

## Usage Guide

### Manual Mode

This is the default mode when the UI loads.

1. **Steering Wheel** — Click/tap and drag to rotate the on-screen steering wheel
   - Range: **-27°** (left) to **+27°** (right)
   - Double-click to snap back to center (0°)
2. **Speed Slider** — Drag the slider to set forward speed
   - Range: **0** (stopped) to **0.2** m/s (max safe speed)
3. The UI sends control commands at **~20 Hz** while you interact, and sends keep-alive updates every **200 ms** while speed > 0

### Auto Mode

1. Click the **Auto** toggle button at the top
2. Enter the URL of your **Server-Sent Events (SSE)** endpoint
3. Click **Connect**

The browser will connect to the SSE stream and forward each event to the Duckiebot. Expected SSE data format:

```json
{
  "steer": 12.5,
  "brake": 0.3
}
```

| Field   | Type  | Range         | Description                                      |
| ------- | ----- | ------------- | ------------------------------------------------ |
| `steer` | float | -27.0 to 27.0 | Steering angle in degrees. Positive = turn right |
| `brake` | float | 0.0 to 1.0    | Brake force. 0 = no braking, ≥0.8 = full stop    |

> **CORS:** Your SSE server must include `Access-Control-Allow-Origin` headers for cross-origin browser connections.

### Emergency Stop

The red **⛔ EMERGENCY STOP** button at the bottom of the UI:

- Immediately sets steer to 0° and brake to maximum
- Disconnects any active SSE stream
- Resets the speed slider to 0

---

## API Reference

The Flask server exposes these REST endpoints on port **8080**:

### `GET /`

Serves the web control panel (`index.html`).

### `POST /api/control`

Send a control command.

**Request body** (JSON):

```json
{
  "steer": 15.0,
  "brake": 0.2
}
```

**Response:**

```json
{ "status": "ok" }
```

### `POST /api/mode`

Switch driving mode.

**Request body** (JSON):

```json
{ "mode": "manual" }
```

Accepted values: `"manual"`, `"auto"`

**Response:**

```json
{ "status": "ok", "mode": "manual" }
```

### `GET /api/status`

Poll current telemetry state.

**Response:**

```json
{
  "steer": 15.0,
  "brake": 0.2,
  "mode": "manual",
  "vel_left": 0.2111,
  "vel_right": 0.1889
}
```

---

## Configuration & Tuning

All tuning constants are defined at the top of [`control_node.py`](packages/my_package/src/control_node.py):

| Constant         | Default | Description                                   |
| ---------------- | ------- | --------------------------------------------- |
| `MAX_SPEED`      | `0.2`   | Maximum forward wheel speed (m/s)             |
| `MAX_STEER`      | `27.0`  | Maximum steering angle (degrees)              |
| `BRAKE_ZERO`     | `0.8`   | Brake force value at which speed reaches zero |
| `CONTROL_HZ`     | `10`    | Wheel command publish rate (Hz)               |
| `SAFETY_TIMEOUT` | `0.5`   | Seconds of silence before auto-stop           |
| `FLASK_PORT`     | `8080`  | Web server port                               |

After changing these values, rebuild and re-run:

```bash
dts devel build -f -H ROBOT_NAME
dts devel run -H ROBOT_NAME
```

---

## How It Works

### Differential Drive Model

The node converts `(steer, brake)` into left/right wheel velocities using a simple differential drive model:

```
1. Brake → Effective Speed
   ─────────────────────────────────────────
   if brake ≥ 0.8:
       effective_speed = 0
   else:
       effective_speed = MAX_SPEED × (1 − brake / 0.8)

   Examples:
     brake = 0.0  →  speed = 0.200 (full speed)
     brake = 0.4  →  speed = 0.100
     brake = 0.8  →  speed = 0.000 (stopped)

2. Steer → Differential Wheel Velocities
   ─────────────────────────────────────────
   n = steer / 27.0                  (normalized: -1 to +1)
   vel_left  = speed × (1 + n)      (faster on right turns)
   vel_right = speed × (1 − n)      (slower on right turns)

   Examples (speed = 0.2):
     steer =   0°  →  VL = 0.200,  VR = 0.200  (straight)
     steer = +27°  →  VL = 0.400,  VR = 0.000  (hard right)
     steer = -27°  →  VL = 0.000,  VR = 0.400  (hard left)
```

### Safety Mechanisms

1. **Timeout auto-stop:** If no `POST /api/control` is received for 500 ms, the node publishes `(0, 0)` wheel commands
2. **Graceful shutdown:** On `Ctrl+C` or container stop, the node sends a final zero-velocity command
3. **Input clamping:** Steer is clamped to ±27°, brake is clamped to [0, 1]
4. **Low max speed:** `MAX_SPEED = 0.2` limits the robot to safe operating speeds

### ROS Topics

| Topic                                           | Type                               | Direction | Description                 |
| ----------------------------------------------- | ---------------------------------- | --------- | --------------------------- |
| `/{VEHICLE_NAME}/wheels_driver_node/wheels_cmd` | `duckietown_msgs/WheelsCmdStamped` | Published | Left/right wheel velocities |

The `VEHICLE_NAME` is read from the environment variable set by the Duckietown runtime.

---

## Troubleshooting

### UI doesn't load

- Verify the Duckiebot is reachable: `ping ROBOT_NAME.local`
- Check that port 8080 isn't blocked by a firewall
- Try using the IP address directly instead of `.local`

### Wheels don't move

- Ensure the speed slider is above 0 (brake force must be below 0.8)
- Check ROS topic output on the Duckiebot:
  ```bash
  dts devel run -H ROBOT_NAME -s /bin/bash
  # Inside the container:
  rostopic echo /ROBOT_NAME/wheels_driver_node/wheels_cmd
  ```
- Verify `VEHICLE_NAME` is set correctly in the container environment

### Auto Mode SSE not connecting

- Ensure your SSE server sets CORS headers (`Access-Control-Allow-Origin: *`)
- Check the browser console (F12 → Console) for connection errors
- Verify the SSE URL is accessible from the browser's network

### Build fails

- Run `dts update` to ensure you have the latest Duckietown Shell
- Use `-f` flag to force a clean rebuild: `dts devel build -f -H ROBOT_NAME`
- Check that the Duckiebot has sufficient disk space: `ssh duckie@ROBOT_NAME.local "df -h"`

---

## Contributing

### Development workflow

1. **Fork** this repository
2. **Clone** your fork locally
3. **Make changes** — the key files are:
   - `packages/my_package/src/control_node.py` — backend logic
   - `packages/my_package/src/static/index.html` — frontend UI
4. **Build & test** on your Duckiebot:
   ```bash
   dts devel build -f -H ROBOT_NAME
   dts devel run -H ROBOT_NAME
   ```
5. **Open a Pull Request** — PR titles must include a Jira key (`DTSW-XXXX`) as enforced by CI

### Adding dependencies

| Dependency type       | File                      |
| --------------------- | ------------------------- |
| System packages (apt) | `dependencies-apt.txt`    |
| Python packages (pip) | `dependencies-py3.txt`    |
| Duckietown libraries  | `dependencies-py3.dt.txt` |

### Adding new launchers

Create a new `.sh` file in `launchers/` with a valid shebang (`#!/bin/bash`). It will automatically become available as `dt-launcher-<filename>` inside the Docker container.

---

## License

See [LICENSE.pdf](LICENSE.pdf) for the Duckietown license terms.
