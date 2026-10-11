from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.geospatial import geometry_to_geojson, measure_geometry
from app.readers import FeatureRecord, read_upload

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))


@dataclass
class StoredFile:
    file_id: str
    filename: str
    crs: str | None
    features: list[FeatureRecord]


files: dict[str, StoredFile] = {}
app = FastAPI(
    title="Geospatial File Measurement API",
    version="1.0.0",
    description="Upload KML or zipped Shapefiles and retrieve feature measurements.",
)
FRONTEND_PATH = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(FRONTEND_PATH)


@app.post("/api/files/", status_code=201)
async def upload_file(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="A filename is required.")

    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Upload exceeds the 50 MB limit.")
        features, crs = read_upload(filename, content)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="The uploaded geospatial file could not be processed.") from exc
    finally:
        await file.close()

    file_id = uuid.uuid4().hex
    stored = StoredFile(file_id=file_id, filename=filename, crs=crs, features=features)
    files[file_id] = stored
    return file_metadata(stored)


@app.get("/api/files/{file_id}/")
def get_file(file_id: str) -> dict[str, Any]:
    stored = files.get(file_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return file_metadata(stored)


@app.get("/api/files/{file_id}/measurements/")
def get_measurements(file_id: str) -> dict[str, Any]:
    stored = files.get(file_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="File not found.")

    results = []
    for index, feature in enumerate(stored.features):
        geometry_type = feature.geometry_type or "Unknown"
        measurement = measure_geometry(feature.geometry, geometry_type, stored.crs)
        results.append(
            {
                "feature_id": feature.feature_id,
                "index": index,
                "geometry_type": geometry_type,
                "geometry": geometry_to_geojson(feature.geometry),
                "properties": feature.properties,
                **measurement,
            }
        )
    return {"file_id": stored.file_id, "crs": stored.crs, "features": results}


def file_metadata(stored: StoredFile) -> dict[str, Any]:
    return {
        "id": stored.file_id,
        "filename": stored.filename,
        "feature_count": len(stored.features),
        "crs": stored.crs,
        "status": "COMPLETED",
    }
