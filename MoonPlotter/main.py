import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import RectangleSelector, Slider, RadioButtons, TextBox, CheckButtons
from PIL import Image
import rasterio
from rasterio.windows import Window
import os
import warnings

# Suppress warnings that happen if you select an area that is entirely "No Data" or all Ocean
warnings.filterwarnings('ignore', r'All-NaN (slice|axis) encountered')
Image.MAX_IMAGE_PIXELS = None

# ==========================================
# 1. SETTINGS & CONFIGURATION
# ==========================================
DATA_FILES = {
    'Moon': {
        'color': 'lroc_color_16bit_srgb_4k.tif',
        'dem': 'ldem_16.tif',
        'multiplier_to_feet': 3280.84  # NASA float TIFF is in km
    },
    'Earth': {
        'color': 'earth_color.tif',
        'dem': 'BE_dem_v2.tif',
        'multiplier_to_feet': 3.28084  # Blue Earth is in meters
    }
}

PAPER_SIZES = {
    'A4_Landscape': (297, 210),
    'A4_Portrait': (210, 297),
    'Letter_Landscape': (11, 8.5),
    'Letter_Portrait': (8.5, 11),
    'Square': (10, 10)
}

CURRENT_PAPER = 'A4_Landscape'
PAPER_WIDTH, PAPER_HEIGHT = PAPER_SIZES[CURRENT_PAPER]
PAPER_ASPECT = PAPER_WIDTH / PAPER_HEIGHT

active_planet = 'Earth'
loaded_maps_cache = {}
current_dem_crop = None
current_data_extent = None
current_view_extent = None

# New Globals for UI State
interval_ft = 1000
show_ocean = True


# ==========================================
# 2. DATA LOADING (LAZY ENGINE)
# ==========================================
def load_planet_data(planet):
    if planet in loaded_maps_cache:
        return loaded_maps_cache[planet]

    print(f"\nLoading data for {planet}...")
    files = DATA_FILES[planet]

    if not os.path.exists(files['color']):
        print(f"  -> WARNING: '{files['color']}' not found. Using placeholder.")
        color_img = np.zeros((1024, 2048, 3), dtype=np.uint8)
    else:
        if files['color'].endswith('.tif'):
            with rasterio.open(files['color']) as src:
                color_img = src.read().transpose(1, 2, 0)
                if color_img.dtype == np.uint16:
                    color_img = (color_img / 256).astype(np.uint8)
        else:
            color_img = np.array(Image.open(files['color']))

    if not os.path.exists(files['dem']):
        print(f"  -> WARNING: '{files['dem']}' not found.")
        dem_src = None
    else:
        dem_src = rasterio.open(files['dem'])
        print(f"  -> Connected to DEM: {dem_src.shape[1]}x{dem_src.shape[0]} pixels")

    loaded_maps_cache[planet] = (color_img, dem_src)
    return color_img, dem_src


current_color, current_dem_src = load_planet_data(active_planet)

# ==========================================
# 3. GUI SETUP
# ==========================================
fig = plt.figure(figsize=(16, 8))

# Layout Areas
ax1 = fig.add_axes([0.05, 0.15, 0.4, 0.70])
ax2 = fig.add_axes([0.55, 0.15, 0.4, 0.70])

# Top Control Bar (Planet & Ocean)
ax_radio = fig.add_axes([0.05, 0.88, 0.08, 0.10])
ax_toggle = fig.add_axes([0.15, 0.88, 0.15, 0.10])

# Bottom Control Bar (Slider & Text Box)
ax_slider = fig.add_axes([0.25, 0.05, 0.4, 0.03])
ax_text = fig.add_axes([0.78, 0.05, 0.08, 0.04])

# Setup Left Panel
img_display = ax1.imshow(current_color, extent=[-180, 180, -90, 90], origin='upper')
ax1.set_title(f"{active_planet} Visual Map (Scroll to Zoom, Draw to Select)", pad=10)
ax1.set_xlabel("Longitude")
ax1.set_ylabel("Latitude")

# Setup Right Panel
ax2.set_box_aspect(PAPER_HEIGHT / PAPER_WIDTH)
ax2.set_title(f"{active_planet} Topo View ({CURRENT_PAPER})\nPress 'e' to export")
ax2.set_xlabel("Longitude")
ax2.set_xticks([]);
ax2.set_yticks([])

# Controls
radio = RadioButtons(ax_radio, ('Earth', 'Moon'), active=0)  # Removed orientation keyword for compatibility
ocean_toggle = CheckButtons(ax_toggle, ['Show Ocean Topo'], [True])

contour_slider = Slider(ax=ax_slider, label='Contour Interval (ft)', valmin=50, valmax=10000, valinit=interval_ft,
                        valstep=50)
text_box = TextBox(ax_text, 'Exact ft: ', initial=str(interval_ft))


# ==========================================
# 4. INTERACTIVE LOGIC & MATH
# ==========================================

