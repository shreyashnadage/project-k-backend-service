"""
Tally Sync Platform — Cloud Backend

FastAPI application for receiving and storing extracted accounting data.
"""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Tally Sync Platform",
    description="Cloud backend for Tally data synchronization",
    version="0.3.0",
)

# CORS — allow browser requests from local dev and any future prod domain
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

# Initialize database
@app.on_event("startup")
def startup():
    """Initialize database on startup."""
    logger.info("Initializing database...")
    init_db()
    logger.info("Database initialized")


# Include routers
app.include_router(auth_router)  # Phase 1: Authentication
app.include_router(device_router)  # Phase 2: Device Registration
app.include_router(ingest_router)
app.include_router(telemetry_router)
app.include_router(registration_router)
app.include_router(dashboard_router)  # Dashboard API for frontend
app.include_router(commands_router)   # Command channel: cloud → agent
app.include_router(companies_router)  # Company mappings: tenant → Tally companies
app.include_router(admin_router)      # Admin dashboard for platform operators
app.include_router(watermarks_router)  # Sync watermarks for incremental sync
app.include_router(test_mode_router)   # Test mode: simulated data ingestion + toggle


# Health check (root)
@app.get("/")
def root():
    """Root endpoint."""
    return {
        "service": "tally-sync-platform",
        "version": "0.2.0",
        "status": "ok"
    }


# Error handlers
@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "cloudplatform.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
