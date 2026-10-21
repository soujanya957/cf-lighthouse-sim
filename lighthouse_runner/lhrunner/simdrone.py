"""Simulated Crazyflie for dry runs: follows the commands the way the firmware's high-level commander plans them."""

import threading

import numpy as np

from . import runtime
from .led import led_rgb


def _smooth(s):
    """Degree 7 profile with zero velocity, acceleration and jerk at both ends, like the firmware's goTo"""
    s = min(max(s, 0.0), 1.0)
    return 35 * s**4 - 84 * s**5 + 70 * s**6 - 20 * s**7


class SimCF:
    def __init__(self, cf_id, start, fence):
        self.id = cf_id
        self.prefix = f"cf{cf_id}"
        self.fence = fence
        self.start = np.array(start, dtype=float)
        self._from = self.start.copy()
        self._to = self.start.copy()
        self._t0 = 0.0
        self._duration = 0.0
        self.led = None
        self.events = []        # (time, kind, detail)
        self.violations = []    # (time, command, target)
        self._lock = threading.Lock()

    def _now(self):
        return runtime.clock.now()

    def _record(self, kind, detail=""):
        self.events.append((self._now(), kind, detail))

    def _move(self, kind, target, duration):
        target = np.asarray(target, dtype=float)
        if not self.fence.contains(target):
            self.violations.append((self._now(), kind, target.round(2).tolist()))
        with self._lock:
            now = self._now()
            self._from = self._position(now)
            self._to = target
            self._t0 = now
            self._duration = max(float(duration), 0.0)
        self._record(kind, target.round(2).tolist())

    def _position(self, now):
        if self._duration <= 0:
            return self._to.copy()
        return self._from + (self._to - self._from) * _smooth((now - self._t0) / self._duration)

    def position(self):
        with self._lock:
            return self._position(self._now())

    getPosition = position

    def takeoff(self, targetHeight, duration, groupMask=0):
        p = self.position()
        self._move("takeoff", [p[0], p[1], targetHeight], duration)

    def land(self, targetHeight, duration, groupMask=0):
        p = self.position()
        self._move("land", [p[0], p[1], targetHeight], duration)

    def goTo(self, goal, yaw, duration, relative=False, groupMask=0):
        goal = np.asarray(goal, dtype=float)
        with self._lock:
            # Relative to where the planner is headed, like the firmware
            base = self._to.copy()
        self._move("goTo", base + goal if relative else goal, duration)

    def cmdPosition(self, pos, yaw=0.0):
        pos = np.asarray(pos, dtype=float)
        if not self.fence.contains(pos):
            self.violations.append((self._now(), "cmdPosition", pos.round(2).tolist()))
        with self._lock:
            self._from = self._to = pos.copy()
            self._duration = 0.0

    def notifySetpointsStop(self, remainValidMillisecs=100, groupMask=0):
        pass

    def setLEDColor(self, r, g, b):
        self.led = led_rgb(r, g, b)
        self._record("led", self.led)

    def setParam(self, name, value):
        self._record("param", f"{name}={value}")

    def emergency(self):
        self._record("emergency")

    def uploadTrajectory(self, *args, **kwargs):
        raise NotImplementedError("uploaded trajectories aren't supported by the Lighthouse runner yet")

    startTrajectory = uploadTrajectory
