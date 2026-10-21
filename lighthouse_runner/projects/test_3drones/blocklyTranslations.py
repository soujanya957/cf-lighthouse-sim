from collections import namedtuple
from types import SimpleNamespace
import numpy as np
from PIL import ImageColor
import rclpy
import random
import time
from crazyflieLoggers import *

import traceback

Hz = 20


def _safe_cf_position(cf, retries=50, retry_sleep=0.01):
    """
    Read Crazyflie position robustly while ROS pose callbacks are catching up.
    """
    last_error = None
    for _ in range(retries):
        try:
            return cf.position()
        except ValueError as e:
            # Seen when pose cache is temporarily empty:
            # "need at least one array to concatenate"
            last_error = e
            time.sleep(retry_sleep)
    if last_error is not None:
        raise last_error
    return cf.position()

###
#  Landing/Takeoff commands
###


def takeoff(groupState, height=1.0, duration=2.0):
    """Takeoff command, runs for every crazyflie in groupState

    Args:
        groupState GroupState: crazyflies and timehelper objects
        height (float, optional): height to takeoff to. Defaults to 1.0.
        duration (float, optional): duration of command. Defaults to 2.0.
    """
    timeHelper = groupState.timeHelper
    crazyflies = groupState.crazyflies
    for cf in crazyflies:
        cf.takeoff(float(height), float(duration))
    timeHelper.sleep(duration)


def land(groupState, height=0.04, duration=2.0):
    """Land command, runs for every crazyflie in groupState

    Args:
        groupState GroupState: crazyflies and timehelper objects
        height (float, optional): height to land to. Defaults to 1.0.
        duration (float, optional): duration of command. Defaults to 2.0.
    """
    timeHelper = groupState.timeHelper
    crazyflies = groupState.crazyflies
    for cf in crazyflies:
        cf.land(float(height), float(duration))
    timeHelper.sleep(duration)


###
#  Motion Primitive Commands
###


def goto_velocity(groupState, x, y, z, v, rel=False):
    """goTo command at a given *average* velocity, runs for every crazyflie in groupState

    Args:
        groupState GroupState: crazyflies and timehelper objects
        x float: Desired x position
        y float: Desired y position
        z float: Desired z position
        v float: Desired average velocity
        rel (bool, optional): Whether or not the command is relative or absolute. Defaults to False.
    """
    timeHelper = groupState.timeHelper
    crazyflies = groupState.crazyflies
    max_duration = 0
    for cf in crazyflies:
        # try:
        #     if not isinstance(cf, CrazyflieSimLogger):
        #         rclpy.spin_once(cf.node)
        # except Exception:
        #     cf.node.get_logger().error(traceback.format_exc())
        #     pass
        curr_pos = cf.position()
        dist = np.linalg.norm(np.array(curr_pos) - np.array([x, y, z]))
        duration = dist / v
        cf.goTo((float(x), float(y), float(z)), 0, duration=duration, relative=rel)
        max_duration = max(duration, max_duration)
    timeHelper.sleep(max_duration)


def goto_duration(groupState, x, y, z, duration, rel=False):
    """goTo command at a given duration, runs for every crazyflie in groupState

    Args:
        groupState GroupState: crazyflies and timehelper objects
        x float: Desired x position
        y float: Desired y position
        z float: Desired z position
        duration float: duration of command
        rel (bool, optional): Whether or not the command is relative or absolute. Defaults to False.
    """
    timeHelper = groupState.timeHelper
    crazyflies = groupState.crazyflies
    for cf in crazyflies:
        cf.goTo((float(x), float(y), float(z)), 0, duration=duration, relative=rel)
    timeHelper.sleep(duration)


def goto_velocity_relative_position(groupState, x, y, z, v):
    """goTo command with given average velocity and relative position

    Args:
        groupState GroupState: crazyflies and timehelper objects
        x float: Desired x position
        y float: Desired y position
        z float: Desired z position
        v float: average velocity
    """
    distance = np.linalg.norm((x, y, z))
    time = distance / v
    # time *= 2
    crazyflies = groupState.crazyflies
    timeHelper = groupState.timeHelper
    for cf in crazyflies:
        cf.goTo((float(x), float(y), float(z)), 0.0, time, relative=True)
    timeHelper.sleep(time)


def goto_duration_relative(groupState, x, y, z, duration):
    goto_duration(groupState, x, y, z, duration, True)