def get_contour_levels(dem_crop_raw, interval):
    multiplier = DATA_FILES[active_planet]['multiplier_to_feet']

    crop_feet = dem_crop_raw.astype(np.float32) * multiplier
    crop_feet[crop_feet < -100000] = np.nan

    # Toggle Ocean Topography off (sets anything below sea level to 'No Data')
    if active_planet == 'Earth' and not show_ocean:
        crop_feet[crop_feet < 0] = np.nan

    # Check if the whole area is NaN (e.g. they selected pure ocean and ocean is off)
    if np.isnan(crop_feet).all():
        return [], crop_feet

    min_elev = np.nanmin(crop_feet)
    max_elev = np.nanmax(crop_feet)

    start_level = np.floor(min_elev / interval) * interval
    end_level = np.ceil(max_elev / interval) * interval

    levels = np.arange(start_level, end_level + interval, interval)

    if len(levels) > 300:
        print(f"Warning: {len(levels)} lines requested. Capping at 300 to protect RAM.")
        levels = np.linspace(min_elev, max_elev, 300)

    if len(levels) < 2:
        levels = [min_elev, max_elev] if min_elev != max_elev else []

    return levels, crop_feet


def redraw_contours():
    if current_dem_crop is None: return
    ax2.clear()
    ax2.set_box_aspect(PAPER_HEIGHT / PAPER_WIDTH)
    ax2.set_title(f"{active_planet} Topo ({interval_ft} ft/line)\nPress 'e' to export")
    ax2.set_xlabel("Longitude")
    ax2.set_xticks([]);
    ax2.set_yticks([])

    levels, crop_feet = get_contour_levels(current_dem_crop, interval_ft)

    # Only draw if there are levels to draw (avoids a crash if pure ocean is selected)
    if len(levels) > 0:
        ax2.contour(crop_feet, levels=levels, colors='black', linewidths=0.5,
                    extent=current_data_extent, origin='upper')
    else:
        ax2.text(0.5, 0.5, "No topography in this range",
                 horizontalalignment='center', verticalalignment='center', transform=ax2.transAxes)

    ax2.set_xlim(current_view_extent[0], current_view_extent[1])
    ax2.set_ylim(current_view_extent[2], current_view_extent[3])
    fig.canvas.draw_idle()


# --- Sync Slider and Text Box ---
def update_from_slider(val):
    global interval_ft
    interval_ft = int(val)
    text_box.set_val(str(interval_ft))
    redraw_contours()


def update_from_text(text):
    global interval_ft
    try:
        val = int(text)
        if val < 1: val = 1
        interval_ft = val
        # Safely update slider position without re-triggering the event
        contour_slider.eventson = False
        contour_slider.set_val(val)
        contour_slider.eventson = True
        redraw_contours()
    except ValueError:
        pass  # Ignore letters/symbols


contour_slider.on_changed(update_from_slider)
text_box.on_submit(update_from_text)


# --- Toggles ---
def toggle_ocean_action(label):
    global show_ocean
    show_ocean = not show_ocean
    redraw_contours()


ocean_toggle.on_clicked(toggle_ocean_action)


def switch_planet(label):
    global active_planet, current_color, current_dem_src, current_dem_crop
    active_planet = label
    current_color, current_dem_src = load_planet_data(label)
    img_display.set_data(current_color)
    ax1.set_xlim(-180, 180);
    ax1.set_ylim(-90, 90)
    ax1.set_title(f"{active_planet} Visual Map (Scroll to Zoom, Draw to Select)", pad=10)
    current_dem_crop = None
    ax2.clear()
    ax2.set_box_aspect(PAPER_HEIGHT / PAPER_WIDTH)
    ax2.set_title(f"{active_planet} Topo View ({CURRENT_PAPER})\nPress 'e' to export")
    ax2.set_xticks([]);
    ax2.set_yticks([])
    fig.canvas.draw_idle()


radio.on_clicked(switch_planet)


# --- Map Interactions ---
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
    global current_dem_crop, current_data_extent, current_view_extent
    if current_dem_src is None: return

    x1, x2 = sorted([eclick.xdata, erelease.xdata])
    y1, y2 = sorted([eclick.ydata, erelease.ydata])
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

    current_data_extent = [
        px_x1_clip / w * 360 - 180, px_x2_clip / w * 360 - 180,
        90 - (px_y2_clip / h * 180), 90 - (px_y1_clip / h * 180)
    ]
    redraw_contours()


def on_key_press(event):
    if event.key == 'e' and current_dem_crop is not None:
        filename = f'{active_planet.lower()}_plot_{CURRENT_PAPER}_{interval_ft}ft_intervals.svg'
        print(f"\nGenerating {filename}...")

        levels, crop_feet = get_contour_levels(current_dem_crop, interval_ft)

        export_fig = plt.figure(figsize=(10, 10 / PAPER_ASPECT))
        export_ax = export_fig.add_axes([0, 0, 1, 1]);
        export_ax.axis('off')

        if len(levels) > 0:
            export_ax.contour(crop_feet, levels=levels, colors='black', linewidths=1,
                              extent=current_data_extent, origin='upper')

        export_ax.set_xlim(current_view_extent[0], current_view_extent[1])
        export_ax.set_ylim(current_view_extent[2], current_view_extent[3])
        export_fig.savefig(filename, format='svg')
        plt.close(export_fig)
        print(f"Success! Saved to your folder. Ready for plotting.")


# Removed minspanx and minspany so zoomed-in boxes are allowed!
selector = RectangleSelector(
    ax1, on_select, useblit=True, button=[1],
    spancoords='data', interactive=True,
    props=dict(facecolor='red', edgecolor='red', alpha=0.3, fill=True)
)

fig.canvas.mpl_connect('key_press_event', on_key_press)
plt.show()