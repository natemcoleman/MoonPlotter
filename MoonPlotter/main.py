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
    print("\nNote: 'geopandas' not installed. Skipping country/state borders.")

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
        'color': 'earth_color2.tif',
        'dem': 'BE_dem_v2.tif',
        'multiplier_to_feet': 3.28084
    }
}

# ALL sizes must be strictly in millimeters for accurate physical SVG scaling
PAPER_SIZES = {
    'Ender3_Safe_Area': (200.0, 200.0),  # 200x200mm safe plotting zone for Ender 3
    'A4_Landscape': (297.0, 210.0),
    'A4_Portrait': (210.0, 297.0),
    'Letter_Landscape': (279.4, 215.9),
    'Letter_Portrait': (215.9, 279.4)
}

# Set your target here!
CURRENT_PAPER = 'Ender3_Safe_Area'
PAPER_WIDTH_MM, PAPER_HEIGHT_MM = PAPER_SIZES[CURRENT_PAPER]
PAPER_ASPECT = PAPER_WIDTH_MM / PAPER_HEIGHT_MM
is_landscape = True

active_planet = 'Earth'
loaded_maps_cache = {}

# UI State variables
current_dem_crop = None
current_data_extent = None
current_view_extent = None
last_drag_coords = None
has_drawn = False
interval_ft = 1000
pen_mm = 0.5  # Default 0.5mm pen
show_ocean = True
show_borders = True
border_artists = []

# Conversion constant: 1 inch = 25.4 mm. 1 point = 1/72 inch.
PT_PER_MM = 72.0 / 25.4


# ==========================================
# 2. DYNAMIC MAP ENGINE (SLIPPY MAP)
# ==========================================
def load_planet_data(planet):
    if planet in loaded_maps_cache:
        return loaded_maps_cache[planet]

    print(f"\nLoading data for {planet}...")
    files = DATA_FILES[planet]

    color_src = None
    base_img = None

    if not os.path.exists(files['color']):
        base_img = np.zeros((512, 1024, 3), dtype=np.uint8)
    else:
        if files['color'].endswith('.tif'):
            color_src = rasterio.open(files['color'])
            base_img = color_src.read(
                out_shape=(color_src.count, 512, 1024),
                resampling=rasterio.enums.Resampling.bilinear
            ).transpose(1, 2, 0)
            if base_img.dtype == np.uint16:
                base_img = (base_img / 256).astype(np.uint8)
        else:
            base_img = np.array(Image.open(files['color']))

    if not os.path.exists(files['dem']):
        dem_src = None
    else:
        dem_src = rasterio.open(files['dem'])

    loaded_maps_cache[planet] = (color_src, dem_src, base_img)
    return color_src, dem_src, base_img


current_color_src, current_dem_src, current_color_base = load_planet_data(active_planet)

# ==========================================
# 3. GUI SETUP
# ==========================================
fig = plt.figure(figsize=(16, 8))

ax1 = fig.add_axes([0.05, 0.15, 0.4, 0.70])
ax2 = fig.add_axes([0.55, 0.15, 0.4, 0.70])

# Top Control Bar
ax_radio = fig.add_axes([0.05, 0.88, 0.08, 0.10])
ax_toggle = fig.add_axes([0.15, 0.88, 0.15, 0.10])
ax_draw_btn = fig.add_axes([0.31, 0.90, 0.05, 0.05])
ax_clear_btn = fig.add_axes([0.365, 0.90, 0.05, 0.05])
ax_rotate_btn = fig.add_axes([0.42, 0.90, 0.05, 0.05])
ax_export_btn = fig.add_axes([0.475, 0.90, 0.06, 0.05])

# Bottom Control Bar (Interval AND Pen Thickness)
ax_slider_int = fig.add_axes([0.12, 0.05, 0.25, 0.03])
ax_text_int = fig.add_axes([0.38, 0.05, 0.06, 0.04])

ax_slider_pen = fig.add_axes([0.60, 0.05, 0.25, 0.03])
ax_text_pen = fig.add_axes([0.86, 0.05, 0.06, 0.04])

# Setup Left Panel
img_base = ax1.imshow(current_color_base, extent=[-180, 180, -90, 90], origin='upper', zorder=0)
img_highres = ax1.imshow(np.zeros((2, 2, 3), dtype=np.uint8), extent=[-180, 180, -90, 90], origin='upper', zorder=1)
img_highres.set_visible(False)

