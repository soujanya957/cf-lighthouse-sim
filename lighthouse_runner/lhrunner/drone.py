"""A Lighthouse-tracked Crazyflie flown through cflib, with the crazyflie_py Crazyflie methods the exported blocks call.

Position comes from the drone's own estimator (stateEstimate.x/y/z), so there is no motion-capture node. Targets are
clamped to the fence, and while flying the drone is watched for leaving the fence, losing tracking and low battery.
"""

import math
import threading
import time

import numpy as np
from cflib.crazyflie import Crazyflie
from cflib.crazyflie.log import LogConfig
from cflib.crazyflie.mem import LighthouseMemHelper
from cflib.crazyflie.syncCrazyflie import SyncCrazyflie

from . import runtime
from .led import led_rgb

SUPERVISOR_BITS = ["canArm", "armed", "autoArm", "canFly", "flying", "tumbled", "locked", "crashed"]
PM_LOW_POWER = 3
LH_ESTIMATING = 2
LAND_SPEED = 0.3          # m/s for the runner's own landings
OUTSIDE_GRACE_S = 0.25    # a Lighthouse glitch shorter than this doesn't count as leaving the fence


# Logged values written next to each pose in flight.csv, to tell a tracking problem from a flying one afterwards.
# bsActive and bsReceive are bit masks of base stations (bit 0 = BS 1): used by the estimate, and heard at all.
TRACK_SLOW = ("lighthouse.status", "lighthouse.bsActive", "lighthouse.bsReceive", "pm.vbat")


