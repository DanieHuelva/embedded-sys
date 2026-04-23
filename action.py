"""
action_runner.py
Maps action tag names to robot movement sequences.
Runs actions in a background thread queue so Flask never blocks.
Each action has a hard time cap for safety.
"""

import threading
import time
import queue


class ActionRunner:
    # Hard time caps (seconds)
    HEAD_CAP = 3.0
    ARM_CAP = 4.0
    DANCE_CAP = 6.0

    def __init__(self, robot_control):
        self._robot = robot_control
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._current_action_stop = threading.Event()

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        print("[ActionRunner] Started.")

    def enqueue(self, actions: list[str]):
        """Add a list of actions to the queue."""
        for action in actions:
            self._queue.put(action)

    def interrupt(self):
        """Stop current action and clear queue immediately."""
        self._current_action_stop.set()
        # Drain queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        print("[ActionRunner] Interrupted — queue cleared.")

    def _run(self):
        while True:
            try:
                action = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            self._current_action_stop.clear()
            print(f"[ACTION] Starting: {action}")
            try:
                self._execute(action)
            except Exception as e:
                print(f"[ACTION] Error during {action}: {e}")
            finally:
                # Wheel deadman: always stop wheels after any action
                try:
                    self._robot.stop_wheels()
                except Exception:
                    pass
            print(f"[ACTION] Finished: {action}")

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

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------

    def _timed_sleep(self, seconds: float):
        """Sleep that can be interrupted."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._current_action_stop.is_set():
                return
            time.sleep(0.05)

    def _head_yes(self):
        """Nod yes: tilt down, back to center, tilt up, back to center."""
        start = time.time()
        robot = self._robot

        robot.set_head_tilt(-0.6)       # tilt down
        self._timed_sleep(0.5)
        if time.time() - start > self.HEAD_CAP: return

        robot.set_head_tilt(0.0)        # center
        self._timed_sleep(0.3)
        if time.time() - start > self.HEAD_CAP: return

        robot.set_head_tilt(-0.6)       # tilt down again
        self._timed_sleep(0.5)
        if time.time() - start > self.HEAD_CAP: return

        robot.set_head_tilt(0.0)        # back to center
        self._timed_sleep(0.2)

    def _head_no(self):
        """Shake no: pan left, center, pan right, center."""
        start = time.time()
        robot = self._robot

        robot.set_head_pan(-0.7)        # left
        self._timed_sleep(0.5)
        if time.time() - start > self.HEAD_CAP: return

        robot.set_head_pan(0.0)         # center
        self._timed_sleep(0.2)
        if time.time() - start > self.HEAD_CAP: return

        robot.set_head_pan(0.7)         # right
        self._timed_sleep(0.5)
        if time.time() - start > self.HEAD_CAP: return

        robot.set_head_pan(0.0)         # back to center
        self._timed_sleep(0.2)

    def _arm_raise(self):
        """Raise waist/arm servo to visible pose, hold, return to neutral."""
        start = time.time()
        robot = self._robot

        robot.set_waist(0.8)            # raise
        self._timed_sleep(1.2)
        if time.time() - start > self.ARM_CAP:
            robot.set_waist(0.0)
            return

        self._timed_sleep(0.8)          # hold
        robot.set_waist(0.0)            # return to neutral
        self._timed_sleep(0.3)

    def _dance90(self):
        """
        Rotate left ~90 degrees, then right ~90 degrees, return to start.
        Uses wheel drive to spin in place. Timing tuned for ~90 degrees.
        """
        start = time.time()
        robot = self._robot

        SPIN_TIME = 0.8   # seconds per 90-degree spin (tune for your robot)
        SPIN_SPEED = 0.7  # wheel speed for spinning

        # Spin left: left wheel backward, right wheel forward
        robot.set_wheels(-SPIN_SPEED, SPIN_SPEED)
        self._timed_sleep(SPIN_TIME)
        if time.time() - start > self.DANCE_CAP:
            robot.stop_wheels()
            return

        robot.stop_wheels()
        self._timed_sleep(0.2)

        # Spin right: left wheel forward, right wheel backward
        robot.set_wheels(SPIN_SPEED, -SPIN_SPEED)
        self._timed_sleep(SPIN_TIME)
        if time.time() - start > self.DANCE_CAP:
            robot.stop_wheels()
            return

        robot.stop_wheels()
        self._timed_sleep(0.2)

        # Small correction to return to start heading
        # (net zero — left and right were equal duration)
