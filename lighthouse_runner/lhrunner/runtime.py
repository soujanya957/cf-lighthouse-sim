"""State shared by the runner and the ROS shims (shims/rclpy, shims/crazyflie_py).

The exported project talks to ROS; the shims answer with what is here: the drones to fly, the clock, and whether
the run is shutting down. One run per process.
"""

import threading
import time


class Clock:
    """Wall clock, sped up by `speed` for dry runs. Every time the exported code sees comes from here."""

    def __init__(self, speed=1.0):
        self.speed = speed
        self._real0 = time.time()

    def now(self):
        return self._real0 + (time.time() - self._real0) * self.speed

    def real_delay(self, seconds):
        """How long to really wait for `seconds` to pass on this clock"""
        return seconds / self.speed


clock = Clock()
drones = []                 # ordered; each has .id (int) and the crazyflie_py Crazyflie methods the blocks call
shutdown_event = threading.Event()
shutdown_reason = None      # "finished", "interrupted", "error" or "fault: ..."
_shutdown_hooks = []
_lock = threading.Lock()


def setup(fleet, speed=1.0):
    global clock
    clock = Clock(speed)
    drones[:] = fleet


def on_shutdown(hook):
    """hook(reason) runs once, in the thread that requested the shutdown"""
    _shutdown_hooks.append(hook)


def request_shutdown(reason):
    """Stops the run. The first reason sticks: a Ctrl-C after the show finished is still 'finished'."""
    global shutdown_reason
    with _lock:
        if shutdown_event.is_set():
            return
        shutdown_reason = reason
        shutdown_event.set()
    for hook in _shutdown_hooks:
        hook(reason)


def stopping():
    return shutdown_event.is_set()
