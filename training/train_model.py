"""
training/train_model.py
═══════════════════════════════════════════════════════════════════
ForgeSight ML Training Pipeline — Hack Malenadu 2026

COMMAND TO RUN:
    python training/train_model.py

DATA SOURCE:
    Fetches 7-day sensor history from the hackathon server:
    GET http://localhost:3000/history/{machine_id}
    → 10,080 readings per machine (1/min × 60min × 24hr × 7days)

    If the server is offline → uses realistic synthetic fallback data.

ALGORITHM: Isolation Forest (sklearn)
    - Unsupervised: no labelled failures needed
    - Learns "normal" from 7-day history
    - Scores each new reading: 0 (normal) → 1 (anomaly)
    - Fast inference: <1ms per reading

FEATURES ENGINEERED (19 per reading):
    4  raw sensor values
    4  10-reading rolling mean
    4  10-reading rolling standard deviation
    4  rate of change (diff vs previous)
    3  cross-sensor interactions

OUTPUT:
    models/model_CNC_01.pkl        ← Trained Isolation Forest
    models/scaler_CNC_01.pkl       ← StandardScaler (for normalization)
    models/meta_CNC_01.json        ← Feature names + training metadata
    (same for CNC_02, PUMP_03, CONVEYOR_04)
"""

import os, sys, json, pickle, requests
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

