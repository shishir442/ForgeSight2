"""
main.py — ForgeSight FastAPI Application
─────────────────────────────────────────
Run command:
    python -m uvicorn main:app --reload --port 8000
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import MACHINES
from core.baseline import baseline_registry
from simulator.data_source import fetch_history
from api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n" + "═"*55)
    print("  ⚙️   ForgeSight — Hack Malenadu 2026")
    print("═"*55)
    print("[Startup] Seeding baselines from hackathon server history…\n")
    for mid in MACHINES:
        history = await fetch_history(mid)
        baseline_registry.seed(mid, history)
    print("\n[Startup] ✅ All baselines ready. ForgeSight is live.\n")
    yield
    print("[Shutdown] ForgeSight stopped.")


app = FastAPI(title="ForgeSight", version="3.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(router)

@app.get("/health")
async def health():
    return {"status": "ok", "machines": list(MACHINES.keys())}
