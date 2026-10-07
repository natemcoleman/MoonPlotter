import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import RectangleSelector, Slider, RadioButtons, TextBox, CheckButtons, Button
from PIL import Image
import rasterio
from rasterio.windows import Window
import os
import warnings
import tkinter as tk
from tkinter import simpledialog

# Attempt to load GeoPandas for borders
try:
    import geopandas as gpd
    import warnings as gpd_warnings

    with gpd_warnings.catch_warnings():
        gpd_warnings.simplefilter("ignore")
        world_borders = gpd.read_file(
            "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip")
        state_borders = gpd.read_file(
            "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_1_states_provinces.zip")
    HAS_GPD = True
except ImportError:
    HAS_GPD = False
    print("\nNote: 'geopandas' not installed. Skipping borders. Run 'pip install geopandas' to enable.")

warnings.filterwarnings('ignore', r'All-NaN (slice|axis) encountered')
Image.MAX_IMAGE_PIXELS = None

# ==========================================
# 1. SETTINGS & CONFIGURATION
# ==========================================
DATA_FILES = {
    'Moon': {
        'color': 'lroc_color_16bit_srgb_4k.tif',
        'dem': 'ldem_16.tif',
        'multiplier_to_feet': 3280.84
    },
    'Earth': {
        'color': 'earth_color.tif',
        'dem': 'BE_dem_v2.tif',  # Reverted to local file
        'multiplier_to_feet': 3.28084
    }
}

# ALL sizes strictly in millimeters for exact SVG plotting scale
PAPER_SIZES = {
    'Ender3_Safe': (200.0, 200.0),
    '100x100mm': (100.0, 100.0),
    'A4_Landscape': (297.0, 210.0),
    'A4_Portrait': (210.0, 297.0),
    'Letter_Landscape': (279.4, 215.9),
}

CURRENT_PAPER = 'Ender3_Safe'
PAPER_WIDTH_MM, PAPER_HEIGHT_MM = PAPER_SIZES[CURRENT_PAPER]
PAPER_ASPECT = PAPER_WIDTH_MM / PAPER_HEIGHT_MM
is_landscape = True

active_planet = 'Earth'
loaded_maps_cache = {}

# UI State variables
pending_dem_read_params = None  # Holds coordinates until 'Draw' is clicked
current_dem_crop = None
current_data_extent = None
current_view_extent = None
last_drag_coords = None
has_drawn = False

interval_ft = 500
pen_mm = 0.5
show_ocean = True
show_borders = False
border_artists = []
PT_PER_MM = 72.0 / 25.4


# ==========================================
# 2. OPTIMIZED MAP ENGINE
# ==========================================
def load_planet_data(planet):
    if planet in loaded_maps_cache:
        return loaded_maps_cache[planet]

    print(f"\nLoading data for {planet}...")
    files = DATA_FILES[planet]

    # Pre-load a crisp 4K color map into RAM.
    # This prevents UI stuttering while zooming, as it never reads from disk again.
    if not os.path.exists(files['color']):
        color_img = np.zeros((2048, 4096, 3), dtype=np.uint8)
    else:
        if files['color'].endswith('.tif'):
            with rasterio.open(files['color']) as src:
                scale = max(1.0, src.width / 4096.0)
                new_w, new_h = int(src.width / scale), int(src.height / scale)
                print(f"  -> Downsampling Visual Map to {new_w}x{new_h} for lag-free UI panning...")
                color_img = src.read(
                    out_shape=(src.count, new_h, new_w),
                    resampling=rasterio.enums.Resampling.bilinear
                ).transpose(1, 2, 0)
                if color_img.dtype == np.uint16:
                    color_img = (color_img / 256).astype(np.uint8)
        else:
            color_img = np.array(Image.open(files['color']))

    # Connect DEM lazily
    if not os.path.exists(files['dem']):
        dem_src = None
        print(f"  -> WARNING: '{files['dem']}' not found.")
    else:
        dem_src = rasterio.open(files['dem'])
        print(f"  -> Connected to DEM: {dem_src.shape[1]}x{dem_src.shape[0]} pixels")

    loaded_maps_cache[planet] = (color_img, dem_src)
    return color_img, dem_src


