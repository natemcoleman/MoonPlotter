"""Project files (.topo.json) and reusable style presets (presets.json)."""
import json
import os

import numpy as np

PROJECT_VERSION = 1
PRESETS_FILE = "presets.json"

# Keys of the UI state that make up a *style* (not the place): applied by presets.
STYLE_KEYS = [
    'contours_on', 'density', 'auto_index', 'index_every', 'blur', 'simplify', 'smooth', 'tanaka', 'ocean',
    'blue_water', 'plot_borders', 'contour_pen', 'index_pen', 'water_pen', 'border_pen',
    'fill_on', 'pattern', 'tone_source', 'inks', 'mono_pen', 'spacing', 'tone', 'auto_contrast', 'join',
    'deco_title', 'deco_scale', 'deco_north', 'deco_graticule', 'deco_frame', 'site_labels', 'deco_text_size', 'deco_scale_units',
    'deco_sub_planet', 'deco_sub_coords', 'deco_sub_interval',
]

BUILTIN_PRESETS = {
    "Classic contours": {
        'contours_on': True, 'fill_on': False, 'density': 0, 'tanaka': False, 'smooth': 640, 'simplify': 40,
        'deco_title': True, 'deco_scale': True, 'deco_north': True, 'deco_graticule': 'Off', 'deco_frame': False},
    "Illuminated relief (Tanaka)": {
        'contours_on': True, 'fill_on': False, 'density': 40, 'tanaka': True, 'smooth': 640, 'simplify': 40,
        'deco_title': True, 'deco_scale': True, 'deco_north': False, 'deco_graticule': 'Off', 'deco_frame': True},
    "Hachures + contours": {
        'contours_on': True, 'fill_on': True, 'density': -60, 'pattern': "Hachures (Slope)",
        'mono_pen': 'base', 'spacing': 1.2, 'tone': 0, 'tanaka': False,
        'deco_title': True, 'deco_scale': True, 'deco_north': True, 'deco_graticule': 'Edge ticks', 'deco_frame': True},
    "Hillshade crosshatch": {
        'contours_on': False, 'fill_on': True, 'pattern': "Multi-Pass Crosshatch", 'tone_source': "Hillshade",
        'inks': "Monochrome", 'mono_pen': 'base', 'spacing': 0.8, 'tone': 0, 'auto_contrast': True,
        'join': True, 'deco_title': True, 'deco_scale': True, 'deco_north': False, 'deco_frame': True},
    "Colour stipple": {
        'contours_on': False, 'fill_on': True, 'pattern': "Stippling", 'tone_source': "Color map × Hillshade",
        'inks': "Color separation", 'spacing': 1.0,
        'tone': -20, 'auto_contrast': True, 'deco_title': True, 'deco_scale': False, 'deco_frame': False},
}


# Colourways: paper colour + every pen choice, switched in one go (Paper > Colourway).
COLORWAYS_FILE = "colorways.json"
COLORWAY_KEYS = ['paper_color', 'contour_pen', 'index_pen', 'water_pen', 'border_pen', 'mono_pen', 'deco_pen',
                 'mission_pen_labels', 'mission_pen_orbits', 'mission_pen_belt', 'ski_run_pen', 'ski_lift_pen']
BUILTIN_COLORWAYS = {
    "Light paper \u00b7 black G2 (thin / thick)": {
        'paper_color': '#FFFFFF', 'contour_pen': 'base', 'index_pen': 'pen_1', 'water_pen': 'base',
        'border_pen': 'pen_1', 'mono_pen': 'pen_1', 'deco_pen': 'base',
        'mission_pen_labels': 'base', 'mission_pen_orbits': 'base', 'mission_pen_belt': 'base',
        'ski_run_pen': 'base', 'ski_lift_pen': 'pen_1'},
    "Dark paper \u00b7 white gel 0.3 / 0.4": {
        'paper_color': '#1C1C1C', 'contour_pen': 'white_gr05', 'index_pen': 'white_gr08',
        'water_pen': 'white_gr05', 'border_pen': 'white_gr08', 'mono_pen': 'white_gr08', 'deco_pen': 'white_gr05',
        'mission_pen_labels': 'white_gr05', 'mission_pen_orbits': 'white_gr05', 'mission_pen_belt': 'white_gr05',
        'ski_run_pen': 'white_gr05', 'ski_lift_pen': 'white_gr08'},
}


def _read_json_dict(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        print(f"Warning: could not read {path} ({e})")
        return {}


def load_colorways():
    out = dict(BUILTIN_COLORWAYS)
    out.update(_read_json_dict(COLORWAYS_FILE))
    return out


def save_user_colorway(name, values):
    user = _read_json_dict(COLORWAYS_FILE)
    user[name] = {k: v for k, v in values.items() if k in COLORWAY_KEYS and v is not None}
    with open(COLORWAYS_FILE, 'w') as f:
        json.dump(user, f, indent=2)


def delete_user_colorway(name):
    user = _read_json_dict(COLORWAYS_FILE)
    if name not in user:
        return False
    del user[name]
    with open(COLORWAYS_FILE, 'w') as f:
        json.dump(user, f, indent=2)
    return True


# Favourite paper colours and regions (the star buttons next to those drop-downs). Per machine.
FAVORITES_FILE = "favorites.json"


def load_favorites(default_papers=(), default_regions=()):
    d = _read_json_dict(FAVORITES_FILE)
    return {'paper_colors': list(d.get('paper_colors', default_papers)),
            'regions': list(d.get('regions', default_regions))}


def save_favorites(fav):
    with open(FAVORITES_FILE, 'w') as f:
        json.dump({'paper_colors': list(fav.get('paper_colors', [])), 'regions': list(fav.get('regions', []))},
                  f, indent=2)


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    return obj


def save_project(path, state):
    data = {'app': 'Topo Plotter Studio', 'version': PROJECT_VERSION, 'state': _jsonable(state)}
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def load_project(path):
    with open(path) as f:
        data = json.load(f)
    if 'state' not in data:
        raise ValueError("Not a Topo Plotter Studio project file.")
    return data['state']


def load_presets():
    presets = dict(BUILTIN_PRESETS)
    if os.path.exists(PRESETS_FILE):
        try:
            with open(PRESETS_FILE) as f:
                user = json.load(f)
            if isinstance(user, dict):
                presets.update(user)
        except Exception as e:
            print(f"Warning: could not read {PRESETS_FILE} ({e})")
    return presets


def save_user_preset(name, style):
    user = {}
    if os.path.exists(PRESETS_FILE):
        try:
            with open(PRESETS_FILE) as f:
                user = json.load(f)
        except Exception:
            user = {}
    user[name] = _jsonable({k: v for k, v in style.items() if k in STYLE_KEYS})
    with open(PRESETS_FILE, 'w') as f:
        json.dump(user, f, indent=2)


def delete_user_preset(name):
    if not os.path.exists(PRESETS_FILE):
        return False
    with open(PRESETS_FILE) as f:
        user = json.load(f)
    if name in user:
        del user[name]
        with open(PRESETS_FILE, 'w') as f:
            json.dump(user, f, indent=2)
        return True
    return False
