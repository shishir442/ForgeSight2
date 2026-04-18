"""
core/anomaly_detector.py
─────────────────────────────────────────────────────────────
Loads trained Isolation Forest models from models/ folder.
Falls back to pure statistical detection if models not yet trained.

Risk Score formula:
  60% × ML model score       (primary signal)
  30% × compound sensor count
  10% × σ-distance magnitude
"""
import os, json, pickle
import numpy as np
from typing import Dict, List
from config import MACHINES, SENSORS, COMPOUND_MIN, RISK_WARN, RISK_ALERT, RISK_CRIT

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")


class MLModel:
    def __init__(self, machine_id: str):
        self.mid        = machine_id
        self.model      = None
        self.scaler     = None
        self.features   = []
        self.loaded     = False
        self._load()

    def _load(self):
        mp = os.path.join(MODELS_DIR, f"model_{self.mid}.pkl")
        sp = os.path.join(MODELS_DIR, f"scaler_{self.mid}.pkl")
        ep = os.path.join(MODELS_DIR, f"meta_{self.mid}.json")
        if not os.path.exists(mp):
            print(f"[ML] ⚠  No model for {self.mid} — run: python training/train_model.py")
            return
        try:
            with open(mp,"rb") as f: self.model  = pickle.load(f)
            with open(sp,"rb") as f: self.scaler = pickle.load(f)
            if os.path.exists(ep):
                with open(ep) as f: self.features = json.load(f).get("feature_names",[])
            self.loaded = True
            print(f"[ML] ✅ Loaded model: {self.mid} ({len(self.features)} features)")
        except Exception as e:
            print(f"[ML] ❌ Load error {self.mid}: {e}")

    def _featurize(self, reading: dict, history: List[dict]) -> np.ndarray:
        win  = (history or [])[-9:] + [reading]
        vibs = [r.get("vibration",   0) for r in win]
        tmps = [r.get("temperature", 0) for r in win]
        rpms = [r.get("rpm",         0) for r in win]
        curs = [r.get("current",     0) for r in win]
        std  = lambda a: float(np.std(a)) if len(a)>1 else 0.0
        v=reading.get("vibration",0); t=reading.get("temperature",0)
        r=reading.get("rpm",0);       c=reading.get("current",0)
        feat = {
            "vibration":v,"temperature":t,"rpm":r,"current":c,
            "vibration_roll_mean":   float(np.mean(vibs)),
            "temperature_roll_mean": float(np.mean(tmps)),
            "rpm_roll_mean":         float(np.mean(rpms)),
            "current_roll_mean":     float(np.mean(curs)),
            "vibration_roll_std":    std(vibs),
            "temperature_roll_std":  std(tmps),
            "rpm_roll_std":          std(rpms),
            "current_roll_std":      std(curs),
            "vibration_diff":   vibs[-1]-vibs[-2] if len(vibs)>1 else 0,
            "temperature_diff": tmps[-1]-tmps[-2] if len(tmps)>1 else 0,
            "rpm_diff":         rpms[-1]-rpms[-2] if len(rpms)>1 else 0,
            "current_diff":     curs[-1]-curs[-2] if len(curs)>1 else 0,
            "temp_per_rpm":   t/max(r,1),
            "vib_x_current":  v*c,
            "temp_per_vib":   t/max(v,0.001),
        }
        vec = [feat.get(fn,0.0) for fn in self.features] if self.features else list(feat.values())
        return np.array(vec, dtype=np.float64).reshape(1,-1)

    def score(self, reading: dict, history: List[dict]) -> float:
        """Returns 0.0 (normal) → 1.0 (anomaly)."""
        if not self.loaded: return 0.0
        try:
            X  = self.scaler.transform(self._featurize(reading, history))
            rs = float(self.model.score_samples(X)[0])
            # score_samples: ~-0.1 normal, ~-0.7 anomaly → normalize to [0,1]
            return float(np.clip((-rs - 0.1) / 0.6, 0.0, 1.0))
        except Exception as e:
            print(f"[ML] Inference error {self.mid}: {e}")
            return 0.0


class Detector:
    def __init__(self, machine_id: str):
        self.mid         = machine_id
        self.name        = MACHINES[machine_id]["name"]
        self.ml          = MLModel(machine_id)
        self._history: List[dict] = []

    def analyze(self, filtered: dict, sigma_dists: Dict[str,float]) -> dict:
        smoothed   = filtered["smoothed"]
        escalate   = filtered["escalate"]

        ml_score   = self.ml.score(smoothed, self._history)
        self._history.append(smoothed)
        if len(self._history) > 20: self._history.pop(0)

        triggered   = [s for s,f in escalate.items() if f]
        n_trig      = len(triggered)
        is_compound = n_trig >= COMPOUND_MIN

        avg_sigma       = float(np.mean([sigma_dists.get(s,0) for s in triggered])) if triggered else 0.0
        sigma_contrib   = min(avg_sigma / 5.0, 1.0)
        compound_contrib= min(n_trig / len(SENSORS), 1.0)

        risk = round(min(ml_score*60 + compound_contrib*30 + sigma_contrib*10, 100.0), 1)

        if   risk >= RISK_CRIT:  level = "CRITICAL"
        elif risk >= RISK_ALERT: level = "ALERT"
        elif risk >= RISK_WARN:  level = "WARN"
        else:                    level = "NORMAL"

        return {
            "machine_id": self.mid, "machine_name": self.name,
            "timestamp":  filtered["timestamp"],
            "risk_score": risk, "risk_level": level,
            "triggered_sensors": triggered, "is_compound": is_compound,
            "ml_score": round(ml_score, 3), "model_loaded": self.ml.loaded,
            "sigma_dists": {s: round(v,2) for s,v in sigma_dists.items()},
            "smoothed": smoothed,
        }


class DetectorRegistry:
    def __init__(self):
        self._d = {mid: Detector(mid) for mid in MACHINES}

    def analyze(self, machine_id, filtered, sigma_dists):
        return self._d[machine_id].analyze(filtered, sigma_dists)


detector_registry = DetectorRegistry()
