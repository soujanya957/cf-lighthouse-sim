"""Fly a webGUI-exported Crazyswarm2 project on Lighthouse-tracked Crazyflies, from macOS, without ROS.

Replaces `python3 configure.py`, `ros2 launch launch.py` and `python3 launch_nodes.py`. The export is not changed:
its launch_nodes.py runs as is, with stand-ins for rclpy and crazyflie_py (shims/) that hand it cflib drones.

    python lh_run.py dry-run ../test_project           # simulate and plot, no radio
    python lh_run.py fly ../test_project               # preflight, confirm, fly
    python lh_run.py check --ids 7,8                    # preflight only
    python lh_run.py upload-geometry lighthouse.yaml --ids 7,8

The dashboard (dashboard.py) runs `session` and `dry-run --json-full` and talks to them in JSON lines.
"""

import argparse
import json
import os
import runpy
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import yaml

from lhrunner import config as fleet_config
from lhrunner import runtime

ROOT = Path(__file__).resolve().parent
SHIMS = ROOT / "shims"
RUNS = ROOT / "runs"
SUMMARY_PREFIX = "LH_SUMMARY "
NOT_COPIED = {".py", ".pyc", ".sh"}


###
#  The exported project
###


def load_export(path):
    export = Path(path).resolve()
    for name in ("launch_nodes.py", "starting_positions.yaml"):
        if not (export / name).exists():
            raise SystemExit(f"{export} doesn't look like a webGUI export: no {name}")
    sp = yaml.safe_load((export / "starting_positions.yaml").read_text())
    return export, sp["names"], [list(map(float, p)) for p in sp["positions"]]


def uses_led(export):
    for f in export.glob("*.py"):
        if f.name in ("blocklyTranslations.py", "crazyflieLoggers.py"):
            continue
        if "LEDColor" in f.read_text():
            return True
    return False


def parse_ids(text):
    return [int(v) for v in text.replace(" ", "").split(",") if v]


def previous_ids(export, n):
    try:
        ids = [int(v) for v in yaml.safe_load((export / "cfs_ordering.yaml").read_text())["cfs"]]
        return ids if len(ids) == n else None
    except Exception:
        return None


def choose_ids(export, names, positions, ids_arg, ask):
    """configure.py's prompts: which Crazyflie flies each robot of the project"""
    n = len(names)
    if ids_arg:
        ids = parse_ids(ids_arg)
        if len(ids) != n:
            raise SystemExit(f"the project has {n} drone(s) ({', '.join(names)}) but --ids gives {len(ids)}")
    elif ask:
        defaults = previous_ids(export, n) or [None] * n
        print(f"You will need {n} crazyflie(s).")
        ids = []
        for name, pos, default in zip(names, positions, defaults):
            prompt = f"Crazyflie ID for {name} (starts at {pos})" + (f" [{default}]" if default is not None else "")
            while True:
                answer = input(prompt + ": ").strip() or (str(default) if default is not None else "")
                if answer.isdigit():
                    ids.append(int(answer))
                    break
                print("  enter a number, the Crazyflie ID")
    else:
        ids = previous_ids(export, n) or list(range(1, n + 1))
    if len(set(ids)) != len(ids):
        raise SystemExit(f"each robot needs its own Crazyflie: {ids}")
    return ids, dict(zip(ids, positions))


def make_run_dir(export, ids, kind):
    """The export stays untouched: launch_nodes.py runs in here, next to a cfs_ordering.yaml and the export's data
    files"""
    run_dir = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{export.name}-{kind}"
    run_dir.mkdir(parents=True)
    for f in export.iterdir():
        if f.is_file() and f.suffix not in NOT_COPIED:
            shutil.copy2(f, run_dir / f.name)
    (run_dir / "cfs_ordering.yaml").write_text(yaml.dump({"cfs": [str(i) for i in ids]}))
    return run_dir


def run_export(export, run_dir):
    # No __pycache__ left in the export
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(SHIMS), str(export)]
    os.chdir(run_dir)
    runpy.run_path(str(export / "launch_nodes.py"), run_name="__main__")


###
#  Dry run
###