current_color_base, current_dem_src = load_planet_data(active_planet)

# ==========================================
# 3. GUI SETUP
# ==========================================
fig = plt.figure(figsize=(16, 8))

ax1 = fig.add_axes([0.05, 0.15, 0.4, 0.70])
ax2 = fig.add_axes([0.55, 0.15, 0.4, 0.70])

ax_radio = fig.add_axes([0.05, 0.88, 0.08, 0.10])
ax_toggle = fig.add_axes([0.15, 0.88, 0.15, 0.10])
ax_draw_btn = fig.add_axes([0.31, 0.90, 0.05, 0.05])
ax_clear_btn = fig.add_axes([0.365, 0.90, 0.05, 0.05])
ax_rotate_btn = fig.add_axes([0.42, 0.90, 0.05, 0.05])
ax_export_btn = fig.add_axes([0.475, 0.90, 0.06, 0.05])

ax_slider_int = fig.add_axes([0.12, 0.05, 0.25, 0.03])
ax_text_int = fig.add_axes([0.38, 0.05, 0.06, 0.04])
ax_slider_pen = fig.add_axes([0.60, 0.05, 0.25, 0.03])
ax_text_pen = fig.add_axes([0.86, 0.05, 0.06, 0.04])

# Setup Left Panel
img_display = ax1.imshow(current_color_base, extent=[-180, 180, -90, 90], origin='upper', zorder=0)

ax1.set_title(f"{active_planet} Map (Scroll to Zoom, Draw Box)", pad=10)
ax1.set_xlabel("Longitude");
ax1.set_ylabel("Latitude")


def draw_borders():
    global border_artists
    for artist in border_artists: artist.remove()
    border_artists = []

    if HAS_GPD and active_planet == 'Earth' and show_borders:
        before = len(ax1.collections)
        world_borders.boundary.plot(ax=ax1, edgecolor='cyan', linewidth=0.6, alpha=0.5, zorder=2)
        state_borders.boundary.plot(ax=ax1, edgecolor='cyan', linewidth=0.2, alpha=0.3, zorder=2)
        border_artists = ax1.collections[before:]


draw_borders()

# Setup Right Panel
ax2.set_box_aspect(PAPER_HEIGHT_MM / PAPER_WIDTH_MM)
ax2.set_title(f"Topo View Preview\nDraw a box and press 'Draw'")
ax2.set_xticks([]);
ax2.set_yticks([])

radio = RadioButtons(ax_radio, ('Earth', 'Moon'), active=0)
top_toggles = CheckButtons(ax_toggle, ['Show Ocean Topo', 'Show Borders'], [True, True])
btn_draw = Button(ax_draw_btn, 'Draw')
btn_clear = Button(ax_clear_btn, 'Clear')
btn_rotate = Button(ax_rotate_btn, 'Rotate')
btn_export = Button(ax_export_btn, 'Export SVG')

slider_int = Slider(ax=ax_slider_int, label='Interval (ft) ', valmin=50, valmax=10000, valinit=interval_ft, valstep=50)
slider_int.valtext.set_visible(False)
text_int = TextBox(ax_text_int, '', initial=str(interval_ft))
slider_pen = Slider(ax=ax_slider_pen, label='Pen Width (mm) ', valmin=0.1, valmax=2.0, valinit=pen_mm, valstep=0.05)
slider_pen.valtext.set_visible(False)
text_pen = TextBox(ax_text_pen, '', initial=str(pen_mm))


