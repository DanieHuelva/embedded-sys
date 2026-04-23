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
    # --- CHANNEL MAP (Mini Maestro 18-Channel) ---
    CH_LEFT_WHEEL   = 0
    CH_RIGHT_WHEEL  = 1
    CH_WAIST        = 2
    CH_HEAD_TILT    = 3
    CH_HEAD_PAN     = 4

    CH_R_SHOULDER_V = 5
    CH_R_ARM_H      = 6
    CH_R_ARM_J3     = 7
    CH_R_ARM_J4     = 8
    CH_R_HAND_TWIST = 9
    CH_R_HAND_GRAB  = 10

    CH_L_ARM_J1     = 11
    CH_L_ARM_J2     = 12
    CH_L_ARM_J3     = 13
    CH_L_ARM_J4     = 14
    CH_L_HAND_TWIST = 15
    CH_L_HAND_GRAB  = 16

    # --- PULSE WIDTHS ---
    SERVO_CENTER = 1500
    WHEEL_MIN    = 1200
    WHEEL_MAX    = 1800

    HEAD_PAN_MIN  = 1200
    HEAD_PAN_MAX  = 1800
    HEAD_TILT_MIN = 1200
    HEAD_TILT_MAX = 1800

    WAIST_MIN     = 1200
    WAIST_MAX     = 1800
    WAIST_TRIM_US = 100

    ARM_MIN = 1200
    ARM_MAX = 1800

    def __init__(self, maestro_port="/dev/ttyACM0", baud=9600):
        self._lock = threading.Lock()
        self._front_blocked = False
        self._rear_blocked = False
        self.maestro = Maestro(maestro_port, baud)
        self.stop()

    def close(self):
        self.stop()
        self.maestro.close()

    def set_obstacle_state(self, front_blocked: bool, rear_blocked: bool):
        with self._lock:
            self._front_blocked = bool(front_blocked)
            self._rear_blocked = bool(rear_blocked)

    def motion_status(self):
        with self._lock:
            return {
                "front_blocked": self._front_blocked,
                "rear_blocked": self._rear_blocked,
            }

    @staticmethod
    def _map_norm_to_us(x: float, us_min: int, us_max: int) -> int:
        x = _clamp(x, -1.0, 1.0)
        t = (x + 1.0) / 2.0
        return int(us_min + t * (us_max - us_min))

    def _motion_allowed_for_wheels(self, left_cmd: float, right_cmd: float) -> bool:
        avg = (left_cmd + right_cmd) / 2.0
        deadband = 0.15

        with self._lock:
            front_blocked = self._front_blocked
            rear_blocked = self._rear_blocked

        # Forward
        if avg > deadband and front_blocked:
            return False

        # Backward
        if avg < -deadband and rear_blocked:
            return False

        # Turning in place / mostly steering is allowed
        return True

    # ------------------------------------------------------------------
    # Wheels — separate left/right channels
    # ------------------------------------------------------------------
    def set_wheels(self, left_cmd: float, right_cmd: float):
        left_cmd = _clamp(left_cmd, -1.0, 1.0)
        right_cmd = _clamp(right_cmd, -1.0, 1.0)

        if not self._motion_allowed_for_wheels(left_cmd, right_cmd):
            self.stop_wheels()
            return False

        # Hardware fix: left wheel is mirrored
        left_hw_cmd = -left_cmd

        left_us = self._map_norm_to_us(left_hw_cmd, self.WHEEL_MIN, self.WHEEL_MAX)
        right_us = self._map_norm_to_us(right_cmd, self.WHEEL_MIN, self.WHEEL_MAX)

        with self._lock:
            self.maestro.set_target_us(self.CH_LEFT_WHEEL, left_us)
            self.maestro.set_target_us(self.CH_RIGHT_WHEEL, right_us)

        return True

    def drive_joystick(self, x: float, y: float):
        """
        x = throttle (forward/back), y = turn (left/right)
        Both in [-1, 1].
        """
        x = _clamp(x, -1.0, 1.0)
        y = _clamp(y, -1.0, 1.0)

        import math
        angle = math.radians(-45)
        x_rot = x * math.cos(angle) - y * math.sin(angle)
        y_rot = x * math.sin(angle) + y * math.cos(angle)

        left = y_rot + x_rot
        right = y_rot - x_rot

        m = max(1.0, abs(left), abs(right))
        left /= m
        right /= m

        return self.set_wheels(left, right)

    def stop_wheels(self):
        with self._lock:
            self.maestro.set_target_us(self.CH_LEFT_WHEEL, self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_RIGHT_WHEEL, self.SERVO_CENTER)

    def stop(self):
        """Full stop — reset wheels/head/waist and put arms in a safe neutral pose."""
        with self._lock:
            self.maestro.set_target_us(self.CH_LEFT_WHEEL,  self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_RIGHT_WHEEL, self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_HEAD_TILT,   self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_HEAD_PAN,    self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_WAIST,       self.SERVO_CENTER)

            self.maestro.set_target_us(self.CH_R_SHOULDER_V,  self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_R_ARM_H,       self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_R_ARM_J3,      self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_R_ARM_J4,      self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_R_HAND_TWIST,  self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_R_HAND_GRAB,   self.SERVO_CENTER)

            self.maestro.set_target_us(self.CH_L_ARM_J1,      self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_L_ARM_J2,      self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_L_ARM_J3,      self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_L_ARM_J4,      self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_L_HAND_TWIST,  self.SERVO_CENTER)
            self.maestro.set_target_us(self.CH_L_HAND_GRAB,   self.SERVO_CENTER)

    # ------------------------------------------------------------------
    # Head & Waist
    # ------------------------------------------------------------------
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
        us += self.WAIST_TRIM_US
        with self._lock:
            self.maestro.set_target_us(self.CH_WAIST, us)

    # ------------------------------------------------------------------
    # Arms — right side
    # ------------------------------------------------------------------
    def set_right_shoulder(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_R_SHOULDER_V, us)

    def set_right_arm_h(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_R_ARM_H, us)

    def set_right_arm_j3(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_R_ARM_J3, us)

    def set_right_arm_j4(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_R_ARM_J4, us)

    def set_right_hand_twist(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_R_HAND_TWIST, us)

    def set_right_hand_grab(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_R_HAND_GRAB, us)

    # ------------------------------------------------------------------
    # Arms — left side
    # ------------------------------------------------------------------
    def set_left_arm_j1(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_L_ARM_J1, us)

    def set_left_arm_j2(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_L_ARM_J2, us)

    def set_left_arm_j3(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_L_ARM_J3, us)

    def set_left_arm_j4(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_L_ARM_J4, us)

    def set_left_hand_twist(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_L_HAND_TWIST, us)

    def set_left_hand_grab(self, v: float):
        us = self._map_norm_to_us(float(v), self.ARM_MIN, self.ARM_MAX)
        with self._lock:
            self.maestro.set_target_us(self.CH_L_HAND_GRAB, us)

    # ------------------------------------------------------------------
    # apply_command
    # ------------------------------------------------------------------
    def apply_command(self, cmd: dict):
        if "joy_x" in cmd and "joy_y" in cmd:
            self.drive_joystick(float(cmd["joy_x"]), float(cmd["joy_y"]))
        elif "left" in cmd and "right" in cmd:
            self.set_wheels(float(cmd["left"]), float(cmd["right"]))

        if "head_pan"     in cmd: self.set_head_pan(float(cmd["head_pan"]))
        if "head_tilt"    in cmd: self.set_head_tilt(float(cmd["head_tilt"]))
        if "waist"        in cmd: self.set_waist(float(cmd["waist"]))

        if "r_shoulder"   in cmd: self.set_right_shoulder(float(cmd["r_shoulder"]))
        if "r_arm_h"      in cmd: self.set_right_arm_h(float(cmd["r_arm_h"]))
        if "r_arm_j3"     in cmd: self.set_right_arm_j3(float(cmd["r_arm_j3"]))
        if "r_arm_j4"     in cmd: self.set_right_arm_j4(float(cmd["r_arm_j4"]))
        if "r_hand_twist" in cmd: self.set_right_hand_twist(float(cmd["r_hand_twist"]))
        if "r_hand_grab"  in cmd: self.set_right_hand_grab(float(cmd["r_hand_grab"]))

        if "l_arm_j1"     in cmd: self.set_left_arm_j1(float(cmd["l_arm_j1"]))
        if "l_arm_j2"     in cmd: self.set_left_arm_j2(float(cmd["l_arm_j2"]))
        if "l_arm_j3"     in cmd: self.set_left_arm_j3(float(cmd["l_arm_j3"]))
        if "l_arm_j4"     in cmd: self.set_left_arm_j4(float(cmd["l_arm_j4"]))
        if "l_hand_twist" in cmd: self.set_left_hand_twist(float(cmd["l_hand_twist"]))
        if "l_hand_grab"  in cmd: self.set_left_hand_grab(float(cmd["l_hand_grab"]))
