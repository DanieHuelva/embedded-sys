import time
import threading
import serial


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class Maestro:
    def __init__(self, port: str = "/dev/ttyACM0", baud: int = 9600):
        self._ser = serial.Serial(port, baud, timeout=1)
        time.sleep(0.2)

    @staticmethod
    def _us_to_qus(us: int) -> int:
        return int(us) * 4

    def set_target_us(self, channel: int, us: int) -> None:
        target = self._us_to_qus(us)
        lsb = target & 0x7F
        msb = (target >> 7) & 0x7F
        self._ser.write(bytes([0x84, channel, lsb, msb]))

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


class RobotControl:
    # --- CHANNEL MAP ---
    CH_LEFT_WHEEL = 0
    CH_RIGHT_WHEEL = 1

    CH_WAIST = 2  # ✅ waist channel from your mapping

    CH_HEAD_TILT = 3
    CH_HEAD_PAN = 4

    # --- PULSE WIDTHS ---
    SERVO_CENTER = 1500
    WHEEL_MIN = 1200
    WHEEL_MAX = 1800

    HEAD_PAN_MIN = 1200
    HEAD_PAN_MAX = 1800
    HEAD_TILT_MIN = 1200
    HEAD_TILT_MAX = 1800

    WAIST_MIN = 1200
    WAIST_MAX = 1800

    def __init__(self, maestro_port="/dev/ttyACM0", baud=9600):
        self._lock = threading.Lock()
        self.maestro = Maestro(maestro_port, baud)
        self.stop()

    def close(self):
        with self._lock:
            self.stop()
            self.maestro.close()

    @staticmethod
    def _map_norm_to_us(x: float, us_min: int, us_max: int) -> int:
        x = _clamp(x, -1.0, 1.0)
        t = (x + 1.0) / 2.0
        return int(us_min + t * (us_max - us_min))

    def set_wheels(self, left_cmd: float, right_cmd: float):
        left_cmd = _clamp(left_cmd, -1.0, 1.0)
        right_cmd = _clamp(right_cmd, -1.0, 1.0)

        # Hardware fix: left wheel is mirrored (invert only left)
        left_cmd = -left_cmd

        left_us = self._map_norm_to_us(left_cmd, self.WHEEL_MIN, self.WHEEL_MAX)
        right_us = self._map_norm_to_us(right_cmd, self.WHEEL_MIN, self.WHEEL_MAX)

        with self._lock:
            self.maestro.set_target_us(self.CH_LEFT_WHEEL, left_us)
            self.maestro.set_target_us(self.CH_RIGHT_WHEEL, right_us)

    def drive_joystick(self, x: float, y: float):
        """
        Joystick inputs x, y in [-1, 1].
        y = forward/back (+1 = forward, -1 = backward)
        x = turn left/right (+1 = right, -1 = left)

        If forward/back is reversed, negate y: use (-y + x) and (-y - x).
        If left/right turning is reversed, negate x.
        """
        x = _clamp(x, -1.0, 1.0)
        y = _clamp(y, -1.0, 1.0)

        # Standard arcade drive
        import math
        angle = math.radians(-45)
        x_rot = x * math.cos(angle) - y * math.sin(angle)
        y_rot = x * math.sin(angle) + y * math.cos(angle)

        # Arcade drive
        left = y_rot + x_rot
        right = y_rot - x_rot

        m = max(1.0, abs(left), abs(right))
        left /= m
        right /= m

        self.set_wheels(left, right)

    def stop_wheels(self):
        with self._lock:
            self.maestro.set_target_us(self.CH_LEFT_WHEEL, self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_RIGHT_WHEEL, self.SERVO_CENTER)

    def stop(self):
        with self._lock:
            self.maestro.set_target_us(self.CH_LEFT_WHEEL, self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_RIGHT_WHEEL, self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_HEAD_TILT, self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_HEAD_PAN, self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_WAIST, self.SERVO_CENTER)  # ✅ center waist on stop

    def set_head_pan(self, v: float):
        us = self._map_norm_to_us(float(v), self.HEAD_PAN_MIN, self.HEAD_PAN_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_HEAD_PAN, us)

    def set_head_tilt(self, v: float):
        us = self._map_norm_to_us(float(v), self.HEAD_TILT_MIN, self.HEAD_TILT_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_HEAD_TILT, us)

    def set_waist(self, v: float):
        us = self._map_norm_to_us(float(v), self.WAIST_MIN, self.WAIST_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_WAIST, us)

    def apply_command(self, cmd: dict):
        if "joy_x" in cmd and "joy_y" in cmd:
            self.drive_joystick(float(cmd["joy_x"]), float(cmd["joy_y"]))
        elif "left" in cmd and "right" in cmd:
            self.set_wheels(float(cmd["left"]), float(cmd["right"]))

        if "head_pan" in cmd:
            self.set_head_pan(float(cmd["head_pan"]))
        if "head_tilt" in cmd:
            self.set_head_tilt(float(cmd["head_tilt"]))
        if "waist" in cmd:
            self.set_waist(float(cmd["waist"]))
