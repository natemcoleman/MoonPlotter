"""High-resolution regional data: registry, import and remote fetch.

Each regional raster lives in REGIONS_DIR as <name>.tif plus a sidecar <name>.json:
    {"planet": "Earth", "kind": "dem" | "color", "bounds": [left, bottom, right, top],
     "units": "m" | "ft" | "km", "source": "...", "res_deg": 0.000277}
Bounds are geographic degrees (east-positive). When a selection lies inside a regional DEM / colour map
that is sharper than the global one, the worker uses it automatically.

Ways to get data in:
  * fetch_region(...)   - cut the current selection straight out of a remote dataset (USGS planetary
                          mosaics, NASA SVS Moon maps, Copernicus GLO-30 tiles). Only the needed part is
                          downloaded (GDAL range requests via /vsicurl/).
  * import_geotiff(...) - bring in any GeoTIFF you downloaded yourself (3DEP, GEBCO subset, OpenTopography,
                          ...). Projected files (e.g. UTM) are re-projected to lat/lon.
"""
import json
import math
import os

import numpy as np

from config import REGIONS_DIR, DATA_FILES

UNIT_MULT = {'m': 3.28084, 'ft': 1.0, 'km': 3280.84}

# ------------------------------------------------------------------------------------------------
# Remote sources. Global simple-cylindrical mosaics are windowed by pixel maths from their known
# lon/lat extent; Copernicus is assembled from 1-degree tiles.
# ------------------------------------------------------------------------------------------------
REMOTE_SOURCES = {
    'Moon': [
        {'label': "Moon elevation 59 m - LOLA + Kaguya merge (60°N-60°S)", 'kind': 'dem',
         'url': "https://planetarymaps.usgs.gov/mosaic/LolaKaguya_Topo/Lunar_LRO_LOLAKaguya_DEMmerge_60N60S_512ppd.tif",
         'extent': (-180.0, -60.0, 180.0, 60.0), 'units': 'm', 'source': "USGS Astrogeology SLDEM merge, 512 ppd"},
        {'label': "Moon elevation 474 m - LOLA ldem_64 (global)", 'kind': 'dem',
         'url': "https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/ldem_64.tif",
         'extent': (-180.0, -90.0, 180.0, 90.0), 'units': 'km', 'source': "NASA SVS CGI Moon Kit, 64 ppd"},
        {'label': "Moon colour 16k - LROC (global)", 'kind': 'color',
         'url': "https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/lroc_color_16bit_srgb_16k.tif",
         'extent': (-180.0, -90.0, 180.0, 90.0), 'units': '', 'source': "NASA SVS CGI Moon Kit, 16384 px"},
    ],
    'Mars': [
        {'label': "Mars elevation 200 m - HRSC + MOLA blend (global)", 'kind': 'dem',
         'url': "https://planetarymaps.usgs.gov/mosaic/Mars/HRSC_MOLA_Blend/Mars_HRSC_MOLA_BlendDEM_Global_200mp_v2.tif",
         'extent': (-180.0, -90.0, 180.0, 90.0), 'units': 'm', 'source': "USGS Astrogeology HRSC-MOLA blend v2"},
    ],
    'Earth': [
        {'label': "Earth elevation 30 m - Copernicus GLO-30 (land)", 'kind': 'dem', 'tiles': 'copernicus30',
         'url': "https://copernicus-dem-30m.s3.amazonaws.com", 'extent': (-180.0, -90.0, 180.0, 90.0),
         'units': 'm', 'source': "Copernicus GLO-30 (ESA), 1 arc-second"},
        {'label': "Earth colour 10 m - Sentinel-2 cloudless 2024 (EOX, global)", 'kind': 'color', 'tiles': 'xyz',
         'url': "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg",
         'max_zoom': 14, 'extent': (-180.0, -85.0, 180.0, 85.0), 'units': '',
         'source': "EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH "
                   "(Contains modified Copernicus Sentinel data 2024)"},
        {'label': "Earth colour ~1 m - USGS orthoimagery / NAIP (USA only)", 'kind': 'color', 'tiles': 'xyz',
         'url': "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
         'max_zoom': 16, 'extent': (-180.0, -85.0, 180.0, 85.0), 'units': '',
         'source': "USDA, USGS The National Map: Orthoimagery (public domain)"},
    ],
}

MAX_OUT_PX = 6000          # longest side of a fetched region (keeps files and redraws reasonable)


def _dir():
    os.makedirs(REGIONS_DIR, exist_ok=True)
    return REGIONS_DIR


