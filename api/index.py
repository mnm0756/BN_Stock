from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse


ROOT = Path(__file__).resolve().parents[1]
MONITOR_ROOT = ROOT / "monitor"
sys.path.insert(0, str(MONITOR_ROOT))

os.environ.setdefault("DATABASE_PATH", "/tmp/cxmt_basis_monitor.db")

from app.calculator import utc_now_iso  # noqa: E402
from app.config import config  # noqa: E402
from app.db import Database  # noqa: E402
from app.schemas import PositionInput, SettingsUpdate  # noqa: E402
from app.service import MonitorService  # noqa: E402


db = Database(config.database_path)
service = MonitorService(db)
app = FastAPI(title="BN Wallet CXMT Basis Monitor API", version="3.1.0")


async def current_snapshot() -> dict:
    return await service.refresh()


@app.get("/api/health")
async def health() -> dict:
    snapshot = await current_snapshot()
    return {
        "status": snapshot["status"],
        "source": snapshot["source"],
        "updated_at": snapshot["updated_at"],
    }


@app.get("/api/snapshot")
async def snapshot() -> dict:
    snapshot = await current_snapshot()
    return {**snapshot, "version": service.version}


@app.post("/api/refresh")
async def refresh() -> dict:
    return await current_snapshot()


@app.get("/api/settings")
async def get_settings() -> dict:
    return db.get_settings()


@app.put("/api/settings")
async def put_settings(payload: SettingsUpdate) -> dict:
    saved = db.save_settings(payload.model_dump())
    await current_snapshot()
    return saved


@app.post("/api/positions", status_code=201)
async def create_position(payload: PositionInput) -> dict:
    created = db.create_position(payload.model_dump(), utc_now_iso())
    await current_snapshot()
    return created


@app.put("/api/positions/{position_id}")
async def update_position(position_id: int, payload: PositionInput) -> dict:
    updated = db.update_position(position_id, payload.model_dump(), utc_now_iso())
    if updated is None:
        raise HTTPException(status_code=404, detail="Position not found")
    await current_snapshot()
    return updated


@app.delete("/api/positions/{position_id}", status_code=204)
async def delete_position(position_id: int):
    if not db.delete_position(position_id):
        raise HTTPException(status_code=404, detail="Position not found")
    await current_snapshot()


@app.get("/api/stream")
async def stream(_: Request) -> StreamingResponse:
    async def events():
        snapshot = await current_snapshot()
        payload = {**snapshot, "version": service.version}
        yield f"event: snapshot\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