def move_direction(groupState, direction, distance, duration):
    timeHelper = groupState.timeHelper
    crazyflies = groupState.crazyflies
    for cf in crazyflies:
        pos = cf.getPosition()
        if direction == "up":
            cf.goTo((0, 0, distance), 0, duration, relative=True)
        elif direction == "forward":
            cf.goTo((distance, 0, 0), 0, duration=duration, relative=True)
        elif direction == "backward":
            cf.goTo((-distance, 0, 0), 0, duration=duration, relative=True)
        elif direction == "down":
            cf.goTo((0, 0, -distance), 0, duration, relative=True)
    timeHelper.sleep(duration)
    return duration


def hover(groupState, duration):
    """Hover in place: the crazyflies already hold their last position, so just wait"""
    groupState.timeHelper.sleep(duration)


# The old "stop and hover" block's code, still in projects saved before it became "hover for N seconds"
def stop_and_hover(groupState, height=None):
    crazyflies = groupState.crazyflies
    for cf in crazyflies:
        position = cf.position()
        cf.goTo(position, 0, 1.0)


###
#  Low Level Commands...
###
def getPosition(cf):
    return cf.position()


# TODO: Enable low level commands...
def cmdPosition(cf, pos):
    cf.cmdPosition(pos)


def enableHighLevelCommander(cf):
    cf.notifySetpointsStop()


###
# Colors
###


# How long an LED change is assumed to take on a real drone, as the simulator models it (setColor in
# simulatorCommands.ts). Not measured yet (issue #15).
_led_change_time = 0.1


def setLEDColorFromHex(groupState, hex):
    setLEDColor(groupState, *ImageColor.getcolor(hex, "RGB"))


def setLEDColor(groupState, r, g, b):
    for cf in groupState.crazyflies:
        cf.setLEDColor(r, g, b)
    # Real drones take however long the change really takes. A modifier's recording (simCommand) counts the assumed
    # time, so the modifier keeps the simulator's schedule; a real drone's own time is absorbed into it (_replay).
    if isinstance(groupState.timeHelper, TimeHelperSimLogger):
        groupState.timeHelper.sleep(_led_change_time)


def setRandomLEDColor(groupState):
    # One random color for the whole group, like the simulator
    setLEDColor(groupState, random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))


###
#  Trajectory commands...
###
def circle(groupState, radius, velocity, radians, direction):
    crazyflies = groupState.crazyflies
    timeHelper = groupState.timeHelper
    clockwise = 1
    if direction == "ccw":
        clockwise = -1

    # velocity is the speed along the circle (m/s), as in the simulator, so the turn rate depends on the radius
    angular_velocity = velocity / radius

    fx = lambda t: 0
    fy = lambda t: radius * (np.cos(t * angular_velocity) - 1)
    fz = lambda t: clockwise * radius * np.sin(t * angular_velocity)

    timesteps = np.arange(0, radians / angular_velocity, 1 / Hz)

    # start positions bumped up by 1 meter in z
    initialPositions = [
        (pos[0], pos[1], pos[2]) if len(pos) >= 3 else pos
        for pos in (cf.position() for cf in crazyflies)
    ]
    for t in timesteps:
        for initPos, cf in zip(initialPositions, crazyflies):
            pos = np.array([fx(t), fy(t), fz(t)])
            pos += np.array(initPos)
            cf.cmdPosition(pos)
        timeHelper.sleepForRate(Hz)

    # for cf in crazyflies:
    #     cf.notifySetpointsStop()


# Names parametric expressions can use; same as the simulator (parametricScope in trajectories.ts)
_parametric_names = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
    "atan2": np.arctan2, "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh, "sqrt": np.sqrt, "abs": np.abs,
    "exp": np.exp, "log": np.log, "pow": np.power, "floor": np.floor, "ceil": np.ceil, "min": min, "max": max,
    "pi": np.pi, "e": np.e,
}


def _parametric_function(expression):
    code = compile(expression, "<parametric>", "eval")

    def f(t):
        # Like the simulator: a value that can't be computed (log(0), sqrt of a negative, overflow) is 0. errstate also
        # keeps numpy from warning, which fails under the empty builtins.
        try:
            with np.errstate(all="ignore"):
                value = float(eval(code, {"__builtins__": {}}, {**_parametric_names, "t": t}))
        except (ArithmeticError, ValueError):
            return 0.0
        return value if np.isfinite(value) else 0.0

    return f


