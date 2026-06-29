"""
Agent Release / OTA Update Endpoint

Returns the latest agent release metadata so agents can self-update.
Releases are stored as JSON in a file or (later) a DB table.
For now, the canonical version lives in releases/latest.json committed
to the repo and served from here — no DB required.
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/agent", tags=["releases"])

# Resolve relative to this file so it works from any cwd
_RELEASES_DIR = Path(__file__).parent.parent.parent / "releases"


class ReleaseInfo(BaseModel):
    version: str
    download_url: str
    checksum_sha256: str
    release_notes: str = ""
    min_agent_version: str = "0.0.0"
    is_critical: bool = False
    published_at: str = ""


@router.get("/releases/latest", response_model=ReleaseInfo, summary="Latest agent release")
def get_latest_release():
    """
    Returns metadata for the latest agent release.
    Agents poll this endpoint daily to check for updates.
    """
    latest_path = _RELEASES_DIR / "latest.json"
    if not latest_path.exists():
        raise HTTPException(status_code=404, detail="No release published yet")

    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
        return ReleaseInfo(**data)
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.error(f"Bad release manifest: {e}")
        raise HTTPException(status_code=500, detail="Malformed release manifest")


_DIST_DIR = Path(__file__).parent.parent.parent / "dist"


@router.get("/download/{filename}", summary="Download agent exe")
def download_agent(filename: str):
    """
    Serve a compiled agent exe.
    Expected filename: TallySyncAgent-<version>.exe
    """
    # Sanitise: only allow simple filenames, no path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filename.endswith(".exe"):
        raise HTTPException(status_code=400, detail="Only .exe files served here")

    path = _DIST_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")

    return FileResponse(
        path=str(path),
        media_type="application/octet-stream",
        filename=filename,
    )