class LighthouseCF:
    SLOW_MS = 250

    def __init__(self, cf_id, uri, config, rw_cache, on_fault):
        self.id = cf_id
        self.uri = uri
        self.prefix = f"cf{cf_id}"
        self.config = config
        self.fence = config.fence
        self.scf = SyncCrazyflie(uri, cf=Crazyflie(rw_cache=rw_cache))
        self.cf = self.scf.cf
        self._on_fault = on_fault

        self.pose = None          # last estimated position
        self.attitude = None      # last roll, pitch, yaw (degrees)
        self.link_quality = None  # % of packets acknowledged
        self.var = None           # last kalman position variances
        self.slow = {}            # last lighthouse/battery/supervisor log
        self.decks = []
        self.track = []           # (time, x, y, z, roll, pitch, and TRACK_SLOW from the last slow log) while flying

        self.flying = False       # sent a flight command
        self.grounded = False     # landing or stopped: flight commands are ignored from now on
        self.killed = False
        self._streaming = False   # sent cmdPosition since the last notifySetpointsStop
        self.land_duration = 0.0
        self._target = None       # where the last command sends it, for relative goTos
        self._ring_on = False
        self._warned = set()
        self._outside_since = None
        self._untracked_since = None
        self._configs = []
        self._closing = False

    def say(self, msg):
        print(f"[{self.prefix}] {msg}", flush=True)

    def _warn_once(self, key, msg):
        if key not in self._warned:
            self._warned.add(key)
            self.say(msg)

    ###
    #  Link
    ###

    def connect(self):
        self.scf.open_link()
        self.cf.connection_lost.add_callback(self._on_lost)
        try:
            self.cf.link_statistics.link_quality_updated.add_callback(self._on_link_quality)
        except AttributeError:
            pass
        self._start_logs()
        self.decks = self._read_decks()

    def close(self):
        self._closing = True
        for conf in self._configs:
            try:
                conf.delete()
            except Exception:
                pass
        if self.scf.is_link_open():
            self.scf.close_link()

    def _on_link_quality(self, value):
        self.link_quality = float(value)

    def _on_lost(self, uri, msg):
        if self._closing:
            return
        self.grounded = True
        self._fault(f"radio link lost ({msg})")

    def _start_logs(self):
        toc = self.cf.log.toc

        def has(name):
            return toc.get_element_by_complete_name(name) is not None

        pos = LogConfig(name="lh_pos", period_in_ms=self.config.pose_period_ms)
        # 6 floats: 24 of the 26 bytes a log packet holds
        for v in ("x", "y", "z", "roll", "pitch", "yaw"):
            pos.add_variable(f"stateEstimate.{v}", "float")
        pos.data_received_cb.add_callback(self._on_pos)

        slow = LogConfig(name="lh_slow", period_in_ms=self.SLOW_MS)
        for name in ("lighthouse.status", "lighthouse.bsReceive", "lighthouse.bsActive", "lighthouse.bsGeoVal",
                     "lighthouse.bsCalVal", "pm.vbat", "pm.state", "supervisor.info"):
            if has(name):
                slow.add_variable(name)
        slow.data_received_cb.add_callback(self._on_slow)

        var = LogConfig(name="lh_var", period_in_ms=self.SLOW_MS)
        for v in "XYZ":
            if has(f"kalman.varP{v}"):
                var.add_variable(f"kalman.varP{v}", "float")
        var.data_received_cb.add_callback(self._on_var)

        for conf in (pos, slow, var):
            # Variables added without a type wait in default_fetch_as until add_config resolves them
            if not conf.variables and not conf.default_fetch_as:
                continue
            self.cf.log.add_config(conf)
            conf.start()
            self._configs.append(conf)

    def _read_decks(self):
        decks = []
        for name in self.cf.param.toc.toc.get("deck", {}):
            try:
                if self.cf.param.get_value(f"deck.{name}") == "1":
                    decks.append(name.removeprefix("bc"))
            except Exception:
                pass
        return sorted(decks)

    def setup(self):
        """Kalman estimator (Lighthouse needs it), high-level commander on, then the fleet file's params"""
        self.cf.param.set_value("stabilizer.estimator", "2")
        self.cf.param.set_value("commander.enHighLevel", "1")
        for name, value in self.config.params.items():
            self.cf.param.set_value(name, str(value))

    def reset_estimator(self):
        self.cf.param.set_value("kalman.resetEstimation", "1")
        time.sleep(0.1)
        self.cf.param.set_value("kalman.resetEstimation", "0")

    def read_geometry(self, timeout=8.0):
        """Base-station poses stored on the drone, {bs: {"origin": [x, y, z], "rot": 3x3}}; None if they couldn't be
        read"""
        done = threading.Event()
        result = {}

        def read(geos):
            result.update({bs: {"origin": list(g.origin), "rot": [list(r) for r in g.rotation_matrix]}
                           for bs, g in geos.items() if g is not None and g.valid})
            done.set()

        LighthouseMemHelper(self.cf).read_all_geos(read)
        return result if done.wait(timeout) else None

    def arm(self):
        self.cf.supervisor.send_arming_request(True)

    def supervisor_flags(self):
        info = self.slow.get("supervisor.info")
        return [] if info is None else [n for i, n in enumerate(SUPERVISOR_BITS) if info >> i & 1]

    ###
    #  Telemetry and in-flight checks
    ###

    def _on_pos(self, timestamp, data, conf):
        p = np.array([data[f"stateEstimate.{v}"] for v in "xyz"], dtype=float)
        self.pose = p
        self.attitude = [data[f"stateEstimate.{v}"] for v in ("roll", "pitch", "yaw")]
        if not (self.flying and not self._closing):
            return
        now = runtime.clock.now()
        slow = [self.slow.get(name, "") for name in TRACK_SLOW]
        var = max(self.var) if self.var else ""
        self.track.append((now, *p, *self.attitude[:2], *slow, var))
        margin = self.config.kill_outside_fence
        if margin is None or self.killed:
            return
        if self.fence.contains(p, margin):
            self._outside_since = None
        elif self._outside_since is None:
            self._outside_since = now
        elif now - self._outside_since > OUTSIDE_GRACE_S:
            self.kill()
            self._fault(f"left the fence by more than {margin} m at {p.round(2).tolist()}: motors cut")

    def _on_slow(self, timestamp, data, conf):
        self.slow = data
        if not self.flying or self.grounded:
            return
        now = runtime.clock.now()
        if self.config.land_on_low_battery and data.get("pm.state") == PM_LOW_POWER:
            self.land_now()
            self._fault(f"battery low ({data.get('pm.vbat', 0):.2f} V): landing")
            return
        limit = self.config.tracking_loss_land_s
        if limit is None or "lighthouse.status" not in data:
            return
        if data["lighthouse.status"] == LH_ESTIMATING:
            self._untracked_since = None
        elif self._untracked_since is None:
            self._untracked_since = now
        elif now - self._untracked_since > limit:
            self.land_now()
            self._fault(f"Lighthouse tracking lost for {limit} s: landing")

    def _on_var(self, timestamp, data, conf):
        self.var = [data[f"kalman.varP{v}"] for v in "XYZ" if f"kalman.varP{v}" in data]

    def _fault(self, reason):
        self.say(reason)
        self._on_fault(self, reason)

    ###
    #  crazyflie_py Crazyflie interface
    ###

    def position(self):
        if self.pose is None:
            # _safe_cf_position in blocklyTranslations retries on ValueError
            raise ValueError(f"{self.prefix}: no position from the drone yet")
        return self.pose.copy()

    getPosition = position

    def _accept(self, command):
        if self.grounded:
            self._warn_once("grounded", f"ignoring {command} and later commands: the drone is landing or stopped")
            return False
        self.flying = True
        return True

    def _clamped(self, command, target):
        target = np.asarray(target, dtype=float)
        clamped = self.fence.clamp(target)
        if not np.allclose(clamped, target):
            self._warn_once(f"fence-{command}",
                            f"{command} to {target.round(2).tolist()} is outside the fence: clamped to "
                            f"{clamped.round(2).tolist()} (warned once per command type)")
        return clamped

    def _high_level(self):
        """The high-level commander is ignored while streamed positions are fresh, and the motors stop when they go
        stale: circle() streams without handing back, so hand back here before any high-level command"""
        if self._streaming:
            self.cf.commander.send_notify_setpoint_stop(100)
            self._streaming = False
        return self.cf.high_level_commander

    def _base(self):
        return self._target if self._target is not None else self.position()

    def takeoff(self, targetHeight, duration, groupMask=0):
        if not self._accept("takeoff"):
            return
        p = self.position()
        target = self._clamped("takeoff", [p[0], p[1], targetHeight])
        self._high_level().takeoff(float(target[2]), _duration(duration), yaw=None)
        self._target = target

    def land(self, targetHeight, duration, groupMask=0):
        if not self._accept("land"):
            return
        p = self._base()
        target = self._clamped("land", [p[0], p[1], targetHeight])
        self._high_level().land(float(target[2]), _duration(duration), yaw=None)
        self._target = target

    def goTo(self, goal, yaw, duration, relative=False, groupMask=0):
        if not self._accept("goTo"):
            return
        goal = np.asarray(goal, dtype=float)
        target = self._base() + goal if relative else goal
        clamped = self._clamped("goTo", target)
        hlc = self._high_level()
        if relative and np.allclose(clamped, target):
            hlc.go_to(*map(float, goal), float(yaw), _duration(duration), relative=True)
        else:
            hlc.go_to(*map(float, clamped), float(yaw), _duration(duration))
        self._target = clamped

    def cmdPosition(self, pos, yaw=0.0):
        if not self._accept("cmdPosition"):
            return
        target = self._clamped("cmdPosition", pos)
        self.cf.commander.send_position_setpoint(*map(float, target), math.degrees(yaw))
        self._streaming = True
        self._target = target

    def notifySetpointsStop(self, remainValidMillisecs=100, groupMask=0):
        if self.grounded:
            return
        self.cf.commander.send_notify_setpoint_stop(remainValidMillisecs)
        self._streaming = False

    def setLEDColor(self, r, g, b):
        if self.decks and "LedRing" not in self.decks:
            self._warn_once("ring", "no LED ring deck: ignoring LED colors")
            return
        r, g, b = led_rgb(r, g, b)
        if not self._ring_on:
            self.cf.param.set_value("ring.effect", "7")  # solid color
            self._ring_on = True
        self.cf.param.set_value("ring.solidRed", str(r))
        self.cf.param.set_value("ring.solidGreen", str(g))
        self.cf.param.set_value("ring.solidBlue", str(b))

    def setParam(self, name, value):
        self.cf.param.set_value(name.replace("/", "."), str(value))

    def emergency(self):
        self.kill()

    def uploadTrajectory(self, *args, **kwargs):
        raise NotImplementedError("uploaded trajectories aren't supported by the Lighthouse runner yet")

    startTrajectory = uploadTrajectory

    ###
    #  Runner's own commands: these work after grounded is set
    ###

    def land_now(self):
        """Land from wherever it is; later flight commands are ignored"""
        if self.killed or self.grounded and self.land_duration:
            return
        self.grounded = True
        if not self.flying or self.pose is None:
            return
        self.land_duration = max(2.0, float(self.pose[2]) / LAND_SPEED)
        try:
            # Ends position streaming (circle, parametric), so the high-level commander takes over
            self.cf.commander.send_notify_setpoint_stop()
            self.cf.high_level_commander.land(0.0, self.land_duration, yaw=None)
        except Exception as e:
            self.say(f"land failed: {e}")

    def kill(self):
        self.grounded = True
        self.killed = True
        try:
            self.cf.supervisor.send_emergency_stop()
        except Exception as e:
            self.say(f"emergency stop failed: {e}")

    def stop_motors(self):
        try:
            self.cf.high_level_commander.stop()
            self.cf.supervisor.send_arming_request(False)
        except Exception:
            pass
        self.flying = False


def _duration(duration):
    # A zero-length plan (goto_velocity to where it already is) isn't something to send the firmware
    return max(float(duration), 0.1)