def list_regions(planet=None, kind=None):
    """All registered regions, optionally filtered. Each item: dict(sidecar) + 'path' + 'name'."""
    out = []
    if not os.path.isdir(REGIONS_DIR):
        return out
    for fn in sorted(os.listdir(REGIONS_DIR)):
        if not fn.lower().endswith('.json'):
            continue
        try:
            with open(os.path.join(REGIONS_DIR, fn)) as f:
                meta = json.load(f)
        except Exception:
            continue
        tif = os.path.join(REGIONS_DIR, fn[:-5] + '.tif')
        if not os.path.exists(tif):
            continue
        if planet and meta.get('planet') != planet:
            continue
        if kind and meta.get('kind') != kind:
            continue
        meta['path'] = tif
        meta['name'] = fn[:-5]
        out.append(meta)
    return out


def _coverage(bounds, extent):
    l, b, r, t = bounds
    x0, x1, y0, y1 = extent
    y0, y1 = max(y0, -90.0), min(y1, 90.0)
    area = max(1e-12, (x1 - x0) * (y1 - y0))
    ix = max(0.0, min(r, x1) - max(l, x0))
    iy = max(0.0, min(t, y1) - max(b, y0))
    return ix * iy / area


def global_res_deg(planet, kind):
    """Degrees per pixel of the built-in global raster (for 'is the regional one sharper?')."""
    path = DATA_FILES[planet]['dem' if kind == 'dem' else 'color']
    try:
        import rasterio
        with rasterio.open(path) as src:
            return 360.0 / src.width
    except Exception:
        return 1.0


def best_region(planet, kind, extent, min_coverage=0.9):
    """Sharpest registered region covering >= min_coverage of the selection, or None."""
    best = None
    g = None
    for meta in list_regions(planet, kind):
        if _coverage(meta['bounds'], extent) < min_coverage:
            continue
        if g is None:
            g = global_res_deg(planet, kind)
        res = meta.get('res_deg', g)
        if res >= g * 0.95:
            continue                          # not actually sharper than the global map
        if best is None or res < best.get('res_deg', 1e9):
            best = meta
    return best


def _write(name, arr, bounds, meta, crs_epsg4326=False):
    """Writes <name>.tif (+ sidecar). arr: (h, w) float32 DEM or (3, h, w) uint8 colour."""
    import rasterio
    from rasterio.transform import from_bounds
    d = _dir()
    path = os.path.join(d, name + '.tif')
    if arr.ndim == 2:
        count, h, w = 1, arr.shape[0], arr.shape[1]
        dtype = 'float32'
        nodata = float('nan')
    else:
        count, h, w = arr.shape
        dtype = 'uint8'
        nodata = None
    profile = dict(driver='GTiff', height=h, width=w, count=count, dtype=dtype,
                   transform=from_bounds(*bounds, w, h), compress='deflate', tiled=True,
                   blockxsize=256, blockysize=256, BIGTIFF='IF_SAFER')
    if crs_epsg4326:
        profile['crs'] = 'EPSG:4326'
    if nodata is not None:
        profile['nodata'] = nodata
    with rasterio.open(path, 'w', **profile) as dst:
        if arr.ndim == 2:
            dst.write(arr.astype(np.float32), 1)
        else:
            dst.write(arr)
    meta = dict(meta)
    meta['bounds'] = [float(b) for b in bounds]
    meta['res_deg'] = float((bounds[2] - bounds[0]) / w)
    with open(os.path.join(d, name + '.json'), 'w') as f:
        json.dump(meta, f, indent=2)
    return path


def safe_name(text):
    keep = "".join(c if c.isalnum() else "_" for c in text)
    while "__" in keep:
        keep = keep.replace("__", "_")
    return keep.strip("_")[:80]


