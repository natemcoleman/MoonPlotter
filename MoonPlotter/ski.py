"""Ski resorts: lifts and downhill runs from OpenStreetMap, drawn over the contours.

Data comes from the Overpass API (OpenStreetMap, (c) OpenStreetMap contributors, ODbL). The first time a
resort is chosen it is downloaded and saved to SKI_DIR/<slug>.json, so later use is offline. Delete that
file to refresh it.

Tags used (see wiki.openstreetmap.org/wiki/Pistes):
  piste:type=downhill        runs (ways; closed ways tagged area=yes are run areas), piste:difficulty=*
  aerialway=*                lifts (chair_lift, gondola, t-bar, magic_carpet, ...)
  landuse=winter_sports      the resort's boundary, used to drop runs/lifts of neighbouring resorts
"""
import json
import math
import os
import time
import urllib.parse

import numpy as np

SKI_DIR = "ski_resorts"
# Public Overpass servers, tried in order (the main one often answers 429/502/504 when busy).
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter",
                 "https://overpass.private.coffee/api/interpreter"]

# name -> centre (lat, lon) of the base area, search radius (km), text that the resort's
# landuse=winter_sports boundary name contains. Centres: Wikipedia / USFS (see docs/data-sources.md).
RESORTS = {
    "Grand Targhee Resort": {'center': (43.789, -110.957), 'radius_km': 6.0, 'match': 'targhee'},
    "Deer Valley Resort": {'center': (40.6228, -111.4894), 'radius_km': 8.0, 'match': 'deer valley'},
    "Sundance Mountain Resort": {'center': (40.392, -111.579), 'radius_km': 4.0, 'match': 'sundance'},
}
REGION_SUFFIX = " (ski runs & lifts)"      # how resorts appear in the Clip to region drop-down

SKIP_LIFTS = {'station', 'pylon', 'goods', 'zip_line'}
DIFFICULTY_GROUP = {'novice': 'easy', 'easy': 'easy', 'intermediate': 'intermediate',
                    'advanced': 'advanced', 'expert': 'expert', 'freeride': 'expert', 'extreme': 'expert'}


def slug(name):
    return "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_")


def cache_path(name):
    return os.path.join(SKI_DIR, slug(name) + ".json")


def region_label(name):
    return name + REGION_SUFFIX


def resort_from_label(text):
    """Clip-to-region text -> resort name, or None."""
    if text and text.endswith(REGION_SUFFIX):
        name = text[:-len(REGION_SUFFIX)]
        return name if name in RESORTS else None
    return None


def overpass_query(lat, lon, radius_m):
    a = f"(around:{int(radius_m)},{lat:.5f},{lon:.5f})"
    return (f'[out:json][timeout:90];('
            f'way["piste:type"="downhill"]{a};'
            f'way["aerialway"]{a};'
            f'way["landuse"="winter_sports"]{a};'
            f'relation["landuse"="winter_sports"]{a};'
            f');out geom;')


def _way_coords(el):
    return [(p['lon'], p['lat']) for p in el.get('geometry') or [] if p]


def _boundary(elements, match):
    """Union of the landuse=winter_sports polygons whose name contains `match` (shapely), or None."""
    from shapely.geometry import Polygon, LineString
    from shapely.ops import polygonize, unary_union
    polys = []
    for el in elements:
        tags = el.get('tags') or {}
        if tags.get('landuse') != 'winter_sports' or match not in (tags.get('name') or '').lower():
            continue
        if el['type'] == 'way':
            c = _way_coords(el)
            if len(c) >= 4:
                polys.append(Polygon(c).buffer(0))
        elif el['type'] == 'relation':
            rings = [LineString(_way_coords(m)) for m in el.get('members') or []
                     if m.get('type') == 'way' and m.get('role') in ('outer', '') and len(m.get('geometry') or []) >= 2]
            polys += [p.buffer(0) for p in polygonize(unary_union(rings))]
    polys = [p for p in polys if not p.is_empty]
    return unary_union(polys) if polys else None


def parse(elements, match):
    """Overpass elements -> (features, boundary_bounds or None). Features are dicts:
    {'kind': 'run'|'lift', 'name', 'difficulty' (runs), 'lift' (lifts), 'area' (bool), 'coords': [[lon, lat], ...]}"""
    from shapely.geometry import LineString
    boundary = _boundary(elements, match)
    near = boundary.buffer(0.003) if boundary is not None else None        # ~300 m slack
    feats = []
    for el in elements:
        if el.get('type') != 'way':
            continue
        tags = el.get('tags') or {}
        c = _way_coords(el)
        if len(c) < 2:
            continue
        if tags.get('piste:type') == 'downhill':
            kind = 'run'
        elif tags.get('aerialway') and tags['aerialway'] not in SKIP_LIFTS:
            kind = 'lift'
        else:
            continue
        if near is not None and not LineString(c).intersects(near):
            continue                                    # belongs to a neighbouring resort
        f = {'kind': kind, 'name': tags.get('name') or tags.get('piste:name') or '', 'coords': c,
             'area': tags.get('area') == 'yes' or (c[0] == c[-1] and kind == 'run' and len(c) > 3)}
        if kind == 'run':
            f['difficulty'] = tags.get('piste:difficulty', '')
        else:
            f['lift'] = tags['aerialway']
        feats.append(f)
    return feats, (list(boundary.bounds) if boundary is not None else None)


def feature_bounds(feats):
    """(lon_min, lon_max, lat_min, lat_max) of all features, or None."""
    pts = np.array([p for f in feats for p in f['coords']], dtype=float)
    if pts.size == 0:
        return None
    return float(pts[:, 0].min()), float(pts[:, 0].max()), float(pts[:, 1].min()), float(pts[:, 1].max())