def parametric(groupState, x, y, z, start_t, end_t, duration):
    """Flies the path (x(t), y(t), z(t)) for t from start_t to end_t over `duration` seconds, shifted so it starts
    where each drone is. Streams positions like circle(), then hands control back to the high-level commander."""
    crazyflies = groupState.crazyflies
    timeHelper = groupState.timeHelper
    functions = [_parametric_function(expression) for expression in (x, y, z)]
    path_at = lambda t: np.array([f(t) for f in functions])
    path_start = path_at(start_t)
    initialPositions = [np.array(_safe_cf_position(cf), dtype=float) for cf in crazyflies]

    for elapsed in list(np.arange(0, duration, 1 / Hz)) + [duration]:
        offset = path_at(start_t + (end_t - start_t) * elapsed / duration) - path_start
        for initPos, cf in zip(initialPositions, crazyflies):
            cf.cmdPosition(initPos + offset)
        timeHelper.sleepForRate(Hz)

    for initPos, cf in zip(initialPositions, crazyflies):
        _hand_back(cf, initPos + path_at(end_t) - path_start)


def _hand_back(cf, position):
    """After streaming positions: hand control back to the high-level commander, which holds the drone at position"""
    cf.notifySetpointsStop()
    cf.goTo(position, 0, 2.0)


def _command_goal(command, position):
    """Where a recorded command takes a drone that's at position; None for one that doesn't move it"""
    if command.command == "goTo":
        vector = np.array(command.position, dtype=float)
        return position + vector if command.relative else vector
    if command.command in ("takeoff", "land"):
        return np.array([position[0], position[1], command.height], dtype=float)
    if command.command == "cmdPos":
        return np.array(command.position, dtype=float)
    return None


# Trajectory Modifiers...


###
#  Trajectory modifiers. Each runs its commands on stand-in crazyflies that record what they're sent and when
#  (simCommand), then sends them to the real ones changed (_replay), or combined with another recording
#  (addTrajectories). The simulator's versions are in trajectories.ts; checks/python.check.ts compares the two.
###


def simCommand(originalGroupState, command):
    """Runs command on stand-in crazyflies (CrazyflieSimLogger) that record what they're sent and when, instead of
    flying. Returns their group state, plus when the recording began and how long it lasts, waits included."""
    # From 0: only times since the recording began matter, and the real clock's size (seconds since 1970) would cost
    # precision
    clock = TimeHelperSimLogger()
    start = clock.currTime = 0.0
    crazyflies = [CrazyflieSimLogger(_safe_cf_position(cf), clock) for cf in originalGroupState.crazyflies]
    command(SimpleNamespace(crazyflies=crazyflies, timeHelper=clock))
    return SimpleNamespace(crazyflies=crazyflies, timeHelper=clock, start=start, duration=clock.currTime - start)


def _sleep_until(timeHelper, time):
    timeHelper.sleep(max(0.0, time - timeHelper.time()))


def _replay(groupState, recording, transform, time_scale=1.0):
    """Sends a recording (simCommand) to the crazyflies at its recorded times, slowed down by time_scale, with every
    position changed by transform(start, position), start being where the crazyflie was when the recording began.
    Go-tos stay go-tos (a relative one's vector changes the way its positions do), takeoffs and landings become go-tos
    to their changed end, streamed positions are changed, and LED changes are sent as they are."""
    timeHelper = groupState.timeHelper
    begin = timeHelper.time()
    drones = []
    for simCF, cf in zip(recording.crazyflies, groupState.crazyflies):
        start = np.array(simCF.initialPosition, dtype=float)
        drones.append(SimpleNamespace(cf=cf, commands=simCF.commands, start=start, position=start.copy()))
    streaming = False
    # All crazyflies in a group are sent the same commands
    for i, command in enumerate(recording.crazyflies[0].commands):
        _sleep_until(timeHelper, begin + (command.startTime - recording.start) * time_scale)
        for drone in drones:
            sent = drone.commands[i]
            moved = lambda point: transform(drone.start, np.array(point, dtype=float))
            goal = _command_goal(sent, drone.position)
            if goal is None:
                if sent.command == "setLEDColor":
                    drone.cf.setLEDColor(*sent.color)
                elif sent.command == "notifySetpointsStop":
                    drone.cf.notifySetpointsStop()
                continue
            if sent.command == "goTo" and sent.relative:
                drone.cf.goTo(moved(goal) - moved(drone.position), sent.yaw, sent.duration * time_scale, relative=True)
            elif sent.command == "goTo":
                drone.cf.goTo(moved(goal), sent.yaw, sent.duration * time_scale)
            elif sent.command == "cmdPos":
                drone.cf.cmdPosition(moved(goal))
            else:
                drone.cf.goTo(moved(goal), 0, sent.duration * time_scale)
            drone.position = goal
        if command.command in ("goTo", "takeoff", "land", "cmdPos", "notifySetpointsStop"):
            streaming = command.command == "cmdPos"
    _sleep_until(timeHelper, begin + recording.duration * time_scale)
    # Ended streaming positions, like parametric() does
    if streaming:
        for drone in drones:
            _hand_back(drone.cf, transform(drone.start, drone.position))


