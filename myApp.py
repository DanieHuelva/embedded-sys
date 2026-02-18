import os
import time
import threading
import subprocess

from flask import Flask, jsonify, render_template, request
from robot_control import RobotControl

app = Flask(__name__)

ROBOT_PORT = os.getenv("MAESTRO_PORT", "/dev/ttyACM0")
ROBOT_BAUD = int(os.getenv("MAESTRO_BAUD", "9600"))
robot = RobotControl(maestro_port=ROBOT_PORT, baud=ROBOT_BAUD)

_last_cmd_time = time.time()
_watchdog_timeout_s = 0.6

PHRASES = {
    "p1": "Hello, Hunter.",
    "p2": "Hunter is so cool.",
    "p3": "Please do not touch my wheels.",
    "p4": "Hunter is the greatest.",
}

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def validate_payload(payload: dict) -> dict:
    """
    Accept:
      joy_x, joy_y in [-1,1]
      OR left, right in [-1,1]
      head_pan, head_tilt, waist in [-1,1]
    """
    allowed = ["joy_x", "joy_y", "left", "right", "head_pan", "head_tilt", "waist"]
    clean = {}
    for k in allowed:
        if k in payload:
            try:
                v = float(payload[k])
            except (TypeError, ValueError):
                continue
            clean[k] = clamp(v, -1.0, 1.0)
    return clean


def watchdog_loop():
    global _last_cmd_time
    while True:
        time.sleep(0.1)
        if time.time() - _last_cmd_time > _watchdog_timeout_s:
            try:
                robot.stop_wheels()  # ← changed from stop()
            except Exception:
                pass


threading.Thread(target=watchdog_loop, daemon=True).start()

def speak(text: str):
    """
    Speak on the Pi using espeak-ng (works with your routing).
    """
    text = (text or "").strip()
    if not (1 <= len(text) <= 160):
        return
    try:
        subprocess.Popen(
            ["espeak-ng", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/api/control")
def api_control():
    global _last_cmd_time
    if not request.is_json:
        return jsonify({"ok": False, "error": "Expected JSON"}), 400

    payload = request.get_json(silent=True) or {}
    cmd = validate_payload(payload)

    # Require joystick pair together if using joystick
    if ("joy_x" in cmd) ^ ("joy_y" in cmd):
        return jsonify({"ok": False, "error": "joy_x and joy_y must be sent together"}), 400

    # Require left/right together if using explicit wheels
    if ("left" in cmd) ^ ("right" in cmd):
        return jsonify({"ok": False, "error": "left and right must be sent together"}), 400

    if not cmd:
        return jsonify({"ok": False, "error": "No valid fields"}), 400

    try:
        robot.apply_command(cmd)
        _last_cmd_time = time.time()
        return jsonify({"ok": True, "applied": cmd})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/stop")
def api_stop():
    global _last_cmd_time
    robot.stop()
    _last_cmd_time = time.time()
    return jsonify({"ok": True})

@app.post("/api/say")
def api_say():
    if not request.is_json:
        return jsonify({"ok": False, "error": "Expected JSON"}), 400
    payload = request.get_json(silent=True) or {}
    key = payload.get("key")
    if isinstance(key, str) and key in PHRASES:
        speak(PHRASES[key])
        return jsonify({"ok": True, "spoken": PHRASES[key]})
    return jsonify({"ok": False, "error": "Invalid phrase key"}), 400

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
