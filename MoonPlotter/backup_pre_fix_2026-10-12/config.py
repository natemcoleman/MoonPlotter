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

PAPER_SIZES = {
    '100x100mm (Default)': (100.0, 100.0),
    'Ender3_Safe (200x200)': (200.0, 200.0),
    'A4': (297.0, 210.0),
    'Letter': (279.4, 215.9),
    'Custom (mm)': (150.0, 150.0)
}

PT_PER_MM = 72.0 / 25.4
PENS_FILE = "plotter_pens.json"


# ==========================================
# PEN PERSISTENCE HANDLERS
# ==========================================
def load_pens():
    """Loads custom pens from JSON, falling back to plotter defaults including CMY and Apollo pens."""
    if os.path.exists(PENS_FILE):
        try:
            with open(PENS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not read {PENS_FILE} ({e}). Loading defaults.")
            pass

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


def save_pens(pens):
    try:
        with open(PENS_FILE, 'w') as f:
            json.dump(pens, f, indent=4)
    except Exception as e:
        print(f"Error saving pens: {e}")