def cmd_dry_run(args, config):
    from lhrunner import dryrun
    from lhrunner.simdrone import SimCF

    export, names, positions = load_export(args.export)
    ids, starts = choose_ids(export, names, positions, args.ids, ask=False)
    run_dir = make_run_dir(export, ids, "dry")
    drones = [SimCF(i, starts[i], config.fence) for i in ids]
    runtime.setup(drones, speed=args.speed)
    recorder = dryrun.Recorder(drones, args.max_time)
    if not (args.json or args.json_full):
        print(f"dry run of {export.name}: {', '.join(d.prefix for d in drones)} at {args.speed}x speed")
    recorder.start()
    try:
        run_export(export, run_dir)
    except KeyboardInterrupt:
        runtime.request_shutdown("interrupted")
    except Exception:
        traceback.print_exc()
        runtime.request_shutdown("error")
    runtime.request_shutdown("finished")
    recorder.join()
    recorder.trim()
    summary = dryrun.analyze(recorder, config)
    if args.json_full:
        print(SUMMARY_PREFIX + json.dumps({"summary": summary, "problems": dryrun.problems(summary),
                                           "sim": recorder.export(starts)}), flush=True)
        return
    if args.json:
        print(SUMMARY_PREFIX + json.dumps(summary), flush=True)
        return
    dryrun.print_summary(summary)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    if not args.no_plot:
        dryrun.plot(recorder, config, starts, run_dir / "dry-run.png", show=not args.no_show)


def sim_precheck(args, ids):
    """Runs the project in a fast dry run first (own process: one run per process), returns its problems"""
    from lhrunner import dryrun

    cmd = [sys.executable, str(ROOT / "lh_run.py"), "--fleet", args.fleet, "dry-run", args.export,
           "--ids", ",".join(map(str, ids)), "--speed", "20", "--json"]
    print("simulating the project first...", flush=True)
    out = subprocess.run(cmd, capture_output=True, text=True)
    lines = [line for line in out.stdout.splitlines() if line.startswith(SUMMARY_PREFIX)]
    if not lines:
        print(out.stdout[-2000:] + out.stderr[-2000:])
        return ["the dry run crashed (output above)"]
    summary = json.loads(lines[-1][len(SUMMARY_PREFIX):])
    dryrun.print_summary(summary)
    return dryrun.problems(summary)


###
#  Hardware
###


CONNECT_TIMEOUT_S = 90  # one drone takes about 4 s alone; drones sharing a Crazyradio take much longer


def check_radio():
    from cflib.drivers.crazyradio import get_serials

    try:
        serials = get_serials()
    except Exception as e:
        raise SystemExit(f"USB error looking for the Crazyradio: {e}")
    if not serials:
        raise SystemExit("no Crazyradio found: plug it in (and close cfclient)")
    print(f"Crazyradio {', '.join(serials)}")


def connect(config, ids, on_fault, report=None):
    import cflib.crtp
    from lhrunner.drone import LighthouseCF

    cflib.crtp.init_drivers()
    check_radio()
    cache = str(ROOT / "cache")
    drones = [LighthouseCF(i, config.uri(i), config, cache, on_fault) for i in ids]
    errors = {}

    def open_one(d):
        try:
            d.connect()
            d.setup()
        except Exception as e:
            errors[d.id] = e

    print(f"connecting to {', '.join(f'{d.prefix} ({d.uri})' for d in drones)}...", flush=True)
    # Daemon threads: a drone whose setup never finishes mustn't keep the process alive after the timeout
    threads = [threading.Thread(target=open_one, args=(d,), daemon=True) for d in drones]
    for t in threads:
        t.start()
    deadline = time.monotonic() + CONNECT_TIMEOUT_S
    for d, t in zip(drones, threads):
        t.join(max(0.0, deadline - time.monotonic()))
        if t.is_alive():
            errors[d.id] = (f"linked but setup didn't finish in {CONNECT_TIMEOUT_S} s; with several drones on one "
                            "Crazyradio, put them on the same channel or power-cycle them")
    if errors:
        from lhrunner.preflight import print_report

        for d in drones:
            if d.id in errors:
                e = errors[d.id]
                (report or print_report)(d, "link", "fail", e if isinstance(e, str) else
                                         f"no answer on {d.uri}: on? same channel/address? ({e})")
        # Closing a link that is still setting up can block too: give up on it after a few seconds
        closer = threading.Thread(target=disconnect, args=(drones,), daemon=True)
        closer.start()
        closer.join(5)
        raise SystemExit(1)
    if report:
        for d in drones:
            report(d, "link", "ok", d.uri)
    return drones


def disconnect(drones):
    for d in drones:
        try:
            d.close()
        except Exception:
            pass


