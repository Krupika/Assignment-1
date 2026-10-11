# Geospatial File Measurement API

A FastAPI service that accepts KML files and zipped Shapefiles, extracts their features, and reports polygon area and line length in metres. Point features are returned without a measurement.

## Run Locally

Python 3.10 or newer is recommended.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

The browser upload interface is available at `http://127.0.0.1:8001`. Interactive OpenAPI documentation is at `/docs`.

Use the interface to select or drag in a `.kml` file or zipped Shapefile, then choose **Upload and measure**. The results area shows the feature count, source CRS, total measured polygon area, per-feature area or length, and available attributes. The same-origin page calls the API endpoints directly.

Try the included `sample.kml` to see polygon area, line length, and point handling together.

Run the test suite with:

```powershell
python -m pytest
```

## API

### Upload and process a file

`POST /api/files/` accepts multipart form data with a field named `file`. Upload either a `.kml` file or a `.zip` containing a Shapefile and its matching `.shp`, `.shx`, and `.dbf` files. A `.prj` is recommended to supply the source CRS.

```powershell
curl.exe -F "file=@survey.kml" http://127.0.0.1:8000/api/files/
curl.exe -F "file=@survey.kml" http://127.0.0.1:8001/api/files/
```

Successful uploads return `201 Created`:

```json
{
  "id": "abc123",
  "filename": "survey.kml",
  "feature_count": 2,
  "crs": "EPSG:4326",
  "status": "COMPLETED"
}
```

Files larger than 50 MB are rejected. Invalid formats and unreadable files return `422`; oversized requests return `413`.

### Retrieve file information

`GET /api/files/{id}/` returns the upload metadata shown above. Unknown IDs return `404`.

### Retrieve feature measurements

`GET /api/files/{id}/measurements/` returns each feature's ID, index, geometry type, GeoJSON geometry, properties, and measurement status. Polygon and MultiPolygon features have `measurement.area_m2`; LineString and MultiLineString features have `measurement.length_m`. Points have a `null` measurement and `not_required` status. Unsupported geometries are included with an `unsupported_geometry` status rather than causing the request to fail.

For example, a measured feature includes:

```json
{
  "feature_id": "parcel-1",
  "index": 0,
  "geometry_type": "Polygon",
  "geometry": {"type": "Polygon", "coordinates": []},
  "properties": {"name": "Sample"},
  "measurement": {"area_m2": 12345.6},
  "measurement_status": "measured",
  "measurement_crs": "EPSG:32618"
}
```

A feature without a known CRS is still returned, with a `missing_crs` measurement status. The response's top-level `crs` reports the source CRS.

## Architecture and Processing Flow

- `app/main.py` defines the three API routes, validates upload size, and holds processed records in a simple in-memory registry.
- `app/readers.py` parses KML placemarks and reads zipped Shapefiles through GeoPandas. Each record is normalized to an ID, Shapely geometry, geometry type, and properties.
- `app/geospatial.py` selects a local UTM CRS based on each geometry's geographic centroid, transforms the geometry with PyProj, then calculates area or length. For latitudes outside the UTM range it uses the global equal-area CRS EPSG:6933.

Geometries are never measured in longitude/latitude degrees. KML is treated as EPSG:4326 as defined by its format. Shapefile coordinates use the archive's `.prj`; without it, features are retained but measurements are marked `missing_crs`.

## Design Decisions and Future Scope

The implementation uses FastAPI for multipart handling and OpenAPI documentation, GeoPandas for Shapefile reading, Shapely for geometry operations, and PyProj for CRS transformations. KML is parsed with Python's XML parser so deployments do not depend on optional GDAL KML driver configuration. Upload and archive size limits bound resource use, and archive paths are checked before extraction.

The in-memory registry keeps this assignment easy to run locally, but records disappear on restart and are not shared across multiple server workers. A production deployment should persist metadata and feature data in a database such as PostGIS, store original files in object storage, move processing to a background queue, and add authentication, retention policies, and observability. The local UTM strategy is suited to compact datasets; very large or cross-zone geometries may need a caller-selected CRS or geodesic measurement policy.

## Learning and Future Scope

This project demonstrates multipart API design, geospatial file parsing, per-feature normalization, and CRS-aware measurements. Useful next steps include persistent storage, asynchronous processing, additional vector formats, and configurable measurement CRS policies.
