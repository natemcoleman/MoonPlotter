"""City road maps, after anvaka/city-roads: every road of a city from OpenStreetMap, drawn as pen lines.

Data: OpenStreetMap ((c) OpenStreetMap contributors, ODbL 1.0), fetched once per city and cached in
CITY_DIR/<slug>.npz (git-ignored; delete the file to refresh it):
  * Nominatim (nominatim.openstreetmap.org) finds the city and its boundary polygon (an OSM relation);
  * the Overpass API returns every road (highway=*) in the boundary's bounding box, plus water, coastline,
    rivers and canals, railways and parks for the optional extras.

Pen-friendliness:
  * roads are grouped into ROAD_CLASSES; everything down to the chosen smallest class is drawn (the same
    rule everywhere on the map, so the look stays consistent);
  * two pens: a thick pen for the major classes and a thin one for the rest, or one pen for the classic look;
  * road pieces are joined at shared end points into long strokes (straightest way through a junction),
    so the pen lifts far less often;
  * crowding() measures where the ink would merge into solid black at the chosen pens and paper size, so
    the app can warn (and highlight the areas) before anything is plotted.

Coordinates: lon/lat are projected to local metres (equirectangular about the boundary's centre - well under
0.1 % error across a city), then fitted onto the paper in mm with the app's paper frame [0, pw, -ph/2, ph/2].
No Qt in this module.
"""
import json
import math
import os
import time
import urllib.parse
from collections import defaultdict

import numpy as np

CITY_DIR = "cities"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
R_EARTH = 6371008.8
MAX_AREA_KM2 = 2500.0           # bounding box limit for one download (Overpass time-outs, plot density)

# (key, label, OSM highway values) - from biggest to smallest. The labels feed the UI combos.
ROAD_CLASSES = [
    ('motorway', "Motorways & trunk roads", ('motorway', 'motorway_link', 'trunk', 'trunk_link')),
    ('primary', "Primary roads", ('primary', 'primary_link')),
    ('secondary', "Secondary roads", ('secondary', 'secondary_link')),
    ('tertiary', "Tertiary roads", ('tertiary', 'tertiary_link')),
    ('residential', "Residential & local streets", ('residential', 'unclassified', 'living_street', 'road')),
    ('service', "Service roads & alleys", ('service',)),
    ('paths', "Paths, tracks & footways", ('pedestrian', 'track', 'footway', 'cycleway', 'path', 'bridleway',
                                           'steps')),
]
HIGHWAY_CLASS = {v: i for i, c in enumerate(ROAD_CLASSES) for v in c[2]}
DEFAULT_MIN_CLASS = 4            # down to residential streets
DEFAULT_MAJOR_UPTO = 2           # thick pen: motorways .. secondary roads
FETCH_CLASS = 4                  # downloaded by default; service roads and paths (often more than all the
                                 # streets together) on request