def land_and_stop(drones):
    """Lands whatever is still in the air, then stops the motors. Ctrl-C while waiting cuts the motors."""
    for d in drones:
        if not d.killed:
            d.land_now()
    longest = max((d.land_duration for d in drones), default=0.0)
    try:
        if longest:
            print(f"landing (Ctrl-C again cuts the motors)...", flush=True)
            end = time.time() + longest + 3.0
            while time.time() < end and any(d.flying and d.pose is not None and d.pose[2] > 0.08
                                            for d in drones if not d.killed):
                time.sleep(0.1)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("cutting the motors", flush=True)
        for d in drones:
            d.kill()
    for d in drones:
        d.stop_motors()


def save_tracks(drones, path):
    rows = ["t,cf,x,y,z,roll,pitch,lh_status,bs_active,bs_receive,vbat,max_var"]
    for d in drones:
        for t, x, y, z, *rest in d.track:
            rows.append(f"{t:.3f},{d.prefix},{x:.4f},{y:.4f},{z:.4f}," + ",".join(
                f"{v:.4g}" if isinstance(v, float) else str(v) for v in rest))
    path.write_text("\n".join(rows) + "\n")


def fly(drones, export, ids, phase=lambda name, **info: None):
    """Arms the connected, checked drones, runs the export, then lands and stops them. phase(name, **info) reports
    flying, landing and done."""
    def on_shutdown(reason):
        if reason != "finished":
            print(f"stopping ({reason}): landing every drone", flush=True)
        phase("landing", reason=reason)
        for d in drones:
            if not d.killed:
                d.land_now()

    run_dir = make_run_dir(export, ids, "fly")
    runtime.setup(drones)
    runtime.on_shutdown(on_shutdown)
    for d in drones:
        d.arm()
    time.sleep(1.0)
    print("flying: Ctrl-C lands every drone", flush=True)
    phase("flying", t0=round(time.time(), 3))
    try:
        run_export(export, run_dir)
    except KeyboardInterrupt:
        runtime.request_shutdown("interrupted")
    except Exception:
        traceback.print_exc()
        runtime.request_shutdown("error")
    runtime.request_shutdown("finished")
    land_and_stop(drones)
    save_tracks(drones, run_dir / "flight.csv")
    print(f"{runtime.shutdown_reason}; flight log in {run_dir / 'flight.csv'}", flush=True)
    phase("done", reason=runtime.shutdown_reason, log=str(run_dir / "flight.csv"))


def cmd_session(args, config):
    """For the dashboard: connect, preflight, then stream telemetry until it sends `fly` (or `check` to run the
    preflight again) on stdin. Stops on EOF or SIGINT; SIGINT while flying lands every drone."""
    from lhrunner import events, preflight

    # The dashboard stops a session with SIGINT; Python ignores it if the dashboard was started in the background
    signal.signal(signal.SIGINT, signal.default_int_handler)
    events.start()
    emit = events.emit

    def phase(name, **info):
        emit({"type": "phase", "phase": name, **info})

    def report(d, step, state, detail):
        emit({"type": "check", "cf": d.prefix, "step": step, "state": state, "detail": detail})

    def on_fault(d, reason):
        runtime.request_shutdown(f"fault: {d.prefix}: {reason}")

    export, names, positions = load_export(args.export)
    ids, starts = choose_ids(export, names, positions, args.ids, ask=False)
    phase("connecting")
    try:
        drones = connect(config, ids, on_fault, report)
    except SystemExit as e:
        if e.code not in (None, 1):
            print(e.code)
        phase("failed", reason="couldn't connect")
        return
    telemetry = events.Telemetry(drones)
    telemetry.start()
    flown = False
    try:
        geos = drones[0].read_geometry()
        if geos:
            emit({"type": "geos", "bs": {str(k): v for k, v in geos.items()}})

        def check():
            phase("checking")
            ok = preflight.run(drones, starts, config, uses_led(export), report)
            phase("ready" if ok else "failed", reason=None if ok else "preflight failed")
            return ok

        ok = check()
        for line in sys.stdin:
            command = line.strip()
            if command == "check":
                ok = check()
            elif command == "fly" and ok:
                flown = True
                fly(drones, export, ids, phase)
                break
    except KeyboardInterrupt:
        if flown:
            land_and_stop(drones)
    finally:
        telemetry.stop()
        disconnect(drones)
        phase("closed")