# Allow importing config from parent dir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MACHINES, HACKATHON_URL, FIELD_MAP, SENSORS

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
os.makedirs(MODELS_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
# STEP 1 — FETCH DATA from hackathon server
# ═══════════════════════════════════════════════════════════════════

def fetch_history(machine_id: str) -> pd.DataFrame:
    """
    Call GET http://localhost:3000/history/{machine_id}
    Returns normalized DataFrame: columns = [vibration, temperature, rpm, current]
    """
    url = f"{HACKATHON_URL}/history/{machine_id}"
    print(f"\n  📡 Fetching: {url}")

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        raw_list = resp.json()
        print(f"  ✅ Received {len(raw_list):,} readings from server")
        return _parse_raw(raw_list, machine_id)

    except requests.exceptions.ConnectionError:
        print(f"  ⚠️  Server offline — using synthetic fallback data")
        return _synthetic_history(machine_id)

    except Exception as e:
        print(f"  ⚠️  Error ({e}) — using synthetic fallback data")
        return _synthetic_history(machine_id)


def _parse_raw(raw_list: list, machine_id: str) -> pd.DataFrame:
    """Normalize hackathon field names (temperature_C → temperature, etc.)"""
    rows = []
    for entry in raw_list:
        row = {}
        for src, dst in FIELD_MAP.items():
            if src in entry:
                row[dst] = float(entry[src])
        # Also accept pre-normalized names
        for s in SENSORS:
            if s not in row and s in entry:
                row[s] = float(entry[s])
        if all(s in row for s in SENSORS):
            rows.append(row)

    df = pd.DataFrame(rows, columns=SENSORS).dropna().clip(lower=0)
    print(f"  📊 Clean rows after parsing: {len(df):,}")
    return df


def _synthetic_history(machine_id: str, n: int = 10080) -> pd.DataFrame:
    """
    Generate realistic synthetic history if hackathon server is offline.
    Mirrors the failure patterns described in the hackathon README:
      CNC_01:      bearing wear  → vibration + temp drift upward
      CNC_02:      thermal spike → temp spikes in last 30% of data
      PUMP_03:     cavitation    → rpm drops + vibration rises
      CONVEYOR_04: healthy       → flat baseline, minimal drift
    """
    rng = np.random.default_rng(seed=abs(hash(machine_id)) % 2**31)
    cfg = MACHINES[machine_id]["normal"]

    drift = {
        "CNC_01":      {"vibration": +0.00025, "temperature": +0.00015},
        "CNC_02":      {"temperature": +0.00030},
        "PUMP_03":     {"rpm": -0.00020, "vibration": +0.00020},
        "CONVEYOR_04": {},
    }.get(machine_id, {})

    rows = []
    for i in range(n):
        row = {}
        for s in SENSORS:
            lo  = cfg[s]["min"]
            hi  = cfg[s]["max"]
            mid = (lo + hi) / 2
            val = rng.normal(mid, (hi - lo) / 6)
            # Apply drift
            if s in drift:
                val += drift[s] * i * (hi - lo)
            # Inject anomalies in last 15%
            if i > int(n * 0.85) and rng.random() < 0.07:
                factor = rng.uniform(1.5, 2.2)
                val = val * factor if s != "rpm" else val * (1 / factor)
            row[s] = round(float(np.clip(val, lo * 0.4, hi * 2.2)), 3)
        rows.append(row)

    df = pd.DataFrame(rows, columns=SENSORS)
    print(f"  📊 Synthetic rows generated: {len(df):,}")
    return df


# ═══════════════════════════════════════════════════════════════════
# STEP 2 — FEATURE ENGINEERING
# ═══════════════════════════════════════════════════════════════════

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build 19 features from 4 raw sensor readings.

    Rolling stats capture TREND (is temperature slowly rising?)
    and VARIABILITY (is vibration becoming erratic?)
    — both are key early warning signals of developing failures.

    Cross-sensor features capture COMPOUND patterns:
    e.g. temp/rpm ratio rises when thermal stress builds under load.
    """
    f = df.copy()

    for s in SENSORS:
        f[f"{s}_roll_mean"] = df[s].rolling(10, min_periods=1).mean()
        f[f"{s}_roll_std"]  = df[s].rolling(10, min_periods=1).std().fillna(0)
        f[f"{s}_diff"]      = df[s].diff().fillna(0)

    # Cross-sensor interactions
    f["temp_per_rpm"]      = f["temperature"] / (f["rpm"].replace(0, 1))
    f["vib_x_current"]     = f["vibration"]   * f["current"]
    f["temp_per_vib"]      = f["temperature"] / (f["vibration"].replace(0, 0.001))

    return f.fillna(0)


# ═══════════════════════════════════════════════════════════════════
# STEP 3 — TRAIN MODEL
# ═══════════════════════════════════════════════════════════════════

def train(X_scaled: np.ndarray) -> IsolationForest:
    """
    Train Isolation Forest.

    contamination=0.05  : assume ~5% of training data has anomalies
                          (failure patterns injected by hackathon server)
    n_estimators=200    : more trees → more stable scores
    n_jobs=-1           : use all CPU cores for speed
    """
    model = IsolationForest(
        n_estimators=200,
        contamination=0.05,
        max_features=1.0,
        max_samples="auto",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_scaled)
    return model


# ═══════════════════════════════════════════════════════════════════
# STEP 4 — EVALUATE
# ═══════════════════════════════════════════════════════════════════

def evaluate(model: IsolationForest, scaler: StandardScaler,
             df_raw: pd.DataFrame, machine_id: str):
    """
    Evaluate model quality:
      1. Report how many training readings are flagged as anomalies
      2. Inject obvious synthetic anomalies and measure recall
    """
    X     = engineer_features(df_raw).values
    Xs    = scaler.transform(X)
    preds = model.predict(Xs)   # 1=normal, -1=anomaly
    score = model.score_samples(Xs)

    n_anom = (preds == -1).sum()
    print(f"\n  📊 Evaluation — {machine_id}")
    print(f"     Readings:    {len(preds):,}")
    print(f"     Anomalies:   {n_anom} ({100*n_anom/len(preds):.1f}%)")
    print(f"     Score range: [{score.min():.3f} → {score.max():.3f}]")

    # Inject obvious anomalies and test recall
    cfg = MACHINES[machine_id]["normal"]
    synthetic = []
    for _ in range(100):
        row = {s: cfg[s]["max"] * np.random.uniform(1.8, 2.8) for s in SENSORS}
        synthetic.append(row)

    df_syn  = pd.DataFrame(synthetic)
    Xsyn    = scaler.transform(engineer_features(df_syn).values)
    recall  = (model.predict(Xsyn) == -1).mean()
    status  = "✅" if recall >= 0.80 else "⚠️ "
    print(f"     Anomaly recall: {recall*100:.0f}%  {status}")


# ═══════════════════════════════════════════════════════════════════
# STEP 5 — SAVE
# ═══════════════════════════════════════════════════════════════════

def save(model, scaler, machine_id: str, feature_cols: list):
    model_path  = os.path.join(MODELS_DIR, f"model_{machine_id}.pkl")
    scaler_path = os.path.join(MODELS_DIR, f"scaler_{machine_id}.pkl")
    meta_path   = os.path.join(MODELS_DIR, f"meta_{machine_id}.json")

    with open(model_path,  "wb") as f: pickle.dump(model,  f)
    with open(scaler_path, "wb") as f: pickle.dump(scaler, f)
    with open(meta_path,   "w")  as f:
        json.dump({
            "machine_id":    machine_id,
            "machine_name":  MACHINES[machine_id]["name"],
            "trained_at":    datetime.utcnow().isoformat() + "Z",
            "algorithm":     "IsolationForest",
            "n_estimators":  200,
            "contamination": 0.05,
            "n_features":    len(feature_cols),
            "feature_names": feature_cols,
        }, f, indent=2)

    print(f"\n  💾 {os.path.basename(model_path)}")
    print(f"  💾 {os.path.basename(scaler_path)}")
    print(f"  💾 {os.path.basename(meta_path)}")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═" * 60)
    print("   🤖  ForgeSight — ML Training Pipeline")
    print("   Data: http://localhost:3000/history/{machine_id}")
    print("═" * 60)

    results = {}

    for mid in MACHINES:
        print(f"\n{'─' * 60}")
        print(f"  Machine: {mid}  ({MACHINES[mid]['name']})")
        print(f"  Pattern: {MACHINES[mid]['failure_pattern']}")
        print("─" * 60)

        # 1. Fetch
        df = fetch_history(mid)
        if len(df) < 50:
            print(f"  ❌ Too few rows ({len(df)}). Skipping.")
            results[mid] = "❌ skipped"
            continue

        # 2. Feature engineering
        df_feat     = engineer_features(df)
        feature_cols = list(df_feat.columns)
        X           = df_feat.values
        print(f"  ✏️  Features: {len(feature_cols)} per reading")

        # 3. Scale
        scaler   = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # 4. Train
        print(f"  🔧 Training Isolation Forest on {len(X_scaled):,} samples …")
        model = train(X_scaled)
        print(f"  ✅ Training done")

        # 5. Evaluate
        evaluate(model, scaler, df, mid)

        # 6. Save
        save(model, scaler, mid, feature_cols)
        results[mid] = "✅"

    # Summary
    print(f"\n{'═' * 60}")
    print("  TRAINING COMPLETE")
    print("─" * 60)
    for mid, status in results.items():
        print(f"  {status}  {mid:20s} ({MACHINES[mid]['name']})")
    print(f"\n  Models saved to: models/")
    print(f"\n  ★ NEXT STEP — start the server:")
    print(f"    python -m uvicorn main:app --reload --port 8000")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
