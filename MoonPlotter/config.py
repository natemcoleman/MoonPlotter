import os
import json

# ==========================================
# CONSTANTS & CONFIGURATION
# ==========================================
DATA_FILES = {
    'Earth': {'color': 'earth_color.tif', 'dem': 'BE_dem_v2.tif', 'multiplier': 3.28084},
    'Moon': {'color': 'lroc_color_16bit_srgb_4k.tif', 'dem': 'ldem_16.tif', 'multiplier': 3280.84},
    # USGS Astrogeology downloads (keep the original file names):
    #   https://planetarymaps.usgs.gov/mosaic/Mars_Viking_ClrMosaic_global_925m.tif   (~764 MB)
    #   https://planetarymaps.usgs.gov/mosaic/Mars_MGS_MOLA_DEM_mosaic_global_463m.tif (~2 GB, metres)
    'Mars': {'color': 'Mars_Viking_ClrMosaic_global_925m.tif', 'dem': 'Mars_MGS_MOLA_DEM_mosaic_global_463m.tif',
             'multiplier': 3.28084}
}

# Lake Tahoe bathymetry, lake-only (NaN outside the shoreline), in feet, EPSG:4326.
# Built from tahoe.tif (NAD83 / UTM 11N, 10 m) by reprojecting and keeping the water body
# connected to the deepest point at or below the lake surface.
TAHOE = {
    'dem': 'tahoe_lake_wgs84.tif',
    'bounds': (-120.175500, 38.917000, -119.914384, 39.259090),   # left, bottom, right, top
    'multiplier': 1.0,          # file is already in feet
    'surface_ft': 6225.0,
}

# Lakes plotted as bathymetry (Clip to region > Lakes): see lakes.py.
#   kind 'survey': a bundled lake-only raster from a real depth survey.
#   kind 'model' : lake bed modelled from the OpenStreetMap shoreline and published depths (no public
#                  survey grid exists). surface_ft = lake surface elevation; interval_ft = contour step.
LAKES = {
    'Lake Tahoe (High-Res Bathymetry)': {
        'kind': 'survey', 'dem': TAHOE['dem'], 'bounds': TAHOE['bounds'], 'multiplier': TAHOE['multiplier'],
        'surface_ft': TAHOE['surface_ft'], 'interval_ft': 75},
    # Max ~73 m, mean ~43 m: Larsen et al. 2016, Quaternary Science Reviews (doi:10.1016/j.quascirev.2016.02.024).
    # Surface 6,783 ft (Wikipedia).
    'Jenny Lake (modelled depth)': {
        'kind': 'model', 'osm_name': 'Jenny Lake', 'center': (43.7639, -110.7300), 'radius_km': 3.0,
        'max_depth_m': 73.0, 'mean_depth_m': 43.0, 'surface_ft': 6783.0, 'interval_ft': 20},
    # Max 134 m at full capacity: Rhea et al. 2005, USGS-CERC-91344. Surface 6,772 ft (Wikipedia).
    # No published mean depth: DEFAULT_MEAN_RATIO in lakes.py is used.
    'Jackson Lake (modelled depth)': {
        'kind': 'model', 'osm_name': 'Jackson Lake', 'center': (43.93, -110.67), 'radius_km': 15.0,
        'max_depth_m': 134.0, 'mean_depth_m': None, 'surface_ft': 6772.0, 'interval_ft': 40},
}

PLANET_RADII = {'Earth': 6371000.0, 'Moon': 1737400.0, 'Mars': 3389500.0}