def negate(groupState, command):
    """Runs command mirrored through where each crazyflie starts (2 * start - position), like NegateTrajectory"""
    _replay(groupState, simCommand(groupState, command), lambda start, point: 2 * start - point)


def _rotation_matrix(x_rot, y_rot, z_rot):
    """Rotation by Euler angles in radians, in the simulator's order (three.js Euler 'XYZ': Rx Ry Rz)"""
    cx, sx, cy, sy, cz, sz = np.cos(x_rot), np.sin(x_rot), np.cos(y_rot), np.sin(y_rot), np.cos(z_rot), np.sin(z_rot)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rx @ ry @ rz


def rotate(groupState, command, x_rot, y_rot, z_rot):
    """Runs command rotated around where each crazyflie starts, like RotationTrajectory"""
    matrix = _rotation_matrix(x_rot, y_rot, z_rot)
    _replay(groupState, simCommand(groupState, command), lambda start, point: start + matrix @ (point - start))


def translate(groupState, command, x, y, z, speed=0.4):
    """Runs command shifted by (x, y, z) meters, like TranslationTrajectory.

    The drones first fly to the shifted start in a straight line at `speed` m/s, so the shifted path doesn't begin
    with a jump.
    """
    recording = simCommand(groupState, command)
    offset = np.array([x, y, z], dtype=float)
    distance = float(np.linalg.norm(offset))
    if distance > 0:
        duration = distance / speed
        for cf in groupState.crazyflies:
            cf.goTo(np.array(_safe_cf_position(cf), dtype=float) + offset, 0, duration)
        groupState.timeHelper.sleep(duration)
    _replay(groupState, recording, lambda start, point: point + offset)


def stretchTrajectory(groupState, command, x_stretch, y_stretch, z_stretch, time_stretch):
    """Runs command stretched away from where each crazyflie starts and slowed down by time_stretch, like
    StretchTrajectory"""
    factors = np.array([x_stretch, y_stretch, z_stretch], dtype=float)
    _replay(groupState, simCommand(groupState, command), lambda start, point: start + (point - start) * factors, time_stretch)


def goToPolynomial(T, p, v, a, p2, v2, a2):
    poly = [0] * 8

    # Taken from crazyflie Firmware Library
    T2 = T * T
    T3 = T2 * T
    T4 = T3 * T
    T5 = T4 * T
    T6 = T5 * T
    T7 = T6 * T
    poly[0] = p
    poly[1] = v
    poly[2] = a / 2
    poly[3] = 0
    poly[4] = -(
        5 * (14 * p - 14 * p2 + 8 * T * v + 6 * T * v2 + 2 * T2 * a - T2 * a2)
    ) / (2 * T4)
    poly[5] = (
        84 * p - 84 * p2 + 45 * T * v + 39 * T * v2 + 10 * T2 * a - 7 * T2 * a2
    ) / T5
    poly[6] = -(
        140 * p - 140 * p2 + 72 * T * v + 68 * T * v2 + 15 * T2 * a - 13 * T2 * a2
    ) / (2 * T6)
    poly[7] = (2 * (10 * p - 10 * p2 + 5 * T * v + 5 * T * v2 + T2 * a - T2 * a2)) / T7
    return lambda t: sum([poly[i] * t**i for i in range(len(poly))])


