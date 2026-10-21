"""Records simulated drones during a dry run, then checks the paths: fence, separation, where the show ends."""

import threading
import time
from itertools import combinations

import numpy as np

from . import runtime

SAMPLE_HZ = 20
AIRBORNE_Z = 0.1


class Recorder:
    def __init__(self, drones, max_time):
        self.drones = drones
        self.max_time = max_time
        self.times = []
        self.positions = []   # per sample: [n_drones, 3]
        self.leds = []        # per sample: per drone (r, g, b) or None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        t0 = self.t0 = runtime.clock.now()
        while not runtime.stopping():
            now = runtime.clock.now()
            self.times.append(now - t0)
            self.positions.append(np.array([d.position() for d in self.drones]))
            self.leds.append([d.led for d in self.drones])
            if now - t0 > self.max_time:
                print(f"dry run: still going after {self.max_time:.0f} s, stopping it", flush=True)
                runtime.request_shutdown("timeout")
            time.sleep(runtime.clock.real_delay(1 / SAMPLE_HZ))

    def join(self):
        self._thread.join(timeout=2)

    def trim(self, lead=0.3):
        """Starts the recording `lead` s before the first command, dropping the time spent importing the export,
        so t=0 is when the show starts (as for a real flight)"""
        first = min((d.events[0][0] for d in self.drones if d.events), default=None)
        if first is None or not self.times:
            return
        offset = max(0.0, first - self.t0 - lead)
        keep = [i for i, t in enumerate(self.times) if t >= offset]
        self.times = [self.times[i] - offset for i in keep]
        self.positions = [self.positions[i] for i in keep]
        self.leds = [self.leds[i] for i in keep]
        self.t0 += offset

    def export(self, starts):
        """The recorded show for the dashboard to play back"""
        positions = np.array(self.positions) if self.positions else np.zeros((0, len(self.drones), 3))
        drones = []
        for i, d in enumerate(self.drones):
            leds, last = [], None
            for t, led in zip(self.times, (sample[i] for sample in self.leds)):
                if led != last:
                    leds.append([round(t, 2), led])
                    last = led
            drones.append({"cf": d.prefix, "id": d.id, "start": list(starts[d.id]),
                           "p": positions[:, i].round(3).tolist(), "leds": leds,
                           "events": [[round(t - self.t0, 2), kind, detail] for t, kind, detail in d.events
                                      if kind != "led"]})
        return {"times": [round(t, 3) for t in self.times], "drones": drones}


def analyze(recorder, config):
    drones = recorder.drones
    positions = np.array(recorder.positions) if recorder.positions else np.zeros((0, len(drones), 3))
    times = recorder.times
    summary = {
        "reason": runtime.shutdown_reason,
        "duration": round(times[-1], 2) if times else 0.0,
        "violations": [],
        "min_separation": None,
        "close_calls": [],
        "airborne_at_end": [],
    }
    for d in drones:
        for t, command, target in d.violations:
            summary["violations"].append({"cf": d.prefix, "command": command, "target": target})
    for i, d in enumerate(drones):
        outside = [k for k in range(len(times)) if not config.fence.contains(positions[k, i])]
        if outside and not any(v["cf"] == d.prefix for v in summary["violations"]):
            summary["violations"].append({"cf": d.prefix, "command": "path",
                                          "target": positions[outside[0], i].round(2).tolist()})
    best = None
    for (i, a), (j, b) in combinations(enumerate(drones), 2):
        both_up = (positions[:, i, 2] > AIRBORNE_Z) & (positions[:, j, 2] > AIRBORNE_Z)
        if not both_up.any():
            continue
        dist = np.linalg.norm(positions[:, i] - positions[:, j], axis=1)
        dist[~both_up] = np.inf
        k = int(np.argmin(dist))
        if best is None or dist[k] < best[0]:
            best = (float(dist[k]), times[k], a.prefix, b.prefix)
        if dist[k] < config.min_separation:
            summary["close_calls"].append({"pair": [a.prefix, b.prefix], "distance": round(float(dist[k]), 2),
                                           "time": round(times[k], 2)})
    if best is not None:
        summary["min_separation"] = {"distance": round(best[0], 2), "time": round(best[1], 2),
                                     "pair": [best[2], best[3]]}
    if len(positions):
        summary["airborne_at_end"] = [d.prefix for i, d in enumerate(drones) if positions[-1, i, 2] > AIRBORNE_Z]
    return summary


def problems(summary):
    """What should stop a real flight until someone has looked at it"""
    out = []
    if summary["reason"] not in ("finished", None):
        out.append(f"the dry run ended with: {summary['reason']}")
    for v in summary["violations"]:
        out.append(f"{v['cf']}: {v['command']} to {v['target']} is outside the fence (it would be clamped)")
    for c in summary["close_calls"]:
        out.append(f"{c['pair'][0]} and {c['pair'][1]} come within {c['distance']} m at t={c['time']} s")
    return out


def print_summary(summary):
    print(f"dry run: {summary['reason']} after {summary['duration']} s of show time")
    sep = summary["min_separation"]
    if sep:
        print(f"  closest approach: {sep['pair'][0]}-{sep['pair'][1]} {sep['distance']} m at t={sep['time']} s")
    if summary["airborne_at_end"]:
        print(f"  still airborne at the end (the runner lands them): {', '.join(summary['airborne_at_end'])}")
    found = problems(summary)
    for p in found:
        print(f"  WARN {p}")
    if not found:
        print("  no fence violations or close calls")


def plot(recorder, config, starts, path, show):
    try:
        import matplotlib
        if not show:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib isn't installed: pip install matplotlib for the dry-run plot")
        return
    positions = np.array(recorder.positions)
    if not len(positions):
        return
    times = np.array(recorder.times)
    fig = plt.figure(figsize=(12, 6))
    ax3 = fig.add_subplot(1, 2, 1, projection="3d")
    axz = fig.add_subplot(1, 2, 2)
    palette = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    for i, d in enumerate(recorder.drones):
        color = palette[i % len(palette)]
        p = positions[:, i]
        ax3.plot(p[:, 0], p[:, 1], p[:, 2], color=color, lw=1.5, label=d.prefix)
        # LED color along the path, where the project sets one
        lit = [k for k, leds in enumerate(recorder.leds) if leds[i] is not None and any(leds[i])]
        if lit:
            ax3.scatter(p[lit, 0], p[lit, 1], p[lit, 2], s=6,
                        c=[np.array(recorder.leds[k][i]) / 255 for k in lit], depthshade=False)
        start = starts.get(d.id)
        if start is not None:
            ax3.scatter(*start, color=color, marker="s", s=40)
        axz.plot(times, p[:, 2], color=color, label=d.prefix)
    lo, hi = config.fence.lo, config.fence.hi
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            ax3.plot([x, x], [y, y], [lo[2], hi[2]], color="0.7", lw=0.6)
        for z in (lo[2], hi[2]):
            ax3.plot([x, x], [lo[1], hi[1]], [z, z], color="0.7", lw=0.6)
    for y in (lo[1], hi[1]):
        for z in (lo[2], hi[2]):
            ax3.plot([lo[0], hi[0]], [y, y], [z, z], color="0.7", lw=0.6)
    ax3.set_xlabel("x (m)")
    ax3.set_ylabel("y (m)")
    ax3.set_zlabel("z (m)")
    ax3.set_title("Paths (squares: start, gray box: fence)")
    ax3.legend(loc="upper left")
    axz.set_xlabel("show time (s)")
    axz.set_ylabel("z (m)")
    axz.set_title("Height")
    axz.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    print(f"plot saved to {path}")
    if show:
        plt.show()