PAPER_SIZES = {
    'Postcard': (152.4, 101.6),
    '100x100mm': (100.0, 100.0),
    'A4': (297.0, 210.0),
    'Ender3_Safe (200x200)': (200.0, 200.0),
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
AUTO_CONTOUR_TARGET_LINES = 40
NICE_INTERVALS_FT = [1, 2, 5, 10, 20, 25, 40, 50, 80, 100, 200, 250, 400, 500, 800, 1000,
                     2000, 2500, 4000, 5000, 10000, 20000, 25000, 50000]

# Longest side (pixels) of the DEM array handed to the contour engine. Larger
# windows are block-averaged down. 4000 px is ~20 px/mm on a 200 mm plot.
MAX_DEM_PX = 4000
# MAX_DEM_PX = 8192

# ==========================================
# HATCHING / COLOR SEPARATION
# ==========================================
# Colour mode no longer uses fixed ink sets: you tick which pens are available and pick the paper
# colour, and the separation finds the best mix of those inks on that paper. Ink colours come from
# each pen's 'color' (calibrate against a scan), and 'opacity' says how much an ink hides what is
# under it: ~0.1 for translucent gel/fineliner ink, ~0.85 for opaque white gel on dark paper.
PAPER_COLORS = {
    'White': '#FFFFFF',
    'Black': '#1C1C1C',
    'Pale blue': '#787f99',
    'Light blue': '#a6b0b9',
    'Navy blue': '#46475b',
    'Teal blue': '#87b3b5',
    'Blue': '#4a5478',
    'Cream / Ivory': '#F2EAD3',
    'Tan / Kraft': '#C9A97C',
    'Light gray': '#C8C8C8',
    'Dark gray': '#555555',
    'Red': '#D7282F',
    'Orange': '#F27022',
    'Yellow': '#FFE234',
    'Green': '#3FAE49',
    # 'Light blue': '#8ECDEB',
    # 'Blue': '#1F6FC5',
    'Navy': '#1E2A5A',
    'Purple': '#6E3FA0',
    'Pink': '#EC4E9A',
}
DEFAULT_PAPER = 'White'
# Paper colours listed first (above a divider) in the paper-colour drop-down.
FAVORITE_PAPER_COLORS = ('White', 'Black')

# "Clip to region" drop-down: these come first (above a divider), then the rest grouped by category.
FAVORITE_REGIONS = ('Lake Tahoe (High-Res Bathymetry)', 'Wyoming', 'Utah', 'California', 'Washington')
# Region entries that are lakes (bathymetry), listed under LAKES in the drop-down.
REGION_LAKES = tuple(LAKES)

# Map scale presets (Source > Map scale): (label, width of the printed map in miles, contour interval ft).
# Picking one sizes the selection to that real-world width (at the paper's aspect ratio) and fixes the
# contour interval, so plots of different places share one scale.
SCALE_PRESETS = [
    ("1 mi wide \u00b7 20 ft contours", 1, 20),
    ("2 mi wide \u00b7 40 ft contours", 2, 40),
    ("5 mi wide \u00b7 100 ft contours", 5, 100),
    ("10 mi wide \u00b7 200 ft contours", 10, 200),
    ("25 mi wide \u00b7 500 ft contours", 25, 500),
    ("50 mi wide \u00b7 1,000 ft contours", 50, 1000),
    ("100 mi wide \u00b7 2,000 ft contours", 100, 2000),
    ("250 mi wide \u00b7 5,000 ft contours", 250, 5000),
]
DEFAULT_MONO_PEN = 'pen_1'
TOOL_PEN_IDS = {'cutter'}            # never offered as an ink


def ink_lightness(hex_color):
    """Plot-order key (Rec.709 luma on sRGB): lightest inks are plotted first."""
    h = hex_color.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def pen_opacity(pen):
    if 'opacity' in pen:
        return float(pen['opacity'])
    return 0.85 if 'white' in pen.get('name', '').lower() else 0.1


def pen_available(pen_id, pen):
    return pen_id not in TOOL_PEN_IDS and bool(pen.get('available', True))


def pen_favorite(pen):
    """Favourite pens float to the top of every pen drop-down (tick 'Fav.' in Tools > Pens)."""
    return bool(pen.get('favorite', False))


DEFAULT_HATCH_SPACING_MM = 1.0

# ==========================================
# PLOTTER / G-CODE
# ==========================================
# The G-code header homes (G28) then declares the head to be at (-50, -50) with G92, so plot
# coordinate (0, 0) sits 50 mm in from the home corner. The usable plot area is therefore the bed
# size minus that offset. Adjust BED_MM if your Ender's travel differs.
BED_MM = (235.0, 235.0)
GCODE_OFFSET_MM = (38.0, 18.0)
DEFAULT_DRAW_FEED = 1200.0      # mm/min, pen down
TRAVEL_FEED = 3000.0            # mm/min, pen up
PEN_LIFT_MM = 1.5               # pen-up height above the paper between strokes (was 1.0); the export dialog can override it
# Height of the drawing plane (pen down, Z0) above the homed Z position. The header declares the homed
# head to be at Z = -PEN_DOWN_ABOVE_HOME_MM. Raise it if the pen drags or hits the plate; lower it if
# lines come out faint. (Was 4.0; raised 1.5 mm after the pen caught the plate edge.)
# This is the plane for the PENS. The drag-knife cutter has its own depth: CUTTER_Z_OFFSET_MM below.
# (Previous values: 2.5 and 3.5 were cutter settings; 5.5 was the last pen setting.)
PEN_DOWN_ABOVE_HOME_MM = 4.55
# Drag-knife cutter ('cutter' pen): blade-down Z relative to the pen drawing plane (Z0). Negative = deeper.
# -2.0 puts the blade at 3.5 mm above home, the cutter height used before this setting existed. The cutter
# is plotted last, after its own M0 tool-change pause; pen-up height and travel moves are unchanged.
CUTTER_Z_OFFSET_MM = -2.0
# Card cut-out (Export dialog > "Cut out the card"): the drag knife traces the paper's outer edge last.
# The blade tip trails the knife's pivot by about this much (2 mm-shank 45 deg blades: ~0.25 mm; 60 deg
# blades trail further, ~0.5 mm).
CUTTER_BLADE_OFFSET_MM = 0.25
# At each corner the knife runs past the corner, swings round a small loop OUTSIDE the card and comes back
# in along the next edge, so the blade is already turned when it reaches the corner (sharp corners, and
# both edges are cut right through the corner). Kept at least 3x the blade offset.
CUTTER_CORNER_LOOP_MM = 2.0
# The cut runs this far past its starting point so the outline closes cleanly.
CUTOUT_OVERLAP_MM = 5.0
# How many times the knife goes over every cut: the card cut-out, layered cut-out pieces and any line drawn
# with the Cutter tool. 2 cuts 65 lb cardstock cleanly through without pressing the blade deeper. Closed
# outlines are cut lap after lap without lifting; open lines are cut again from their start.
CUTOUT_PASSES = 3
# Clearance used for long moves: leaving home for the start point, parking and pen changes.
SAFE_Z_MM = 5.5
# Fastest Z move the printer's firmware allows, mm/min. Stock Ender 3 Marlin caps Z at 5 mm/s (300);
# send M503 and look at the M203 line to check yours. Only used for plot-time estimates.
Z_MAX_FEED = 300.0
# SAFE_Z_MM = 0.5
# SAFE_Z_MM = 0.0

# Folder for high-resolution regional data (DEMs and colour maps) - see regions.py
REGIONS_DIR = "regions"


# ==========================================
# PEN PERSISTENCE HANDLERS
# ==========================================
def default_pens():
    return {
        'base': {'id': 'base', 'name': 'Thin G2 Black', 'color': '#000000', 'width': 0.38, 'favorite': True},
        'pen_1': {'id': 'pen_1', 'name': 'Thick G2 Black', 'color': '#000000', 'width': 0.7, 'favorite': True},
        'blue_038': {'id': 'blue_038', 'name': 'Thin Blue G2 (0.38)', 'color': '#0000B2', 'width': 0.38},
        'red_038': {'id': 'red_038', 'name': 'Thin Red G2 (0.38)', 'color': '#E32636', 'width': 0.38},
        'pink_07': {'id': 'pink_07', 'name': 'Thick Pink G2 (0.7)', 'color': '#FF1493', 'width': 0.70},
        'orange_07': {'id': 'orange_07', 'name': 'Thick Orange G2 (0.7)', 'color': '#FF6600', 'width': 0.70},
        'cyan_07': {'id': 'cyan_07', 'name': 'Thick Cyan G2 (0.7)', 'color': '#00A3CC', 'width': 0.70},
        # G2 0.7 assorted 8-pack (new colours; black/pink/orange already above)
        'blue_07': {'id': 'blue_07', 'name': 'Thick Blue G2 (0.7)', 'color': '#1D3FBF', 'width': 0.70},
        'red_07': {'id': 'red_07', 'name': 'Thick Red G2 (0.7)', 'color': '#D9213A', 'width': 0.70},
        'green_07': {'id': 'green_07', 'name': 'Thick Green G2 (0.7)', 'color': '#16934A', 'width': 0.70},
        'yellow_07': {'id': 'yellow_07', 'name': 'Thick Yellow G2 (0.7)', 'color': '#F3DA2C', 'width': 0.70},
        'purple_07': {'id': 'purple_07', 'name': 'Thick Purple G2 (0.7)', 'color': '#6A2C9E', 'width': 0.70},
        # Sakura Gelly Roll Classic White - opaque, for dark paper. The pigment ink skips if the pen moves
        # too fast, so each size has its own slower feed (mm/min); plotter_pens.json holds the live values.
        'white_gr05': {'id': 'white_gr05', 'name': 'White Gelly Roll 05 (0.3)', 'color': '#F6F6F2',
                       'width': 0.30, 'opacity': 0.85, 'feed': 800, 'favorite': True},
        'white_gr08': {'id': 'white_gr08', 'name': 'White Gelly Roll 08 (0.4)', 'color': '#F6F6F2',
                       'width': 0.40, 'opacity': 0.85, 'feed': 650, 'favorite': True},
        'white_gr10': {'id': 'white_gr10', 'name': 'White Gelly Roll 10 (0.5)', 'color': '#F6F6F2',
                       'width': 0.50, 'opacity': 0.85, 'feed': 500},
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


# ---------------------------------------------------------------------------------------------
# Per-machine calibration (Tools > Calibrate pen & knife heights). The values above are defaults; the
# calibration dialog saves this machine's measured values in MACHINE_SETTINGS_FILE (not in the
# repository), which overrides them when the app starts. Delete the file to go back to the defaults.
# ---------------------------------------------------------------------------------------------
MACHINE_SETTINGS_FILE = "machine_settings.json"
MACHINE_SETTING_KEYS = ('PEN_DOWN_ABOVE_HOME_MM', 'PEN_LIFT_MM', 'CUTTER_Z_OFFSET_MM', 'Z_MAX_FEED')
MACHINE_DEFAULTS = {k: globals()[k] for k in MACHINE_SETTING_KEYS}


def apply_machine_settings(values):
    """Sets the calibrated values on this module (code reads them as config.NAME at run time)."""
    g = globals()
    for k in MACHINE_SETTING_KEYS:
        v = (values or {}).get(k)
        if isinstance(v, (int, float)) and v == v:          # number, not NaN
            g[k] = float(v)


def load_machine_settings():
    """Applies MACHINE_SETTINGS_FILE if it exists. Returns the values read ({} if none)."""
    if not os.path.exists(MACHINE_SETTINGS_FILE):
        return {}
    try:
        with open(MACHINE_SETTINGS_FILE, 'r') as f:
            values = json.load(f)
    except Exception as e:
        print(f"Warning: could not read {MACHINE_SETTINGS_FILE} ({e}) - using the defaults in config.py.")
        return {}
    if isinstance(values, dict):
        apply_machine_settings(values)
        return values
    return {}


def save_machine_settings(values):
    """Saves the calibrated values and applies them straight away."""
    out = {k: float(values[k]) for k in MACHINE_SETTING_KEYS if k in values}
    with open(MACHINE_SETTINGS_FILE, 'w') as f:
        json.dump(out, f, indent=2)
    apply_machine_settings(out)


def machine_settings():
    """Current values (defaults overridden by this machine's calibration)."""
    return {k: globals()[k] for k in MACHINE_SETTING_KEYS}


load_machine_settings()