def _recorded_path(recording, simCF):
    """A recorded stand-in crazyflie (simCommand) as one path, like the simulator's SequenceTrajectory: its position at
    any time since the recording began, and its LED changes as (time, color). Go-tos, takeoffs and landings follow the
    same degree 7 polynomial as the firmware and the simulator (robotGoTo); a streamed position is held until the
    next."""
    segments = []
    leds = []
    start = np.array(simCF.initialPosition, dtype=float)
    position = start
    for command in simCF.commands:
        at = command.startTime - recording.start
        if command.command == "setLEDColor":
            leds.append((at, command.color))
            continue
        goal = _command_goal(command, position)
        if goal is None:
            continue
        moving = None
        if command.command != "cmdPos" and command.duration > 0:
            fs = [goToPolynomial(command.duration, p, 0, 0, g, 0, 0) for p, g in zip(position, goal)]
            moving = lambda t, fs=fs: np.array([f(t) for f in fs])
        segments.append((at, command.duration, moving, goal))
        position = goal

    def position_at(t):
        current = start
        for segment_start, duration, moving, goal in segments:
            if segment_start > t + 1e-9:
                break
            current = moving(t - segment_start) if moving is not None and t - segment_start < duration else goal
        return current

    return position_at, leds


def addTrajectories(groupState, command1, command2, add=True):
    """Flies command1 and command2 at the same time, adding up their movements, or subtracting command2's when add is
    False. Mirrors the simulator's AddTrajectories (trajectories.ts): start + movement of 1 +/- movement of 2, with the
    shorter one stretched to last as long as the longer one. LED changes come at their stretched times; at the same
    moment, command2's wins. Streams positions like parametric(), then hands control back to the high-level commander
    and holds the end."""
    timeHelper = groupState.timeHelper
    recording1, recording2 = simCommand(groupState, command1), simCommand(groupState, command2)
    duration = max(recording1.duration, recording2.duration)
    if duration <= 0:
        return
    sign = 1 if add else -1
    stretched = lambda t, recording: t * duration / recording.duration if recording.duration > 0 else 0.0
    drones = []
    for cf, simCF1, simCF2 in zip(groupState.crazyflies, recording1.crazyflies, recording2.crazyflies):
        path1, leds1 = _recorded_path(recording1, simCF1)
        path2, leds2 = _recorded_path(recording2, simCF2)
        # Sorted by time, then slot, so on the same moment the second slot's change is sent last and stays
        leds = sorted(
            [(stretched(t, recording1), 0, color) for t, color in leds1] + [(stretched(t, recording2), 1, color) for t, color in leds2],
            key=lambda change: change[:2],
        )
        drones.append(SimpleNamespace(cf=cf, start=np.array(simCF1.initialPosition, dtype=float), path1=path1, path2=path2, leds=leds))

    def position_at(drone, fraction):
        return drone.start + (drone.path1(fraction * recording1.duration) - drone.start) + sign * (drone.path2(fraction * recording2.duration) - drone.start)

    def send_leds(drone, until):
        while drone.leds and drone.leds[0][0] <= until + 1e-9:
            drone.cf.setLEDColor(*drone.leds.pop(0)[2])

    # Every 1/20 s on the clock, sending the position for the moment it's sent: time an LED change really takes on the
    # drones delays the step it's in, not the rest of the path. The step count, not the clock, decides when it's done:
    # on a clock in seconds since 1970, a time difference can come out a hair short of the step it reached.
    begin = timeHelper.time()
    steps = int(np.ceil(duration * Hz - 1e-9))
    step = 0
    while step < steps:
        elapsed = max(step / Hz, timeHelper.time() - begin)
        for drone in drones:
            send_leds(drone, elapsed)
            drone.cf.cmdPosition(position_at(drone, min(1.0, elapsed / duration)))
        step = max(step + 1, int(elapsed * Hz + 1e-6) + 1)
        next_step = min(step / Hz, duration)
        # LED changes before the next step go out at their own moment
        for at in sorted({change[0] for drone in drones for change in drone.leds if change[0] < next_step - 1e-9}):
            _sleep_until(timeHelper, begin + at)
            for drone in drones:
                send_leds(drone, at)
        _sleep_until(timeHelper, begin + next_step)
    for drone in drones:
        send_leds(drone, duration)
        end = position_at(drone, 1.0)
        drone.cf.cmdPosition(end)
        _hand_back(drone.cf, end)


def subtractTrajectories(groupState, command1, command2, add=False):
    """Like addTrajectories, subtracting command2's movement. Saved projects pass add=False; it's ignored."""
    addTrajectories(groupState, command1, command2, add=False)
