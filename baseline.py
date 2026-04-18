"""core/baseline.py — Rolling μ±2σ adaptive baseline per machine per sensor."""
import collections
import numpy as np
from typing import Dict, Tuple
from config import MACHINES, SENSORS, BASELINE_WINDOW, SIGMA_THRESHOLD, MIN_BASELINE_POINTS


class SensorBaseline:
    def __init__(self, sensor, machine_id):
        self.sensor     = sensor
        self.machine_id = machine_id
        self._buf       = collections.deque(maxlen=BASELINE_WINDOW)

    def push(self, v): self._buf.append(float(v))

    @property
    def warm(self): return len(self._buf) >= MIN_BASELINE_POINTS

    @property
    def stats(self) -> Tuple[float, float]:
        if not self.warm:
            cfg = MACHINES[self.machine_id]["normal"][self.sensor]
            mid = (cfg["min"] + cfg["max"]) / 2
            return mid, (cfg["max"] - cfg["min"]) / 4
        a = np.array(self._buf)
        return float(a.mean()), float(a.std(ddof=1))

    @property
    def envelope(self) -> Tuple[float, float]:
        m, s = self.stats
        return m - SIGMA_THRESHOLD * s, m + SIGMA_THRESHOLD * s

    def is_deviant(self, v: float) -> bool:
        lo, hi = self.envelope
        return v < lo or v > hi

    def sigma_dist(self, v: float) -> float:
        m, s = self.stats
        return abs(v - m) / s if s else 0.0

    def to_dict(self):
        m, s = self.stats
        lo, hi = self.envelope
        return {"sensor": self.sensor, "mean": round(m, 3), "std": round(s, 3),
                "lower": round(lo, 3), "upper": round(hi, 3), "n": len(self._buf)}


class MachineBaseline:
    def __init__(self, machine_id):
        self.machine_id = machine_id
        self.sensors    = {s: SensorBaseline(s, machine_id) for s in SENSORS}

    def push(self, reading):
        for s in SENSORS:
            if s in reading: self.sensors[s].push(reading[s])

    def deviations(self, reading) -> Dict[str, bool]:
        return {s: self.sensors[s].is_deviant(reading[s]) for s in SENSORS if s in reading}

    def sigma_dists(self, reading) -> Dict[str, float]:
        return {s: self.sensors[s].sigma_dist(reading[s]) for s in SENSORS if s in reading}

    def stats_dict(self):
        return {s: self.sensors[s].to_dict() for s in SENSORS}


class BaselineRegistry:
    def __init__(self):
        self._bl = {mid: MachineBaseline(mid) for mid in MACHINES}

    def seed(self, machine_id, history):
        for r in history: self._bl[machine_id].push(r)
        print(f"[Baseline] {machine_id}: seeded {len(history):,} readings")

    def push(self, machine_id, reading):
        self._bl[machine_id].push(reading)

    def get(self, machine_id) -> MachineBaseline:
        return self._bl[machine_id]


baseline_registry = BaselineRegistry()