def download(name, progress=lambda m: None):
    """Fetches a resort from Overpass, filters it to the resort and saves SKI_DIR/<slug>.json."""
    from net import fetch_bytes
    info = RESORTS[name]
    lat, lon = info['center']
    progress(f"Downloading {name} lifts and runs from OpenStreetMap...")
    q = urllib.parse.quote(overpass_query(lat, lon, info['radius_km'] * 1000.0))
    errors = []
    for base in [OVERPASS_URLS[0]] + OVERPASS_URLS:        # the main server gets a second chance
        try:
            data = json.loads(fetch_bytes(base + "?data=" + q, timeout=90).decode('utf-8'))
            break
        except Exception as e:                       # busy server, timeout, bad JSON: try the next one
            errors.append(f"{base.split('/')[2]}: {e}")
            progress(f"{base.split('/')[2]} unavailable - trying another OpenStreetMap server...")
    else:
        raise IOError("No Overpass server answered:\n" + "\n".join(e[:200] for e in errors))
    feats, bbox = parse(data.get('elements', []), info['match'])
    if not feats:
        raise ValueError(f"OpenStreetMap returned no runs or lifts for {name}.")
    out = {'name': name, 'source': "OpenStreetMap via Overpass API",
           'licence': "(c) OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright)",
           'downloaded': time.strftime("%Y-%m-%d"), 'boundary_bounds': bbox, 'features': feats}
    os.makedirs(SKI_DIR, exist_ok=True)
    tmp = cache_path(name) + ".part"
    with open(tmp, 'w') as f:
        json.dump(out, f)
    os.replace(tmp, cache_path(name))
    return out


def load(name, progress=lambda m: None, allow_download=True):
    p = cache_path(name)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    if not allow_download:
        return None
    return download(name, progress)


def frame_extent(data, pad=0.08):
    """Lon/lat box around the resort's runs and lifts, padded by `pad` of its size."""
    b = feature_bounds(data['features'])
    x0, x1, y0, y1 = b
    px, py = (x1 - x0) * pad + 1e-3, (y1 - y0) * pad + 1e-3
    return x0 - px, x1 + px, y0 - py, y1 + py


def dem_extent(data, factor=3.0, min_km=12.0):
    """Square-ish lon/lat box for the one-time 30 m elevation fetch: `factor` x the resort's size
    (at least `min_km` across), so the map can be panned, zoomed out or split into pages."""
    x0, x1, y0, y1 = feature_bounds(data['features'])
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    kx = 111.32 * math.cos(math.radians(cy))               # km per degree of longitude
    half_km = max((x1 - x0) * kx, (y1 - y0) * 111.32, min_km / factor) * factor / 2
    return cx - half_km / kx, cx + half_km / kx, cy - half_km / 111.32, cy + half_km / 111.32


# ---------------------------------------------------------------------------------------------
# Strokes for the plot
# ---------------------------------------------------------------------------------------------
def segments(data, extent, paper_w_mm, paper_h_mm, opts):
    """Plot strokes (lon/lat) clipped to the view. opts: runs, lifts (bool), run_pen, lift_pen,
    by_difficulty (bool), difficulty_pens {easy, intermediate, advanced, expert: pen id}."""
    from shapely.geometry import LineString, box
    lon0, lon1, lat0, lat1 = extent
    view = box(lon0, lat0, lon1, lat1)
    mm_x = (lon1 - lon0) / max(paper_w_mm, 1e-6)          # degrees per paper mm
    mm_y = (lat1 - lat0) / max(paper_h_mm, 1e-6)
    tick = 0.7                                             # half-length (mm) of a lift tower tick
    out = []

    def clipped(coords):
        g = LineString(coords).intersection(view)
        if g.is_empty:
            return []
        geoms = getattr(g, 'geoms', [g])
        return [np.asarray(x.coords) for x in geoms if x.geom_type == 'LineString' and len(x.coords) >= 2]

    for f in data.get('features', []):
        if f['kind'] == 'run' and opts.get('runs', True):
            pen = opts.get('run_pen') or 'base'
            if opts.get('by_difficulty'):
                grp = DIFFICULTY_GROUP.get((f.get('difficulty') or '').lower())
                pen = (opts.get('difficulty_pens') or {}).get(grp) or pen
            for seg in clipped(f['coords']):
                out.append({'type': 'ski', 'layer': 'ski_runs', 'seg': seg, 'level': 0, 'pen_id': pen})
        elif f['kind'] == 'lift' and opts.get('lifts', True):
            pen = opts.get('lift_pen') or 'pen_1'
            for seg in clipped(f['coords']):
                out.append({'type': 'ski', 'layer': 'ski_lifts', 'seg': seg, 'level': 0, 'pen_id': pen})
                # tower ticks at every vertex, perpendicular to the line (in paper space)
                p = seg / [mm_x, mm_y]
                for i in range(len(p)):
                    a, b = p[max(0, i - 1)], p[min(len(p) - 1, i + 1)]
                    d = b - a
                    n = np.hypot(*d)
                    if n < 1e-9:
                        continue
                    nx, ny = -d[1] / n * tick, d[0] / n * tick
                    t = np.array([[p[i, 0] - nx, p[i, 1] - ny], [p[i, 0] + nx, p[i, 1] + ny]]) * [mm_x, mm_y]
                    out.append({'type': 'ski', 'layer': 'ski_lifts', 'seg': t, 'level': 0, 'pen_id': pen})
    return out