# ==========================================
# 4. MATH & DATA PREP (NO DISK I/O HERE)
# ==========================================
def process_selection(x1, x2, y1, y2):
    global current_view_extent, pending_dem_read_params, has_drawn
    if current_dem_src is None: return

    geo_width, geo_height = x2 - x1, y2 - y1
    if geo_width == 0 or geo_height == 0: return

    # Latitude Aspect Correction
    mid_lat = np.radians((y1 + y2) / 2)
    true_width = geo_width * np.cos(mid_lat)
    true_aspect = true_width / geo_height

    center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
    if true_aspect > PAPER_ASPECT:
        pad_true_w = true_width
        pad_geo_h = pad_true_w / PAPER_ASPECT
        pad_geo_w = geo_width
    else:
        pad_geo_h = geo_height
        pad_true_w = pad_geo_h * PAPER_ASPECT
        pad_geo_w = pad_true_w / np.cos(mid_lat)

    ex1, ex2 = center_x - pad_geo_w / 2, center_x + pad_geo_w / 2
    ey1, ey2 = center_y - pad_geo_h / 2, center_y + pad_geo_h / 2
    current_view_extent = [ex1, ex2, ey1, ey2]

    h, w = current_dem_src.shape
    px_x1, px_x2 = int((ex1 + 180) / 360 * w), int((ex2 + 180) / 360 * w)
    px_y1, px_y2 = int((90 - ey2) / 180 * h), int((90 - ey1) / 180 * h)
    px_x1_clip, px_x2_clip = max(0, px_x1), min(w, px_x2)
    px_y1_clip, px_y2_clip = max(0, px_y1), min(h, px_y2)
    if px_x2_clip <= px_x1_clip or px_y2_clip <= px_y1_clip: return

    # Save parameters for when "Draw" is clicked. NO disk reading happens here!
    pending_dem_read_params = {
        'window': Window(col_off=px_x1_clip, row_off=px_y1_clip, width=(px_x2_clip - px_x1_clip),
                         height=(px_y2_clip - px_y1_clip)),
        'extent': [px_x1_clip / w * 360 - 180, px_x2_clip / w * 360 - 180, 90 - (px_y2_clip / h * 180),
                   90 - (px_y1_clip / h * 180)]
    }

    has_drawn = False
    ax2.clear();
    ax2.set_box_aspect(PAPER_HEIGHT_MM / PAPER_WIDTH_MM);
    ax2.set_xticks([]);
    ax2.set_yticks([])
    ax2.text(0.5, 0.5, "Selection captured.\nClick 'Draw' to load data.", ha='center', va='center',
             transform=ax2.transAxes)
    fig.canvas.draw_idle()


# ==========================================
# 5. DRAWING & RENDERING
# ==========================================
def get_contour_levels(dem_crop_raw, interval):
    multiplier = DATA_FILES[active_planet]['multiplier_to_feet']
    crop_feet = dem_crop_raw.astype(np.float32) * multiplier
    crop_feet[crop_feet < -100000] = np.nan

    if np.isnan(crop_feet).all(): return [], crop_feet
    min_elev, max_elev = np.nanmin(crop_feet), np.nanmax(crop_feet)

    start_level = np.floor(min_elev / interval) * interval
    end_level = np.ceil(max_elev / interval) * interval
    levels = np.arange(start_level, end_level + interval, interval)

    if active_planet == 'Earth' and not show_ocean:
        levels = [lvl for lvl in levels if lvl >= 0]
        if len(levels) == 0 and max_elev >= 0: levels = [0]

    if len(levels) > 300:
        print(f"Warning: {len(levels)} lines requested. Capping at 300.")
        min_l = max(0, min_elev) if (active_planet == 'Earth' and not show_ocean) else min_elev
        levels = np.linspace(min_l, max_elev, 300)

    return levels, crop_feet


