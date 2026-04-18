"""
config.py — ForgeSight Central Config
Data source: https://github.com/Jnanik-AI/malendau-hackathon

Machine IDs and field names taken exactly from hackathon server README:
  Machine IDs : CNC_01, CNC_02, PUMP_03, CONVEYOR_04
  SSE fields  : temperature_C, vibration_mm_s, rpm, current_A, status
  History URL : GET http://localhost:3000/history/{machine_id}
  Stream URL  : GET http://localhost:3000/stream/{machine_id}
"""

# ── Hackathon data server ─────────────────────────────────────────────────────
HACKATHON_URL = "http://localhost:3000"

# ── Machines (IDs match hackathon server exactly) ─────────────────────────────
MACHINES = {
    "CNC_01": {
        "name":            "CNC Mill",
        "failure_pattern": "Bearing wear — vibration + temp gradually rise",
        "normal": {
            "vibration":   {"min": 0.5,  "max": 3.5},
            "temperature": {"min": 60.0, "max": 95.0},
            "rpm":         {"min": 1400, "max": 1560},
            "current":     {"min": 10.0, "max": 16.0},
        },
    },
    "CNC_02": {
        "name":            "CNC Lathe",
        "failure_pattern": "Thermal runaway — afternoon temperature spikes",
        "normal": {
            "vibration":   {"min": 0.3,  "max": 3.0},
            "temperature": {"min": 55.0, "max": 90.0},
            "rpm":         {"min": 1400, "max": 1560},
            "current":     {"min": 9.0,  "max": 15.0},
        },
    },
    "PUMP_03": {
        "name":            "Pump",
        "failure_pattern": "Cavitation + slow RPM drop (developing clog)",
        "normal": {
            "vibration":   {"min": 0.5,  "max": 4.0},
            "temperature": {"min": 45.0, "max": 78.0},
            "rpm":         {"min": 1440, "max": 1510},
            "current":     {"min": 8.0,  "max": 14.0},
        },
    },
    "CONVEYOR_04": {
        "name":            "Conveyor Belt",
        "failure_pattern": "Mostly healthy — use as baseline reference",
        "normal": {
            "vibration":   {"min": 0.1,  "max": 1.8},
            "temperature": {"min": 35.0, "max": 60.0},
            "rpm":         {"min": 575,  "max": 625},
            "current":     {"min": 4.5,  "max": 9.0},
        },
    },
}

# Hackathon server field names → our internal names
FIELD_MAP = {
    "temperature_C":  "temperature",
    "vibration_mm_s": "vibration",
    "rpm":            "rpm",
    "current_A":      "current",
}

SENSORS = ["vibration", "temperature", "rpm", "current"]

# ── Baseline engine ───────────────────────────────────────────────────────────
BASELINE_WINDOW     = 10080   # 7 days × 1 reading/min
SIGMA_THRESHOLD     = 2.0
MIN_BASELINE_POINTS = 100

# ── Noise filter ──────────────────────────────────────────────────────────────
MOVING_AVG_WINDOW   = 5
SUPPRESS_COUNT      = 3       # consecutive deviant readings before escalating

# ── Risk thresholds ───────────────────────────────────────────────────────────
COMPOUND_MIN        = 2       # how many sensors must trigger together
RISK_WARN           = 40
RISK_ALERT          = 70
RISK_CRIT           = 90

# ── Fallback simulator (if hackathon server offline) ──────────────────────────
SIM_INTERVAL        = 1.0
SIM_ANOMALY_PROB    = 0.008
SIM_ANOMALY_LEN     = 20
SIM_TRANSIENT_PROB  = 0.04
