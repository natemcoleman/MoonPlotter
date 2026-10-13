import os
import json

# ==========================================
# CONSTANTS & CONFIGURATION
# ==========================================
DATA_FILES = {
    'Earth': {'color': 'earth_color.tif', 'dem': 'BE_dem_v2.tif', 'multiplier': 3.28084},
    'Moon': {'color': 'lroc_color_16bit_srgb_4k.tif', 'dem': 'ldem_16.tif', 'multiplier': 3280.84},
    'Mars': {'color': 'mars_viking_color.tif', 'dem': 'mars_mola_dem_463m.tif', 'multiplier': 3.28084}
}

PLANET_RADII = {'Earth': 6371000.0, 'Moon': 1737400.0, 'Mars': 3389500.0}

PAPER_SIZES = {
    '100x100mm (Default)': (100.0, 100.0),
    'Ender3_Safe (200x200)': (200.0, 200.0),
    'A4': (297.0, 210.0),
    'Letter': (279.4, 215.9),
    'Custom (mm)': (150.0, 150.0)
}

PT_PER_MM = 72.0 / 25.4
PENS_FILE = "plotter_pens.json"

# Shortest stroke that survives the vpype 'filter --min-length' step at export.
# The hatch engine drops anything shorter so the preview matches the G-code.
MIN_SEGMENT_MM = 2.0

# Auto contour interval: pick the smallest "nice" interval that gives at most
# this many contour levels across the selection's relief.
AUTO_CONTOUR_TARGET_LINES = 20
NICE_INTERVALS_FT = [1, 2, 5, 10, 20, 25, 40, 50, 80, 100, 200, 250, 400, 500, 800, 1000,
                     2000, 2500, 4000, 5000, 10000, 20000, 25000, 50000]

# Longest side (pixels) of the DEM array handed to the contour engine. Larger
# windows are block-averaged down. 4000 px is ~20 px/mm on a 200 mm plot.
MAX_DEM_PX = 4000

# ==========================================
# HATCHING / COLOR SEPARATION
# ==========================================
# Each ink set: paper colour + list of (pen_id, base hatch angle in degrees).
# Ink colours used for separation come from each pen's 'color' in the pen list,
# so calibrate those hex values against a scan of real swatches for best results.
INK_SETS = {
    'Blue / Red / Orange on White': {
        'paper': '#FFFFFF',
        'inks': [('blue_038', 15.0), ('red_038', 75.0), ('orange_07', 45.0)],
    },
    'Cyan / Magenta on Yellow (Legacy)': {
        'paper': '#FFE14D',
        'inks': [('cyan_07', 15.0), ('pink_07', 75.0)],
    },
}
DEFAULT_INK_SET = 'Blue / Red / Orange on White'

MONO_INK_SET = {'paper': '#FFFFFF', 'inks': [('pen_1', 45.0)]}

DEFAULT_HATCH_SPACING_MM = 1.0


# ==========================================
# PEN PERSISTENCE HANDLERS
# ==========================================
def default_pens():
    return {
        'base': {'id': 'base', 'name': 'Thin G2 Black', 'color': '#000000', 'width': 0.38},
        'pen_1': {'id': 'pen_1', 'name': 'Thick G2 Black', 'color': '#000000', 'width': 0.7},
        'blue_038': {'id': 'blue_038', 'name': 'Thin Blue G2 (0.38)', 'color': '#0000B2', 'width': 0.38},
        'red_038': {'id': 'red_038', 'name': 'Thin Red G2 (0.38)', 'color': '#E32636', 'width': 0.38},
        'pink_07': {'id': 'pink_07', 'name': 'Thick Pink G2 (0.7)', 'color': '#FF1493', 'width': 0.70},
        'orange_07': {'id': 'orange_07', 'name': 'Thick Orange G2 (0.7)', 'color': '#FF6600', 'width': 0.70},
        'cyan_07': {'id': 'cyan_07', 'name': 'Thick Cyan G2 (0.7)', 'color': '#00A3CC', 'width': 0.70},
        'cutter': {'id': 'cutter', 'name': 'Cutter Blade', 'color': '#0000FF', 'width': 0.1}
    }


def load_pens():
    """Loads pens from JSON on top of the built-in defaults.

    Saved entries override defaults, but built-in pens that the engines rely on
    (pen_1, blue_038, ...) are always present even if the JSON predates them.
    """
    pens = default_pens()
    if os.path.exists(PENS_FILE):
        try:
            with open(PENS_FILE, 'r') as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                pens.update(saved)
        except Exception as e:
            print(f"Warning: Could not read {PENS_FILE} ({e}). Loading defaults.")
    return pens


def save_pens(pens):
    try:
        with open(PENS_FILE, 'w') as f:
            json.dump(pens, f, indent=4)
    except Exception as e:
        print(f"Error saving pens: {e}")
