# -*- coding: utf-8 -*-
"""
Tally Sync Platform — Cloud Backend

FastAPI application for receiving and storing extracted accounting data.
"""

import gzip
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from cloudplatform.db.database import init_db
from cloudplatform.api.ingest import router as ingest_router
from cloudplatform.api.telemetry import router as telemetry_router
from cloudplatform.api.registration import router as registration_router
from cloudplatform.api.dashboard import router as dashboard_router
from cloudplatform.auth import router as auth_router
from cloudplatform.keys import router as device_router
from cloudplatform.api.commands import router as commands_router
from cloudplatform.api.companies import router as companies_router
from cloudplatform.api.admin import router as admin_router
from cloudplatform.api.watermarks import router as watermarks_router
from cloudplatform.api.test_mode import router as test_mode_router
from cloudplatform.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Gzip request decompression ────────────────────────────────────────────────
# The Tally agent compresses payloads > 1 KB with gzip (Content-Encoding: gzip).
# FastAPI/uvicorn does not decompress request bodies by default.

class GzipRequestMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.headers.get("Content-Encoding", "").lower() == "gzip":
            compressed = await request.body()
            try:
                decompressed = gzip.decompress(compressed)
            except Exception:
                return JSONResponse({"detail": "Invalid gzip body"}, status_code=400)

            async def receive():
                return {"type": "http.request", "body": decompressed, "more_body": False}

            request._receive = receive
        return await call_next(request)


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Tally Sync Platform",
    description="Cloud backend for Tally data synchronization",
    version="0.3.0",
)

app.add_middleware(GzipRequestMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://15.206.90.21:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    logger.info("Initializing database...")
    init_db()
    logger.info("Database initialized")
    start_scheduler()


@app.on_event("shutdown")
def shutdown():
    stop_scheduler()


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(device_router)
app.include_router(ingest_router)
app.include_router(telemetry_router)
app.include_router(registration_router)
app.include_router(dashboard_router)
app.include_router(commands_router)
app.include_router(companies_router)
app.include_router(admin_router)
app.include_router(watermarks_router)
app.include_router(test_mode_router)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"service": "tally-sync-platform", "version": "0.3.0", "status": "ok"}


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("cloudplatform.main:app", host="0.0.0.0", port=8000, reload=True)
