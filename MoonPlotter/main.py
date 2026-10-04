import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import RectangleSelector, Slider
import tifffile
import os

# ==========================================
# 1. SETTINGS & CONFIGURATION
# ==========================================
COLOR_FILE = 'lroc_color_16bit_srgb_4k.tif'
DEM_FILE = 'ldem_16.tif'

# Define your paper sizes here (Width, Height).
PAPER_SIZES = {
    'A4_Landscape': (297, 210),
    'A4_Portrait': (210, 297),
    'Letter_Landscape': (11, 8.5),
    'Letter_Portrait': (8.5, 11),
    'Square': (10, 10)
}

# Change this to switch your target export size
CURRENT_PAPER = 'A4_Landscape'
PAPER_WIDTH, PAPER_HEIGHT = PAPER_SIZES[CURRENT_PAPER]
PAPER_ASPECT = PAPER_WIDTH / PAPER_HEIGHT

# Globals to hold current view data
current_dem_crop = None
current_data_extent = None
current_view_extent = None

# ==========================================
# 2. LOAD & DEBUG IMAGES
# ==========================================
print("Loading maps...")

if not os.path.exists(COLOR_FILE):
    print(f"\nERROR: Could not find '{COLOR_FILE}'. Creating dummy data for testing.")
    color_img = np.zeros((2048, 4096, 3), dtype=np.uint8)
else:
    try:
        color_img = tifffile.imread(COLOR_FILE)
        print(f"Color map loaded! Shape: {color_img.shape}")
        # Convert 16-bit to 8-bit for Matplotlib display
        if color_img.dtype == np.uint16:
            color_img = (color_img / 256).astype(np.uint8)
    except Exception as e:
        print(f"\nERROR loading color map: {e}")
        color_img = np.zeros((2048, 4096, 3), dtype=np.uint8)

if not os.path.exists(DEM_FILE):
    print(f"\nERROR: Could not find '{DEM_FILE}'.")
    exit()
else:
    dem_img = tifffile.imread(DEM_FILE)
    print(f"Elevation map loaded! Shape: {dem_img.shape}")

# ==========================================
# 3. GUI SETUP
# ==========================================
fig = plt.figure(figsize=(16, 8))

# Define custom layout to leave room at the bottom for the slider
ax1 = fig.add_axes([0.05, 0.15, 0.4, 0.75])
ax2 = fig.add_axes([0.55, 0.15, 0.4, 0.75])
ax_slider = fig.add_axes([0.3, 0.05, 0.4, 0.03])

# Setup Left Panel (Visual Map)
ax1.imshow(color_img, extent=[-180, 180, -90, 90], origin='upper')
ax1.set_title("Visual Map (Draw free-form selection)")
ax1.set_xlabel("Longitude")
ax1.set_ylabel("Latitude")

# Setup Right Panel (Topo Preview)
# Physically lock the display frame to match the paper aspect ratio
ax2.set_box_aspect(PAPER_HEIGHT / PAPER_WIDTH)
ax2.set_title(f"Topo View ({CURRENT_PAPER})\nPress 'e' to export")
ax2.set_xlabel("Longitude")
ax2.set_xticks([])
ax2.set_yticks([])

# Add Slider
contour_slider = Slider(
    ax=ax_slider,
    label='Number of Lines',
    valmin=5,
    valmax=150,
    valinit=30,
    valstep=1
)


# ==========================================
# 4. INTERACTIVE LOGIC
# ==========================================
def redraw_contours():
    """Clears the right panel and redraws contours based on slider value."""
    if current_dem_crop is None: return

    ax2.clear()
    ax2.set_box_aspect(PAPER_HEIGHT / PAPER_WIDTH)
    ax2.set_title(f"Topo View ({CURRENT_PAPER})\nPress 'e' to export")
    ax2.set_xlabel("Longitude")
    ax2.set_xticks([])
    ax2.set_yticks([])

    # Draw contours using the current slider value
    num_lines = int(contour_slider.val)
    ax2.contour(current_dem_crop, levels=num_lines, colors='black', linewidths=0.5,
                extent=current_data_extent, origin='upper')

    # Enforce the paper-aspect-ratio framing window
    ax2.set_xlim(current_view_extent[0], current_view_extent[1])
    ax2.set_ylim(current_view_extent[2], current_view_extent[3])
    fig.canvas.draw_idle()