ax1.set_title(f"{active_planet} Visual Map (Scroll to Zoom, Draw to Select)", pad=10)
ax1.set_xlabel("Longitude")
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
ax2.set_title(f"Topo View Preview\nPress 'Draw' to render contours")
ax2.set_xlabel("Longitude")
ax2.set_xticks([]);
ax2.set_yticks([])

# Controls
radio = RadioButtons(ax_radio, ('Earth', 'Moon'), active=0)
top_toggles = CheckButtons(ax_toggle, ['Show Ocean Topo', 'Show Borders'], [True, True])
btn_draw = Button(ax_draw_btn, 'Draw')
btn_clear = Button(ax_clear_btn, 'Clear')
btn_rotate = Button(ax_rotate_btn, 'Rotate')
btn_export = Button(ax_export_btn, 'Export SVG')

slider_int = Slider(ax=ax_slider_int, label='Interval (ft) ', valmin=50, valmax=10000, valinit=interval_ft, valstep=50)
text_int = TextBox(ax_text_int, 'ft: ', initial=str(interval_ft))

slider_pen = Slider(ax=ax_slider_pen, label='Pen Width (mm) ', valmin=0.1, valmax=2.0, valinit=pen_mm, valstep=0.05)
text_pen = TextBox(ax_text_pen, 'mm: ', initial=str(pen_mm))


# ==========================================
# 4. HIGH-RESOLUTION OVERLAY UPDATER
# ==========================================
def refresh_highres_map():
    if current_color_src is None:
        img_highres.set_visible(False)
        return

    xlim, ylim = ax1.get_xlim(), ax1.get_ylim()
    lon_min, lon_max = max(-180, xlim[0]), min(180, xlim[1])
    lat_min, lat_max = max(-90, ylim[0]), min(90, ylim[1])

    h, w = current_color_src.height, current_color_src.width
    px_x0 = int((lon_min + 180) / 360 * w)
    px_x1 = int((lon_max + 180) / 360 * w)
    px_y0 = int((90 - lat_max) / 180 * h)
    px_y1 = int((90 - lat_min) / 180 * h)

    px_x0, px_x1 = max(0, px_x0), min(w, px_x1)
    px_y0, px_y1 = max(0, px_y0), min(h, px_y1)

    win_w, win_h = px_x1 - px_x0, px_y1 - px_y0
    if win_w <= 0 or win_h <= 0: return

    max_px = 1500
    scale = max(1.0, win_w / max_px, win_h / max_px)
    out_w, out_h = int(win_w / scale), int(win_h / scale)

    window = Window(col_off=px_x0, row_off=px_y0, width=win_w, height=win_h)
    try:
        highres_slice = current_color_src.read(
            out_shape=(current_color_src.count, out_h, out_w),
            resampling=rasterio.enums.Resampling.bilinear,
            window=window
        ).transpose(1, 2, 0)

        if highres_slice.dtype == np.uint16:
            highres_slice = (highres_slice / 256).astype(np.uint8)

        img_highres.set_data(highres_slice)
        img_highres.set_extent([lon_min, lon_max, lat_min, lat_max])
        img_highres.set_visible(True)
        fig.canvas.draw_idle()
    except Exception:
        pass


fig.canvas.mpl_connect('button_release_event', lambda e: refresh_highres_map() if e.inaxes == ax1 else None)
refresh_highres_map()


# ==========================================
# 5. INTERACTIVE LOGIC & MATH
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
        print(f"Warning: {len(levels)} lines requested. Capping at 300 to protect RAM.")
        min_l = max(0, min_elev) if (active_planet == 'Earth' and not show_ocean) else min_elev
        levels = np.linspace(min_l, max_elev, 300)

    if len(levels) < 2:
        if len(levels) == 1:
            levels = [levels[0], levels[0] + 0.001]
        else:
            levels = [min_elev, max_elev] if min_elev != max_elev else []

    return levels, crop_feet