def cmd_fly(args, config):
    from lhrunner import preflight

    export, names, positions = load_export(args.export)
    ids, starts = choose_ids(export, names, positions, args.ids, ask=True)

    if not args.skip_sim:
        found = sim_precheck(args, ids)
        if found and not confirm("The dry run found problems. Fly anyway?"):
            return

    def on_fault(d, reason):
        runtime.request_shutdown(f"fault: {d.prefix}: {reason}")

    drones = connect(config, ids, on_fault)
    try:
        print("preflight:")
        if not preflight.run(drones, starts, config, uses_led(export)):
            print("preflight failed: fix the FAIL lines and try again")
            return
        if not args.yes and not confirm(f"Ready: fly {', '.join(d.prefix for d in drones)}?", word="fly"):
            return

        fly(drones, export, ids)
    except KeyboardInterrupt:
        land_and_stop(drones)
    finally:
        disconnect(drones)


def cmd_check(args, config):
    from lhrunner import preflight

    starts, led = None, False
    if args.export:
        export, names, positions = load_export(args.export)
        ids, starts = choose_ids(export, names, positions, args.ids, ask=not args.ids)
        led = uses_led(export)
    elif args.ids:
        ids = parse_ids(args.ids)
    else:
        raise SystemExit("give --ids, or an export to check its drones and start positions")
    drones = connect(config, ids, lambda d, reason: None)
    try:
        print("preflight:")
        ok = preflight.run(drones, starts, config, led)
        print("ready to fly" if ok else "preflight failed")
    finally:
        disconnect(drones)


def cmd_upload_geometry(args, config):
    """Writes one Lighthouse system config (saved from cfclient) to every drone, so they share a frame"""
    from cflib.localization.lighthouse_config_manager import LighthouseConfigWriter

    ids = parse_ids(args.ids) if args.ids else sorted(config.drones)
    if not ids:
        raise SystemExit("give --ids or list the drones in the fleet file")
    drones = connect(config, ids, lambda d, reason: None)
    try:
        for d in drones:
            done = threading.Event()
            result = {}

            def stored(success):
                result["ok"] = success
                done.set()

            LighthouseConfigWriter(d.cf).write_and_store_config_from_file(stored, args.config_file)
            ok = done.wait(20) and result.get("ok")
            print(f"  {d.prefix:<6} {'stored' if ok else 'FAILED'}", flush=True)
    finally:
        disconnect(drones)


def confirm(question, word="y"):
    try:
        answer = input(f"{question} [type {word}]: ").strip().lower()
    except EOFError:
        return False
    return answer == word


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fleet", help="fleet file (default: fleet.yaml here, made from fleet.example.yaml)")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dry-run", help="simulate the export and plot it; no radio")
    p.add_argument("export")
    p.add_argument("--ids", help="Crazyflie IDs in the order of starting_positions.yaml, e.g. 7,8")
    p.add_argument("--speed", type=float, default=4.0, help="how much faster than real time (default 4)")
    p.add_argument("--max-time", type=float, default=600.0, help="stop a show still going after this (s)")
    p.add_argument("--no-plot", action="store_true")
    p.add_argument("--no-show", action="store_true", help="save the plot without opening a window")
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--json-full", action="store_true", help=argparse.SUPPRESS)

    p = sub.add_parser("fly", help="preflight, then fly the export")
    p.add_argument("export")
    p.add_argument("--ids", help="Crazyflie IDs in the order of starting_positions.yaml (default: ask)")
    p.add_argument("--skip-sim", action="store_true", help="don't dry-run the project first")
    p.add_argument("--yes", action="store_true", help="fly without asking once the preflight passes")

    p = sub.add_parser("session", help=argparse.SUPPRESS)
    p.add_argument("export")
    p.add_argument("--ids", required=True)

    p = sub.add_parser("check", help="preflight only")
    p.add_argument("export", nargs="?")
    p.add_argument("--ids")

    p = sub.add_parser("upload-geometry", help="write a cfclient Lighthouse system config to every drone")
    p.add_argument("config_file")
    p.add_argument("--ids")

    args = ap.parse_args()
    args.fleet = args.fleet or str(fleet_config.local_fleet(ROOT))
    config = fleet_config.load(args.fleet)
    {"dry-run": cmd_dry_run, "fly": cmd_fly, "check": cmd_check, "session": cmd_session,
     "upload-geometry": cmd_upload_geometry}[args.command](args, config)


if __name__ == "__main__":
    main()
