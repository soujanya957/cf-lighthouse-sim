"""Runs the preflight check or a flight script and streams telemetry as JSON lines.

Started by viz.py as a subprocess so a run can be stopped with SIGINT, which
lands the drone before the link is closed. Events go to the original stdout;
the script's own print() output is wrapped into {"type": "log"} events.

The flight script is not modified: it is imported as a module and its
SyncCrazyflie is replaced by a subclass that attaches extra log blocks to the
same link, so the radio is opened only once.
"""

import argparse
import importlib.util
import json
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

_out = os.fdopen(os.dup(1), "w", buffering=1)
_out_lock = threading.Lock()


def emit(event):
    line = json.dumps(event)
    with _out_lock:
        _out.write(line + "\n")


def status(text, level="info"):
    emit({"type": "meta", "meta": {"status": text, "level": level}})


class Telemetry:
    """Log blocks, link statistics and lighthouse geometry on an open cflib link."""

    POSE_MS = 50
    SLOW_MS = 250
    SUPERVISOR_BITS = ["canArm", "armed", "autoArm", "canFly", "flying", "tumbled", "locked", "crashed"]

    def __init__(self):
        self.configs = []
        self.att = (0.0, 0.0, 0.0, 1.0)
        self.geo_mask = None
        self.reading_geos = False
        self.cf = None
        self.link = {}
        self.link_lock = threading.Lock()
        self.latest = None        # last slow-log event (battery, lighthouse, supervisor)
        self.pose = None          # last position [x, y, z]
        self.var = None           # last kalman position variances
        self.decks = []
        self.geos = None
        self.geos_read = threading.Event()

    def attach(self, cf):
        from cflib.crazyflie.log import LogConfig

        self.cf = cf
        log_toc, param_toc = cf.log.toc, cf.param.toc

        def has(name):
            return log_toc.get_element_by_complete_name(name) is not None

        pos = LogConfig(name="viz_pos", period_in_ms=self.POSE_MS)
        for v in ("x", "y", "z", "vx", "vy", "vz"):
            pos.add_variable(f"stateEstimate.{v}", "float")
        att = LogConfig(name="viz_att", period_in_ms=self.POSE_MS)
        for v in ("qx", "qy", "qz", "qw"):
            att.add_variable(f"stateEstimate.{v}", "float")
        slow = LogConfig(name="viz_slow", period_in_ms=self.SLOW_MS)
        for n in ("lighthouse.status", "lighthouse.bsReceive", "lighthouse.bsActive", "lighthouse.bsGeoVal",
                  "lighthouse.bsCalVal", "pm.vbat", "pm.state", "supervisor.info", "radio.rssi"):
            if has(n):
                slow.add_variable(n)

        var = LogConfig(name="viz_var", period_in_ms=self.SLOW_MS)
        for v in ("varPX", "varPY", "varPZ"):
            if has(f"kalman.{v}"):
                var.add_variable(f"kalman.{v}", "float")

        pos.data_received_cb.add_callback(self._on_pos)
        att.data_received_cb.add_callback(self._on_att)
        slow.data_received_cb.add_callback(self._on_slow)
        var.data_received_cb.add_callback(self._on_var)
        for conf in (pos, att, slow, var):
            # Variables added without a type wait in default_fetch_as until add_config resolves them
            if not conf.variables and not conf.default_fetch_as:
                continue
            conf.error_cb.add_callback(lambda c, msg: status(f"log block {c.name} rejected: {msg}", "warn"))
            try:
                cf.log.add_config(conf)
                conf.start()
                self.configs.append(conf)
            except Exception as e:
                status(f"log block {conf.name} failed: {e}", "warn")

        stats = cf.link_statistics
        for name, cb in (("quality", stats.link_quality_updated), ("rssi", stats.uplink_rssi_updated),
                         ("up", stats.uplink_rate_updated), ("down", stats.downlink_rate_updated),
                         ("upCongestion", stats.uplink_congestion_updated),
                         ("downCongestion", stats.downlink_congestion_updated), ("latency", stats.latency_updated)):
            cb.add_callback(lambda v, name=name: self._on_link(name, v))
        threading.Thread(target=self._link_loop, daemon=True).start()

        decks = []
        for group, params in param_toc.toc.items():
            if group == "deck":
                for pname in params:
                    try:
                        if cf.param.get_value(f"deck.{pname}") == "1":
                            decks.append(pname.removeprefix("bc"))
                    except Exception:
                        pass
        self.decks = sorted(decks)
        emit({"type": "meta", "meta": {"connected": True, "decks": sorted(decks),
                                       "lighthouseDeck": "Lighthouse4" in decks}})
        if "Lighthouse4" in decks or not decks:
            self.read_geometry()
        else:
            self.geos_read.set()

    def detach(self):
        for conf in self.configs:
            try:
                conf.delete()
            except Exception:
                pass
        self.configs.clear()
        self.cf = None
        emit({"type": "meta", "meta": {"connected": False}})

    def read_geometry(self):
        if self.reading_geos or self.cf is None:
            return
        from cflib.crazyflie.mem import LighthouseMemHelper

        try:
            helper = LighthouseMemHelper(self.cf)
        except Exception as e:
            status(f"no lighthouse memory: {e}", "warn")
            self.geos_read.set()
            return
        self.reading_geos = True

        def done(geos):
            self.reading_geos = False
            bs = {str(ch): {"origin": list(g.origin), "rot": [list(r) for r in g.rotation_matrix]}
                  for ch, g in geos.items() if g is not None and g.valid}
            self.geos = bs
            self.geos_read.set()
            emit({"type": "geos", "bs": bs})

        helper.read_all_geos(done)

    def _on_link(self, name, value):
        with self.link_lock:
            self.link[name] = round(float(value), 1)

    def _link_loop(self):
        while self.cf is not None:
            time.sleep(0.5)
            with self.link_lock:
                if self.link:
                    emit({"type": "link", **self.link})

    def _on_var(self, ts, data, conf):
        self.var = [data[f"kalman.varP{k}"] for k in "XYZ" if f"kalman.varP{k}" in data]

    def _on_pos(self, ts, data, conf):
        self.pose = [data[f"stateEstimate.{k}"] for k in "xyz"]
        emit({"type": "pose",
              "p": [round(data[f"stateEstimate.{k}"], 4) for k in "xyz"],
              "v": [round(data[f"stateEstimate.v{k}"], 4) for k in "xyz"],
              "q": [round(c, 5) for c in self.att]})

    def _on_att(self, ts, data, conf):
        self.att = tuple(data[f"stateEstimate.q{k}"] for k in "xyzw")

    def _on_slow(self, ts, data, conf):
        ev = {"type": "lh",
              "status": data.get("lighthouse.status"),
              "receive": data.get("lighthouse.bsReceive"),
              "active": data.get("lighthouse.bsActive"),
              "geoVal": data.get("lighthouse.bsGeoVal"),
              "calVal": data.get("lighthouse.bsCalVal"),
              "vbat": round(data["pm.vbat"], 2) if "pm.vbat" in data else None,
              "pmState": data.get("pm.state"),
              "rssi": data.get("radio.rssi")}
        if "supervisor.info" in data:
            ev["supervisor"] = [n for i, n in enumerate(self.SUPERVISOR_BITS) if data["supervisor.info"] >> i & 1]
        self.latest = ev
        emit(ev)
        # Geometry written to the drone while connected (e.g. from cfclient): read it again
        if ev["geoVal"] is not None and ev["geoVal"] != self.geo_mask:
            if self.geo_mask is not None:
                threading.Thread(target=self.read_geometry, daemon=True).start()
            self.geo_mask = ev["geoVal"]


