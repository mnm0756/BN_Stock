from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .calculator import utc_now_iso
from .config import ROOT, config
from .db import Database
from .schemas import PositionInput, SettingsUpdate
from .service import MonitorService


db = Database(config.database_path)
service = MonitorService(db)


async def refresh_loop() -> None:
    while True:
        try:
            await service.refresh()
        except Exception as exc:  # Keep the monitor alive and surface the error in the UI.
            service.snapshot = {
                **service.snapshot,
                "status": "error",
                "error": f"刷新失败: {exc}",
                "updated_at": utc_now_iso(),
            }
        settings = db.get_settings()
        await asyncio.sleep(max(int(settings.get("refresh_seconds", 15)), 5))


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(refresh_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="BN OKX Funding Monitor", version="2.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": service.snapshot["status"],
        "source": service.snapshot["source"],
        "updated_at": service.snapshot["updated_at"],
    }


@app.get("/api/snapshot")
async def snapshot() -> dict:
    return {**service.snapshot, "version": service.version}


@app.post("/api/refresh")
async def refresh() -> dict:
    return await service.refresh()


@app.get("/api/settings")
async def get_settings() -> dict:
    return db.get_settings()


@app.put("/api/settings")
async def put_settings(payload: SettingsUpdate) -> dict:
    saved = db.save_settings(payload.model_dump())
    await service.refresh()
    return saved


@app.post("/api/positions", status_code=201)
async def create_position(payload: PositionInput) -> dict:
    created = db.create_position(payload.model_dump(), utc_now_iso())
    await service.refresh()
    return created


@app.put("/api/positions/{position_id}")
async def update_position(position_id: int, payload: PositionInput) -> dict:
    updated = db.update_position(position_id, payload.model_dump(), utc_now_iso())
    if updated is None:
        raise HTTPException(status_code=404, detail="Position not found")
    await service.refresh()
    return updated


@app.delete("/api/positions/{position_id}", status_code=204)
async def delete_position(position_id: int):
    if not db.delete_position(position_id):
        raise HTTPException(status_code=404, detail="Position not found")
    await service.refresh()


@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    async def events():
        version = -1
        while not await request.is_disconnected():
            version = await service.wait_for_update(version, timeout=20)
            payload = {**service.snapshot, "version": version}
            yield f"event: snapshot\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