def redraw_contours():
    if current_dem_crop is None: return
    ax2.clear();
    ax2.set_box_aspect(PAPER_HEIGHT_MM / PAPER_WIDTH_MM)

    orientation = "Landscape" if is_landscape else "Portrait"
    ax2.set_title(f"{active_planet} ({orientation})\n{interval_ft} ft/line | Pen: {pen_mm} mm")
    ax2.set_xticks([]);
    ax2.set_yticks([])

    levels, crop_feet = get_contour_levels(current_dem_crop, interval_ft)

    # Calculate linewidth in points for Matplotlib
    lw_points = pen_mm * PT_PER_MM

    if len(levels) > 0:
        ax2.contour(crop_feet, levels=levels, colors='black', linewidths=lw_points, extent=current_data_extent,
                    origin='upper')
    else:
        ax2.text(0.5, 0.5, "No topography in this range\n(Or Ocean is hidden)", ha='center', va='center',
                 transform=ax2.transAxes)

    ax2.set_xlim(current_view_extent[0], current_view_extent[1])
    ax2.set_ylim(current_view_extent[2], current_view_extent[3])
    fig.canvas.draw_idle()


def process_selection(x1, x2, y1, y2):
    global current_dem_crop, current_data_extent, current_view_extent, has_drawn
    if current_dem_src is None: return

    width, height = x2 - x1, y2 - y1
    if width == 0 or height == 0: return

    center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
    pad_width, pad_height = (width, width / PAPER_ASPECT) if width / height > PAPER_ASPECT else (
    height * PAPER_ASPECT, height)
    ex1, ex2 = center_x - pad_width / 2, center_x + pad_width / 2
    ey1, ey2 = center_y - pad_height / 2, center_y + pad_height / 2
    current_view_extent = [ex1, ex2, ey1, ey2]

    h, w = current_dem_src.shape
    px_x1, px_x2 = int((ex1 + 180) / 360 * w), int((ex2 + 180) / 360 * w)
    px_y1, px_y2 = int((90 - ey2) / 180 * h), int((90 - ey1) / 180 * h)

    px_x1_clip, px_x2_clip = max(0, px_x1), min(w, px_x2)
    px_y1_clip, px_y2_clip = max(0, px_y1), min(h, px_y2)
    if px_x2_clip <= px_x1_clip or px_y2_clip <= px_y1_clip: return

    window = Window(col_off=px_x1_clip, row_off=px_y1_clip, width=(px_x2_clip - px_x1_clip),
                    height=(px_y2_clip - px_y1_clip))
    current_dem_crop = current_dem_src.read(1, window=window)
    current_data_extent = [px_x1_clip / w * 360 - 180, px_x2_clip / w * 360 - 180, 90 - (px_y2_clip / h * 180),
                           90 - (px_y1_clip / h * 180)]

    has_drawn = False
    ax2.clear();
    ax2.set_box_aspect(PAPER_HEIGHT_MM / PAPER_WIDTH_MM);
    ax2.set_xticks([]);
    ax2.set_yticks([])
    ax2.text(0.5, 0.5, "Selection updated.\nClick 'Draw' to render contours.", ha='center', va='center',
             transform=ax2.transAxes)
    fig.canvas.draw_idle()


def draw_action(event):
    global has_drawn
    has_drawn = True;
    redraw_contours()


def clear_action(event=None):
    global current_dem_crop, last_drag_coords, has_drawn
    current_dem_crop = None;
    last_drag_coords = None;
    has_drawn = False
    try:
        selector.extents = (0, 0, 0, 0)
    except Exception:
        pass
    ax2.clear();
    ax2.set_box_aspect(PAPER_HEIGHT_MM / PAPER_WIDTH_MM);
    ax2.set_xticks([]);
    ax2.set_yticks([])
    ax2.text(0.5, 0.5, "Selection cleared.", ha='center', va='center', transform=ax2.transAxes)
    fig.canvas.draw_idle()


def rotate_action(event):
    global PAPER_WIDTH_MM, PAPER_HEIGHT_MM, PAPER_ASPECT, is_landscape
    PAPER_WIDTH_MM, PAPER_HEIGHT_MM = PAPER_HEIGHT_MM, PAPER_WIDTH_MM
    PAPER_ASPECT = PAPER_WIDTH_MM / PAPER_HEIGHT_MM
    is_landscape = not is_landscape
    if last_drag_coords:
        process_selection(*last_drag_coords)
        if has_drawn: redraw_contours()
    else:
        ax2.set_box_aspect(PAPER_HEIGHT_MM / PAPER_WIDTH_MM);
        fig.canvas.draw_idle()


