from __future__ import annotations

import io
import math
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

import geopandas as gpd
from shapely.geometry import (
    GeometryCollection,
    LineString,
    LinearRing,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2000


@dataclass
class FeatureRecord:
    feature_id: str | int
    geometry: Any
    geometry_type: str | None
    properties: dict[str, Any]


def read_upload(filename: str, content: bytes) -> tuple[list[FeatureRecord], str | None]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".kml":
        return read_kml(content)
    if suffix == ".zip":
        return read_shapefile_zip(content)
    raise ValueError("Unsupported file type. Upload a .kml or zipped Shapefile (.zip).")


def read_kml(content: bytes) -> tuple[list[FeatureRecord], str]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ValueError("The KML document is not valid XML.") from exc

    placemarks = [element for element in root.iter() if _local_name(element.tag) == "Placemark"]
    features: list[FeatureRecord] = []
    for index, placemark in enumerate(placemarks):
        geometry_element = next(
            (child for child in placemark if _local_name(child.tag) in {
                "Point", "LineString", "Polygon", "MultiGeometry"
            }),
            None,
        )
        geometry = _parse_kml_geometry(geometry_element) if geometry_element is not None else None
        properties: dict[str, Any] = {}
        for child in placemark:
            name = _local_name(child.tag)
            if name in {"name", "description"} and child.text:
                properties[name] = child.text.strip()
            elif name == "ExtendedData":
                for data in child.iter():
                    data_name = data.attrib.get("name")
                    value = next((node.text for node in data if _local_name(node.tag) == "value"), None)
                    if data_name and value is not None:
                        properties[data_name] = value
                    elif _local_name(data.tag) == "SimpleData" and data.attrib.get("name"):
                        properties[data.attrib["name"]] = data.text

        features.append(
            FeatureRecord(
                feature_id=placemark.attrib.get("id", str(index)),
                geometry=geometry,
                geometry_type=geometry.geom_type if geometry is not None else None,
                properties=properties,
            )
        )

    if not features:
        raise ValueError("The KML file contains no Placemark features.")
    return features, "EPSG:4326"


def _parse_kml_geometry(element: ElementTree.Element) -> Any:
    kind = _local_name(element.tag)
    if kind == "Point":
        return Point(_parse_coordinates(_descendant_text(element, "coordinates"))[0])
    if kind == "LineString":
        return LineString(_parse_coordinates(_descendant_text(element, "coordinates")))
    if kind == "Polygon":
        exterior: list[tuple[float, ...]] = []
        interiors: list[list[tuple[float, ...]]] = []
        for boundary in element.iter():
            boundary_kind = _local_name(boundary.tag)
            if boundary_kind not in {"outerBoundaryIs", "innerBoundaryIs"}:
                continue
            coordinates = _parse_coordinates(_descendant_text(boundary, "coordinates"))
            if boundary_kind == "outerBoundaryIs":
                exterior = coordinates
            else:
                interiors.append(coordinates)
        if not exterior:
            raise ValueError("A KML polygon is missing its outer boundary.")
        return Polygon(exterior, interiors)
    if kind == "MultiGeometry":
        geometries = [_parse_kml_geometry(child) for child in element if _local_name(child.tag) in {
            "Point", "LineString", "Polygon", "MultiGeometry"
        }]
        if not geometries:
            return GeometryCollection()
        geometry_types = {geometry.geom_type for geometry in geometries}
        if geometry_types == {"Point"}:
            return MultiPoint(geometries)
        if geometry_types == {"LineString"}:
            return MultiLineString(geometries)
        if geometry_types == {"Polygon"}:
            return MultiPolygon(geometries)
        return GeometryCollection(geometries)
    raise ValueError(f"Unsupported KML geometry element: {kind}.")


def _descendant_text(element: ElementTree.Element, tag_name: str) -> str:
    for child in element.iter():
        if _local_name(child.tag) == tag_name and child.text:
            return child.text
    raise ValueError(f"KML geometry is missing {tag_name}.")


def _parse_coordinates(text: str) -> list[tuple[float, ...]]:
    coordinates: list[tuple[float, ...]] = []
    for item in text.split():
        values = tuple(float(value) for value in item.split(",")[:3])
        if len(values) < 2 or not all(math.isfinite(value) for value in values):
            raise ValueError("KML coordinates must contain finite longitude and latitude values.")
        coordinates.append(values)
    if not coordinates:
        raise ValueError("KML geometry has an empty coordinates element.")
    return coordinates


def read_shapefile_zip(content: bytes) -> tuple[list[FeatureRecord], str | None]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded .zip file is not a valid archive.") from exc

    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_ENTRIES:
            raise ValueError("The archive contains too many files.")
        if sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("The uncompressed archive exceeds the 250 MB limit.")
        for entry in entries:
            path = PurePosixPath(entry.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("The archive contains an unsafe file path.")

        names = [PurePosixPath(entry.filename).name for entry in entries]
        shapefiles = [name for name in names if name.lower().endswith(".shp")]
        if not shapefiles:
            raise ValueError("The archive does not contain a .shp file.")
        stem = Path(shapefiles[0]).stem.lower()
        companions = {Path(name).suffix.lower() for name in names if Path(name).stem.lower() == stem}
        if not {".shx", ".dbf"}.issubset(companions):
            raise ValueError("The Shapefile archive must include matching .shx and .dbf files.")

        with tempfile.TemporaryDirectory(prefix="geo-upload-") as temp_dir:
            root = Path(temp_dir)
            for entry in entries:
                if entry.is_dir():
                    continue
                destination = root.joinpath(*PurePosixPath(entry.filename.replace("\\", "/")).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, destination.open("wb") as target:
                    target.write(source.read())
            shp_path = next(path for path in root.rglob("*") if path.is_file() and path.name.lower() == shapefiles[0].lower())
            try:
                frame = gpd.read_file(shp_path)
            except Exception as exc:
                raise ValueError("The Shapefile archive could not be read.") from exc

    if frame.empty:
        raise ValueError("The Shapefile contains no features.")

    features = []
    for index, row in frame.iterrows():
        geometry = row.geometry
        properties = {key: _json_value(value) for key, value in row.drop(labels="geometry").to_dict().items()}
        features.append(
            FeatureRecord(
                feature_id=str(index),
                geometry=geometry,
                geometry_type=geometry.geom_type if geometry is not None else None,
                properties=properties,
            )
        )
    return features, frame.crs.to_string() if frame.crs else None


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
