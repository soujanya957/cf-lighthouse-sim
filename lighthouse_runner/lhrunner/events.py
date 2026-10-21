"""JSON-lines events on stdout, for the dashboard (lh_run.py session). Adapted from ../runner.py."""

import json
import os
import sys
import threading
import time

_out = None
_lock = threading.Lock()


class _LogStdout:
    """Turns print() lines (the runner's and the export's) into log events"""

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


def start():
    """Events go to the real stdout from now on; print() output becomes log events"""
    global _out
    _out = os.fdopen(os.dup(1), "w", buffering=1)
    sys.stdout = _LogStdout()


def emit(event):
    line = json.dumps(event)
    with _lock:
        _out.write(line + "\n")


class Telemetry:
    """Streams every drone's pose (10 Hz) and status (2 Hz) while the session is connected"""

    POSE_S = 0.1
    STATUS_EVERY = 5

    def __init__(self, drones):
        self.drones = drones
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        tick = 0
        while not self._stop.wait(self.POSE_S):
            poses = {d.prefix: {"p": [round(v, 4) for v in d.pose], "a": [round(v, 1) for v in d.attitude or [0, 0, 0]]}
                     for d in self.drones if d.pose is not None}
            if poses:
                emit({"type": "poses", "t": round(time.time(), 3), "drones": poses})
            if tick % self.STATUS_EVERY == 0:
                emit({"type": "status", "drones": {d.prefix: status(d) for d in self.drones}})
            tick += 1


def status(d):
    s = d.slow
    return {
        "vbat": round(s["pm.vbat"], 2) if "pm.vbat" in s else None,
        "pmState": s.get("pm.state"),
        "lhStatus": s.get("lighthouse.status"),
        "receive": s.get("lighthouse.bsReceive"),
        "active": s.get("lighthouse.bsActive"),
        "geoVal": s.get("lighthouse.bsGeoVal"),
        "calVal": s.get("lighthouse.bsCalVal"),
        "supervisor": d.supervisor_flags(),
        "link": round(d.link_quality) if d.link_quality is not None else None,
        "decks": d.decks,
        "flying": d.flying,
        "grounded": d.grounded,
        "killed": d.killed,
    }
