"""Preflight checks for connected drones, run in parallel. Adapted from ../runner.py."""

import threading
import time

import numpy as np

from .drone import LH_ESTIMATING

STEPS = ["drone", "decks", "basestations", "estimate", "start"]
GEOMETRY_TOLERANCE = 0.02  # m: base stations placed differently on two drones means two coordinate frames


def wait_for(cond, timeout, poll=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(poll)
    return cond()


def bits(mask):
    return {i for i in range(16) if mask and (mask >> i) & 1}


def names(ids):
    return ", ".join(str(i + 1) for i in sorted(ids))


def check_drone(d, config):
    if not wait_for(lambda: d.slow and d.pose is not None, 5):
        return "fail", "connected but no telemetry"
    flags = d.supervisor_flags()
    for bad, hint in (("locked", "locked: restart the drone"), ("crashed", "crashed: restart the drone"),
                      ("tumbled", "tumbled: place it upright")):
        if bad in flags:
            return "fail", hint
    vbat = d.slow.get("pm.vbat")
    if vbat is None:
        return "warn", "no battery reading"
    # Resting voltage; a worn or half-empty pack sags far lower once the motors spin up
    if vbat < config.min_battery_v:
        return "fail", f"battery low ({vbat:.2f} V), charge or swap it"
    return ("warn" if vbat < config.min_battery_v + 0.1 else "ok"), f"{vbat:.2f} V"


def check_decks(d, uses_led):
    if not d.decks:
        return "warn", "couldn't read the deck list"
    if "Lighthouse4" not in d.decks:
        return "fail", "no Lighthouse deck detected"
    if uses_led and "LedRing" not in d.decks:
        return "warn", "the project sets LED colors but there is no LED ring deck"
    return "ok", ", ".join(d.decks)


def check_base_stations(d):
    """Geometry is stored on the drone and the deck sees sweeps from at least two base stations at once"""
    geo = bits(d.slow.get("lighthouse.bsGeoVal"))
    if not geo:
        return "fail", "no base-station geometry on the drone: run upload-geometry or set it up in cfclient"
    seen, cal = set(), set()
    samples, together, dropped = 0, 0, 0
    last = None
    end = time.time() + 4
    while time.time() < end:
        ev = d.slow
        if ev is not last:
            last = ev
            rx = bits(ev.get("lighthouse.bsReceive"))
            seen |= rx
            cal |= bits(ev.get("lighthouse.bsCalVal"))
            samples += 1
            together += len(rx & geo) >= min(2, len(geo))
            dropped += ev.get("lighthouse.status") != LH_ESTIMATING
        time.sleep(0.05)
    if not seen:
        return "fail", f"no sweeps from BS {names(geo)}: base stations on? deck uncovered?"
    share = together / samples if samples else 0
    if dropped:
        return "fail", f"tracking dropped in {dropped}/{samples} samples: improve base-station line of sight"
    if len(geo) >= 2 and share < 0.7:
        return "fail", f"two or more base stations seen together only {share:.0%} of the time"
    notes = [f"BS {names(seen)} seen" + (f", together {share:.0%}" if len(geo) >= 2 else "")]
    if geo - seen:
        notes.append(f"BS {names(geo - seen)} not seen")
    if seen - cal:
        notes.append(f"BS {names(seen - cal)} uncalibrated")
    if seen - geo:
        notes.append(f"BS {names(seen - geo)} has no geometry")
    weak = len(geo) >= 2 and share < 0.9
    return ("warn" if geo - seen or weak or len(seen & geo) < 2 or seen - cal else "ok"), "; ".join(notes)


def check_estimate(d):
    d.reset_estimator()
    time.sleep(0.5)
    if not wait_for(lambda: d.slow.get("lighthouse.status") == LH_ESTIMATING, 5):
        return "fail", "Lighthouse data not reaching the estimator"
    if d.var is not None:
        wait_for(lambda: d.var and max(d.var) < 0.001, 5)
    p = d.pose
    if p is None or not np.all(np.isfinite(p)) or np.max(np.abs(p)) > 20:
        return "fail", "position estimate invalid"
    if d.var and max(d.var) >= 0.001:
        return "warn", f"at {_fmt(p)}, estimate not settled"
    return "ok", f"at {_fmt(p)}"


def check_start(d, start, config):
    p = d.pose
    start = np.asarray(start, dtype=float)
    off = float(np.linalg.norm(p[:2] - start[:2]))
    where = f"at {_fmt(p)}, start {_fmt(start)}"
    if off > config.start_tolerance:
        return "fail", f"{off:.2f} m from its start position ({where}): move it, or check the IDs"
    if p[2] > 0.3:
        return "fail", f"not on the ground ({where})"
    if not config.fence.contains(p, 0.05):
        return "fail", f"outside the fence ({where})"
    return "ok", f"{off:.2f} m off"


def _fmt(p):
    return "(" + ", ".join(f"{v:.2f}" for v in p) + ")"


def run_drone(d, start, config, uses_led, report):
    """Runs the steps in order, stopping at the first failure"""
    steps = [("drone", lambda: check_drone(d, config)),
             ("decks", lambda: check_decks(d, uses_led)),
             ("basestations", lambda: check_base_stations(d)),
             ("estimate", lambda: check_estimate(d))]
    if start is not None:
        steps.append(("start", lambda: check_start(d, start, config)))
    for name, step in steps:
        report(d, name, "running", "")
        try:
            state, detail = step()
        except Exception as e:
            state, detail = "fail", f"error: {e}"
        report(d, name, state, detail)
        if state == "fail":
            return False
    return True


def check_same_geometry(drones, report):
    """Every drone must hold the same base-station geometry, or they fly in different coordinate frames"""
    geos = {}
    threads = [threading.Thread(target=lambda d=d: geos.__setitem__(d.id, d.read_geometry())) for d in drones]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    unread = [d for d in drones if geos.get(d.id) is None]
    for d in unread:
        report(d, "geometry", "fail", "couldn't read the base-station geometry")
    readable = [d for d in drones if geos.get(d.id) is not None]
    if not readable:
        return False
    reference = readable[0]
    ok = not unread
    for d in readable:
        ref, mine = geos[reference.id], geos[d.id]
        differ = sorted(bs for bs in set(ref) | set(mine)
                        if bs not in ref or bs not in mine
                        or np.linalg.norm(np.subtract(ref[bs]["origin"], mine[bs]["origin"])) > GEOMETRY_TOLERANCE)
        if differ:
            ok = False
            report(d, "geometry", "fail", f"BS {names(differ)} differ from {reference.prefix}: upload the same "
                                          "system config to every drone (upload-geometry)")
        else:
            report(d, "geometry", "ok", f"{len(mine)} base stations" + ("" if d is reference else
                                                                          f", same as {reference.prefix}"))
    return ok


def print_report(d, step, state, detail):
    if state == "running":
        return
    marks = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}
    print(f"  {d.prefix:<6} {step:<13} {marks[state]}  {detail}", flush=True)


def run(drones, starts, config, uses_led, report=print_report):
    """starts: {id: [x, y, z]} or None. Reports each result with report(drone, step, state, detail) and returns
    whether every drone passed."""
    lock = threading.Lock()
    reporter = report

    def report(d, step, state, detail):
        with lock:
            reporter(d, step, state, detail)

    results = {}
    threads = [threading.Thread(target=lambda d=d: results.__setitem__(
        d.id, run_drone(d, (starts or {}).get(d.id), config, uses_led, report))) for d in drones]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = all(results.values())
    if ok and len(drones) > 1:
        ok = check_same_geometry(drones, report)
    return ok
