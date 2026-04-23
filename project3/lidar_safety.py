import threading
import time
from pyrplidar import PyRPlidar


class LidarSafety:
    """
    Continuous lidar safety monitor.

    Updates front/rear blocked flags based on recent lidar hits.
    Angles assume:
      - front is around 0 degrees
      - rear is around 180 degrees

    Project spec says 359/0/1 should be directly in front and suggests
    front/rear zones with reasonable stop distances. This file follows that pattern.
    """

    def __init__(
        self,
        robot,
        port="/dev/ttyUSB0",
        baudrate=115200,
        front_stop_mm=800,
        rear_stop_mm=800,
        front_half_angle_deg=30,
        rear_half_angle_deg=30,
        min_valid_mm=80,
        hit_hold_seconds=0.25,
        motor_pwm=660,
    ):
        self.robot = robot
        self.port = port
        self.baudrate = baudrate

        self.front_stop_mm = front_stop_mm
        self.rear_stop_mm = rear_stop_mm
        self.front_half_angle_deg = front_half_angle_deg
        self.rear_half_angle_deg = rear_half_angle_deg
        self.min_valid_mm = min_valid_mm
        self.hit_hold_seconds = hit_hold_seconds
        self.motor_pwm = motor_pwm

        self._lidar = None
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._front_blocked = False
        self._rear_blocked = False
        self._last_front_hit_ts = 0.0
        self._last_rear_hit_ts = 0.0
        self._last_front_mm = None
        self._last_rear_mm = None
        self._last_debug_print = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[LIDAR] Safety thread started on {self.port}")

    def stop(self):
        self._stop_event.set()
        self._disconnect()

    def status(self):
        with self._lock:
            return {
                "front_blocked": self._front_blocked,
                "rear_blocked": self._rear_blocked,
                "last_front_mm": self._last_front_mm,
                "last_rear_mm": self._last_rear_mm,
                "port": self.port,
            }

    def _connect(self):
        self._lidar = PyRPlidar()
        self._lidar.connect(port=self.port, baudrate=self.baudrate, timeout=3)
        print("[LIDAR] connected")
        print("[LIDAR] info:", self._lidar.get_info())
        print("[LIDAR] health:", self._lidar.get_health())
        self._lidar.set_motor_pwm(self.motor_pwm)
        time.sleep(1.0)

    def _disconnect(self):
        lidar = self._lidar
        self._lidar = None
        if lidar is None:
            return

        try:
            lidar.stop()
        except Exception:
            pass
        try:
            lidar.set_motor_pwm(0)
        except Exception:
            pass
        try:
            lidar.disconnect()
        except Exception:
            pass

    @staticmethod
    def _angle_in_front(angle_deg, half_angle_deg):
        angle_deg = angle_deg % 360.0
        return angle_deg <= half_angle_deg or angle_deg >= (360.0 - half_angle_deg)

    @staticmethod
    def _angle_in_rear(angle_deg, half_angle_deg):
        angle_deg = angle_deg % 360.0
        return (180.0 - half_angle_deg) <= angle_deg <= (180.0 + half_angle_deg)

    def _update_blocked_flags(self):
        now = time.time()

        with self._lock:
            front_blocked = (now - self._last_front_hit_ts) <= self.hit_hold_seconds
            rear_blocked = (now - self._last_rear_hit_ts) <= self.hit_hold_seconds

            changed = (
                front_blocked != self._front_blocked
                or rear_blocked != self._rear_blocked
            )

            self._front_blocked = front_blocked
            self._rear_blocked = rear_blocked

            front_mm = self._last_front_mm
            rear_mm = self._last_rear_mm

        self.robot.set_obstacle_state(front_blocked, rear_blocked)

        if changed or (time.time() - self._last_debug_print) > 1.0:
            self._last_debug_print = time.time()
            print(
                f"[LIDAR] front_blocked={front_blocked} rear_blocked={rear_blocked} "
                f"front_mm={front_mm} rear_mm={rear_mm}"
            )

    def _handle_measurement(self, angle, distance):
        if distance is None:
            return
        if distance <= 0:
            return
        if distance < self.min_valid_mm:
            return

        now = time.time()

        if self._angle_in_front(angle, self.front_half_angle_deg):
            if distance <= self.front_stop_mm:
                with self._lock:
                    self._last_front_hit_ts = now
                    self._last_front_mm = distance

        if self._angle_in_rear(angle, self.rear_half_angle_deg):
            if distance <= self.rear_stop_mm:
                with self._lock:
                    self._last_rear_hit_ts = now
                    self._last_rear_mm = distance

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._connect()

                # In pyrplidar, start_scan() returns a generator function,
                # so you must call it again to get the iterator.
                scan_iter = self._lidar.start_scan()()

                for measurement in scan_iter:
                    if self._stop_event.is_set():
                        break

                    angle = getattr(measurement, "angle", None)
                    distance = getattr(measurement, "distance", None)

                    if angle is None or distance is None:
                        continue

                    self._handle_measurement(angle, distance)
                    self._update_blocked_flags()

            except Exception as e:
                print("[LIDAR ERROR]", e)
                self.robot.set_obstacle_state(True, True)
                self.robot.stop_wheels()
                time.sleep(1.0)
            finally:
                self._disconnect()