# More public Overpass servers, tried after ski.OVERPASS_URLS (VK Maps: whole planet, often less busy)
EXTRA_OVERPASS_URLS = ["https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
TILE_KM2 = 60.0                  # roads are downloaded in tiles of about this size (small, quick answers)
MAJOR_LABELS = ["Motorways & trunk", "+ primary", "+ secondary", "+ tertiary"]

# Optional extras: key -> (label, layer). 'outline' is the city boundary itself.
EXTRAS = {'water': ("Water", 'city_water'), 'coast': ("Coastline", 'city_water'),
          'rivers': ("Rivers & canals", 'city_rivers'), 'rail': ("Railways", 'city_rail'),
          'parks': ("Parks", 'city_parks'), 'outline': ("City outline", 'city_outline')}
EXTRA_KINDS = ('water', 'coast', 'rivers', 'rail', 'parks')
LAYER_LABELS = {'city_major': "Major roads", 'city_minor': "Minor roads", 'city_roads': "Roads",
                'city_water': "Water & coastline", 'city_rivers': "Rivers & canals", 'city_rail': "Railways",
                'city_parks': "Parks", 'city_outline': "City outline"}
CITY_LAYERS = tuple(LAYER_LABELS)

SIMPLIFY_MM = 0.04               # Douglas-Peucker tolerance on paper for roads (well under a pen width)
SIMPLIFY_EXTRAS_MM = 0.1         # ... for water, coast, rivers, rail, parks and the outline (wiggly shorelines)
MIN_SHAPE_MM = 2.0               # water / park / other extra shapes smaller than this on paper are skipped:
                                 # a pond 1 mm across is just a blob of ink, and hundreds of them a stipple
JOIN_TOL_MM = 1e-3               # end points closer than this are the same junction
CROWD_WINDOW_MM = 3.0            # neighbourhood over which crowding is judged
CROWD_LIMIT = 0.7                # share of that neighbourhood covered by ink above which lines merge


# ---------------------------------------------------------------------------------------------
# Cache files
# ---------------------------------------------------------------------------------------------
def slug(name):
    s = "".join(c.lower() if c.isalnum() else "_" for c in name)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_") or "city"


def cache_path(name):
    return os.path.join(CITY_DIR, slug(name) + ".npz")


def cached_cities():
    """Names (as typed when downloaded) of the cities in CITY_DIR, sorted."""
    out = []
    if os.path.isdir(CITY_DIR):
        for f in sorted(os.listdir(CITY_DIR)):
            if f.endswith(".npz"):
                try:
                    with np.load(os.path.join(CITY_DIR, f)) as z:
                        out.append(json.loads(str(z['meta']))['query'])
                except Exception:
                    pass
    return sorted(set(out), key=str.lower)


def _pack(lines):
    """list of (n, 2) arrays -> (xy float32 (N, 2), offsets int64 (len + 1))."""
    if not lines:
        return np.zeros((0, 2), np.float32), np.zeros(1, np.int64)
    off = np.zeros(len(lines) + 1, np.int64)
    off[1:] = np.cumsum([len(l) for l in lines])
    return np.concatenate(lines).astype(np.float32), off


def _unpack(xy, off):
    return [xy[off[i]:off[i + 1]].astype(np.float64) for i in range(len(off) - 1)]


def save(data, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rxy, roff = _pack(data['roads'])
    exy, eoff = _pack(data['extras'])
    tmp = path + ".part.npz"
    np.savez_compressed(tmp, meta=json.dumps(data['meta']), road_xy=rxy, road_off=roff,
                        road_cls=np.asarray(data['road_cls'], np.int8), extra_xy=exy, extra_off=eoff,
                        extra_kind=np.asarray(data['extra_kind'], np.int8))
    os.replace(tmp, path)


def load_file(path):
    with np.load(path) as z:
        return {'meta': json.loads(str(z['meta'])), 'roads': _unpack(z['road_xy'], z['road_off']),
                'road_cls': z['road_cls'].astype(int), 'extras': _unpack(z['extra_xy'], z['extra_off']),
                'extra_kind': z['extra_kind'].astype(int)}


def load(name):
    """Cached city data or None. Dict: meta, roads [arrays, metres], road_cls, extras, extra_kind."""
    p = cache_path(name)
    return load_file(p) if os.path.exists(p) else None


# ---------------------------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------------------------
def to_metres(lon, lat, lon0, lat0):
    k = math.pi / 180.0 * R_EARTH
    return (np.asarray(lon, float) - lon0) * k * math.cos(math.radians(lat0)), (np.asarray(lat, float) - lat0) * k


def to_lonlat(x, y, lon0, lat0):
    k = math.pi / 180.0 * R_EARTH
    return np.asarray(x, float) / (k * math.cos(math.radians(lat0))) + lon0, np.asarray(y, float) / k + lat0


def boundary_metres(meta):
    """The city boundary as a shapely (Multi)Polygon in local metres."""
    from shapely.geometry import shape
    from shapely.ops import transform
    lon0, lat0 = meta['center']
    g = shape(meta['boundary'])
    return transform(lambda x, y, z=None: to_metres(x, y, lon0, lat0), g).buffer(0)


# ---------------------------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------------------------
def geocode(query):
    """First Nominatim hit for `query` that is an OSM relation with a boundary polygon."""
    from net import fetch_bytes
    q = urllib.parse.urlencode({'q': query, 'format': 'jsonv2', 'polygon_geojson': 1,
                                'polygon_threshold': 0.00003, 'limit': 10})
    hits = json.loads(fetch_bytes(NOMINATIM_URL + "?" + q, timeout=60).decode('utf-8'))
    for h in hits:
        gj = h.get('geojson') or {}
        if h.get('osm_type') == 'relation' and gj.get('type') in ('Polygon', 'MultiPolygon'):
            return h
    if hits:
        raise ValueError(f"OpenStreetMap knows '{query}' ({hits[0].get('display_name', '')}) but not as an area "
                         "with a boundary. Try the city's full name, e.g. 'Portland, Oregon'.")
    raise ValueError(f"OpenStreetMap found nothing called '{query}'.")


def _bbox(bbox):
    s, w, n, e = bbox
    return f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"


def overpass_query(bbox, max_class):
    """Roads and the extras' ways in one tile. bbox: (south, west, north, east) in degrees."""
    b = _bbox(bbox)
    hw = "|".join(v for c in ROAD_CLASSES[:max_class + 1] for v in c[2])
    return ("[out:json][timeout:180];"
            f'(way["highway"~"^({hw})$"]["area"!="yes"]{b};'
            f'way["natural"="water"]{b};way["waterway"="riverbank"]{b};way["natural"="coastline"]{b};'
            f'way["waterway"~"^(river|canal)$"]{b};'
            f'way["railway"~"^(rail|light_rail|narrow_gauge|subway)$"]{b};'
            f'way["leisure"="park"]{b};);out tags geom qt;')


def relations_query(bbox):
    """Water and park multipolygons over the whole area, their members cropped to it."""
    b = _bbox(bbox)
    return ("[out:json][timeout:180];"
            f'(relation["natural"="water"]{b};relation["waterway"="riverbank"]{b};'
            f'relation["leisure"="park"]{b};);out body geom{b} qt;')


def tiles(bbox, area_km2, tile_km2=TILE_KM2):
    """Splits (south, west, north, east) into a grid of tiles of about tile_km2 each."""
    s, w, n, e = bbox
    k = max(1, int(math.ceil(math.sqrt(area_km2 / tile_km2))))
    lat = np.linspace(s, n, k + 1)
    lon = np.linspace(w, e, k + 1)
    return [(float(lat[i]), float(lon[j]), float(lat[i + 1]), float(lon[j + 1])) for i in range(k) for j in range(k)]


def _wait_for_slot(base, progress, what):
    """Overpass gives each computer a couple of query slots; a slot is blocked for a while after each query
    and asking early just earns an HTTP 429 / 502. Waits until /api/status says a slot is free (max 2 min)."""
    import re
    from net import fetch_bytes
    try:
        txt = fetch_bytes(base.replace('/interpreter', '/status'), timeout=20).decode('utf-8', 'replace')
    except Exception:
        return
    if re.search(r"^\s*[1-9]\d* slots? available now", txt, re.M):
        return
    waits = [int(x) for x in re.findall(r"in (\d+) seconds", txt)]
    if waits:
        w = min(min(waits) + 1, 120)
        progress(f"{what}: waiting {w} s for a free OpenStreetMap server slot...")
        time.sleep(w)


class _TooBig(IOError):
    """The server gave up on a query (HTTP 504 / time-out): split the area and ask again."""


def _overpass(query, progress, what, split_ok=False):
    """Runs one Overpass query and returns its elements. The public servers are often busy (HTTP 429 / 502 /
    504, or no answer), so the main server is retried with growing pauses (after waiting for a free query
    slot) and the others are tried in between."""
    from net import fetch_bytes
    from ski import OVERPASS_URLS
    main, others = OVERPASS_URLS[0], OVERPASS_URLS[1:] + EXTRA_OVERPASS_URLS
    plan = [(main, 0), (main, 5), (main, 15)] + [(u, 0) for u in others] + \
        [(main, p) for p in (30, 45, 60, 90, 120)]
    body = urllib.parse.urlencode({'data': query}).encode('ascii')
    errors = []
    main_fails = 0
    for base, pause in plan:
        host = base.split('/')[2]
        if pause:
            progress(f"{what}: the OpenStreetMap servers are busy - trying again in {pause} s...")
            time.sleep(pause)
        if base == main:
            _wait_for_slot(base, progress, what)
        try:
            # POST: long queries in the URL (GET) get HTTP 502 from overpass-api.de's front end
            raw = fetch_bytes(base, timeout=200 if base == main else 45, data=body)
            return json.loads(raw.decode('utf-8')).get('elements', [])
        except Exception as ex:
            errors.append(f"{host}: {ex}")
            progress(f"{what}: {host} did not answer ({str(ex).splitlines()[0][:60]}).")
            if split_ok and base == main:
                main_fails += 1
                # time-outs, or repeated 502s (the server dropping a heavy query): ask for less at a time
                if getattr(ex, 'code', None) == 504 or 'timed out' in str(ex) or main_fails >= 3:
                    raise _TooBig(str(ex))
    raise IOError("No Overpass server answered:\n" + "\n".join(x[:200] for x in errors))


def _runs(geometry):
    """Overpass 'geometry' list -> list of [[lon, lat], ...] runs (split where nodes are missing: relation
    members cropped to the bounding box have null points)."""
    runs, cur = [], []
    for p in geometry or []:
        if p:
            cur.append((p['lon'], p['lat']))
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def _extra_kind(tags):
    if tags.get('natural') == 'coastline':
        return 'coast'
    if tags.get('natural') == 'water' or tags.get('waterway') == 'riverbank':
        return 'water'
    if tags.get('waterway') in ('river', 'canal'):
        return None if tags.get('tunnel') in ('yes', 'culvert') else 'rivers'
    if tags.get('railway') in ('rail', 'light_rail', 'narrow_gauge', 'subway'):
        if tags.get('tunnel') not in (None, 'no') or tags.get('location') == 'underground':
            return None                                     # underground lines don't show on a map
        if tags.get('service') in ('yard', 'siding', 'spur', 'crossover'):
            return None                                     # rail yards would be solid ink
        return 'rail'
    if tags.get('leisure') == 'park':
        return 'parks'
    return None


def _water_area(lines_m, rivers_m, box):
    """Water polygon from one water feature's outline pieces (local metres), clipped to `box`.
    Relation members are cropped to the bounding box, so their rings may be open. Inside the box the pieces
    are still the complete boundary, so the even-odd rule works there: faces of (pieces + box edge) whose
    path from a reference point crosses an odd number of pieces have the opposite state to that point. The
    state of the reference point itself is chosen so the river centrelines lie mostly in water (else the
    smaller area is water)."""
    import shapely
    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import polygonize, unary_union
    pieces = [LineString(l) for l in lines_m if len(l) >= 2]
    if not pieces:
        return None
    mls = unary_union(MultiLineString([list(p.coords) for p in pieces]).intersection(box))
    if mls.is_empty:
        return None
    faces = list(polygonize(unary_union([mls, box.exterior])))
    if not faces:
        return None
    ref = shapely.Point(box.bounds[0] + 1e-3, box.bounds[1] + 1e-3)
    odd, even = [], []
    for f in faces:
        rp = f.representative_point()
        hits = LineString([ref.coords[0], rp.coords[0]]).intersection(mls)
        n = len(shapely.get_parts(hits)) if not hits.is_empty else 0
        (odd if n % 2 else even).append(f)
    a, b = unary_union(odd) if odd else None, unary_union(even) if even else None
    if a is None or b is None:
        return a or b
    if rivers_m is not None and not rivers_m.is_empty:
        la, lb = rivers_m.intersection(a).length, rivers_m.intersection(b).length
        if abs(la - lb) > 1.0:
            return a if la > lb else b
    return a if a.area <= b.area else b


def parse(elements, lon0, lat0, bbox_m):
    """Overpass elements -> (roads, road_cls, extras, extra_kind) in local metres. River and canal centre
    lines are trimmed where they run inside a mapped water area (the area's outline already shows them)."""
    import shapely
    from shapely.geometry import box as sbox, LineString
    from shapely.ops import unary_union
    roads, cls, extras, kinds = [], [], [], []
    water_feats = []              # (outline pieces in metres) per water area, for the river trimming
    rivers = []

    def metres(run):
        a = np.asarray(run, float)
        x, y = to_metres(a[:, 0], a[:, 1], lon0, lat0)
        return np.column_stack([x, y])

    for el in elements:
        tags = el.get('tags') or {}
        if el.get('type') == 'way':
            hw = tags.get('highway')
            if hw in HIGHWAY_CLASS and tags.get('area') != 'yes':
                for r in _runs(el.get('geometry')):
                    roads.append(metres(r))
                    cls.append(HIGHWAY_CLASS[hw])
                continue
            kind = _extra_kind(tags)
            if kind is None:
                continue
            runs = [metres(r) for r in _runs(el.get('geometry'))]
            if kind == 'rivers':
                rivers += runs
                continue
            for r in runs:
                extras.append(r)
                kinds.append(EXTRA_KINDS.index(kind))
            if kind == 'water':
                water_feats.append(runs)
        elif el.get('type') == 'relation':
            kind = _extra_kind(tags)
            if kind not in ('water', 'parks'):
                continue
            runs = []
            for m in el.get('members') or []:
                if m.get('type') == 'way' and m.get('role') in ('outer', 'inner', ''):
                    runs += [metres(r) for r in _runs(m.get('geometry'))]
            for r in runs:
                extras.append(r)
                kinds.append(EXTRA_KINDS.index(kind))
            if kind == 'water':
                water_feats.append(runs)

    if rivers:
        box = sbox(*bbox_m)
        riv = unary_union([LineString(r) for r in rivers])
        water = [w for w in (_water_area(f, riv, box) for f in water_feats) if w is not None and not w.is_empty]
        if water:
            riv = riv.difference(unary_union(water).buffer(2.0))
        for g in shapely.get_parts(riv):
            if g.geom_type == 'LineString' and len(g.coords) >= 2:
                extras.append(np.asarray(g.coords))
                kinds.append(EXTRA_KINDS.index('rivers'))
    return roads, cls, extras, kinds


def download(query, max_class=FETCH_CLASS, progress=lambda m: None):
    """Finds the city, downloads its roads and extras and saves CITY_DIR/<slug>.npz. Returns the data."""
    progress(f"Looking up '{query}' on OpenStreetMap...")
    hit = geocode(query)
    s, n, w, e = (float(v) for v in hit['boundingbox'])
    lat0, lon0 = 0.5 * (s + n), 0.5 * (w + e)
    pad_y, pad_x = 0.01 * (n - s), 0.01 * (e - w)
    s, n, w, e = s - pad_y, n + pad_y, w - pad_x, e + pad_x
    x0, y0 = to_metres(w, s, lon0, lat0)
    x1, y1 = to_metres(e, n, lon0, lat0)
    area = (x1 - x0) * (y1 - y0) / 1e6
    if area > MAX_AREA_KM2:
        raise ValueError(f"{hit.get('display_name', query)} spans {area:,.0f} km² - more than the "
                         f"{MAX_AREA_KM2:,.0f} km² one download allows. Try a smaller city, or a district "
                         "of this one (e.g. 'Manhattan' rather than 'New York').")
    name = hit.get('name') or query
    parts = tiles((s, w, n, e), area)
    # Each finished part is kept until the whole city is in, so a failed download resumes where it stopped.
    part_dir = os.path.join(CITY_DIR, "_parts", f"{slug(query)}_{max_class}")
    os.makedirs(part_dir, exist_ok=True)

    def fetch_part(fname, q, what, split_ok=False):
        import gzip
        fp = os.path.join(part_dir, fname)
        if os.path.exists(fp):
            with gzip.open(fp, 'rt', encoding='utf-8') as f:
                return json.load(f)
        els = _overpass(q, progress, what, split_ok)
        with gzip.open(fp + ".part", 'wt', encoding='utf-8') as f:
            json.dump(els, f)
        os.replace(fp + ".part", fp)
        return els

    def fetch_tile(tag, t, what, depth=0):
        """A tile's elements; a tile too busy for the server is split into four, up to twice."""
        try:
            return fetch_part(f"{tag}.json.gz", overpass_query(t, max_class), what, split_ok=depth < 2)
        except _TooBig:
            progress(f"{what}: too much at once for the server - splitting it into four smaller parts...")
            out = []
            for j, sub in enumerate(tiles(t, 4.0, 1.0)):
                out += fetch_tile(f"{tag}{'abcd'[j]}", sub, f"{what}{'abcd'[j]}", depth + 1)
            return out

    elements, seen = [], set()
    for i, t in enumerate(parts):
        progress(f"Downloading {name} from OpenStreetMap: part {i + 1} of {len(parts)} ({area:,.0f} km\u00b2 "
                 "in all)...")
        for el in fetch_tile(f"{i}_of_{len(parts)}", t, f"Part {i + 1}"):
            if el.get('id') not in seen:                 # ways crossing a tile edge come back twice
                seen.add(el.get('id'))
                elements.append(el)
    progress(f"Downloading {name} from OpenStreetMap: lakes, rivers and parks...")
    elements += fetch_part("relations.json.gz", relations_query((s, w, n, e)), "Water and parks")
    progress(f"Processing {len(elements):,} OpenStreetMap features...")
    roads, cls, extras, kinds = parse(elements, lon0, lat0, (float(x0), float(y0), float(x1), float(y1)))
    if not roads:
        raise ValueError(f"OpenStreetMap returned no roads for {name}.")
    meta = {'query': query, 'name': name, 'display_name': hit.get('display_name', name),
            'osm_relation': int(hit['osm_id']), 'center': [lon0, lat0],
            'bbox': [w, s, e, n], 'bbox_m': [float(x0), float(y0), float(x1), float(y1)],
            'boundary': hit['geojson'], 'max_class': int(max_class),
            'downloaded': time.strftime("%Y-%m-%d"), 'source': "OpenStreetMap via Nominatim and the Overpass API",
            'licence': "(c) OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright)"}
    data = {'meta': meta, 'roads': roads, 'road_cls': np.asarray(cls, int), 'extras': extras,
            'extra_kind': np.asarray(kinds, int)}
    save(data, cache_path(query))
    import shutil
    shutil.rmtree(part_dir, ignore_errors=True)
    return data


# ---------------------------------------------------------------------------------------------
# Layout and strokes
# ---------------------------------------------------------------------------------------------
def margins(pw, ph):
    return float(np.clip(0.05 * min(pw, ph), 5.0, 12.0))


def title_caps(pw, ph, line_mm):
    from decorations import min_cap_for_pen
    floor = min_cap_for_pen(line_mm)
    cap = max(floor * 1.3, float(np.clip(0.04 * min(pw, ph), 3.0, 10.0)))
    return cap, max(floor, 0.42 * cap)


def map_box(pw, ph, title=False, line_mm=0.38):
    """(x0, y0, x1, y1) paper box (y up from the bottom edge) that the map is fitted into: the page inside the
    margins. The title sits in the bottom-left corner and takes no band of its own: the map moves up only as
    far as it has room to spare, and any road under the title is cut away around it."""
    m = margins(pw, ph)
    return m, m, pw - m, ph - m


def city_name(meta):
    """The city's name for the title: OpenStreetMap's name, plus a short abbreviation the user typed after a
    comma ('Washington, D.C.' -> 'Washington DC', 'Portland, OR' -> 'Portland OR', 'Paris, France' -> 'Paris')."""
    name = meta.get('name') or ''
    parts = [p.strip() for p in (meta.get('query') or '').split(',')]
    if parts and parts[0] and parts[0].lower() == name.lower():
        name = parts[0]                                   # the user's own spelling
    for p in parts[1:]:
        a = p.replace('.', '').replace(' ', '')
        if 1 < len(a) <= 3 and a.isalpha() and p.upper() == p:      # 'D.C.', 'NY', 'OR' - not 'France'
            return f"{name} {a.upper()}"
    return name


def default_title(meta):
    lon, lat = meta['center']
    return (f"{city_name(meta)} | {abs(lat):.4f}° {'N' if lat >= 0 else 'S'}   "
            f"{abs(lon):.4f}° {'E' if lon >= 0 else 'W'}")


def _drop_small(lines, min_size):
    """(lines whose extent - the larger side of their bounding box - is at least min_size mm, number dropped)."""
    if min_size <= 0 or not lines:
        return lines, 0
    keep = [l for l in lines if float(np.ptp(l, axis=0).max()) >= min_size]
    return keep, len(lines) - len(keep)


def _loose_bits(lines, min_size, touch_mm=0.05):
    """Road version of _drop_small: a boolean 'keep' per line, False only for bits smaller than min_size that
    come within touch_mm of no other line (stray dots, and the dashes left where a road runs along the clip
    edge). A short block between two junctions touches its neighbours, so it stays: no gaps in the grid."""
    small = [i for i, l in enumerate(lines) if min_size > 0 and float(np.ptp(l, axis=0).max()) < min_size]
    keep = [True] * len(lines)
    if not small:
        return keep
    import shapely
    from shapely.geometry import LineString
    geoms = [LineString(l) for l in lines]
    tree = shapely.STRtree(geoms)
    for i in small:
        near = tree.query(geoms[i], predicate='dwithin', distance=touch_mm)
        if not any(j != i for j in near):
            keep[i] = False
    return keep


def _cut_out(lines, box):
    """lines (paper mm) with the parts inside box (x0, y0, x1, y1) removed."""
    if box is None or not lines:
        return lines
    from shapely.geometry import LineString, box as sbox
    import shapely
    x0, y0, x1, y1 = box
    hole = sbox(x0, y0, x1, y1)
    out = []
    for l in lines:
        if l[:, 0].max() < x0 or l[:, 0].min() > x1 or l[:, 1].max() < y0 or l[:, 1].min() > y1:
            out.append(l)
            continue
        for g in shapely.get_parts(LineString(l).difference(hole)):
            if g.geom_type == 'LineString' and len(g.coords) >= 2:
                out.append(np.asarray(g.coords))
    return out


def fit_rect(rect_m, pw, ph, title, line_mm=0.38):
    """Grows a rectangle (x0, y0, x1, y1 in metres) about its centre to the map box's shape."""
    bx0, by0, bx1, by1 = map_box(pw, ph, title, line_mm)
    want = (bx1 - bx0) / max(by1 - by0, 1e-6)
    x0, y0, x1, y1 = rect_m
    cx, cy, w, h = 0.5 * (x0 + x1), 0.5 * (y0 + y1), max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    if w / h < want:
        w = h * want
    else:
        h = w / want
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def chain_lines(lines, tol=JOIN_TOL_MM):
    """Joins polylines that share end points into longer strokes. At a junction the walk carries on along
    the straightest unused piece, so a street drawn as many OSM pieces becomes one stroke. Returns a list
    of (n, 2) arrays covering every input piece exactly once."""
    lines = [np.asarray(l, float) for l in lines if len(l) >= 2]
    n = len(lines)
    if n == 0:
        return []
    key = lambda p: (int(round(p[0] / tol)), int(round(p[1] / tol)))
    ends = defaultdict(list)                       # node -> [(line, end 0|1)]
    for i, l in enumerate(lines):
        ends[key(l[0])].append((i, 0))
        ends[key(l[-1])].append((i, 1))
    used = np.zeros(n, bool)

    def leave_dir(i, end):                         # unit vector leaving the node along line i
        l = lines[i]
        a, b = (l[0], l[1]) if end == 0 else (l[-1], l[-2])
        d = b - a
        nrm = math.hypot(d[0], d[1])
        return d / nrm if nrm > 0 else d

    out = []
    # dead ends first (odd nodes), so strokes start at the ends of streets rather than in the middle
    starts = [k for k, v in ends.items() if len(v) % 2 == 1] + [k for k, v in ends.items() if len(v) % 2 == 0]
    for k in starts:
        while True:
            cand = [(i, e) for i, e in ends[k] if not used[i]]
            if not cand:
                break
            i, e = cand[0]
            parts = []
            while True:
                used[i] = True
                seg = lines[i] if e == 0 else lines[i][::-1]
                parts.append(seg if not parts else seg[1:])
                node = key(seg[-1])
                d_in = seg[-1] - seg[-2]
                nrm = math.hypot(d_in[0], d_in[1])
                d_in = d_in / nrm if nrm > 0 else d_in
                nxt = [(j, f) for j, f in ends[node] if not used[j]]
                if not nxt:
                    break
                i, e = max(nxt, key=lambda c: float(np.dot(d_in, leave_dir(*c))))
            out.append(np.concatenate(parts))
    return out


def _clip_and_fit(lines, clip, rect, xform, simplify_m):
    """lines (metres) -> clipped, simplified lines in paper mm. clip: shapely polygon or None; rect:
    (x0, y0, x1, y1) metres or None."""
    import shapely
    if not lines:
        return []
    xy = np.concatenate(lines)
    idx = np.repeat(np.arange(len(lines)), [len(l) for l in lines])
    geoms = shapely.linestrings(xy, indices=idx)
    if rect is not None:
        geoms = shapely.clip_by_rect(geoms, *rect)
    if clip is not None:
        shapely.prepare(clip)
        inside = shapely.contains_properly(clip, geoms)
        touch = ~inside & shapely.intersects(clip, geoms)
        geoms = np.concatenate([geoms[inside], shapely.intersection(geoms[touch], clip)])
    geoms = shapely.simplify(geoms, simplify_m, preserve_topology=False)
    parts = shapely.get_parts(geoms)
    parts = parts[shapely.get_type_id(parts) == 1]               # LineStrings only
    if len(parts) == 0:
        return []
    coords, ix = shapely.get_coordinates(parts, return_index=True)
    coords = xform(coords)
    cut = np.flatnonzero(np.diff(ix)) + 1
    return [c for c in np.split(coords, cut) if len(c) >= 2]


def crowding(lines_widths, extent, window=CROWD_WINDOW_MM, limit=CROWD_LIMIT):
    """Where the ink would merge into solid black. The strokes are drawn at their true pen widths on a
    fine raster (overlapping strokes, e.g. both halves of a dual carriageway, count once), then the inked
    share of the paper is averaged over a sliding window a few mm across: above `limit`, neighbouring lines
    leave no visible gap. A plain crossing or two streets a mm apart stay well under it.
    lines_widths: [(lines in paper mm, pen width mm)]; extent (x0, x1, y0, y1) in paper mm.
    Returns (fraction of the ink lying in crowded places, boolean grid of ~0.5 mm cells (rows from y0 up),
    grid extent)."""
    from scipy import ndimage
    x0, x1, y0, y1 = extent
    px = max(0.075, max(x1 - x0, y1 - y0) / 3000.0)            # raster cell (mm): 0.38 / 0.5 / 0.7 mm pens come out 5 / 7 / 9 cells wide
    nx, ny = int(math.ceil((x1 - x0) / px)) + 1, int(math.ceil((y1 - y0) / px)) + 1
    ink = np.zeros((ny, nx), bool)
    for lines, width in lines_widths:
        lines = [l for l in lines if len(l) >= 2]
        if not lines:
            continue
        a = np.concatenate([l[:-1] for l in lines])
        b = np.concatenate([l[1:] for l in lines])
        seg = np.hypot(*(b - a).T)
        k = np.maximum(1, np.ceil(seg / (0.5 * px)).astype(int))
        rep = np.repeat(np.arange(len(seg)), k)
        t = (np.arange(len(rep)) - np.repeat(np.cumsum(k) - k, k)) / np.repeat(k, k)
        pts = np.concatenate([a[rep] + (b[rep] - a[rep]) * t[:, None], b])
        m = np.zeros_like(ink)
        m[np.clip(((pts[:, 1] - y0) / px).astype(int), 0, ny - 1),
          np.clip(((pts[:, 0] - x0) / px).astype(int), 0, nx - 1)] = True
        r = 0.5 * float(width) / px
        if r >= 0.75:
            kk = int(math.ceil(r))
            yy, xx = np.mgrid[-kk:kk + 1, -kk:kk + 1]
            m = ndimage.binary_dilation(m, structure=(xx * xx + yy * yy) <= r * r + 0.25)
        ink |= m
    cover = ndimage.uniform_filter(ink.astype(np.float32), size=max(3, int(round(window / px))), mode='constant')
    full = (cover > limit) & ink
    n_ink = int(ink.sum())
    frac = float(full.sum()) / n_ink if n_ink else 0.0
    f = max(1, int(round(0.5 / px)))                              # ~0.5 mm cells for the preview overlay
    gy, gx = ny // f, nx // f
    grid = full[:gy * f, :gx * f].reshape(gy, f, gx, f).any(axis=(1, 3))
    return frac, grid, (x0, x0 + gx * f * px, y0, y0 + gy * f * px)


def build_city_map(data, opts, pw, ph):
    """Strokes for the paper. opts: min_class, major_upto, single_pen, pen_major, pen_minor, pen_extras,
    width_major, width_minor, width_extras (mm), extras (set of EXTRAS keys), clip ('boundary'|'rectangle'),
    rect (lon0, lat0, lon1, lat1) for 'rectangle', title (bool), title_text ('Title | subtitle', blank =
    default), frame (bool), min_shape_mm (extra shapes smaller than this are skipped, default MIN_SHAPE_MM),
    crowding (bool, default True: False leaves the crowding check, about a second
    on a big map, to a later crowding(info['ink'], info['map_extent']) call). Returns (segments, masks, info)."""
    from decorations import vector_text
    meta = data['meta']
    lon0, lat0 = meta['center']
    min_class = int(opts.get('min_class', DEFAULT_MIN_CLASS))
    major_upto = int(opts.get('major_upto', DEFAULT_MAJOR_UPTO))
    single = bool(opts.get('single_pen'))
    pen_major, pen_minor = opts.get('pen_major') or 'pen_1', opts.get('pen_minor') or 'base'
    if single:
        pen_major = pen_minor
    pen_extras = opts.get('pen_extras') or pen_minor
    w_major = float(opts.get('width_major', 0.7))
    w_minor = float(opts.get('width_minor', 0.38))
    w_extras = float(opts.get('width_extras', w_minor))
    extras_on = set(opts.get('extras') or ())
    min_shape = float(opts.get('min_shape_mm', MIN_SHAPE_MM))
    n_skipped = 0
    title_on = bool(opts.get('title', True))

    boundary = boundary_metres(meta)
    rect = None
    if opts.get('clip') == 'rectangle' and opts.get('rect'):
        a, b, c, d = opts['rect']
        rx0, ry0 = to_metres(a, b, lon0, lat0)
        rx1, ry1 = to_metres(c, d, lon0, lat0)
        rect = (float(min(rx0, rx1)), float(min(ry0, ry1)), float(max(rx0, rx1)), float(max(ry0, ry1)))
        clip_poly, region = None, rect
    else:
        clip_poly, region = boundary, boundary.bounds

    yshift = ph / 2.0                                 # app paper frame: y from -ph/2 to ph/2
    segs = []

    def add(layer, pen, lines):
        for l in lines:
            segs.append({'type': 'city', 'layer': layer, 'seg': l, 'level': 0, 'pen_id': pen})

    # ---- title, bottom left: the city's name, then the coordinates in smaller capitals under it
    m = margins(pw, ph)
    title_box = None                                  # paper mm, y up from the bottom edge
    if title_on:
        raw = (opts.get('title_text') or "").strip() or default_title(meta)
        title, _, sub = (x.strip() for x in raw.partition("|"))
        cap, cap_sub = title_caps(pw, ph, w_minor)
        y, boxes = m, []
        if sub:
            polys, box = vector_text(sub.upper(), m, y, cap_sub, anchor='left')
            add('labels', pen_minor, [p - [0, yshift] for p in polys])
            boxes.append(box)
            y = (box[3] if box else y + cap_sub) + 0.9 * cap_sub
        if title:
            polys, box = vector_text(title.upper(), m, y, cap, anchor='left')
            add('labels', pen_major if not single else pen_minor, [p - [0, yshift] for p in polys])
            boxes.append(box)
        boxes = [b for b in boxes if b]
        if boxes:
            pad = 1.5                                 # clear paper around the lettering
            title_box = (min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
                         max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad)

    # ---- fit the region into the map box (the whole page inside the margins)
    bx0, by0, bx1, by1 = map_box(pw, ph)
    gx0, gy0, gx1, gy1 = region
    s = min((bx1 - bx0) / max(gx1 - gx0, 1e-9), (by1 - by0) / max(gy1 - gy0, 1e-9))     # mm per metre
    ox = bx0 + 0.5 * ((bx1 - bx0) - (gx1 - gx0) * s)
    slack = (by1 - by0) - (gy1 - gy0) * s             # spare height
    need = (title_box[3] - by0) if title_box else 0.0  # height the title needs above the bottom margin
    oy = by0 + min(slack, max(0.5 * slack, need))     # centred, but lifted clear of the title if there's room
    cut = None
    if title_box:
        cut = (title_box[0], title_box[1] - yshift, title_box[2], title_box[3] - yshift)

    def xform(c):
        return np.column_stack([ox + (c[:, 0] - gx0) * s, oy + (c[:, 1] - gy0) * s - yshift])

    simp = SIMPLIFY_MM / s
    roads, rcls = data['roads'], np.asarray(data['road_cls'])
    keep = [i for i in range(len(roads)) if rcls[i] <= min_class]
    major = [roads[i] for i in keep if rcls[i] <= major_upto]
    minor = [roads[i] for i in keep if rcls[i] > major_upto]

    major_mm = _clip_and_fit(major, clip_poly, rect, xform, simp)
    minor_mm = _clip_and_fit(minor, clip_poly, rect, xform, simp)
    loose = min(1.0, min_shape)                       # stray road bits under 1 mm that touch nothing
    if single:
        roads_mm = _cut_out(chain_lines(major_mm + minor_mm), cut)
        ok = _loose_bits(roads_mm, loose)
        n_skipped += ok.count(False)
        roads_mm = [l for l, k in zip(roads_mm, ok) if k]
        add('city_roads', pen_minor, roads_mm)
        ink = [(roads_mm, w_minor)]
    else:
        major_mm, minor_mm = _cut_out(chain_lines(major_mm), cut), _cut_out(chain_lines(minor_mm), cut)
        ok = _loose_bits(major_mm + minor_mm, loose)      # a minor road touching a major one isn't loose
        n_skipped += ok.count(False)
        nmaj = len(major_mm)
        major_mm = [l for l, k in zip(major_mm, ok[:nmaj]) if k]
        minor_mm = [l for l, k in zip(minor_mm, ok[nmaj:]) if k]
        add('city_major', pen_major, major_mm)
        add('city_minor', pen_minor, minor_mm)
        ink = [(major_mm, w_major), (minor_mm, w_minor)]

    ex, ek = data['extras'], np.asarray(data['extra_kind'])
    for kind in EXTRA_KINDS:
        if kind not in extras_on:
            continue
        lines = [ex[i] for i in range(len(ex)) if ek[i] == EXTRA_KINDS.index(kind)]
        mm = chain_lines(_clip_and_fit(lines, clip_poly, rect, xform, SIMPLIFY_EXTRAS_MM / s))
        # after joining (a long shore made of short pieces stays) and after the title cut (no slivers left)
        mm, n = _drop_small(_cut_out(mm, cut), min_shape)
        n_skipped += n
        add(EXTRAS[kind][1], pen_extras, mm)
        ink.append((mm, w_extras))
    if 'outline' in extras_on:
        from shapely import get_parts
        rings = []
        for p in get_parts(boundary):
            if p.geom_type == 'Polygon':
                rings += [p.exterior] + list(p.interiors)
        # only the city's own edge: clipping a line to the rectangle adds no box edges
        mm = chain_lines(_clip_and_fit([np.asarray(r.coords) for r in rings], None, rect, xform,
                                       SIMPLIFY_EXTRAS_MM / s))
        mm, n = _drop_small(_cut_out(mm, cut), min_shape)
        n_skipped += n
        add('city_outline', pen_extras, mm)
        ink.append((mm, w_extras))

    # ---- frame
    masks = []
    if opts.get('frame'):
        f = 2.0
        add('frame', pen_minor, [np.array([[f, f], [pw - f, f], [pw - f, ph - f], [f, ph - f], [f, f]]) - [0, yshift]])

    gx = (ox, ox + (gx1 - gx0) * s, oy - yshift, oy + (gy1 - gy0) * s - yshift)
    frac, full, gext = crowding(ink, gx) if opts.get('crowding', True) else (None, None, None)
    ink_m = sum(float(np.sum(np.hypot(*np.diff(l, axis=0).T))) for lines, _ in ink for l in lines) / 1000.0
    info = {'scale_m_per_mm': 1.0 / s, 'width_km': (gx1 - gx0) / 1000.0, 'height_km': (gy1 - gy0) / 1000.0,
            'n_strokes': sum(len(lines) for lines, _ in ink), 'ink_m': ink_m,
            'crowded_frac': frac, 'crowd_grid': full, 'crowd_extent': gext, 'ink': ink, 'map_extent': gx,
            'region_m': tuple(float(v) for v in region), 'n_major': len(major), 'n_minor': len(minor),
            'n_skipped': n_skipped}
    return segs, masks, info
