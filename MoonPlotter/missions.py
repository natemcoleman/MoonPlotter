"""Interplanetary missions: catalogue, NASA JPL Horizons trajectory download + cache, planet orbits.

Spacecraft trajectories are heliocentric positions (ecliptic J2000, AU) from JPL Horizons, fetched the
first time a mission is used and cached in MISSIONS_DIR/<id>.json (afterwards everything works offline).
Planet orbits use JPL's approximate Keplerian elements (Standish, valid 1800-2050) - plenty for drawing
orbit rings; flyby markers are placed on the spacecraft's own (real) position at the flyby date.
"""
import datetime as _dt
import json
import math
import os
import re
import urllib.parse

import numpy as np

import net

MISSIONS_DIR = "missions"
HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"

# ----------------------------------------------------------------------------------------------
# Catalogue. events: (date, body, label). 'apl': 'led' (APL built/led the spacecraft) or
# 'instrument' (APL-built instrument aboard); 'apl_note' says what.
# ----------------------------------------------------------------------------------------------
MISSIONS = [
    {'key': 'pioneer10', 'name': 'Pioneer 10', 'hid': '-23', 'launch': '1972-03-03', 'end': '2003-01-23',
     'events': [('1973-12-04', 'Jupiter', 'Jupiter')], 'escapes': True},
    {'key': 'pioneer11', 'name': 'Pioneer 11', 'hid': '-24', 'launch': '1973-04-06', 'end': '1995-09-30',
     'events': [('1974-12-03', 'Jupiter', 'Jupiter'), ('1979-09-01', 'Saturn', 'Saturn')], 'escapes': True},
    {'key': 'voyager1', 'name': 'Voyager 1', 'hid': '-31', 'launch': '1977-09-05', 'end': None,
     'events': [('1979-03-05', 'Jupiter', 'Jupiter'), ('1980-11-12', 'Saturn', 'Saturn')], 'escapes': True,
     'apl': 'instrument', 'apl_note': 'LECP particle instrument built by APL'},
    {'key': 'voyager2', 'name': 'Voyager 2', 'hid': '-32', 'launch': '1977-08-20', 'end': None,
     'events': [('1979-07-09', 'Jupiter', 'Jupiter'), ('1981-08-25', 'Saturn', 'Saturn'),
                ('1986-01-24', 'Uranus', 'Uranus'), ('1989-08-25', 'Neptune', 'Neptune')], 'escapes': True,
     'apl': 'instrument', 'apl_note': 'LECP particle instrument built by APL'},
    {'key': 'galileo', 'name': 'Galileo', 'hid': '-77', 'launch': '1989-10-18', 'end': '1995-12-07',
     'events': [('1990-02-10', 'Venus', 'Venus'), ('1990-12-08', 'Earth', 'Earth'), ('1991-10-29', None, 'Gaspra'),
                ('1992-12-08', 'Earth', 'Earth'), ('1993-08-28', None, 'Ida'), ('1995-12-07', 'Jupiter', 'Jupiter')],
     'apl': 'instrument', 'apl_note': 'EPD energetic-particle detector built by APL'},
    {'key': 'cassini', 'name': 'Cassini', 'hid': '-82', 'launch': '1997-10-15', 'end': '2004-07-01',
     'events': [('1998-04-26', 'Venus', 'Venus'), ('1999-06-24', 'Venus', 'Venus'), ('1999-08-18', 'Earth', 'Earth'),
                ('2000-12-30', 'Jupiter', 'Jupiter'), ('2004-07-01', 'Saturn', 'Saturn')],
     'apl': 'instrument', 'apl_note': 'MIMI magnetospheric imaging instrument built by APL'},
    {'key': 'juno', 'name': 'Juno', 'hid': '-61', 'launch': '2011-08-05', 'end': '2016-07-05',
     'events': [('2013-10-09', 'Earth', 'Earth'), ('2016-07-05', 'Jupiter', 'Jupiter')],
     'apl': 'instrument', 'apl_note': 'JEDI energetic-particle instrument built by APL'},
    {'key': 'newhorizons', 'name': 'New Horizons', 'hid': '-98', 'launch': '2006-01-19', 'end': None,
     'events': [('2007-02-28', 'Jupiter', 'Jupiter'), ('2015-07-14', 'Pluto', 'Pluto'),
                ('2019-01-01', None, 'Arrokoth')], 'escapes': True,
     'apl': 'led', 'apl_note': 'Built and operated by APL'},
    {'key': 'messenger', 'name': 'MESSENGER', 'hid': '-236', 'launch': '2004-08-03', 'end': '2011-03-18',
     'events': [('2005-08-02', 'Earth', 'Earth'), ('2006-10-24', 'Venus', 'Venus'), ('2007-06-05', 'Venus', 'Venus'),
                ('2008-01-14', 'Mercury', 'Mercury'), ('2008-10-06', 'Mercury', 'Mercury'),
                ('2009-09-29', 'Mercury', 'Mercury'), ('2011-03-18', 'Mercury', 'Mercury orbit')],
     'apl': 'led', 'apl_note': 'Built and operated by APL'},
    {'key': 'parker', 'name': 'Parker Solar Probe', 'hid': '-96', 'launch': '2018-08-12', 'end': '2025-06-30',
     'events': [('2018-10-03', 'Venus', 'Venus'), ('2019-12-26', 'Venus', 'Venus'), ('2020-07-11', 'Venus', 'Venus'),
                ('2021-02-20', 'Venus', 'Venus'), ('2021-10-16', 'Venus', 'Venus'), ('2023-08-21', 'Venus', 'Venus'),
                ('2024-11-06', 'Venus', 'Venus')],
     'apl': 'led', 'apl_note': 'Built and operated by APL'},
    {'key': 'near', 'name': 'NEAR Shoemaker', 'hid': '-93', 'launch': '1996-02-17', 'end': '2001-02-12',
     'events': [('1997-06-27', None, 'Mathilde'), ('1998-01-23', 'Earth', 'Earth'), ('2000-02-14', None, 'Eros orbit')],
     'apl': 'led', 'apl_note': 'Built and operated by APL'},
    {'key': 'dart', 'name': 'DART', 'hid': '-135', 'launch': '2021-11-24', 'end': '2022-09-26',
     'events': [('2022-09-26', None, 'Dimorphos impact')],
     'apl': 'led', 'apl_note': 'Built and operated by APL'},
    {'key': 'europaclipper', 'name': 'Europa Clipper', 'hid': '-159', 'launch': '2024-10-14', 'end': '2030-04-11',
     'events': [('2025-03-01', 'Mars', 'Mars'), ('2026-12-03', 'Earth', 'Earth'), ('2030-04-11', 'Jupiter', 'Jupiter')],
     'apl': 'led', 'apl_note': 'Spacecraft developed by JPL and APL', 'planned_after': '2026-10-15'},
    {'key': 'stereoa', 'name': 'STEREO-A', 'hid': '-234', 'launch': '2006-10-26', 'end': '2023-08-12',
     'events': [], 'apl': 'led', 'apl_note': 'Built and operated by APL'},
    {'key': 'lucy', 'name': 'Lucy', 'hid': '-49', 'launch': '2021-10-16', 'end': '2033-03-02',
     'events': [('2022-10-16', 'Earth', 'Earth'), ('2023-11-01', None, 'Dinkinesh'), ('2024-12-12', 'Earth', 'Earth'),
                ('2025-04-20', None, 'Donaldjohanson'), ('2027-08-12', None, 'Eurybates')],
     'apl': 'instrument', 'apl_note': "L'LORRI camera built by APL", 'planned_after': '2026-10-15'},
    {'key': 'rosetta', 'name': 'Rosetta', 'hid': '-226', 'launch': '2004-03-02', 'end': '2014-08-06',
     'events': [('2005-03-04', 'Earth', 'Earth'), ('2007-02-25', 'Mars', 'Mars'), ('2007-11-13', 'Earth', 'Earth'),
                ('2009-11-13', 'Earth', 'Earth'), ('2014-08-06', None, 'Comet 67P')]},
    {'key': 'dawn', 'name': 'Dawn', 'hid': '-203', 'launch': '2007-09-27', 'end': '2015-03-06',
     'events': [('2009-02-17', 'Mars', 'Mars'), ('2011-07-16', None, 'Vesta'), ('2015-03-06', None, 'Ceres')]},
]
MISSION_BY_KEY = {m['key']: m for m in MISSIONS}

