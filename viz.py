"""Lighthouse viz: edit a Crazyflie flight script, run it, and watch it in 3D.

    python lighthouse-viz/viz.py [--script flight.py] [--port 8765]

The page shows the path parsed from the code (exact go_to/takeoff/land
coordinates) and, while connected, the live state estimate, base stations,
radio link and lighthouse status. Check runs a staged preflight (radio, drone,
base stations, position) and stays connected; Fly is only accepted while a
passed check is still connected. Both run in runner.py as a subprocess; Stop
sends SIGINT, which lands the drone before disconnecting.
Each run's code and telemetry are saved in runs/.
"""

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from flightplan import extract_plan

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
RUNS = HERE / "runs"
MAX_HISTORY = 30000  # pose samples kept for browsers that (re)connect mid-run


class Hub:
    """Fan-out of events to browsers, plus the state a newly opened page needs."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients = []
        self.t0 = time.time()
        self.meta = {"connected": False, "run": "idle"}
        self.geos = {}
        self.lh = None
        self.link = None
        self.poses = []
        self.logs = []
        self.checks = {}
        self.record = None

    def publish(self, event):
        event.setdefault("t", round(time.time() - self.t0, 3))
        with self.lock:
            kind = event["type"]
            if kind == "pose" and self.meta.get("run") == "fly":
                # History (for pages opened mid-run) only covers hardware flights
                self.poses.append(event)
                if len(self.poses) > MAX_HISTORY:
                    del self.poses[: len(self.poses) - MAX_HISTORY]
            elif kind == "geos":
                self.geos = event["bs"]
            elif kind == "lh":
                self.lh = event
            elif kind == "link":
                self.link = event
            elif kind == "log":
                self.logs = (self.logs + [event])[-500:]
            elif kind == "meta":
                self.meta.update(event["meta"])
            elif kind == "check":
                # "skip" only applies to stages that never started
                if event.get("only_if") and self.checks.get(event["step"], {}).get("state") != event["only_if"]:
                    return
                self.checks[event["step"]] = {"state": event["state"], "detail": event.get("detail", "")}
            elif kind == "checks_reset":
                self.checks.clear()
            elif kind == "reset":
                self.poses.clear()
                self.logs.clear()
            if self.record:
                self.record.write(json.dumps(event) + "\n")
            for q in list(self.clients):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def meta_update(self, **kw):
        self.publish({"type": "meta", "meta": kw})

    def snapshot(self):
        with self.lock:
            return {"type": "snapshot", "meta": self.meta, "geos": self.geos, "lh": self.lh, "checks": self.checks,
                    "link": self.link, "poses": list(self.poses), "logs": list(self.logs)}

    def checks_passed(self):
        steps = ("radio", "drone", "basestations", "ready")
        return all(self.checks.get(s, {}).get("state") in ("ok", "warn") for s in steps)

    def subscribe(self):
        q = queue.Queue(maxsize=5000)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)


def interrupt(proc):
    """Ask runner.py to stop (it lands first if flying): SIGINT, or Ctrl+Break on Windows."""
    proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)


class Runner:
    """Owns the runner.py subprocess (and therefore the radio)."""

    def __init__(self, hub, workdir):
        self.hub = hub
        self.workdir = workdir
        self.proc = None
        self.mode = None
        self.lock = threading.Lock()

    def busy(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, mode, code=None, uri=None):
        with self.lock:
            if mode == "fly" and not (self.busy() and self.mode == "check" and self.hub.checks_passed()):
                return False, "run Check first (it must pass and stay connected)"
            if self.busy():
                if self.mode == "check":
                    # Free the radio for the flight / restart the check
                    self._stop_and_wait()
                else:
                    return False, "a run is already in progress"
            RUNS.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            cmd = [sys.executable, "-u", str(HERE / "runner.py"), mode]
            if mode == "fly":
                code_path = RUNS / f"{stamp}-flight.py"
                code_path.write_text(code)
                cmd += ["--code", str(code_path)]
                self.hub.publish({"type": "reset"})
            else:
                self.hub.publish({"type": "checks_reset"})
                cmd += ["--uri", uri]
            if self.hub.record:
                self.hub.record.close()
            self.hub.record = open(RUNS / f"{stamp}-{mode}.jsonl", "w")
            # Windows can only interrupt a child that has its own process group (via Ctrl+Break)
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            self.proc = subprocess.Popen(cmd, cwd=self.workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, bufsize=1, creationflags=flags)
            self.mode = mode
            self.hub.meta_update(run=mode, status="starting", level="info")
            threading.Thread(target=self._pump_stdout, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._pump_stderr, args=(self.proc,), daemon=True).start()
            return True, ""

    def stop(self):
        with self.lock:
            if not self.busy():
                return
            if getattr(self.proc, "_viz_interrupted", False):
                self.proc.kill()  # second press: stop immediately
            else:
                self.proc._viz_interrupted = True
                interrupt(self.proc)

    def _stop_and_wait(self):
        self.proc._viz_interrupted = True
        interrupt(self.proc)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def _pump_stdout(self, proc):
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                ev = {"type": "log", "text": line.rstrip()}
            ev.pop("t", None)
            self.hub.publish(ev)
        code = proc.wait()
        if proc is self.proc:
            # pyusb can crash while the interpreter shuts down after SIGINT; that is not a run failure
            stopped = getattr(proc, "_viz_interrupted", False)
            level = "info" if code in (0, -signal.SIGINT) or stopped else "error"
            self.hub.meta_update(run="idle", connected=False,
                                 **({"status": f"exited ({code})", "level": level} if level == "error" else {}))

    def _pump_stderr(self, proc):
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                self.hub.publish({"type": "log", "text": line, "level": "err"})


def make_handler(hub, runner, script):
    # The file being edited; Save under a new name switches to it (same folder)
    current = {"path": script}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(STATIC), **kw)

        def log_message(self, *a):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/events":
                return self.events()
            if path == "/api/code":
                path = current["path"]
                return self.json({"name": path.name, "code": path.read_text() if path.exists() else ""})
            return super().do_GET()

        def do_POST(self):
            path = self.path.split("?")[0]
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if path == "/api/plan":
                return self.json(extract_plan(body.get("code", ""), current["path"].name))
            if path == "/api/run":
                code = body.get("code", "")
                mode = body.get("mode", "fly")
                if mode not in ("fly", "check"):
                    return self.json({"ok": False, "error": f"unknown mode {mode}"}, 400)
                plan = extract_plan(code, current["path"].name)
                uri = body.get("uri") or plan.get("uri")
                if mode == "check" and not uri:
                    return self.json({"ok": False, "error": "no URI in code"}, 400)
                ok, err = runner.start(mode, code=code, uri=uri)
                return self.json({"ok": ok, "error": err}, 200 if ok else 409)
            if path == "/api/stop":
                runner.stop()
                return self.json({"ok": True})
            if path == "/api/save":
                name = body.get("name") or current["path"].name
                if not re.fullmatch(r"[\w.-]+\.py", name):
                    return self.json({"ok": False, "error": "file name must look like my_flight.py"}, 400)
                path = script.parent / name
                path.write_text(body.get("code", ""))
                current["path"] = path
                return self.json({"ok": True, "name": name, "path": str(path)})
            self.send_error(404)

        def json(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            q = hub.subscribe()
            try:
                self._send(hub.snapshot())
                while True:
                    try:
                        ev = q.get(timeout=10)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    batch = [ev]
                    while not q.empty() and len(batch) < 200:
                        batch.append(q.get_nowait())
                    self._send({"type": "batch", "events": batch})
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                hub.unsubscribe(q)

        def _send(self, obj):
            self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
            self.wfile.flush()

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", default=str(HERE / "flight.py"), help="flight script (default: the lab template, flight.py)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    script = Path(args.script).resolve()
    hub = Hub()
    # Runs execute in the script's folder so its relative paths (./cache) behave as usual
    runner = Runner(hub, script.parent)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(hub, runner, script))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Lighthouse viz: {url}  (editing {script})")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if runner.busy():
            runner._stop_and_wait()
        server.server_close()


if __name__ == "__main__":
    main()
