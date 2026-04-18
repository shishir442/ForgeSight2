"""core/noise_filter.py — Moving average + transient suppression."""
import collections
from typing import Dict
from config import MACHINES, SENSORS, MOVING_AVG_WINDOW, SUPPRESS_COUNT


class SensorFilter:
    def __init__(self, sensor, machine_id):
        self.sensor     = sensor
        self.machine_id = machine_id
        self._buf       = collections.deque(maxlen=MOVING_AVG_WINDOW)
        self._consec    = 0

    def smooth(self, v: float) -> float:
        self._buf.append(v)
        return sum(self._buf) / len(self._buf)

    def should_escalate(self, deviant: bool) -> bool:
        self._consec = (self._consec + 1) if deviant else 0
        return self._consec >= SUPPRESS_COUNT

    @property
    def consec(self): return self._consec


class MachineFilter:
    def __init__(self, machine_id):
        self.machine_id = machine_id
        self._f         = {s: SensorFilter(s, machine_id) for s in SENSORS}

    def process(self, raw: dict, deviations: Dict[str, bool]) -> dict:
        smoothed, escalate, consec = {}, {}, {}
        for s in SENSORS:
            if s not in raw: continue
            smoothed[s]  = round(self._f[s].smooth(raw[s]), 3)
            escalate[s]  = self._f[s].should_escalate(deviations.get(s, False))
            consec[s]    = self._f[s].consec
        return {"machine_id": raw["machine_id"], "timestamp": raw["timestamp"],
                "raw": {s: raw[s] for s in SENSORS if s in raw},
                "smoothed": smoothed, "escalate": escalate, "consec": consec}


class FilterRegistry:
    def __init__(self):
        self._f = {mid: MachineFilter(mid) for mid in MACHINES}

    def process(self, machine_id, raw, deviations):
        return self._f[machine_id].process(raw, deviations)


filter_registry = FilterRegistry()
