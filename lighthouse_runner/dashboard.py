"""Lighthouse dashboard: pick a webGUI export from projects/, simulate it in 3D, check the drones, and fly it.

    python lighthouse_runner/dashboard.py [--port 8766] [--fleet fleet.yaml]

Put each export in its own folder under projects/ (the folder that has launch_nodes.py). Simulate runs
`lh_run.py dry-run` and plays the recording back. Check starts `lh_run.py session`, which connects every drone,
streams their status, runs the preflight, and stays connected; Fly is accepted only while a passed check is still
connected. Stop sends SIGINT: it lands every drone when flying, and cuts the motors when pressed again while landing.
Based on ../viz.py.
"""

import argparse
import json
import queue
import re
import signal
import subprocess
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from lhrunner import config as fleet_config

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
# In the lighthouse-viz repo, three.js, CodeMirror and the Crazyflie model come from the viz's static folder (../static)
SHARED_STATIC = HERE.parent / "static"
PROJECTS = HERE / "projects"
LH_RUN = HERE / "lh_run.py"
SUMMARY_PREFIX = "LH_SUMMARY "
MAX_HISTORY = 20000  # pose batches kept for pages opened mid-flight
PROJECT_NAME = re.compile(r"[\w.-]+")
# radio://<dongle>/<channel>/<data rate>/<5-byte address>
RADIO_URI = re.compile(r"radio://\d+/\d{1,3}/(250K|1M|2M)/[0-9A-Fa-f]{10}")
OTHER_URI = re.compile(r"(usb|udp|debug)://\S+")


###
#  Projects
###


def project_dir(name):
    if not name or not PROJECT_NAME.fullmatch(name):
        return None
    path = PROJECTS / name
    return path if (path / "launch_nodes.py").exists() and (path / "starting_positions.yaml").exists() else None


def group_assignments(launch_nodes):
    """Which robots (indices into starting_positions) each group module flies, from launch_nodes.py's node section"""
    groups = {}
    current = None
    for line in launch_nodes.split("Insert Nodes Here", 1)[-1].splitlines():
        m = re.match(r"\s*import (\w+)", line)
        if m:
            current = m.group(1)
            groups.setdefault(current, [])
        for index in re.findall(r"crazyflies\[(\d+)\]", line):
            if current:
                groups[current].append(int(index))
    return groups


def project_info(path, code=False):
    sp = yaml.safe_load((path / "starting_positions.yaml").read_text())
    groups = group_assignments((path / "launch_nodes.py").read_text())
    files = sorted(path.glob("*.py"))
    info = {
        "name": path.name,
        "robots": [{"name": n, "start": [float(v) for v in p]} for n, p in zip(sp["names"], sp["positions"])],
        "groups": groups,
        "modified": max(f.stat().st_mtime for f in path.iterdir() if f.is_file()),
    }
    if code:
        info["code"] = {f"{g}.py": (path / f"{g}.py").read_text() for g in groups if (path / f"{g}.py").exists()}
        info["code"]["launch_nodes.py"] = (path / "launch_nodes.py").read_text()
        info["led"] = any("LEDColor" in f.read_text() for f in files
                          if f.name not in ("blocklyTranslations.py", "crazyflieLoggers.py"))
    return info


def list_projects():
    PROJECTS.mkdir(exist_ok=True)
    out = []
    for path in sorted(PROJECTS.iterdir()):
        if path.is_dir() and project_dir(path.name):
            try:
                out.append(project_info(path))
            except Exception as e:
                out.append({"name": path.name, "error": str(e), "robots": [], "groups": {}, "modified": 0})
    return out


def fleet_info(fleet_path):
    config = fleet_config.load(fleet_path)
    data = yaml.safe_load(Path(fleet_path).read_text()) or {}
    geos = {}
    # Optional: a cfclient system config, so the base stations show before anything connects
    lh_file = data.get("lighthouse_config")
    if lh_file:
        path = (Path(fleet_path).parent / lh_file).resolve()
        try:
            lh = yaml.safe_load(path.read_text())
            geos = {str(k): {"origin": g["origin"], "rot": g["rotation"]} for k, g in (lh.get("geos") or {}).items()}
        except Exception as e:
            print(f"couldn't read {path}: {e}")
    return {
        "file": str(fleet_path),
        "drones": {str(k): v for k, v in config.drones.items()},
        "template": config.uri_template,
        "fence": {"lo": config.fence.lo.tolist(), "hi": config.fence.hi.tolist()},
        "minSeparation": config.min_separation,
        "startTolerance": config.start_tolerance,
        "geos": geos,
    }


def check_uri(uri):
    if RADIO_URI.fullmatch(uri) or OTHER_URI.fullmatch(uri):
        return None
    return (f"{uri!r} isn't a Crazyflie URI: radio://<dongle>/<channel 0-125>/<250K|1M|2M>/<10 hex digits>, "
            "for example radio://0/80/2M/E7E7E7E749")


