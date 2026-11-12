"""Lake bathymetry regions (Clip to region > Lakes).

Each lake in config.LAKES is plotted like Lake Tahoe: a lake-only elevation raster (feet, NaN outside the
shoreline) contoured every `interval_ft` below the lake surface, with the shoreline as an index line.

Two kinds of lake:
  'survey'  a bundled raster made from a real depth survey (Lake Tahoe, 10 m).
  'model'   no public survey grid exists, so the lake bed is MODELLED once, on first use:
            shoreline from OpenStreetMap (natural=water, Overpass API) + published maximum (and, when
            known, mean) depth. Depth grows smoothly with distance from the shore and is scaled so the
            deepest point matches the published maximum and the average matches the published mean. It
            shows the lake's real outline and real depth range, not the true shape of the bed.
            The shoreline is kept in LAKES_DIR/<slug>.json (in the repository, so it works offline) and
            the modelled raster in LAKES_DIR/<slug>.tif (rebuilt from the json when missing).

To use a real survey later, export it as a GeoTIFF in feet (EPSG:4326, NaN outside the lake), set the
lake's 'kind' to 'survey' and 'dem' to its file name.
"""
import json
import math
import os
import time
import urllib.parse

import numpy as np

from config import LAKES

LAKES_DIR = "lakes"
OSM_LICENCE = "(c) OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright)"
MODEL_PX_M = 10.0          # finest grid spacing of a modelled lake bed (metres)
MODEL_MAX_PX = 1600        # ...but at most this many pixels on the long side
DEFAULT_MEAN_RATIO = 0.45  # mean / max depth when no mean is published (typical of glacial lakes)


def slug(name):
    base = name.split(" (")[0]
    return "".join(c.lower() if c.isalnum() else "_" for c in base).strip("_")


def lake_from_label(text):
    """Clip-to-region text -> lake name (a key of LAKES), or None."""
    return text if text in LAKES else None


def is_modelled(name):
    return LAKES[name].get('kind') == 'model'


def shoreline_path(name):
    return os.path.join(LAKES_DIR, slug(name) + ".json")


def dem_path(name):
    info = LAKES[name]
    return info['dem'] if info.get('kind') != 'model' else os.path.join(LAKES_DIR, slug(name) + ".tif")


def is_ready(name):
    return os.path.exists(dem_path(name))


def render_info(name):
    """What the render worker / contour engine need: raster path, bounds, units, surface."""
    info = LAKES[name]
    if info.get('kind') == 'model':
        meta = _read_json(shoreline_path(name)) or {}
        bounds = tuple(meta.get('bounds') or (0, 0, 0, 0))
        mult = 1.0
    else:
        bounds, mult = tuple(info['bounds']), float(info.get('multiplier', 1.0))
    return {'name': name, 'dem': dem_path(name), 'bounds': bounds, 'multiplier': mult,
            'surface_ft': float(info['surface_ft']), 'interval_ft': int(info['interval_ft'])}


def frame_bounds(name):
    """(left, bottom, right, top) to frame on the map, or None if not built yet."""
    info = LAKES[name]
    if info.get('kind') != 'model':
        return tuple(info['bounds'])
    meta = _read_json(shoreline_path(name))
    return tuple(meta['bounds']) if meta else None


def _read_json(p):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------
# Shoreline from OpenStreetMap
# ---------------------------------------------------------------------------------------------
def overpass_query(osm_name, lat, lon, radius_m):
    a = f"(around:{int(radius_m)},{lat:.5f},{lon:.5f})"
    n = osm_name.replace('"', '\\"')
    return (f'[out:json][timeout:90];('
            f'way["natural"="water"]["name"="{n}"]{a};'
            f'relation["natural"="water"]["name"="{n}"]{a};'
            f'way["landuse"="reservoir"]["name"="{n}"]{a};'
            f'relation["landuse"="reservoir"]["name"="{n}"]{a};'
            f');out geom;')


def _coords(geom):
    return [(p['lon'], p['lat']) for p in geom or [] if p]


