import io
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from app.main import app, files

client = TestClient(app)


KML = b'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark id="parcel"><name>Test parcel</name><ExtendedData><Data name="zone"><value>north</value></Data></ExtendedData>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      -73.001,40.0 -73.0,40.0 -73.0,40.001 -73.001,40.001 -73.001,40.0
    </coordinates></LinearRing></outerBoundaryIs></Polygon>
  </Placemark>
  <Placemark><Point><coordinates>-73.0,40.0</coordinates></Point></Placemark>
  <Placemark><LineString><coordinates>-73.0,40.0 -72.999,40.0</coordinates></LineString></Placemark>
</Document></kml>'''


def setup_function() -> None:
    files.clear()


def test_frontend_is_available_at_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "Fieldnote" in response.text
    assert "Upload and measure" in response.text


def upload_kml():
    return client.post("/api/files/", files={"file": ("survey.kml", KML, "application/vnd.google-earth.kml+xml")})


def test_upload_and_metadata():
    response = upload_kml()
    assert response.status_code == 201
    metadata = response.json()
    assert metadata["filename"] == "survey.kml"
    assert metadata["feature_count"] == 3
    assert metadata["crs"] == "EPSG:4326"
    assert client.get(f"/api/files/{metadata['id']}/").json() == metadata


def test_measurements_reproject_before_calculation():
    uploaded = upload_kml().json()
    response = client.get(f"/api/files/{uploaded['id']}/measurements/")
    assert response.status_code == 200
    features = response.json()["features"]

    polygon = features[0]
    assert polygon["geometry_type"] == "Polygon"
    assert polygon["measurement_status"] == "measured"
    assert polygon["measurement_crs"].startswith("EPSG:326")
    assert polygon["measurement"]["area_m2"] > 9000
    assert polygon["properties"] == {"name": "Test parcel", "zone": "north"}

    assert features[1]["measurement"] is None
    assert features[1]["measurement_status"] == "not_required"
    assert features[2]["measurement"]["length_m"] > 80


def test_rejects_unsupported_upload_and_unknown_id():
    invalid = client.post("/api/files/", files={"file": ("notes.txt", b"not geospatial")})
    assert invalid.status_code == 422
    assert client.get("/api/files/missing/").status_code == 404


def test_uploads_zipped_shapefile_and_measures_features():
    with tempfile.TemporaryDirectory() as directory:
        shape_path = Path(directory) / "district.shp"
        frame = gpd.GeoDataFrame(
            {"district": ["central"]},
            geometry=[Polygon([(-73.001, 40), (-73, 40), (-73, 40.001), (-73.001, 40.001), (-73.001, 40)])],
            crs="EPSG:4326",
        )
        frame.to_file(shape_path)
        archive_data = io.BytesIO()
        with zipfile.ZipFile(archive_data, "w") as archive:
            for path in Path(directory).iterdir():
                archive.write(path, path.name)

    uploaded = client.post(
        "/api/files/",
        files={"file": ("district.zip", archive_data.getvalue(), "application/zip")},
    )
    assert uploaded.status_code == 201
    metadata = uploaded.json()
    assert metadata["feature_count"] == 1
    assert metadata["crs"] == "EPSG:4326"

    measurements = client.get(f"/api/files/{metadata['id']}/measurements/").json()
    feature = measurements["features"][0]
    assert feature["geometry_type"] == "Polygon"
    assert feature["properties"]["district"] == "central"
    assert feature["measurement"]["area_m2"] > 9000


def test_unsupported_geometry_is_returned_without_crashing():
        kml = b'''<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark>
            <MultiGeometry>
                <Point><coordinates>-73,40</coordinates></Point>
                <Point><coordinates>-72.999,40</coordinates></Point>
            </MultiGeometry>
        </Placemark></kml>'''
        uploaded = client.post("/api/files/", files={"file": ("points.kml", kml)})
        assert uploaded.status_code == 201

        response = client.get(f"/api/files/{uploaded.json()['id']}/measurements/")
        feature = response.json()["features"][0]
        assert feature["geometry_type"] == "MultiPoint"
        assert feature["measurement"] is None
        assert feature["measurement_status"] == "unsupported_geometry"
