"""Central server: FastAPI dashboard + REST API, with the TCP collector running in background threads.

Run:  uvicorn server:app --port 8000      (do not use --reload: it would start the TCP server twice)
"""
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse

import config
import db
import tcp_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    # Background threads live as long as the web server process.
    threading.Thread(target=tcp_server.run_tcp_server, daemon=True, name="tcp-listener").start()
    threading.Thread(target=tcp_server.offline_checker, daemon=True, name="offline-checker").start()
    yield


app = FastAPI(title="System Resource & Log Monitor", lifespan=lifespan)


def _serialize(row):
    """DB stores naive UTC datetimes; send them as ISO-8601 with a 'Z' so browsers convert correctly."""
    return {k: (v.isoformat() + "Z" if isinstance(v, datetime) else v) for k, v in row.items()}


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config():
    """Thresholds the dashboard uses to highlight values."""
    return {
        "cpu_alert": config.CPU_ALERT,
        "mem_alert": config.MEM_ALERT,
        "disk_alert": config.DISK_ALERT,
        "offline_after": config.OFFLINE_AFTER,
    }


@app.get("/api/machines")
def list_machines():
    """All machines with their status and latest metrics sample."""
    return [_serialize(r) for r in db.list_machines()]


@app.get("/api/machines/{machine_id}/metrics")
def machine_metrics(machine_id: int, minutes: int = Query(30, ge=1, le=1440)):
    """Time series of one machine's metrics for the last `minutes` minutes."""
    return [_serialize(r) for r in db.get_metrics(machine_id, minutes)]


@app.get("/api/events")
def list_events(limit: int = Query(50, ge=1, le=500), machine_id: int | None = None):
    """Most recent alerts, log events and status changes (newest first)."""
    return [_serialize(r) for r in db.get_events(limit, machine_id)]
