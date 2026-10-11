from __future__ import annotations

from typing import Any

from pyproj import CRS, Transformer
from shapely.geometry import mapping
from shapely.ops import transform


def choose_measurement_crs(geometry: Any, source_crs: str | None) -> CRS | None:
    if geometry is None or not source_crs:
        return None

    source = CRS.from_user_input(source_crs)
    geographic_geometry = transform(
        Transformer.from_crs(source, "EPSG:4326", always_xy=True).transform,
        geometry,
    )
    centroid = geographic_geometry.centroid
    longitude = max(-180.0, min(180.0, centroid.x))
    latitude = max(-90.0, min(90.0, centroid.y))

    if not -80 <= latitude <= 84:
        return CRS.from_epsg(6933)

    zone = min(60, max(1, int((longitude + 180) // 6) + 1))
    epsg = (32600 if latitude >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


def measure_geometry(geometry: Any, geometry_type: str, source_crs: str | None) -> dict[str, Any]:
    if geometry_type == "Point":
        return {"measurement": None, "measurement_status": "not_required"}

    if geometry_type not in {"Polygon", "MultiPolygon", "LineString", "MultiLineString"}:
        return {"measurement": None, "measurement_status": "unsupported_geometry"}

    if geometry is None:
        return {"measurement": None, "measurement_status": "missing_geometry"}

    if not source_crs:
        return {"measurement": None, "measurement_status": "missing_crs"}

    projected_crs = choose_measurement_crs(geometry, source_crs)
    projected = transform(
        Transformer.from_crs(source_crs, projected_crs, always_xy=True).transform,
        geometry,
    )
    if geometry_type in {"Polygon", "MultiPolygon"}:
        measurement = {"area_m2": float(projected.area)}
    else:
        measurement = {"length_m": float(projected.length)}

    return {
        "measurement": measurement,
        "measurement_status": "measured",
        "measurement_crs": projected_crs.to_string(),
    }


def geometry_to_geojson(geometry: Any) -> dict[str, Any] | None:
    return mapping(geometry) if geometry is not None else None