def update_slider(val):
    redraw_contours()


contour_slider.on_changed(update_slider)


def on_select(eclick, erelease):
    """Triggered when dragging a box on the left map."""
    global current_dem_crop, current_data_extent, current_view_extent

    x1, x2 = sorted([eclick.xdata, erelease.xdata])
    y1, y2 = sorted([eclick.ydata, erelease.ydata])

    width = x2 - x1
    height = y2 - y1
    if width == 0 or height == 0: return

    # 1. Calculate the padded bounds to fit the exact Paper Aspect Ratio
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2

    if width / height > PAPER_ASPECT:
        pad_width = width
        pad_height = width / PAPER_ASPECT
    else:
        pad_width = height * PAPER_ASPECT
        pad_height = height

    ex1 = center_x - pad_width / 2
    ex2 = center_x + pad_width / 2
    ey1 = center_y - pad_height / 2
    ey2 = center_y + pad_height / 2

    current_view_extent = [ex1, ex2, ey1, ey2]

    # 2. Slice the Elevation Map safely
    h, w = dem_img.shape
    px_x1 = int((ex1 + 180) / 360 * w)
    px_x2 = int((ex2 + 180) / 360 * w)
    px_y1 = int((90 - ey2) / 180 * h)  # Y is inverted (top is row 0)
    px_y2 = int((90 - ey1) / 180 * h)

    px_x1_clip, px_x2_clip = max(0, px_x1), min(w, px_x2)
    px_y1_clip, px_y2_clip = max(0, px_y1), min(h, px_y2)

    if px_x2_clip <= px_x1_clip or px_y2_clip <= px_y1_clip:
        return

    current_dem_crop = dem_img[px_y1_clip:px_y2_clip, px_x1_clip:px_x2_clip]

    # Calculate the true geographic extent of the sliced pixels
    # (in case the selection goes slightly off the edge of the map)
    actual_ex1 = px_x1_clip / w * 360 - 180
    actual_ex2 = px_x2_clip / w * 360 - 180
    actual_ey2 = 90 - (px_y1_clip / h * 180)
    actual_ey1 = 90 - (px_y2_clip / h * 180)
    current_data_extent = [actual_ex1, actual_ex2, actual_ey1, actual_ey2]

    redraw_contours()


def on_key_press(event):
    """Exports the exact SVG path mapping."""
    if event.key == 'e' and current_dem_crop is not None:
        num_lines = int(contour_slider.val)
        filename = f'moon_plot_{CURRENT_PAPER}_{num_lines}lines.svg'
        print(f"\nGenerating {filename}...")

        # Create a figure perfectly scaled to the paper ratio
        export_fig = plt.figure(figsize=(10, 10 / PAPER_ASPECT))
        export_ax = export_fig.add_axes([0, 0, 1, 1])
        export_ax.axis('off')

        export_ax.contour(current_dem_crop, levels=num_lines, colors='black', linewidths=1,
                          extent=current_data_extent, origin='upper')

        export_ax.set_xlim(current_view_extent[0], current_view_extent[1])
        export_ax.set_ylim(current_view_extent[2], current_view_extent[3])

        export_fig.savefig(filename, format='svg')
        plt.close(export_fig)

        print(f"Success! Saved to your folder. Ready for plotting.")


# Setup interactive selection tool on the left panel
selector = RectangleSelector(
    ax1, on_select,
    useblit=True,
    button=[1],
    minspanx=5, minspany=5,
    spancoords='data',
    interactive=True,
    props=dict(facecolor='red', edgecolor='red', alpha=0.3, fill=True)
)

fig.canvas.mpl_connect('key_press_event', on_key_press)
plt.show()