def parse_shoreline(elements):
    """Overpass elements -> largest water polygon (shapely), islands cut out, or None."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union
    outers, inners = [], []
    for el in elements:
        if el.get('type') == 'way':
            c = _coords(el.get('geometry'))
            if len(c) >= 4 and c[0] == c[-1]:
                outers.append(Polygon(c).buffer(0))
        elif el.get('type') == 'relation':
            lines = {'outer': [], 'inner': []}
            for m in el.get('members') or []:
                c = _coords(m.get('geometry'))
                if m.get('type') == 'way' and len(c) >= 2:
                    lines['inner' if m.get('role') == 'inner' else 'outer'].append(LineString(c))
            outers += [p.buffer(0) for p in polygonize(unary_union(lines['outer']))] if lines['outer'] else []
            inners += [p.buffer(0) for p in polygonize(unary_union(lines['inner']))] if lines['inner'] else []
    outers = [p for p in outers if not p.is_empty]
    if not outers:
        return None
    water = unary_union(outers)
    if inners:
        water = water.difference(unary_union(inners))
    parts = list(getattr(water, 'geoms', [water]))
    return max(parts, key=lambda g: g.area)                  # the lake itself, not a nearby pond


def download_shoreline(name, progress=lambda m: None):
    """Fetches the lake outline from Overpass and saves LAKES_DIR/<slug>.json."""
    from net import fetch_bytes
    from shapely.geometry import mapping
    from ski import OVERPASS_URLS
    info = LAKES[name]
    lat, lon = info['center']
    progress(f"Downloading the {info['osm_name']} shoreline from OpenStreetMap...")
    q = urllib.parse.quote(overpass_query(info['osm_name'], lat, lon, info['radius_km'] * 1000.0))
    errors = []
    for base in [OVERPASS_URLS[0]] + OVERPASS_URLS:        # the main server gets a second chance
        try:
            data = json.loads(fetch_bytes(base + "?data=" + q, timeout=90).decode('utf-8'))
            break
        except Exception as e:
            errors.append(f"{base.split('/')[2]}: {e}")
            progress(f"{base.split('/')[2]} unavailable - trying another OpenStreetMap server...")
    else:
        raise IOError("No Overpass server answered:\n" + "\n".join(e[:200] for e in errors))
    poly = parse_shoreline(data.get('elements', []))
    if poly is None:
        raise ValueError(f"OpenStreetMap returned no water area named '{info['osm_name']}'.")
    poly = poly.simplify(0.00002, preserve_topology=True)   # ~2 m: drops redundant vertices only
    l, b, r, t = poly.bounds
    out = {'name': name, 'source': "OpenStreetMap via Overpass API", 'licence': OSM_LICENCE,
           'downloaded': time.strftime("%Y-%m-%d"), 'bounds': [l, b, r, t],
           'area_km2': round(_area_km2(poly), 3), 'geometry': mapping(poly)}
    os.makedirs(LAKES_DIR, exist_ok=True)
    tmp = shoreline_path(name) + ".part"
    with open(tmp, 'w') as f:
        json.dump(out, f)
    os.replace(tmp, shoreline_path(name))
    return out


def _area_km2(poly):
    lat0 = (poly.bounds[1] + poly.bounds[3]) / 2.0
    return poly.area * (111.32 ** 2) * math.cos(math.radians(lat0))


# ---------------------------------------------------------------------------------------------
# Modelled lake bed
# ---------------------------------------------------------------------------------------------
def model_depth(inside, px_m, max_depth, mean_depth=None, smooth_frac=0.12):
    """(depth, p): depth (same units as max_depth) on a boolean lake mask, 0 outside.

    The distance to the shore is smoothed (so the bed has no sharp ridge along the lake's centre line)
    and raised to a power p chosen so the average depth over the lake matches mean_depth (or
    DEFAULT_MEAN_RATIO * max_depth). Deepest point = max_depth."""
    import scipy.ndimage
    if not inside.any():
        return np.zeros(inside.shape, dtype=np.float32), 1.0
    dist = scipy.ndimage.distance_transform_edt(inside) * px_m        # metres to the nearest shore
    sigma = max(1.0, smooth_frac * float(dist.max()) / px_m)
    sm = scipy.ndimage.gaussian_filter(dist, sigma)
    # keep the shore at zero depth after smoothing: blend toward the raw distance near the edge
    w = np.clip(dist / (2.0 * sigma * px_m), 0.0, 1.0)
    d = np.where(inside, w * sm + (1.0 - w) * dist, 0.0)
    t = d / max(float(d.max()), 1e-9)
    target = (mean_depth / max_depth) if mean_depth else DEFAULT_MEAN_RATIO
    tv = t[inside]
    lo, hi = 0.05, 8.0                                                # mean(t**p) falls as p grows
    for _ in range(60):
        p = 0.5 * (lo + hi)
        if float(np.mean(tv ** p)) > target:
            lo = p
        else:
            hi = p
    return np.where(inside, max_depth * t ** p, 0.0).astype(np.float32), p


def build_model(name, progress=lambda m: None):
    """Modelled lake-bed GeoTIFF (feet, EPSG:4326, NaN outside the lake) from the saved shoreline."""
    import rasterio
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    from shapely.geometry import shape
    info = LAKES[name]
    meta = _read_json(shoreline_path(name)) or download_shoreline(name, progress)
    poly = shape(meta['geometry'])
    l, b, r, t = poly.bounds
    pad_x, pad_y = (r - l) * 0.02 + 1e-4, (t - b) * 0.02 + 1e-4
    l, b, r, t = l - pad_x, b - pad_y, r + pad_x, t + pad_y
    lat0 = (b + t) / 2.0
    w_m = (r - l) * 111320.0 * math.cos(math.radians(lat0))
    h_m = (t - b) * 111320.0
    px_m = max(MODEL_PX_M, max(w_m, h_m) / MODEL_MAX_PX)
    W, H = max(8, int(round(w_m / px_m))), max(8, int(round(h_m / px_m)))
    transform = from_bounds(l, b, r, t, W, H)
    progress(f"Modelling the {info['osm_name']} lake bed ({W}x{H} px)...")
    inside = rasterize([(poly, 1)], out_shape=(H, W), transform=transform, fill=0, dtype='uint8').astype(bool)
    px_eff = math.sqrt((w_m / W) * (h_m / H))
    max_ft = info['max_depth_m'] * 3.28084
    mean_ft = info['mean_depth_m'] * 3.28084 if info.get('mean_depth_m') else None
    depth_ft, p = model_depth(inside, px_eff, max_ft, mean_ft)
    elev = np.where(inside, info['surface_ft'] - depth_ft, np.nan).astype(np.float32)
    os.makedirs(LAKES_DIR, exist_ok=True)
    out = dem_path(name)
    tmp = out + ".part.tif"
    with rasterio.open(tmp, 'w', driver='GTiff', width=W, height=H, count=1, dtype='float32',
                       crs='EPSG:4326', transform=transform, nodata=float('nan'), compress='deflate') as dst:
        dst.write(elev, 1)
        dst.update_tags(units='feet', kind='modelled lake bed', shoreline=meta.get('source', ''),
                        max_depth_m=str(info['max_depth_m']), mean_depth_m=str(info.get('mean_depth_m') or ''),
                        profile_power=f"{p:.3f}")
    os.replace(tmp, out)
    meta['bounds'] = [l, b, r, t]                       # raster bounds (shoreline + a little margin)
    with open(shoreline_path(name), 'w') as f:
        json.dump(meta, f)
    return out


def ensure(name, progress=lambda m: None):
    """Makes sure the lake's raster exists (downloads / models it if needed). Returns render_info."""
    if is_modelled(name) and not is_ready(name):
        build_model(name, progress)
    if not is_ready(name):
        raise FileNotFoundError(f"{dem_path(name)} is missing.")
    return render_info(name)
