"""Stand-in for Crazyswarm2's crazyflie_py: Crazyswarm() hands out the runner's drones (cflib or simulated)."""

import time

from lhrunner import runtime
from rclpy.node import Node


class TimeHelper:
    def __init__(self, node=None):
        self.rateHz = None
        self.nextTime = None

    def time(self):
        return runtime.clock.now()

    def sleep(self, duration):
        end = self.time() + duration
        while self.time() < end:
            time.sleep(0.001)

    def sleepForRate(self, rateHz):
        if self.nextTime is None or self.rateHz != rateHz:
            self.rateHz = rateHz
            self.nextTime = self.time() + 1.0 / rateHz
        while self.time() < self.nextTime:
            time.sleep(0.001)
        self.nextTime += 1.0 / rateHz

    def isShutdown(self):
        return runtime.stopping()


class CrazyflieServer(Node):
    """swarm.allcfs: every drone, plus the commands Crazyswarm2 sends to all of them at once"""

    def __init__(self, drones):
        super().__init__("crazyflie_server")
        self.crazyflies = list(drones)
        self.crazyfliesById = {cf.id: cf for cf in self.crazyflies}
        self.crazyfliesByName = {cf.prefix: cf for cf in self.crazyflies}

    def takeoff(self, targetHeight, duration, groupMask=0):
        for cf in self.crazyflies:
            cf.takeoff(targetHeight, duration)

    def land(self, targetHeight, duration, groupMask=0):
        for cf in self.crazyflies:
            cf.land(targetHeight, duration)

    def goTo(self, goal, yaw, duration, groupMask=0):
        # Crazyswarm2's broadcast goTo is relative
        for cf in self.crazyflies:
            cf.goTo(goal, yaw, duration, relative=True)

    def emergency(self):
        for cf in self.crazyflies:
            cf.emergency()

    def setParam(self, name, value):
        for cf in self.crazyflies:
            cf.setParam(name, value)


class Crazyswarm:
    def __init__(self, *args, **kwargs):
        self.allcfs = CrazyflieServer(runtime.drones)
        self.timeHelper = TimeHelper(self.allcfs)
        self.input = None
