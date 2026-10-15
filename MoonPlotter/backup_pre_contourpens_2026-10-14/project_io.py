"""Project files (.topo.json) and reusable style presets (presets.json)."""
import json
import os

import numpy as np

PROJECT_VERSION = 1
PRESETS_FILE = "presets.json"

# Keys of the UI state that make up a *style* (not the place): applied by presets.
STYLE_KEYS = [
    'contours_on', 'density', 'auto_index', 'index_every', 'blur', 'simplify', 'smooth', 'tanaka', 'ocean',
    'blue_water', 'plot_borders',
    'fill_on', 'pattern', 'tone_source', 'inks', 'mono_pen', 'spacing', 'tone', 'auto_contrast', 'join',
    'deco_title', 'deco_scale', 'deco_north', 'deco_graticule', 'deco_frame', 'site_labels',
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
