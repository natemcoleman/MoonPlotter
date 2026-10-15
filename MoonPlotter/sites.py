"""Points of interest for each body: landing sites, landmarks and good plot subjects.

Each entry: (name, lat, lon_east, category, span_deg, note)
  lon is east-positive in -180..180 (planetocentric for Mars, as in the USGS MOLA/Viking mosaics).
  span_deg is a suggested selection width when you double-click the site to frame it.
Landing-site coordinates are the published landing / rover positions (rounded); large features use an
approximate centre.
"""
import numpy as np
from matplotlib.textpath import TextPath
from stroke_font import stroke_text
from matplotlib.font_manager import FontProperties

from config import PLANET_RADII
from geometry_utils import ortho_project

CATEGORY_STYLE = {
    # category: (map marker, colour on the source map)
    'Landing site': ('o', '#E8B04B'),
    'Mountain':     ('^', '#D9643A'),
    'Crater':       ('o', '#7FB3D5'),
    'Basin':        ('s', '#9B7FD4'),
    'Canyon':       ('v', '#D98C5F'),
    'Volcano':      ('^', '#E0533D'),
    'Lowest point': ('v', '#5DADE2'),
    'Landmark':     ('D', '#7DCEA0'),
}

SITES = {
    'Moon': [
        ("Apollo 11", 0.674, 23.473, 'Landing site', 8, "First crewed landing, Jul 1969 - Tranquility Base"),
        ("Apollo 12", -3.013, -23.422, 'Landing site', 8, "Nov 1969, next to the Surveyor 3 probe"),
        ("Apollo 14", -3.646, -17.472, 'Landing site', 8, "Feb 1971, Fra Mauro highlands"),
        ("Apollo 15", 26.132, 3.633, 'Landing site', 8, "Jul 1971, Hadley Rille - first lunar rover"),
        ("Apollo 16", -8.973, 15.501, 'Landing site', 8, "Apr 1972, Descartes highlands"),
        ("Apollo 17", 20.191, 30.772, 'Landing site', 8, "Dec 1972, Taurus-Littrow valley - last crewed landing"),
        ("Luna 9", 7.08, -64.37, 'Landing site', 8, "1966 - first soft landing on the Moon"),
        ("Luna 16", -0.51, 56.36, 'Landing site', 8, "1970 - first robotic sample return"),
        ("Lunokhod 1 (Luna 17)", 38.24, -35.00, 'Landing site', 8, "1970 - first planetary rover"),
        ("Lunokhod 2 (Luna 21)", 25.85, 30.45, 'Landing site', 8, "1973 rover, Le Monnier crater"),
        ("Surveyor 1", -2.47, -43.34, 'Landing site', 8, "1966 - first US soft landing"),
        ("Chang'e 3", 44.12, -19.51, 'Landing site', 8, "2013, Yutu rover - Mare Imbrium"),
        ("Chang'e 4", -45.44, 177.60, 'Landing site', 10, "2019 - first far-side landing, Von Karman crater"),
        ("Chang'e 5", 43.06, -51.92, 'Landing site', 8, "2020 sample return, Mons Rumker"),
        ("Chandrayaan-3", -69.37, 32.32, 'Landing site', 8, "2023 - nearest landing to the south pole at the time"),
        ("SLIM", -13.32, 25.25, 'Landing site', 8, "2024 Japanese precision lander"),
        ("Tycho", -43.31, -11.36, 'Crater', 8, "Young 85 km crater with the bright ray system"),
        ("Copernicus", 9.62, -20.08, 'Crater', 7, "93 km crater with terraced walls and central peaks"),
        ("Aristarchus & Vallis Schroteri", 24.5, -49.5, 'Crater', 10, "Brightest crater + the largest sinuous rille"),
        ("Plato", 51.6, -9.4, 'Crater', 6, "Flat dark-floored crater on the edge of Mare Imbrium"),
        ("Theophilus", -11.4, 26.4, 'Crater', 6, "100 km crater with a big central peak cluster"),
        ("Shackleton (south pole)", -89.67, 129.2, 'Crater', 6, "Permanently shadowed polar crater"),
        ("Montes Apenninus", 19.9, -2.9, 'Mountain', 14, "Rim of the Imbrium basin, peaks ~5 km high"),
        ("Selenean summit", 5.41, -158.64, 'Mountain', 12, "Highest point on the Moon (~10.8 km above datum)"),
        ("Rupes Recta (Straight Wall)", -22.1, -7.8, 'Landmark', 6, "110 km straight fault scarp"),
        ("Mare Imbrium", 32.8, -15.6, 'Basin', 34, "Huge lava-flooded impact basin"),
        ("Mare Orientale", -19.9, -94.7, 'Basin', 30, "Bullseye multi-ring basin on the western limb"),
        ("South Pole-Aitken basin", -53.0, -169.0, 'Basin', 60, "Largest, deepest impact basin; lowest point on the Moon"),
    ],
    'Mars': [
        ("Olympus Mons", 18.65, -133.80, 'Volcano', 26, "Tallest volcano in the Solar System (~22 km)"),
        ("Arsia Mons", -8.26, -120.09, 'Volcano', 14, "Southern Tharsis shield volcano"),
        ("Pavonis Mons", 1.48, -112.96, 'Volcano', 12, "Central Tharsis shield volcano"),
        ("Ascraeus Mons", 11.92, -104.08, 'Volcano', 14, "Northern Tharsis shield volcano"),
        ("Elysium Mons", 25.02, 147.21, 'Volcano', 14, "Main volcano of the Elysium rise"),
        ("Valles Marineris", -13.9, -59.2, 'Canyon', 50, "4,000 km canyon system, up to 7 km deep"),
        ("Noctis Labyrinthus", -6.8, -101.3, 'Canyon', 14, "Maze of fault valleys at the head of Valles Marineris"),
        ("Hellas Planitia", -42.4, 70.5, 'Lowest point', 40, "Giant impact basin - lowest point on Mars"),
        ("Argyre Planitia", -49.7, -43.4, 'Basin', 28, "Large southern impact basin"),
        ("Nili Fossae", 22.0, 75.0, 'Landmark', 10, "Ancient troughs rich in clay minerals"),
        ("Curiosity (Gale crater)", -4.59, 137.44, 'Landing site', 6, "2012 rover, climbing Mount Sharp"),
        ("Perseverance (Jezero crater)", 18.44, 77.45, 'Landing site', 4, "2021 rover in an ancient river delta"),
        ("Viking 1", 22.27, -47.95, 'Landing site', 8, "1976, Chryse Planitia - first successful Mars landing"),
        ("Viking 2", 47.64, 134.29, 'Landing site', 8, "1976, Utopia Planitia"),
        ("Mars Pathfinder", 19.13, -33.22, 'Landing site', 8, "1997, Sojourner rover - Ares Vallis"),
        ("Spirit (Gusev crater)", -14.57, 175.47, 'Landing site', 6, "2004 rover"),
        ("Opportunity (Meridiani)", -1.95, -5.53, 'Landing site', 6, "2004 rover, drove 45 km"),
        ("Phoenix", 68.22, -125.75, 'Landing site', 8, "2008 polar lander that touched water ice"),
        ("InSight", 4.50, 135.62, 'Landing site', 8, "2018 seismometer lander, Elysium Planitia"),
        ("Zhurong", 25.07, 109.93, 'Landing site', 8, "2021 Chinese rover, Utopia Planitia"),
    ],
    'Earth': [
        ("Mount Everest", 27.988, 86.925, 'Mountain', 2.0, "Highest point on Earth, 8,849 m"),
        ("Tibetan Plateau", 33.0, 88.0, 'Landmark', 22, "Highest, largest plateau - spectacular contours"),
        ("Grand Canyon", 36.10, -112.10, 'Canyon', 1.6, "1.8 km deep canyon of the Colorado River"),
        ("Death Valley", 36.25, -116.83, 'Lowest point', 1.6, "Lowest point in North America, -86 m"),
        ("Lake Tahoe", 39.09, -120.04, 'Landmark', 0.8, "Deep alpine lake - try the Tahoe bathymetry region"),
        ("Sierra Nevada / Mt Whitney", 36.58, -118.29, 'Mountain', 2.5, "Highest peak in the contiguous US"),
        ("Hawaii (Big Island)", 19.6, -155.5, 'Volcano', 2.4, "Mauna Kea & Mauna Loa, 10 km base-to-peak"),
        ("Mariana Trench", 11.35, 142.20, 'Lowest point', 8, "Challenger Deep - deepest ocean point, ~10.9 km"),
        ("Iceland", 64.9, -18.6, 'Volcano', 12, "Mid-Atlantic Ridge rising above sea level"),
        ("Alps / Mont Blanc", 45.83, 6.86, 'Mountain', 4, "Highest peak of the Alps"),
        ("Aconcagua", -32.65, -70.01, 'Mountain', 4, "Highest peak outside Asia"),
        ("Patagonia / Fitz Roy", -49.27, -73.04, 'Mountain', 3, "Granite spires and ice fields"),
        ("Southern Alps / Aoraki", -43.60, 170.14, 'Mountain', 4, "New Zealand's main divide"),
        ("Mount Fuji", 35.36, 138.73, 'Volcano', 1.6, "Near-perfect stratovolcano cone"),
        ("Kilimanjaro", -3.07, 37.35, 'Volcano', 1.6, "Free-standing volcano rising ~4.9 km above the plains"),
        ("Dead Sea", 31.50, 35.50, 'Lowest point', 2.0, "Lowest land on Earth, -430 m"),
        ("Cape Verde", 16.00, -24.00, 'Volcano', 4, "Volcanic island chain - nice ocean-depth plot"),
        ("Himalaya & Ganges plain", 28.5, 84.0, 'Mountain', 14, "Sharpest relief transition on Earth"),
    ],
}