# ----------------------------------------------------------------------------------------------
# Planet orbits: JPL approximate Keplerian elements, J2000 ecliptic (E.M. Standish, 1800-2050 table)
# a [AU], e, I, L, long.peri, long.node [deg] and their rates per Julian century.
# ----------------------------------------------------------------------------------------------
PLANET_ELEMENTS = {
    'Mercury': ((0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593),
                (0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081)),
    'Venus': ((0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
              (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418)),
    'Earth': ((1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
              (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0)),
    'Mars': ((1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
             (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343)),
    'Jupiter': ((5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
                (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106)),
    'Saturn': ((9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448),
               (-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794)),
    'Uranus': ((19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503),
               (-0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589)),
    'Neptune': ((30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574),
                (0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.00508664)),
    'Pluto': ((39.48211675, 0.24882730, 17.14001206, 238.92903833, 224.06891629, 110.30393684),
              (-0.00031596, 0.00005170, 0.00004818, 145.20780515, -0.04062942, -0.01183482)),
}
PLANET_ORDER = ['Mercury', 'Venus', 'Earth', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto']
ASTEROID_BELT_AU = (2.15, 3.3)


def jd_from_date(s):
    d = _dt.datetime.strptime(s[:10], "%Y-%m-%d")
    return 2451545.0 + (d - _dt.datetime(2000, 1, 1, 12)).total_seconds() / 86400.0


def date_from_jd(jd):
    return _dt.datetime(2000, 1, 1, 12) + _dt.timedelta(days=float(jd) - 2451545.0)


def _elements(planet, jd):
    base, rate = PLANET_ELEMENTS[planet]
    T = (jd - 2451545.0) / 36525.0
    return [b + r * T for b, r in zip(base, rate)]


def _kepler_E(M, e):
    E = M + e * np.sin(M)
    for _ in range(12):
        E = E - (E - e * np.sin(E) - M) / (1 - e * np.cos(E))
    return E


def _orbit_xyz(a, e, I, w_peri, node, E):
    """Heliocentric ecliptic coordinates for eccentric anomaly E (array)."""
    xp = a * (np.cos(E) - e)
    yp = a * np.sqrt(1 - e * e) * np.sin(E)
    w = math.radians(w_peri - node)
    O, i = math.radians(node), math.radians(I)
    cw, sw, cO, sO, ci, si = math.cos(w), math.sin(w), math.cos(O), math.sin(O), math.cos(i), math.sin(i)
    x = (cw * cO - sw * sO * ci) * xp + (-sw * cO - cw * sO * ci) * yp
    y = (cw * sO + sw * cO * ci) * xp + (-sw * sO + cw * cO * ci) * yp
    z = (sw * si) * xp + (cw * si) * yp
    return x, y, z


def planet_position(planet, jd):
    a, e, I, L, wp, node = _elements(planet, jd)
    M = math.radians(((L - wp) + 180.0) % 360.0 - 180.0)
    x, y, z = _orbit_xyz(a, e, I, wp, node, _kepler_E(np.array([M]), e))
    return float(x[0]), float(y[0]), float(z[0])


def planet_orbit(planet, jd=2451545.0, n=360):
    """Closed (x, y) polyline of the orbit (projected onto the ecliptic), AU."""
    a, e, I, L, wp, node = _elements(planet, jd)
    E = np.linspace(0, 2 * np.pi, n + 1)
    x, y, _ = _orbit_xyz(a, e, I, wp, node, E)
    return np.column_stack([x, y])


# ----------------------------------------------------------------------------------------------
# Horizons
# ----------------------------------------------------------------------------------------------
_SPAN_RE = re.compile(r"(prior to|after) A\.D\. (\d{4})-([A-Z]{3})-(\d{2})")
_MON = {m: i for i, m in enumerate(['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV',
                                    'DEC'], 1)}


def parse_vectors(text):
    """Horizons VECTORS CSV (VEC_TABLE=1) -> (jd array, xyz array [AU or whatever OUT_UNITS was])."""
    if '$$SOE' not in text:
        return None, None
    block = text.split('$$SOE', 1)[1].split('$$EOE', 1)[0]
    jd, xyz = [], []
    for line in block.splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 5:
            continue
        try:
            jd.append(float(parts[0]))
            xyz.append([float(parts[2]), float(parts[3]), float(parts[4])])
        except ValueError:
            continue
    return np.array(jd), np.array(xyz)


def _horizons_query(hid, start, stop, step):
    params = {'format': 'json', 'COMMAND': f"'{hid}'", 'OBJ_DATA': "'NO'", 'MAKE_EPHEM': "'YES'",
              'EPHEM_TYPE': "'VECTORS'", 'CENTER': "'500@10'", 'START_TIME': f"'{start}'",
              'STOP_TIME': f"'{stop}'", 'STEP_SIZE': f"'{step}'", 'VEC_TABLE': "'1'", 'REF_PLANE': "'ECLIPTIC'",
              'OUT_UNITS': "'AU-D'", 'CSV_FORMAT': "'YES'"}
    url = HORIZONS_URL + '?' + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    data = json.loads(net.fetch_bytes(url, timeout=90).decode('utf-8', 'replace'))
    return (data.get('result') or '') + '\n' + (data.get('error') or '')


def _step_for(start, stop, target_points=2500):
    days = max(1.0, jd_from_date(stop) - jd_from_date(start))
    for s in (1, 2, 3, 5, 7, 10, 15, 20, 30):
        if days / s <= target_points:
            return f"{s} d"
    return "30 d"


def cache_path(m):
    return os.path.join(MISSIONS_DIR, f"{m['key']}.json")


def load_cached(m):
    p = cache_path(m)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            d = json.load(f)
        return np.array(d['jd']), np.array(d['xyz'])
    except Exception:
        return None


def fetch_mission(m, progress=lambda s: None, today=None):
    """Downloads (or loads from cache) the mission trajectory. Returns (jd, xyz AU)."""
    cached = load_cached(m)
    if cached is not None:
        return cached
    today = today or _dt.date.today().isoformat()
    start = (_dt.datetime.strptime(m['launch'], "%Y-%m-%d") + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    stop = m.get('end') or today
    if m.get('escapes') and not m.get('end'):
        stop = today
    for attempt in range(4):
        step = _step_for(start, stop)
        progress(f"Downloading {m['name']} trajectory from JPL Horizons ({start} to {stop}, every {step})...")
        text = _horizons_query(m['hid'], start, stop, step)
        jd, xyz = parse_vectors(text)
        if jd is not None and len(jd) > 2:
            os.makedirs(MISSIONS_DIR, exist_ok=True)
            with open(cache_path(m), 'w') as f:
                json.dump({'name': m['name'], 'hid': m['hid'], 'source': 'JPL Horizons', 'start': start,
                           'stop': stop, 'step': step, 'jd': jd.tolist(), 'xyz': np.round(xyz, 7).tolist()}, f)
            return jd, xyz
        # ephemeris span errors: move the start/stop inside the available data and retry
        hit = _SPAN_RE.search(text)
        if not hit:
            raise IOError(f"Horizons returned no data for {m['name']}:\n{text.strip()[:400]}")
        kind, y, mon, d = hit.groups()
        edge = _dt.date(int(y), _MON[mon], int(d))
        if kind == 'prior to':
            start = (edge + _dt.timedelta(days=1)).isoformat()
        else:
            stop = (edge - _dt.timedelta(days=1)).isoformat()
    raise IOError(f"Could not find a usable time span for {m['name']} in Horizons.")


def position_at(jd_arr, xyz, jd):
    """Linear interpolation of the trajectory at jd (None outside the data)."""
    if jd < jd_arr[0] or jd > jd_arr[-1]:
        return None
    return np.array([np.interp(jd, jd_arr, xyz[:, k]) for k in range(3)])