def fetch_region(source, planet, extent, name, progress=lambda m: None, max_px=MAX_OUT_PX):
    """Downloads only the selection's window from a remote source into REGIONS_DIR. Returns the tif path."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import Window

    lon_min, lon_max, lat_min, lat_max = extent
    l, b, r, t = source['extent']
    lat_min, lat_max = max(lat_min, b), min(lat_max, t)
    if lon_max - lon_min >= 360 or lon_min < -180 or lon_max > 180:
        raise ValueError("Regional fetch needs a selection inside -180..180 longitude (no dateline wrap).")
    if lat_max <= lat_min:
        raise ValueError("The selection is outside this dataset's latitude range "
                         f"({b:g}° to {t:g}°).")

    if source.get('tiles') == 'copernicus30':
        return _fetch_copernicus(source, planet, (lon_min, lon_max, lat_min, lat_max), name, progress, max_px)
    if source.get('tiles') == 'xyz':
        return _fetch_xyz(source, planet, (lon_min, lon_max, lat_min, lat_max), name, progress, max_px)

    url = "/vsicurl/" + source['url']
    progress("Opening remote dataset (reads only the part you selected)...")
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', GDAL_HTTP_MAX_RETRY='3', GDAL_HTTP_RETRY_DELAY='2',
                      CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif'):
        with rasterio.open(url) as src:
            W, H = src.width, src.height
            c0 = int(math.floor((lon_min - l) / (r - l) * W))
            c1 = int(math.ceil((lon_max - l) / (r - l) * W))
            r0 = int(math.floor((t - lat_max) / (t - b) * H))
            r1 = int(math.ceil((t - lat_min) / (t - b) * H))
            c0, r0 = max(0, c0), max(0, r0)
            c1, r1 = min(W, c1), min(H, r1)
            win_w, win_h = c1 - c0, r1 - r0
            k = max(1, int(math.ceil(max(win_w, win_h) / float(max_px))))
            out_w, out_h = max(1, win_w // k), max(1, win_h // k)
            progress(f"Downloading {win_w}x{win_h} px window" + (f" (1/{k} resolution)" if k > 1 else "") + "...")
            if source['kind'] == 'dem':
                arr = src.read(1, window=Window(c0, r0, win_w, win_h), out_shape=(out_h, out_w),
                               resampling=Resampling.average if k > 1 else Resampling.nearest).astype(np.float32)
                if src.nodata is not None and not np.isnan(src.nodata):
                    arr[arr == np.float32(src.nodata)] = np.nan
                arr[arr < -1e5] = np.nan
            else:
                bands = [1, 2, 3] if src.count >= 3 else [1, 1, 1]
                arr = src.read(bands, window=Window(c0, r0, win_w, win_h), out_shape=(3, out_h, out_w),
                               resampling=Resampling.average if k > 1 else Resampling.nearest)
                if arr.dtype == np.uint16:
                    arr = (arr / 257.0 + 0.5).astype(np.uint8)
                elif arr.dtype != np.uint8:
                    arr = np.clip(arr, 0, 255).astype(np.uint8)
    bounds = (l + c0 / W * (r - l), t - r1 / H * (t - b), l + c1 / W * (r - l), t - r0 / H * (t - b))
    progress("Writing regional file...")
    meta = {'planet': planet, 'kind': source['kind'], 'units': source['units'], 'source': source['source']}
    return _write(name, arr, bounds, meta)


def copernicus_tile_url(base, lat, lon):
    """1x1 degree tile whose south-west corner is (lat, lon)."""
    ns = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00"
    ew = f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00"
    stem = f"Copernicus_DSM_COG_10_{ns}_{ew}_DEM"
    return f"{base}/{stem}/{stem}.tif"


def _fetch_copernicus(source, planet, extent, name, progress, max_px):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject

    lon_min, lon_max, lat_min, lat_max = extent
    tiles = [(la, lo) for la in range(int(math.floor(lat_min)), int(math.ceil(lat_max)))
             for lo in range(int(math.floor(lon_min)), int(math.ceil(lon_max)))]
    if len(tiles) > 64:
        raise ValueError(f"That selection needs {len(tiles)} one-degree tiles - pick a smaller area "
                         "(GLO-30 is meant for close-ups; the global map is fine for big regions).")
    res = max(1.0 / 3600.0, max(lon_max - lon_min, lat_max - lat_min) / max_px)
    out_w = int(math.ceil((lon_max - lon_min) / res))
    out_h = int(math.ceil((lat_max - lat_min) / res))
    dst = np.zeros((out_h, out_w), dtype=np.float32)          # ocean / missing tiles = sea level
    dst_transform = from_bounds(lon_min, lat_min, lon_max, lat_max, out_w, out_h)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', GDAL_HTTP_MAX_RETRY='3',
                      CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif'):
        for i, (la, lo) in enumerate(tiles, 1):
            url = copernicus_tile_url(source['url'], la, lo)
            progress(f"Tile {i}/{len(tiles)}: {os.path.basename(url)}")
            try:
                with rasterio.open("/vsicurl/" + url) as src:
                    reproject(source=rasterio.band(src, 1), destination=dst, dst_transform=dst_transform,
                              dst_crs='EPSG:4326', src_nodata=src.nodata, dst_nodata=0.0,
                              init_dest_nodata=False, resampling=Resampling.average)
            except Exception as e:                            # ocean tiles do not exist (404)
                progress(f"  (no tile - treated as sea level: {type(e).__name__})")
    meta = {'planet': planet, 'kind': 'dem', 'units': 'm', 'source': source['source']}
    return _write(name, dst, (lon_min, lat_min, lon_max, lat_max), meta, crs_epsg4326=True)


# ---------------------------------------------------------------- web-mercator imagery tiles (z/y/x)
MAX_XYZ_TILES = 900
_UA = "TopoPlotterStudio/1.0 (personal pen-plotter maps)"


def _merc_y(lat, z):
    """Global pixel row (256-px tiles) of a latitude at zoom z."""
    lat = np.clip(lat, -85.05112878, 85.05112878)
    s = np.sin(np.radians(lat))
    return (0.5 - np.log((1 + s) / (1 - s)) / (4 * np.pi)) * 256.0 * 2 ** z


def _merc_x(lon, z):
    return (np.asarray(lon) + 180.0) / 360.0 * 256.0 * 2 ** z


def xyz_plan(extent, max_zoom, max_px=MAX_OUT_PX, max_tiles=MAX_XYZ_TILES):
    """Picks the deepest zoom whose crop stays within max_px and max_tiles. Returns (z, tx0, tx1, ty0, ty1)."""
    lon_min, lon_max, lat_min, lat_max = extent
    for z in range(max_zoom, -1, -1):
        x0, x1 = _merc_x(lon_min, z), _merc_x(lon_max, z)
        y0, y1 = _merc_y(lat_max, z), _merc_y(lat_min, z)
        tx0, tx1 = int(x0 // 256), int((x1 - 1e-9) // 256)
        ty0, ty1 = int(y0 // 256), int((y1 - 1e-9) // 256)
        n = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
        if max(x1 - x0, y1 - y0) <= max_px and n <= max_tiles:
            return z, tx0, tx1, ty0, ty1
    return 0, 0, 0, 0, 0


def _get_tile(url):
    import io
    import urllib.request
    from PIL import Image
    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            return np.asarray(Image.open(io.BytesIO(data)).convert('RGB'))
        except Exception as e:
            code = getattr(e, 'code', None)
            if code in (400, 404):
                return None                          # no imagery here (e.g. outside the USA)
            if attempt == 2:
                raise
    return None


def _fetch_xyz(source, planet, extent, name, progress, max_px):
    """Downloads Web-Mercator image tiles covering the selection and re-grids them to plain lat/lon."""
    from concurrent.futures import ThreadPoolExecutor

    lon_min, lon_max, lat_min, lat_max = extent
    z, tx0, tx1, ty0, ty1 = xyz_plan(extent, source.get('max_zoom', 14), max_px)
    jobs = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    mpp = 40075016.686 * math.cos(math.radians((lat_min + lat_max) / 2)) / (256 * 2 ** z)
    progress(f"Zoom {z} (~{mpp:.1f} m/px): downloading {len(jobs)} image tiles...")
    mosaic = np.full(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256, 3), 255, dtype=np.uint8)
    got = [0]

    def work(job):
        tx, ty = job
        return job, _get_tile(source['url'].format(z=z, x=tx, y=ty))

    errors = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (tx, ty), img in pool.map(lambda j: _safe(work, j, errors), jobs):
            got[0] += 1
            if got[0] % 20 == 0 or got[0] == len(jobs):
                progress(f"Tiles {got[0]}/{len(jobs)}...")
            if img is None:
                continue
            h, w = img.shape[:2]
            if (h, w) != (256, 256):
                from PIL import Image
                img = np.asarray(Image.fromarray(img).resize((256, 256)))
            r0, c0 = (ty - ty0) * 256, (tx - tx0) * 256
            mosaic[r0:r0 + 256, c0:c0 + 256] = img
    if errors and len(errors) == len(jobs):
        raise IOError(f"No tiles could be downloaded ({errors[0]}). Check your internet connection.")
    if errors:
        progress(f"{len(errors)} tiles failed and were left blank.")

    # re-grid: columns are already linear in longitude; rows are Mercator -> sample to linear latitude
    gx0, gx1 = _merc_x(lon_min, z) - tx0 * 256, _merc_x(lon_max, z) - tx0 * 256
    out_w = max(1, int(round(gx1 - gx0)))
    lat_span_px = _merc_y(lat_min, z) - _merc_y(lat_max, z)
    out_h = max(1, int(round(out_w * (lat_max - lat_min) / (lon_max - lon_min))))
    out_h = min(out_h, int(round(lat_span_px * 1.5)) + 1)
    cols = np.clip(np.round(np.linspace(gx0, gx1 - 1, out_w)).astype(int), 0, mosaic.shape[1] - 1)
    lats = lat_max - (np.arange(out_h) + 0.5) / out_h * (lat_max - lat_min)
    rows = np.clip(np.round(_merc_y(lats, z) - ty0 * 256 - 0.5).astype(int), 0, mosaic.shape[0] - 1)
    out = mosaic[rows][:, cols]                                    # (h, w, 3)
    progress("Writing regional file...")
    meta = {'planet': planet, 'kind': 'color', 'units': '', 'source': source['source']}
    return _write(name, np.ascontiguousarray(out.transpose(2, 0, 1)), (lon_min, lat_min, lon_max, lat_max), meta,
                  crs_epsg4326=True)


def _safe(fn, job, errors):
    try:
        return fn(job)
    except Exception as e:
        errors.append(f"{type(e).__name__}: {e}")
        return job, None


def import_geotiff(src_path, planet, kind, units, name, progress=lambda m: None, max_px=MAX_OUT_PX):
    """Copies any georeferenced GeoTIFF into the regions folder, re-projected to lat/lon if needed."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import calculate_default_transform, reproject, transform_bounds

    with rasterio.open(src_path) as src:
        if src.crs is None:
            raise ValueError("This file has no coordinate system. For planetary files, use 'Fetch' or add a "
                             "sidecar .json with the bounds by hand.")
        geographic = src.crs.is_geographic
        progress("Reading / re-projecting to latitude-longitude...")
        if geographic and planet != 'Earth' or (geographic and src.crs.to_epsg() == 4326):
            bounds = tuple(src.bounds)
            k = max(1, int(math.ceil(max(src.width, src.height) / float(max_px))))
            out_h, out_w = max(1, src.height // k), max(1, src.width // k)
            if kind == 'dem':
                arr = src.read(1, out_shape=(out_h, out_w),
                               resampling=Resampling.average if k > 1 else Resampling.nearest).astype(np.float32)
                if src.nodata is not None and not np.isnan(src.nodata):
                    arr[arr == np.float32(src.nodata)] = np.nan
            else:
                bands = [1, 2, 3] if src.count >= 3 else [1, 1, 1]
                arr = src.read(bands, out_shape=(3, out_h, out_w), resampling=Resampling.average)
        else:
            if planet != 'Earth':
                raise ValueError("Only geographic (lat/lon) planetary files can be imported.")
            bounds = transform_bounds(src.crs, 'EPSG:4326', *src.bounds, densify_pts=21)
            tr, w, h = calculate_default_transform(src.crs, 'EPSG:4326', src.width, src.height, *src.bounds)
            k = max(1, int(math.ceil(max(w, h) / float(max_px))))
            w, h = max(1, w // k), max(1, h // k)
            from rasterio.transform import from_bounds
            tr = from_bounds(*bounds, w, h)
            if kind == 'dem':
                arr = np.full((h, w), np.nan, dtype=np.float32)
                reproject(source=rasterio.band(src, 1), destination=arr, dst_transform=tr, dst_crs='EPSG:4326',
                          src_nodata=src.nodata, dst_nodata=np.nan, resampling=Resampling.bilinear)
            else:
                arr = np.zeros((3, h, w), dtype=np.uint8)
                for i, bnd in enumerate([1, 2, 3] if src.count >= 3 else [1, 1, 1]):
                    reproject(source=rasterio.band(src, bnd), destination=arr[i], dst_transform=tr,
                              dst_crs='EPSG:4326', resampling=Resampling.bilinear)
    if kind == 'color' and arr.dtype == np.uint16:
        arr = (arr / 257.0 + 0.5).astype(np.uint8)
    progress("Writing regional file...")
    meta = {'planet': planet, 'kind': kind, 'units': units, 'source': os.path.basename(src_path)}
    return _write(name, arr, bounds, meta, crs_epsg4326=(planet == 'Earth'))


def delete_region(name):
    for ext in ('.tif', '.json'):
        p = os.path.join(REGIONS_DIR, name + ext)
        if os.path.exists(p):
            os.remove(p)