def save_drones(fleet_path, drones):
    """Rewrites only the drones: block of the fleet file, so its comments and other settings stay as they are"""
    path = Path(fleet_path)
    lines = path.read_text().splitlines(keepends=True)
    block = ["drones:\n" if drones else "drones: {}\n"]
    block += [f"  {cf_id}: {uri}\n" for cf_id, uri in sorted(drones.items())]
    start = next((i for i, line in enumerate(lines) if re.match(r"drones\s*:", line)), None)
    if start is None:
        lines += ["\n"] + block
    else:
        end = start + 1
        # The block is the indented lines after drones:
        while end < len(lines) and lines[end][:1] in (" ", "\t"):
            end += 1
        lines[start:end] = block
    path.write_text("".join(lines))


def parse_crazyflies_yaml(text):
    """Crazyswarm2's crazyflies.yaml: robots: {cf73: {uri: ...}} -> {73: uri}"""
    data = yaml.safe_load(text) or {}
    drones = {}
    for name, robot in (data.get("robots") or {}).items():
        digits = re.sub(r"\D", "", str(name))
        if digits and isinstance(robot, dict) and robot.get("uri"):
            # Crazyswarm2's radio://*/... means any Crazyradio; cflib needs a number, so use the first one
            drones[int(digits)] = re.sub(r"^radio://\*/", "radio://0/", str(robot["uri"]))
    if not drones:
        raise ValueError("no robots with a uri in it: is this Crazyswarm2's crazyflies.yaml?")
    return drones


###
#  Events to browsers
###


class Hub:
    """Fan-out of events to browsers, plus what a newly opened page needs"""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients = []
        self.session = None   # {"project", "ids"} of the running session
        self.phase = {"phase": "idle"}
        self.checks = {}      # cf -> step -> {state, detail}
        self.status = {}
        self.geos = {}
        self.poses = []       # pose batches of the current flight
        self.logs = []

    def publish(self, event):
        with self.lock:
            kind = event["type"]
            if kind == "poses":
                if self.phase["phase"] in ("flying", "landing"):
                    self.poses.append(event)
                    del self.poses[:-MAX_HISTORY]
            elif kind == "status":
                self.status = event["drones"]
            elif kind == "geos":
                self.geos = event["bs"]
            elif kind == "check":
                self.checks.setdefault(event["cf"], {})[event["step"]] = {"state": event["state"],
                                                                         "detail": event["detail"]}
            elif kind == "phase":
                if event["phase"] == "checking":
                    # A new preflight; the link result stays
                    self.checks = {cf: {k: v for k, v in steps.items() if k == "link"} for cf, steps in self.checks.items()}
                if event["phase"] == "flying":
                    self.poses = []
                self.phase = event
            elif kind == "session":
                self.session = event.get("session")
                if self.session:
                    self.checks, self.status, self.poses = {}, {}, []
            elif kind == "log":
                self.logs = (self.logs + [event])[-500:]
            for q in list(self.clients):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def snapshot(self):
        with self.lock:
            return {"type": "snapshot", "session": self.session, "phase": self.phase, "checks": self.checks,
                    "status": self.status, "geos": self.geos, "poses": list(self.poses), "logs": list(self.logs)}

    def subscribe(self):
        q = queue.Queue(maxsize=5000)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)


def log(hub, text, level=""):
    hub.publish({"type": "log", "text": text, "level": level})


###
#  The hardware session (owns the radio)
###


class Session:
    def __init__(self, hub, fleet):
        self.hub = hub
        self.fleet = fleet
        self.proc = None
        self.interrupts = 0
        self.lock = threading.Lock()

    def busy(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, project, ids):
        with self.lock:
            if self.busy():
                if self.hub.phase["phase"] in ("flying", "landing"):
                    return False, "a flight is in progress"
                self._stop_and_wait()
            path = project_dir(project)
            if path is None:
                return False, f"no project {project}"
            cmd = [sys.executable, "-u", str(LH_RUN), "--fleet", self.fleet, "session", str(path),
                   "--ids", ",".join(map(str, ids))]
            self.interrupts = 0
            self.hub.publish({"type": "session", "session": {"project": project, "ids": ids}})
            self.hub.publish({"type": "phase", "phase": "starting"})
            # Own process group: Ctrl-C in the dashboard's terminal reaches only the dashboard, which then sends the
            # session exactly one SIGINT (land). A second one would cut the motors.
            self.proc = subprocess.Popen(cmd, cwd=HERE, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
            threading.Thread(target=self._pump_stdout, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._pump_stderr, args=(self.proc,), daemon=True).start()
            return True, ""

    def send(self, command, phases):
        with self.lock:
            if not self.busy():
                return False, "not connected: run Check first"
            if self.hub.phase["phase"] not in phases:
                return False, f"can't {command} while {self.hub.phase['phase']}"
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.flush()
            return True, ""

    def stop(self):
        """1st: land if flying, else disconnect. 2nd (while landing): cut the motors. 3rd: kill the process."""
        with self.lock:
            if not self.busy():
                return
            self.interrupts += 1
            if self.interrupts >= 3:
                self.proc.kill()
            else:
                self.proc.send_signal(signal.SIGINT)

    def _stop_and_wait(self):
        self.proc.send_signal(signal.SIGINT)
        try:
            # Long enough for a landing
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def _pump_stdout(self, proc):
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                event = {"type": "log", "text": line.rstrip()}
            if proc is self.proc:
                self.hub.publish(event)
        code = proc.wait()
        if proc is self.proc:
            if self.hub.phase["phase"] not in ("closed", "failed", "done"):
                self.hub.publish({"type": "phase", "phase": "closed", "reason": f"exited ({code})"})
            self.hub.publish({"type": "session", "session": None})

    def _pump_stderr(self, proc):
        for line in proc.stderr:
            line = line.rstrip()
            # cflib's deprecation notices aren't worth showing
            if line and "DeprecationWarning" not in line and "warnings.warn" not in line:
                log(self.hub, line, "err")


###
#  Simulation
###


class Simulator:
    def __init__(self, fleet):
        self.fleet = fleet
        self.cache = {}
        self.lock = threading.Lock()

    def run(self, project, ids):
        path = project_dir(project)
        if path is None:
            return {"ok": False, "error": f"no project {project}"}
        stamp = max(f.stat().st_mtime for f in path.iterdir() if f.is_file())
        key = (project, tuple(ids), stamp, Path(self.fleet).stat().st_mtime)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        cmd = [sys.executable, str(LH_RUN), "--fleet", self.fleet, "dry-run", str(path),
               "--ids", ",".join(map(str, ids)), "--speed", "20", "--json-full"]
        try:
            out = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "the simulation took too long"}
        lines = [line for line in out.stdout.splitlines() if line.startswith(SUMMARY_PREFIX)]
        if not lines:
            tail = "\n".join((out.stdout + out.stderr).strip().splitlines()[-20:])
            return {"ok": False, "error": f"the simulation crashed:\n{tail}"}
        result = {"ok": True, **json.loads(lines[-1][len(SUMMARY_PREFIX):]),
                  "output": [line for line in out.stdout.splitlines() if not line.startswith(SUMMARY_PREFIX)]}
        with self.lock:
            self.cache[key] = result
        return result