def sites_for(planet):
    return SITES.get(planet, [])


def site_segments(planet, names, view_extent, params, pen_id='red_038', label=True):
    """Crosshair (+ optional vector label) strokes for the chosen sites, sized in paper mm."""
    chosen = [s for s in sites_for(planet) if s[0] in names]
    if not chosen:
        return []
    pw = float(params.get('paper_w_mm', 100.0))
    ph = float(params.get('paper_h_mm', 100.0))
    circle = params.get('circle_mode', False)
    R = PLANET_RADII.get(planet, 6371000.0)
    lon_min, lon_max, lat_min, lat_max = view_extent
    mid_lon = 0.5 * (lon_min + lon_max)
    deg2rad = np.pi / 180.0
    if circle:
        mm_per_unit_x = mm_per_unit_y = min(pw, ph) / (2.1 * R)
    else:
        mm_per_unit_x = pw / (lon_max - lon_min)
        mm_per_unit_y = ph / (lat_max - lat_min)
    arm_x, arm_y = 1.8 / mm_per_unit_x, 1.8 / mm_per_unit_y        # 1.8 mm crosshair arms

    out = []
    for name, lat, lon, cat, span, note in chosen:
        if circle:
            if abs((lon - mid_lon + 180) % 360 - 180) > 90:
                continue                                      # far hemisphere
            cx, cy = ortho_project(np.array([[lon, lat]]), R, mid_lon, deg2rad)[0]
        else:
            # bring the site into the selection's longitude frame (dateline-wrapped views)
            lon_w = lon
            while lon_w < lon_min - 1e-9: lon_w += 360.0
            while lon_w > lon_max + 1e-9: lon_w -= 360.0
            if not (lon_min <= lon_w <= lon_max and lat_min <= lat <= lat_max):
                continue
            cx, cy = lon_w, lat
        segs = [np.array([[cx - arm_x, cy], [cx + arm_x, cy]]),
                np.array([[cx, cy - arm_y], [cx, cy + arm_y]])]
        if label:
            cap = float(params.get('site_label_cap_mm', 2.4))
            res = stroke_text(name, 0.0, 0.0, cap)
            if res is not None:
                polys_mm = res[0]
            else:
                tp = TextPath((0, 0), name, size=cap / 0.73, prop=FontProperties(family='DejaVu Sans'))
                polys_mm = [p for p in tp.to_polygons() if len(p) >= 2]
            ox = cx + arm_x * 1.6
            oy = cy - 0.45 * cap / mm_per_unit_y
            for poly in polys_mm:
                segs.append(np.column_stack([ox + poly[:, 0] / mm_per_unit_x, oy + poly[:, 1] / mm_per_unit_y]))
        for s in segs:
            out.append({'type': 'border', 'layer': 'sites', 'seg': s, 'level': 0, 'pen_id': pen_id})
    return out
