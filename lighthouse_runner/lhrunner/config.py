import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml


@dataclass
class Fence:
    lo: np.ndarray
    hi: np.ndarray

    def contains(self, p, margin=0.0):
        p = np.asarray(p, dtype=float)
        return bool(np.all(p >= self.lo - margin) and np.all(p <= self.hi + margin))

    def clamp(self, p):
        return np.clip(np.asarray(p, dtype=float), self.lo, self.hi)


@dataclass
class Config:
    drones: dict                      # id -> radio URI
    uri_template: str
    fence: Fence
    kill_outside_fence: float | None  # m beyond the fence before the motors are cut; None: never
    tracking_loss_land_s: float | None
    land_on_low_battery: bool
    min_battery_v: float
    start_tolerance: float
    min_separation: float
    pose_period_ms: int
    params: dict = field(default_factory=dict)

    def uri(self, cf_id):
        if cf_id in self.drones:
            return self.drones[cf_id]
        if not self.uri_template:
            raise SystemExit(f"no radio URI for Crazyflie {cf_id}: add it under drones: in the fleet file")
        return self.uri_template.format(id=cf_id)


def local_fleet(folder):
    """fleet.yaml in folder, copied from fleet.example.yaml the first time. It holds this setup's drones, so git
    ignores it."""
    path = Path(folder) / "fleet.yaml"
    example = Path(folder) / "fleet.example.yaml"
    if not path.exists() and example.exists():
        shutil.copyfile(example, path)
    return path


def load(path):
    data = yaml.safe_load(Path(path).read_text()) or {}
    fence = data.get("fence") or {}
    safety = data.get("safety") or {}
    lo = np.array([fence.get(axis, [-1.5, 1.5])[0] for axis in "xy"] + [fence.get("z", [0.0, 2.0])[0]], dtype=float)
    hi = np.array([fence.get(axis, [-1.5, 1.5])[1] for axis in "xy"] + [fence.get("z", [0.0, 2.0])[1]], dtype=float)
    return Config(
        drones={int(k): str(v) for k, v in (data.get("drones") or {}).items()},
        uri_template=data.get("uri_template", ""),
        fence=Fence(lo, hi),
        kill_outside_fence=safety.get("kill_outside_fence", 0.4),
        tracking_loss_land_s=safety.get("land_on_tracking_loss", 1.0),
        land_on_low_battery=safety.get("land_on_low_battery", True),
        min_battery_v=float(safety.get("min_battery_v", 3.8)),
        start_tolerance=float(safety.get("start_tolerance", 0.3)),
        min_separation=float(safety.get("min_separation", 0.3)),
        pose_period_ms=int(data.get("pose_period_ms", 50)),
        params=data.get("params") or {},
    )