class TeeStdout:
    """Turns the flight script's print() lines into log events."""

    def __init__(self):
        self.buf = ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                emit({"type": "log", "text": line.rstrip()})
        return len(s)

    def flush(self):
        pass


def load_script(path):
    spec = importlib.util.spec_from_file_location("flight_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def instrument(mod, telemetry):
    base = mod.SyncCrazyflie

    class VizSyncCrazyflie(base):
        def open_link(self):
            status(f"connecting {self._link_uri}")
            super().open_link()
            status("connected")
            telemetry.attach(self.cf)

        def __exit__(self, exc_type, exc, tb):
            if exc_type is KeyboardInterrupt and self.is_link_open():
                # Stopped mid-flight: land instead of dropping when the link closes
                status("stopping: landing", "warn")
                try:
                    self.cf.high_level_commander.land(0.0, 2.5)
                    time.sleep(3.0)
                    self.cf.high_level_commander.stop()
                except Exception:
                    pass
            telemetry.detach()
            return super().__exit__(exc_type, exc, tb)

    mod.SyncCrazyflie = VizSyncCrazyflie


def run_fly(code_path):
    sys.stdout = TeeStdout()
    mod = load_script(code_path)
    if not hasattr(mod, "SyncCrazyflie"):
        raise SystemExit("script must use SyncCrazyflie (from cflib.crazyflie.syncCrazyflie import SyncCrazyflie)")
    instrument(mod, Telemetry())
    mod.main()


def step(name, state, detail=""):
    emit({"type": "check", "step": name, "state": state, "detail": detail})


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


def check_radio(uri):
    """Step 1: a Crazyradio is plugged in and not held by another program."""
    if not re.match(r"^[a-z]+://\S+$", uri):
        step("radio", "fail", f'URI "{uri}" is not an address: set it to your radio address from cfclient, e.g. radio://0/80/2M/E7E7E7E7E7')
        return False
    if not uri.startswith("radio://"):
        step("radio", "ok", "not a radio link")
        return True
    from cflib.drivers.crazyradio import Crazyradio, get_serials

    try:
        serials = get_serials()
    except Exception as e:
        step("radio", "fail", f"USB error: {e}")
        return False
    if not serials:
        step("radio", "fail", "no Crazyradio found, plug it in")
        return False
    dev = uri[len("radio://"):].split("/")[0]
    devid = int(dev) if dev.isdigit() else 0
    try:
        radio = Crazyradio(devid=devid)
        radio.close()
    except Exception as e:
        step("radio", "fail", f"radio busy or unavailable, close cfclient ({e})")
        return False
    step("radio", "ok", f"Crazyradio {serials[devid] if devid < len(serials) else serials[0]}")
    return True


def check_drone(scf, telemetry, uri):
    """Step 2: the drone answers on the URI and is in a flyable state."""
    try:
        scf.open_link()
    except Exception as e:
        step("drone", "fail", f"no answer on {uri}: is it on, same channel/address? ({e})")
        return False
    status("connected")
    telemetry.attach(scf.cf)
    if not wait_for(lambda: telemetry.latest is not None and telemetry.pose is not None, 5):
        step("drone", "fail", "connected but no telemetry")
        return False
    time.sleep(1.0)  # let link statistics settle
    ev = telemetry.latest
    sup = ev.get("supervisor") or []
    vbat = ev.get("vbat")
    quality = telemetry.link.get("quality")
    detail = " · ".join(filter(None, [f"{vbat:.2f} V" if vbat else None,
                                      f"link {quality:.0f}%" if quality is not None else None]))
    for bad, hint in (("locked", "locked: restart the drone"), ("crashed", "crashed: restart the drone"),
                      ("tumbled", "tumbled: place it upright")):
        if bad in sup:
            step("drone", "fail", hint)
            return False
    # Resting voltage; a worn or half-empty pack sags far lower once the motors spin up
    if vbat is not None and vbat < 3.8:
        step("drone", "fail", f"battery low ({vbat:.2f} V), charge or swap it")
        return False
    if (vbat is not None and vbat < 3.9) or (quality is not None and quality < 80):
        step("drone", "warn", detail)
    else:
        step("drone", "ok", detail)
    return True


def check_base_stations(telemetry):
    """Step 3: geometry is stored on the drone and the deck sees sweeps from the base stations."""
    if telemetry.decks and "Lighthouse4" not in telemetry.decks:
        step("basestations", "fail", "no Lighthouse deck detected")
        return False
    telemetry.geos_read.wait(8)
    geo = {int(k) for k in (telemetry.geos or {})}
    if not geo:
        step("basestations", "fail", "no base-station geometry on the drone: set it up in cfclient")
        return False
    # Sample the log for 4 s: which base stations are received at the same time, and
    # whether lighthouse data ever stops reaching the estimator
    seen, cal = set(), set()
    samples, together, dropped = 0, 0, 0
    end = time.time() + 4
    last = None
    while time.time() < end:
        ev = telemetry.latest or {}
        if ev is not last:
            last = ev
            rx = bits(ev.get("receive"))
            seen |= rx
            cal |= bits(ev.get("calVal"))
            samples += 1
            together += len(rx & geo) >= min(2, len(geo))
            dropped += ev.get("status") != 2
        time.sleep(0.05)
    if not seen:
        step("basestations", "fail", f"no sweeps from BS {names(geo)}: base stations on? deck uncovered?")
        return False
    share = together / samples if samples else 0
    if dropped:
        step("basestations", "fail", f"tracking dropped in {dropped}/{samples} samples: improve base-station line of sight")
        return False
    if len(geo) >= 2 and share < 0.7:
        step("basestations", "fail", f"BS {names(geo)} seen together only {share:.0%} of the time: check placement/line of sight")
        return False
    notes = [f"BS {names(seen)} seen" + (f" · together {share:.0%}" if len(geo) >= 2 else "")]
    missing = geo - seen
    if missing:
        notes.append(f"BS {names(missing)} not seen")
    if seen - cal:
        notes.append(f"BS {names(seen - cal)} uncalibrated")
    no_geo = seen - geo
    if no_geo:
        notes.append(f"BS {names(no_geo)} has no geometry")
    weak = len(geo) >= 2 and share < 0.9
    step("basestations", "warn" if missing or weak or len(seen & geo) < 2 or seen - cal else "ok", " · ".join(notes))
    return True


def check_ready(telemetry):
    """Step 4: lighthouse data reaches the estimator and the position estimate is sane."""
    if not wait_for(lambda: (telemetry.latest or {}).get("status") == 2, 5):
        step("ready", "fail", "lighthouse data not reaching the estimator")
        return False
    if telemetry.var is not None:
        wait_for(lambda: max(telemetry.var) < 0.001, 5)
    x, y, z = telemetry.pose
    if any(v != v for v in (x, y, z)) or max(abs(x), abs(y), abs(z)) > 20:
        step("ready", "fail", "position estimate invalid")
        return False
    where = f"at {x:.2f}, {y:.2f}, {z:.2f}"
    if telemetry.var is not None and max(telemetry.var) >= 0.001:
        step("ready", "warn", f"{where} · estimate not settled")
    elif z > 0.3:
        step("ready", "warn", f"{where} · not on the ground?")
    else:
        step("ready", "ok", where)
    return True


def run_check(uri):
    """Staged preflight: radio, drone, base stations, position. Stays connected afterwards."""
    import cflib.crtp
    from cflib.crazyflie import Crazyflie
    from cflib.crazyflie.syncCrazyflie import SyncCrazyflie

    steps = ["radio", "drone", "basestations", "ready"]
    for s in steps:
        step(s, "pending")
    cflib.crtp.init_drivers()
    telemetry = Telemetry()
    scf = SyncCrazyflie(uri, cf=Crazyflie(rw_cache="./cache"))

    def run(name, fn, *args):
        step(name, "running")
        return fn(*args)

    try:
        ok = (run("radio", check_radio, uri)
              and run("drone", check_drone, scf, telemetry, uri)
              and run("basestations", check_base_stations, telemetry)
              and run("ready", check_ready, telemetry))
        if not ok:
            # Mark the stages that never ran
            for s in steps:
                emit({"type": "check", "step": s, "state": "skip", "only_if": "pending"})
        status("ready to fly" if ok else "check failed", "info" if ok else "error")
        if not scf.is_link_open():
            return
        while True:  # keep streaming telemetry until stopped or Fly takes over the radio
            time.sleep(1)
    finally:
        if scf.is_link_open():
            telemetry.detach()
            scf.close_link()


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main():
    if os.name == "nt":
        # viz.py stops runs with Ctrl+Break on Windows; treat it like Ctrl+C so the drone lands
        signal.signal(signal.SIGBREAK, _interrupt)
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["fly", "check"])
    ap.add_argument("--code", help="flight script to run (fly)")
    ap.add_argument("--uri", help="Crazyflie URI (check)")
    args = ap.parse_args()
    try:
        if args.mode == "fly":
            run_fly(Path(args.code))
            status("done")
        else:
            run_check(args.uri)
    except KeyboardInterrupt:
        status("stopped", "warn")


if __name__ == "__main__":
    main()