def export_action(event):
    if current_dem_crop is None or not has_drawn:
        print("Please make a selection and click 'Draw' before exporting.")
        return

    root = tk.Tk()
    root.withdraw()
    orientation_str = "landscape" if is_landscape else "portrait"
    default_name = f"{active_planet.lower()}_{orientation_str}_{CURRENT_PAPER}"

    base_name = simpledialog.askstring("Export SVG", "Enter save name (without extension):", initialvalue=default_name)
    root.destroy()

    if not base_name:
        print("Export cancelled.")
        return

    file_prefix = f"{base_name}_{interval_ft}ft_pen{pen_mm}mm"
    filename = f"{file_prefix}.svg"

    version = 2
    while os.path.exists(filename):
        filename = f"{file_prefix}_v{version}.svg"
        version += 1

    print(f"\nGenerating {filename} with exact physical dimensions...")

    levels, crop_feet = get_contour_levels(current_dem_crop, interval_ft)

    # Define exact physical size in inches for Matplotlib SVG Exporter
    inches_w = PAPER_WIDTH_MM / 25.4
    inches_h = PAPER_HEIGHT_MM / 25.4

    export_fig = plt.figure(figsize=(inches_w, inches_h))
    export_ax = export_fig.add_axes([0, 0, 1, 1]);
    export_ax.axis('off')

    lw_points = pen_mm * PT_PER_MM

    if len(levels) > 0:
        export_ax.contour(crop_feet, levels=levels, colors='black', linewidths=lw_points, extent=current_data_extent,
                          origin='upper')

    export_ax.set_xlim(current_view_extent[0], current_view_extent[1])
    export_ax.set_ylim(current_view_extent[2], current_view_extent[3])

    export_fig.savefig(filename, format='svg')
    plt.close(export_fig)
    print(f"Success! Saved to your folder.")
    print(f"-> This SVG is exactly {PAPER_WIDTH_MM}mm x {PAPER_HEIGHT_MM}mm, ready for vpype.")


btn_draw.on_clicked(draw_action)
btn_clear.on_clicked(clear_action)
btn_rotate.on_clicked(rotate_action)
btn_export.on_clicked(export_action)


def toggle_features_action(label):
    global show_ocean, show_borders
    if label == 'Show Ocean Topo':
        show_ocean = not show_ocean
        if has_drawn: redraw_contours()
    elif label == 'Show Borders':
        show_borders = not show_borders
        draw_borders();
        fig.canvas.draw_idle()


top_toggles.on_clicked(toggle_features_action)


# Slider Syncing
def update_from_slider_int(val):
    global interval_ft
    interval_ft = int(val)
    text_int.set_val(str(interval_ft))
    if has_drawn: redraw_contours()


def update_from_text_int(text):
    global interval_ft
    try:
        val = int(text)
        if val < 1: val = 1
        interval_ft = val
        slider_int.eventson = False;
        slider_int.set_val(val);
        slider_int.eventson = True
        if has_drawn: redraw_contours()
    except ValueError:
        pass


def update_from_slider_pen(val):
    global pen_mm
    pen_mm = float(val)
    text_pen.set_val(f"{pen_mm:.2f}")
    if has_drawn: redraw_contours()


def update_from_text_pen(text):
    global pen_mm
    try:
        val = float(text)
        if val < 0.01: val = 0.01
        pen_mm = val
        slider_pen.eventson = False;
        slider_pen.set_val(val);
        slider_pen.eventson = True
        if has_drawn: redraw_contours()
    except ValueError:
        pass


slider_int.on_changed(update_from_slider_int)
text_int.on_submit(update_from_text_int)
slider_pen.on_changed(update_from_slider_pen)
text_pen.on_submit(update_from_text_pen)


def switch_planet(label):
    global active_planet, current_color_src, current_dem_src, current_color_base
    active_planet = label
    current_color_src, current_dem_src, current_color_base = load_planet_data(label)

    img_base.set_data(current_color_base)
    img_highres.set_visible(False)
    ax1.set_xlim(-180, 180);
    ax1.set_ylim(-90, 90)
    ax1.set_title(f"{active_planet} Visual Map (Scroll to Zoom, Draw to Select)", pad=10)

    refresh_highres_map()
    draw_borders()
    clear_action()


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

    refresh_highres_map()
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