###
#  HTTP
###


def make_handler(hub, session, simulator, fleet):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(STATIC), **kw)

        def log_message(self, *a):
            pass

        def translate_path(self, path):
            local = Path(super().translate_path(path))
            if not local.exists() and STATIC in local.parents:
                shared = SHARED_STATIC / local.relative_to(STATIC)
                if shared.exists():
                    return str(shared)
            return str(local)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self):
            path, _, query = self.path.partition("?")
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            if path == "/events":
                return self.events()
            if path == "/api/projects":
                return self.json({"projects": list_projects()})
            if path == "/api/project":
                project = project_dir(params.get("name"))
                if project is None:
                    return self.json({"error": "no such project"}, 404)
                return self.json(project_info(project, code=True))
            if path == "/api/fleet":
                return self.json(fleet_info(fleet))
            return super().do_GET()

        def do_POST(self):
            path = self.path.split("?")[0]
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if path == "/api/simulate":
                ids = [int(i) for i in body.get("ids", [])]
                return self.json(simulator.run(body.get("project"), ids))
            if path == "/api/connect":
                ids = [int(i) for i in body.get("ids", [])]
                if len(set(ids)) != len(ids):
                    return self.json({"ok": False, "error": "each robot needs its own Crazyflie ID"}, 400)
                return self.result(*session.start(body.get("project"), ids))
            if path == "/api/check":
                return self.result(*session.send("check", ("ready", "failed")))
            if path == "/api/fly":
                return self.result(*session.send("fly", ("ready",)))
            if path == "/api/fleet/drones":
                try:
                    drones = {int(k): str(v).strip() for k, v in (body.get("drones") or {}).items()}
                except ValueError:
                    return self.json({"ok": False, "error": "IDs must be whole numbers"}, 400)
                if any(k <= 0 for k in drones):
                    return self.json({"ok": False, "error": "IDs must be positive"}, 400)
                errors = [f"cf{k}: {e}" for k, v in drones.items() if (e := check_uri(v))]
                if errors:
                    return self.json({"ok": False, "error": "; ".join(errors)}, 400)
                save_drones(fleet, drones)
                return self.json({"ok": True, "fleet": fleet_info(fleet)})
            if path == "/api/fleet/import":
                try:
                    return self.json({"ok": True, "drones": {str(k): v for k, v in
                                                             parse_crazyflies_yaml(body.get("text", "")).items()}})
                except Exception as e:
                    return self.json({"ok": False, "error": f"couldn't read that file: {e}"}, 400)
            if path == "/api/stop":
                session.stop()
                return self.json({"ok": True})
            self.send_error(404)

        def result(self, ok, error):
            return self.json({"ok": ok, "error": error}, 200 if ok else 409)

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
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--fleet")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    args.fleet = args.fleet or str(fleet_config.local_fleet(HERE))

    fleet = str(Path(args.fleet).resolve())
    PROJECTS.mkdir(exist_ok=True)
    hub = Hub()
    session = Session(hub, fleet)
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(hub, session, Simulator(fleet), fleet))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Lighthouse dashboard: {url}  (projects in {PROJECTS})")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if session.busy():
            session._stop_and_wait()
        server.server_close()


if __name__ == "__main__":
    main()
