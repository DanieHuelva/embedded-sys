"""
action_runner.py
Executes robot action tags in a background queue.
Never blocks the Flask thread.
All actions have hard time caps for safety.
Wheel deadman stop runs after every action.
"""

import threading
import time
import queue


class ActionRunner:
    HEAD_CAP = 3.0
    ARM_CAP = 4.0
    DANCE_CAP = 8.0

    def __init__(self, robot_control, on_action_start=None, on_actions_idle=None):
        self._robot = robot_control
        self._queue = queue.Queue()
        self._current_action_stop = threading.Event()
        self._on_action_start = on_action_start
        self._on_actions_idle = on_actions_idle

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        print("[ActionRunner] Started.")

    def enqueue(self, actions: list):
        for action in actions:
            self._queue.put(action)

    def interrupt(self):
        """Stop current action and drain the queue immediately."""
        self._current_action_stop.set()

        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break

        try:
            self._robot.stop_wheels()
        except Exception:
            pass

        if self._on_actions_idle:
            try:
                self._on_actions_idle()
            except Exception:
                pass

        print("[ActionRunner] Interrupted — queue cleared.")

    def _run(self):
        while True:
            try:
                action = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            self._current_action_stop.clear()

            if self._on_action_start:
                try:
                    self._on_action_start()
                except Exception:
                    pass

            print(f"[ACTION] Started: {action}")
            try:
                self._execute(action)
            except Exception as e:
                print(f"[ACTION] Error during {action}: {e}")
            finally:
                try:
                    self._robot.stop_wheels()
                except Exception:
                    pass

                self._queue.task_done()

                if self._queue.empty() and self._on_actions_idle:
                    try:
                        self._on_actions_idle()
                    except Exception:
                        pass

            print(f"[ACTION] Finished: {action}")

    def _stopped(self) -> bool:
        return self._current_action_stop.is_set()

    def _sleep(self, seconds: float) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._stopped():
                return False
            time.sleep(0.05)
        return True

    def _execute(self, action: str):
        if action == "head_yes":
            self._head_yes()
        elif action == "head_no":
            self._head_no()
        elif action == "arm_raise":
            self._arm_raise()
        elif action == "dance90":
            self._dance90()
        else:
            print(f"[ACTION] Unknown action skipped: {action}")

    def _head_yes(self):
        start = time.time()
        r = self._robot

        r.set_head_tilt(-0.6)
        if not self._sleep(0.5):
            r.set_head_tilt(0.0)
            return
        if time.time() - start > self.HEAD_CAP:
            r.set_head_tilt(0.0)
            return

        r.set_head_tilt(0.0)
        if not self._sleep(0.25):
            r.set_head_tilt(0.0)
            return
        if time.time() - start > self.HEAD_CAP:
            return

        r.set_head_tilt(-0.6)
        if not self._sleep(0.5):
            r.set_head_tilt(0.0)
            return
        if time.time() - start > self.HEAD_CAP:
            r.set_head_tilt(0.0)
            return

        r.set_head_tilt(0.0)
        self._sleep(0.2)

    def _head_no(self):
        start = time.time()
        r = self._robot

        r.set_head_pan(-0.7)
        if not self._sleep(0.5):
            r.set_head_pan(0.0)
            return
        if time.time() - start > self.HEAD_CAP:
            r.set_head_pan(0.0)
            return

        r.set_head_pan(0.0)
        if not self._sleep(0.2):
            r.set_head_pan(0.0)
            return
        if time.time() - start > self.HEAD_CAP:
            return

        r.set_head_pan(0.7)
        if not self._sleep(0.5):
            r.set_head_pan(0.0)
            return
        if time.time() - start > self.HEAD_CAP:
            r.set_head_pan(0.0)
            return

        r.set_head_pan(0.0)
        self._sleep(0.2)

    def _arm_raise(self):
        start = time.time()
        r = self._robot

        r.set_right_shoulder(0.8)
        r.set_right_arm_h(0.5)

        if not self._sleep(1.0):
            r.set_right_shoulder(0.0)
            r.set_right_arm_h(0.0)
            return
        if time.time() - start > self.ARM_CAP:
            r.set_right_shoulder(0.0)
            r.set_right_arm_h(0.0)
            return

        if not self._sleep(1.0):
            r.set_right_shoulder(0.0)
            r.set_right_arm_h(0.0)
            return
        if time.time() - start > self.ARM_CAP:
            r.set_right_shoulder(0.0)
            r.set_right_arm_h(0.0)
            return

        r.set_right_shoulder(0.0)
        r.set_right_arm_h(0.0)
        self._sleep(0.3)

    def _dance90(self):
        start = time.time()
        r = self._robot

        SPIN_TIME = 1.4
        SPIN_SPEED = 1.0

        # Spin left
        r.set_wheels(-SPIN_SPEED, SPIN_SPEED)
        if not self._sleep(SPIN_TIME):
            r.stop_wheels()
            return
        if time.time() - start > self.DANCE_CAP:
            r.stop_wheels()
            return

        r.stop_wheels()
        if not self._sleep(0.25):
            return

        # Spin right
        r.set_wheels(SPIN_SPEED, -SPIN_SPEED)
        if not self._sleep(SPIN_TIME):
            r.stop_wheels()
            return
        if time.time() - start > self.DANCE_CAP:
            r.stop_wheels()
            return

        r.stop_wheels()
        self._sleep(0.25)