def redraw_contours():
    if current_dem_crop is None: return
    ax2.clear();
    ax2.set_box_aspect(PAPER_HEIGHT_MM / PAPER_WIDTH_MM)

    ax2.set_title(f"{active_planet} ({PAPER_WIDTH_MM}x{PAPER_HEIGHT_MM}mm)\n{interval_ft} ft/line | Pen: {pen_mm} mm")
    ax2.set_xticks([]);
    ax2.set_yticks([])

    levels, crop_feet = get_contour_levels(current_dem_crop, interval_ft)
    lw_points = pen_mm * PT_PER_MM

    if len(levels) > 0:
        mid_lat = np.radians((current_view_extent[2] + current_view_extent[3]) / 2)
        ax2.set_aspect(1 / np.cos(mid_lat))
        ax2.contour(crop_feet, levels=levels, colors='black', linewidths=lw_points, extent=current_data_extent,
                    origin='upper')
    else:
        ax2.text(0.5, 0.5, "No topography in this range", ha='center', va='center', transform=ax2.transAxes)

    ax2.set_xlim(current_view_extent[0], current_view_extent[1])
    ax2.set_ylim(current_view_extent[2], current_view_extent[3])
    fig.canvas.draw_idle()


def draw_action(event):
    global has_drawn, current_dem_crop, current_data_extent
    if not pending_dem_read_params: return

    # 1. Show loading text so the UI feels responsive instantly
    ax2.clear()
    ax2.text(0.5, 0.5, "Reading DEM from disk...", ha='center', va='center', transform=ax2.transAxes)
    fig.canvas.draw()
    fig.canvas.flush_events()

    # 2. Execute the heavy disk read
    current_dem_crop = current_dem_src.read(1, window=pending_dem_read_params['window'])
    current_data_extent = pending_dem_read_params['extent']

    # 3. Render
    has_drawn = True
    redraw_contours()


def export_action(event):
    if current_dem_crop is None or not has_drawn:
        print("Please make a selection and click 'Draw' before exporting.")
        return

    root = tk.Tk();
    root.withdraw()
    orientation_str = "landscape" if is_landscape else "portrait"
    default_name = f"{active_planet.lower()}_{orientation_str}_{int(PAPER_WIDTH_MM)}x{int(PAPER_HEIGHT_MM)}"

    base_name = simpledialog.askstring("Export SVG", "Enter save name:", initialvalue=default_name)
    if not base_name:
        root.destroy()
        return

    file_prefix = f"{base_name}_{interval_ft}ft"
    filename = f"{file_prefix}.svg"

    version = 2
    while os.path.exists(filename):
        filename = f"{file_prefix}_v{version}.svg"
        version += 1

    print(f"\n[1/2] Generating {filename} with exact physical dimensions...")
    levels, crop_feet = get_contour_levels(current_dem_crop, interval_ft)

    inches_w, inches_h = PAPER_WIDTH_MM / 25.4, PAPER_HEIGHT_MM / 25.4
    export_fig = plt.figure(figsize=(inches_w, inches_h))
    export_ax = export_fig.add_axes([0, 0, 1, 1]);
    export_ax.axis('off')

    mid_lat = np.radians((current_view_extent[2] + current_view_extent[3]) / 2)
    export_ax.set_aspect(1 / np.cos(mid_lat))

    if len(levels) > 0:
        export_ax.contour(crop_feet, levels=levels, colors='black', linewidths=(pen_mm * PT_PER_MM),
                          extent=current_data_extent, origin='upper')

    export_ax.set_xlim(current_view_extent[0], current_view_extent[1])
    export_ax.set_ylim(current_view_extent[2], current_view_extent[3])

    export_fig.savefig(filename, format='svg')
    plt.close(export_fig)

    # Generate vpype command and push to clipboard
    vpype_cmd = (f'vpype read "{filename}" linesimplify -t 0.1mm '
                 f'linemerge -t 0.3mm linesort gwrite --profile ender3 output.gcode')

    root.clipboard_clear()
    root.clipboard_append(vpype_cmd)
    root.update()
    root.destroy()

    print(f"[2/2] Success! Copied the plotting command to your clipboard.")
    print("-" * 60)
    print("Paste this into your terminal to generate your G-Code:")
    print(vpype_cmd)
    print("-" * 60)


# --- Interactions ---
btn_draw.on_clicked(draw_action)
btn_export.on_clicked(export_action)
btn_clear.on_clicked(lambda e: process_selection(*last_drag_coords) if last_drag_coords else None)


