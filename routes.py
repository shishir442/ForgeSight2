"""api/routes.py — All ForgeSight API endpoints."""
import asyncio, json, uuid
from datetime import datetime
from typing import AsyncGenerator
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

from config import MACHINES, RISK_WARN, RISK_ALERT, RISK_CRIT
from simulator.data_source import stream_machine
from core.baseline import baseline_registry
from core.noise_filter import filter_registry
from core.anomaly_detector import detector_registry
from core.alert_engine import alert_engine

router = APIRouter()

_latest  = {mid: None for mid in MACHINES}
_risk    = {mid: {"score": 0.0, "level": "NORMAL"} for mid in MACHINES}
_tickets = []


class MaintenanceIn(BaseModel):
    machine_id:   str
    machine_name: str
    priority:     str
    reason:       str
    scheduled_by: str = "ForgeSight-Agent"
    timestamp:    str


async def _pipeline(machine_id: str, raw: dict) -> dict:
    bl         = baseline_registry.get(machine_id)
    deviations = bl.deviations(raw)
    sigmas     = bl.sigma_dists(raw)
    baseline_registry.push(machine_id, raw)
    filtered   = filter_registry.process(machine_id, raw, deviations)
    result     = detector_registry.analyze(machine_id, filtered, sigmas)
    alert      = alert_engine.process(result)
    _latest[machine_id] = {**raw, "smoothed": filtered["smoothed"]}
    _risk[machine_id]   = {"score": result["risk_score"], "level": result["risk_level"]}
    return {
        "machine_id":  machine_id,
        "machine_name":MACHINES[machine_id]["name"],
        "timestamp":   raw["timestamp"],
        "sensors":     {s: raw[s] for s in ["vibration","temperature","rpm","current"]},
        "smoothed":    filtered["smoothed"],
        "baseline":    {s: {"lower": bl.sensors[s].envelope[0], "upper": bl.sensors[s].envelope[1]}
                        for s in ["vibration","temperature","rpm","current"]},
        "risk_score":  result["risk_score"],
        "risk_level":  result["risk_level"],
        "triggered":   result["triggered_sensors"],
        "is_compound": result["is_compound"],
        "ml_score":    result["ml_score"],
        "model_loaded":result["model_loaded"],
        "alert":       alert,
    }


@router.get("/stream/{machine_id}", tags=["SSE"])
async def sse_machine(machine_id: str):
    if machine_id not in MACHINES:
        raise HTTPException(404, f"Unknown machine: {machine_id}")
    async def gen():
        async for raw in stream_machine(machine_id):
            payload = await _pipeline(machine_id, raw)
            yield {"data": json.dumps(payload)}
    return EventSourceResponse(gen())


@router.get("/alerts/stream", tags=["SSE"])
async def sse_alerts():
    seen = set()
    async def gen():
        while True:
            for a in alert_engine.recent(50):
                if a["alert_id"] not in seen:
                    seen.add(a["alert_id"])
                    yield {"event": "alert", "data": json.dumps(a)}
            await asyncio.sleep(0.5)
    return EventSourceResponse(gen())


@router.get("/dashboard", tags=["REST"])
async def dashboard():
    machines_out = []
    for mid, cfg in MACHINES.items():
        bl = baseline_registry.get(mid)
        machines_out.append({
            "machine_id":   mid, "machine_name": cfg["name"],
            "risk_score":   _risk[mid]["score"], "risk_level": _risk[mid]["level"],
            "last_reading": _latest[mid], "baseline": bl.stats_dict(),
        })
    max_risk = max(_risk[m]["score"] for m in MACHINES)
    health   = "RED" if max_risk >= RISK_CRIT else "YELLOW" if max_risk >= RISK_WARN else "GREEN"
    return {"snapshot_time": datetime.utcnow().isoformat()+"Z",
            "machines": machines_out, "recent_alerts": alert_engine.recent(10),
            "system_health": health}


@router.get("/machines/{machine_id}", tags=["REST"])
async def machine_status(machine_id: str):
    if machine_id not in MACHINES:
        raise HTTPException(404)
    bl = baseline_registry.get(machine_id)
    return {"machine_id": machine_id, "machine_name": MACHINES[machine_id]["name"],
            "risk_score": _risk[machine_id]["score"], "risk_level": _risk[machine_id]["level"],
            "last_reading": _latest[machine_id], "baseline": bl.stats_dict(),
            "alerts": alert_engine.for_machine(machine_id)}


@router.get("/alerts", tags=["REST"])
async def get_alerts(limit: int = 20):
    return {"alerts": alert_engine.recent(limit)}


@router.post("/schedule-maintenance", tags=["REST"])
async def schedule_maintenance(req: MaintenanceIn):
    ticket_id = f"FS-{str(uuid.uuid4())[:6].upper()}"
    ticket = {"ticket_id": ticket_id, "status": "SCHEDULED",
              "machine_id": req.machine_id, "machine_name": req.machine_name,
              "priority": req.priority, "reason": req.reason,
              "scheduled_by": req.scheduled_by, "timestamp": req.timestamp}
    _tickets.append(ticket)
    print(f"[Maintenance] 🔧 {ticket_id} | {req.machine_name} | {req.priority}")
    return {"ticket_id": ticket_id, "status": "SCHEDULED",
            "message": f"Work order {ticket_id} created — {req.machine_name} ({req.priority})"}


@router.get("/maintenance", tags=["REST"])
async def get_tickets():
    return {"tickets": list(reversed(_tickets[-20:]))}
