import os
import time
import threading
import subprocess
import sys
import argparse
import atexit
import signal

from flask import Flask, jsonify, render_template, request

from robot_control import RobotControl
from dialog_engine import DialogEngine
from action_runner import ActionRunner
from lidar_safety import LidarSafety

app = Flask(__name__)

ROBOT_PORT = os.getenv("MAESTRO_PORT", "/dev/ttyACM0")
ROBOT_BAUD = int(os.getenv("MAESTRO_BAUD", "9600"))
robot = RobotControl(maestro_port=ROBOT_PORT, baud=ROBOT_BAUD)

parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=None)
parser.add_argument(
    "--script",
    type=str,
    default=os.getenv("DIALOG_SCRIPT", "testDialogFileForPractice.txt"),
)
args, _ = parser.parse_known_args()

try:
    engine = DialogEngine(script_path=args.script, seed=args.seed)
except Exception as e:
    print(f"[FATAL] Could not load dialog engine: {e}")
    sys.exit(1)

_last_cmd_time = time.time()
_watchdog_timeout_s = 0.6
_action_active = False
_cleanup_done = False

PHRASES = {
    "p1": "Hello, Hunter.",
    "p2": "Hunter is so cool.",
    "p3": "Please do not touch my wheels.",
    "p4": "Hunter is the greatest.",
}


def cleanup_robot():
    global _cleanup_done, _action_active
    if _cleanup_done:
        return
    _cleanup_done = True

    print("[CLEANUP] Stopping robot and closing Maestro...")

    try:
        lidar_safety.stop()
    except Exception:
        pass

    try:
        _action_active = False
        action_runner.interrupt()
    except Exception:
        pass

    try:
        robot.stop()
    except Exception:
        pass

    try:
        robot.close()
    except Exception:
        pass


def _handle_exit_signal(signum, frame):
    cleanup_robot()
    raise SystemExit(0)


atexit.register(cleanup_robot)
signal.signal(signal.SIGINT, _handle_exit_signal)
signal.signal(signal.SIGTERM, _handle_exit_signal)


def on_action_start():
    global _action_active
    _action_active = True
    engine.begin_action_execution()


def on_actions_idle():
    global _action_active
    _action_active = False
    engine.end_action_execution()


action_runner = ActionRunner(
    robot_control=robot,
    on_action_start=on_action_start,
    on_actions_idle=on_actions_idle,
)

lidar_safety = LidarSafety(
    robot=robot,
    port=os.getenv(
        "LIDAR_PORT",
        "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0",
    ),
    baudrate=115200,
    front_stop_mm=int(os.getenv("LIDAR_FRONT_STOP_MM", "800")),
    rear_stop_mm=int(os.getenv("LIDAR_REAR_STOP_MM", "800")),
    front_half_angle_deg=int(os.getenv("LIDAR_FRONT_HALF_ANGLE_DEG", "30")),
    rear_half_angle_deg=int(os.getenv("LIDAR_REAR_HALF_ANGLE_DEG", "30")),
    min_valid_mm=int(os.getenv("LIDAR_MIN_VALID_MM", "80")),
    hit_hold_seconds=float(os.getenv("LIDAR_HIT_HOLD_SECONDS", "0.25")),
    motor_pwm=int(os.getenv("LIDAR_MOTOR_PWM", "660")),
)
lidar_safety.start()


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def validate_payload(payload: dict) -> dict:
    allowed = [
        "joy_x", "joy_y",
        "left", "right",
        "head_pan", "head_tilt", "waist",
        "r_shoulder", "r_arm_h", "r_arm_j3", "r_arm_j4", "r_hand_twist", "r_hand_grab",
        "l_arm_j1", "l_arm_j2", "l_arm_j3", "l_arm_j4", "l_hand_twist", "l_hand_grab",
    ]
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
    global _last_cmd_time, _action_active
    while True:
        time.sleep(0.1)
        if _action_active:
            continue
        if time.time() - _last_cmd_time > _watchdog_timeout_s:
            try:
                robot.stop_wheels()
            except Exception:
                pass


threading.Thread(target=watchdog_loop, daemon=True).start()


def speak(text: str):
    text = (text or "").strip()
    if not (1 <= len(text) <= 500):
        return

    try:
        time.sleep(0.2)
        safe_text = " " + text
        subprocess.run(
            ["espeak-ng", "-v", "en+m3", "-s", "130", safe_text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
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

    if ("joy_x" in cmd) ^ ("joy_y" in cmd):
        return jsonify({"ok": False, "error": "joy_x and joy_y must be sent together"}), 400

    if ("left" in cmd) ^ ("right" in cmd):
        return jsonify({"ok": False, "error": "left and right must be sent together"}), 400

    if not cmd:
        return jsonify({"ok": False, "error": "No valid fields"}), 400

    try:
        robot.apply_command(cmd)
        _last_cmd_time = time.time()
        return jsonify({
            "ok": True,
            "applied": cmd,
            "lidar": lidar_safety.status(),
            "motion": robot.motion_status(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/stop")
def api_stop():
    global _last_cmd_time, _action_active
    try:
        robot.stop()
    finally:
        action_runner.interrupt()
        engine.reset(clear_variables=False)
        _action_active = False
        _last_cmd_time = time.time()
    return jsonify({"ok": True})


@app.post("/api/say")
def api_say():
    if not request.is_json:
        return jsonify({"ok": False, "error": "Expected JSON"}), 400

    payload = request.get_json(silent=True) or {}
    key = payload.get("key")

    if isinstance(key, str) and key in PHRASES:
        threading.Thread(target=speak, args=(PHRASES[key],), daemon=True).start()
        return jsonify({"ok": True, "spoken": PHRASES[key]})

    return jsonify({"ok": False, "error": "Invalid phrase key"}), 400


@app.post("/api/dialog")
def api_dialog():
    global _last_cmd_time, _action_active

    if not request.is_json:
        return jsonify({"ok": False, "error": "Expected JSON"}), 400

    payload = request.get_json(silent=True) or {}
    user_text = payload.get("text", "").strip()

    if not user_text:
        return jsonify({"ok": False, "error": "Empty input"}), 400

    print(f"[DIALOG] User: {user_text}")

    spoken_text, actions, interrupted = engine.process_input(user_text)

    if interrupted:
        try:
            robot.stop()
        finally:
            action_runner.interrupt()
            engine.reset(clear_variables=False)
            _action_active = False
            _last_cmd_time = time.time()

        state = engine.get_state()
        print(f"[DIALOG] Interrupt handled. State: {state}")
        return jsonify({
            "ok": True,
            "spoken": spoken_text,
            "actions": [],
            "state": state,
            "interrupted": True,
        })

    if spoken_text:
        threading.Thread(target=speak, args=(spoken_text,), daemon=True).start()

    if actions:
        action_runner.enqueue(actions)

    state = engine.get_state()
    print(f"[DIALOG] Response: {spoken_text} | Actions: {actions} | State: {state}")

    return jsonify({
        "ok": True,
        "spoken": spoken_text,
        "actions": actions,
        "state": state,
        "interrupted": False,
    })


@app.get("/api/state")
def api_state():
    return jsonify({
        "ok": True,
        "state": engine.get_state(),
        "variables": engine.variables,
        "lidar": lidar_safety.status(),
        "motion": robot.motion_status(),
    })


if __name__ == "__main__":
    try:
        port = int(os.getenv("PORT", "5000"))
        app.run(host="0.0.0.0", port=port, debug=False)
    finally:
        cleanup_robot()
