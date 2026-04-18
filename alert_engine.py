"""core/alert_engine.py — Pattern-matched plain-English alerts + maintenance API."""
import uuid, asyncio, httpx
from datetime import datetime
from typing import Dict, List, Optional
from config import MACHINES, RISK_WARN, RISK_ALERT, RISK_CRIT

PATTERNS = [
    (frozenset({"vibration","temperature","rpm"}), "Probable bearing wear — vibration + heat + RPM drop",       "Inspect and lubricate bearings. Schedule replacement within 4 hours."),
    (frozenset({"vibration","temperature","current"}), "Electrical-mechanical fault — current + vibration + heat", "Check motor windings and coupling. Reduce load immediately."),
    (frozenset({"temperature","rpm","current"}),  "Thermal stress with load imbalance",                          "Verify coolant flow. Inspect VFD for faults."),
    (frozenset({"vibration","rpm"}),              "Mechanical looseness or misalignment",                        "Check shaft alignment and fastener torques."),
    (frozenset({"temperature","current"}),        "Electrical overload — excessive current with heat",           "Check electrical balance. Inspect contactor."),
    (frozenset({"vibration","temperature"}),      "Bearing or lubrication issue",                                "Check lube levels and bearing temps with thermal camera."),
    (frozenset({"rpm","current"}),                "Drive or load anomaly — RPM/current mismatch",               "Inspect VFD and mechanical load. Check belt or coupling."),
    (frozenset({"vibration"}),                    "Elevated vibration on single sensor",                         "Run vibration analysis. Check for imbalance or looseness."),
    (frozenset({"temperature"}),                  "Thermal elevation above baseline",                            "Check ventilation and coolant levels."),
    (frozenset({"rpm"}),                          "RPM deviation — possible drive issue",                        "Inspect VFD output and mechanical load."),
    (frozenset({"current"}),                      "Abnormal current draw",                                       "Check motor leads and downstream load."),
]

def diagnose(triggered):
    ts  = frozenset(triggered)
    best, best_size = None, 0
    for pat, diag, action in PATTERNS:
        if pat.issubset(ts) and len(pat) > best_size:
            best, best_size = (diag, action), len(pat)
    return best or (f"Anomaly: {', '.join(triggered)}", "Perform full sensor inspection.")


class AlertEngine:
    COOLDOWN = 30

    def __init__(self):
        self._last: Dict[str, datetime] = {}
        self._log:  List[dict] = []

    def _cooldown(self, mid):
        last = self._last.get(mid)
        return last and (datetime.utcnow() - last).total_seconds() < self.COOLDOWN

    def process(self, result: dict) -> Optional[dict]:
        risk      = result["risk_score"]
        mid       = result["machine_id"]
        triggered = result["triggered_sensors"]
        if risk < RISK_WARN or not triggered or self._cooldown(mid):
            return None

        sev = "CRITICAL" if risk >= RISK_CRIT else "ALERT" if risk >= RISK_ALERT else "WARN"
        diag, action = diagnose(triggered)

        alert = {
            "alert_id":          str(uuid.uuid4())[:8].upper(),
            "machine_id":        mid,
            "machine_name":      result["machine_name"],
            "timestamp":         result["timestamp"],
            "severity":          sev,
            "risk_score":        risk,
            "sensors_triggered": triggered,
            "diagnosis":         diag,
            "recommended_action":action,
            "is_compound":       result["is_compound"],
        }
        self._last[mid] = datetime.utcnow()
        self._log.append(alert)
        if len(self._log) > 100: self._log.pop(0)
        print(f"[Alert] 🚨 {sev} {mid} risk={risk} | {diag[:60]}")

        if sev in ("ALERT","CRITICAL"):
            priority = {"ALERT":"HIGH","CRITICAL":"URGENT"}[sev]
            asyncio.create_task(self._post_maintenance(mid, result["machine_name"], priority, diag))

        return alert

    async def _post_maintenance(self, mid, name, priority, reason):
        payload = {"machine_id": mid, "machine_name": name, "priority": priority,
                   "reason": reason, "scheduled_by": "ForgeSight-Agent",
                   "timestamp": datetime.utcnow().isoformat()+"Z"}
        try:
            async with httpx.AsyncClient() as c:
                await c.post("http://localhost:8000/schedule-maintenance", json=payload, timeout=5)
        except Exception:
            pass

    def recent(self, n=20): return list(reversed(self._log[-n:]))
    def for_machine(self, mid, n=10):
        return list(reversed([a for a in self._log if a["machine_id"]==mid][-n:]))


alert_engine = AlertEngine()