def rotate_paper_action(e):
    global PAPER_WIDTH_MM, PAPER_HEIGHT_MM, PAPER_ASPECT, is_landscape
    PAPER_WIDTH_MM, PAPER_HEIGHT_MM = PAPER_HEIGHT_MM, PAPER_WIDTH_MM
    PAPER_ASPECT = PAPER_WIDTH_MM / PAPER_HEIGHT_MM
    is_landscape = not is_landscape
    if last_drag_coords:
        process_selection(*last_drag_coords)
        if has_drawn: draw_action(None)  # Auto-re-fetch bounds on rotate
    else:
        ax2.set_box_aspect(PAPER_HEIGHT_MM / PAPER_WIDTH_MM)
        fig.canvas.draw_idle()


btn_rotate.on_clicked(rotate_paper_action)

top_toggles.on_clicked(lambda l: [
    globals().update(show_ocean=not show_ocean) if l == 'Show Ocean Topo' else globals().update(
        show_borders=not show_borders),
    draw_borders() if l == 'Show Borders' else (redraw_contours() if has_drawn else None), fig.canvas.draw_idle()])
slider_int.on_changed(lambda v: [globals().update(interval_ft=int(v)), text_int.set_val(str(int(v))),
                                 redraw_contours() if has_drawn else None])
text_int.on_submit(lambda t: [globals().update(interval_ft=max(1, int(t))), slider_int.set_val(max(1, int(t))),
                              redraw_contours() if has_drawn else None])
slider_pen.on_changed(lambda v: [globals().update(pen_mm=float(v)), text_pen.set_val(f"{float(v):.2f}"),
                                 redraw_contours() if has_drawn else None])
text_pen.on_submit(lambda t: [globals().update(pen_mm=max(0.01, float(t))), slider_pen.set_val(max(0.01, float(t))),
                              redraw_contours() if has_drawn else None])


def switch_planet(label):
    global active_planet, current_color_base, current_dem_src, has_drawn
    active_planet = label
    current_color_base, current_dem_src = load_planet_data(label)
    img_display.set_data(current_color_base)
    ax1.set_xlim(-180, 180);
    ax1.set_ylim(-90, 90)
    ax1.set_title(f"{active_planet} Map (Scroll to Zoom, Draw Box)", pad=10)
    has_drawn = False
    draw_borders()
    ax2.clear()
    fig.canvas.draw_idle()


radio.on_clicked(switch_planet)


def zoom_map(event):
    if event.inaxes != ax1: return
    base_scale = 1.2
    scale = 1 / base_scale if event.button == 'up' else base_scale if event.button == 'down' else None
    if scale is None: return
    cur_xlim, cur_ylim = ax1.get_xlim(), ax1.get_ylim()
    xdata, ydata = event.xdata, event.ydata
    if xdata is None or ydata is None: return
    new_width, new_height = (cur_xlim[1] - cur_xlim[0]) * scale, (cur_ylim[1] - cur_ylim[0]) * scale
    relx, rely = (cur_xlim[1] - xdata) / (cur_xlim[1] - cur_xlim[0]), (cur_ylim[1] - ydata) / (
                cur_ylim[1] - cur_ylim[0])
    ax1.set_xlim([max(-180, xdata - new_width * (1 - relx)), min(180, xdata + new_width * relx)])
    ax1.set_ylim([max(-90, ydata - new_height * (1 - rely)), min(90, ydata + new_height * rely)])
    fig.canvas.draw_idle()


fig.canvas.mpl_connect('scroll_event', zoom_map)


def on_select(eclick, erelease):
    global last_drag_coords
    x1, x2 = sorted([eclick.xdata, erelease.xdata])
    y1, y2 = sorted([eclick.ydata, erelease.ydata])
    last_drag_coords = (x1, x2, y1, y2)
    process_selection(x1, x2, y1, y2)


selector = RectangleSelector(ax1, on_select, useblit=True, button=[1], spancoords='data', interactive=True,
                             props=dict(facecolor='red', edgecolor='red', alpha=0.3, fill=True))
plt.show()