import sys
import os
import numpy as np
import rasterio
from rasterio.windows import Window
from PIL import Image

# PyQt6 Imports
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QSlider, QLineEdit, QPushButton,
                             QRadioButton, QCheckBox, QComboBox, QSplitter,
                             QFileDialog, QMessageBox, QFrame)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QDoubleValidator, QIntValidator, QFont

# Matplotlib Backend Imports
import matplotlib

matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.widgets import RectangleSelector

import warnings

warnings.filterwarnings('ignore', r'All-NaN (slice|axis) encountered')
Image.MAX_IMAGE_PIXELS = None

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

# ==========================================
# 1. CONFIGURATION
# ==========================================
DATA_FILES = {
    'Earth': {'color': 'earth_color.tif', 'dem': 'BE_dem_v2.tif', 'multiplier': 3.28084},
    'Moon': {'color': 'lroc_color_16bit_srgb_4k.tif', 'dem': 'ldem_16.tif', 'multiplier': 3280.84}
}

PAPER_SIZES = {
    '100x100mm (Default)': (100.0, 100.0),
    'Ender3_Safe (200x200)': (200.0, 200.0),
    'A4': (297.0, 210.0),
    'Letter': (279.4, 215.9),
    'Custom (mm)': (150.0, 150.0)
}

PT_PER_MM = 72.0 / 25.4
loaded_maps_cache = {}


def load_planet_data(planet):
    if planet in loaded_maps_cache:
        return loaded_maps_cache[planet]

    print(f"\nLoading data for {planet}...")
    files = DATA_FILES[planet]

    # 4096 high-resolution loader
    if not os.path.exists(files['color']):
        base_img = np.zeros((2048, 4096, 3), dtype=np.uint8)
    else:
        if files['color'].endswith('.tif'):
            with rasterio.open(files['color']) as src:
                scale = max(1.0, src.width / 4096.0)
                new_w, new_h = int(src.width / scale), int(src.height / scale)
                print(f"  -> Loading visual map at {new_w}x{new_h} resolution...")
                base_img = src.read(out_shape=(src.count, new_h, new_w),
                                    resampling=rasterio.enums.Resampling.bilinear).transpose(1, 2, 0)
                if base_img.dtype == np.uint16:
                    base_img = (base_img / 256).astype(np.uint8)
        else:
            base_img = np.array(Image.open(files['color']))

    dem_path = files['dem']
    if not os.path.exists(dem_path):
        dem_src = None
        print(f"  -> WARNING: '{dem_path}' not found.")
    else:
        dem_src = rasterio.open(dem_path)
        print(f"  -> Connected to DEM: {dem_src.shape[1]}x{dem_src.shape[0]} pixels")

    loaded_maps_cache[planet] = (dem_src, base_img)
    return dem_src, base_img


# ==========================================
# 2. BACKGROUND WORKER THREAD
# ==========================================
class DataFetchWorker(QThread):
    finished = pyqtSignal(object, list, float, float)
    error = pyqtSignal(str)

    def __init__(self, planet, dem_src, bounds):
        super().__init__()
        self.planet = planet
        self.dem_src = dem_src
        self.bounds = bounds

    def run(self):
        try:
            px_x1, px_y1, px_x2, px_y2 = self.bounds
            window = Window(col_off=px_x1, row_off=px_y1, width=(px_x2 - px_x1), height=(px_y2 - px_y1))
            dem_data = self.dem_src.read(1, window=window)

            h, w = self.dem_src.shape
            extent = [px_x1 / w * 360 - 180, px_x2 / w * 360 - 180, 90 - (px_y2 / h * 180), 90 - (px_y1 / h * 180)]

            # Calculate relief for adaptive interval
            mult = DATA_FILES[self.planet]['multiplier']
            crop_ft = dem_data * mult
            crop_ft[crop_ft < -100000] = np.nan
            min_e, max_e = np.nanmin(crop_ft), np.nanmax(crop_ft)

            self.finished.emit(dem_data, extent, min_e, max_e)
        except Exception as e:
            self.error.emit(str(e))


# ==========================================
# 3. MAIN PYQT6 GUI
# ==========================================
class PlotterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Topo Plotter Studio - Professional")
        self.resize(1500, 850)

        # State Variables
        self.active_planet = 'Earth'
        self.paper_name = '100x100mm (Default)'
        self.is_landscape = True
        self.interval_ft = 500
        self.pen_mm = 0.5
        self.show_ocean = True
        self.show_borders = True

        self.current_dem_src = None
        self.current_dem_crop = None
        self.current_data_extent = None
        self.current_view_extent = None
        self.last_drag_coords = None
        self.has_drawn = False
        self.border_artists = []

        # Panning variables
        self.pan_active = False
        self.pan_start_x = None
        self.pan_start_y = None
        self.pan_xlim = None
        self.pan_ylim = None

        self.init_ui()
        self.switch_planet()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # --- LEFT PANEL: CONTROLS ---
        left_panel = QWidget()
        left_panel.setFixedWidth(300)
        vbox = QVBoxLayout(left_panel)
        vbox.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Planet Selection
        vbox.addWidget(QLabel("<b>Planet Source</b>"))
        self.radio_earth = QRadioButton("Earth (Local TIF)")
        self.radio_moon = QRadioButton("Moon (Local TIF)")
        self.radio_earth.setChecked(True)
        self.radio_earth.toggled.connect(self.switch_planet)
        vbox.addWidget(self.radio_earth)
        vbox.addWidget(self.radio_moon)

        # Line separator
        line1 = QFrame();
        line1.setFrameShape(QFrame.Shape.HLine);
        vbox.addWidget(line1)

        # Paper Setup
        vbox.addWidget(QLabel("<b>Paper / Bed Size</b>"))
        self.combo_paper = QComboBox()
        self.combo_paper.addItems(list(PAPER_SIZES.keys()))
        self.combo_paper.currentTextChanged.connect(self.change_paper)
        vbox.addWidget(self.combo_paper)

        # Custom Dimension Inputs (Hidden by default)
        self.widget_custom = QWidget()
        h_custom = QHBoxLayout(self.widget_custom)
        h_custom.setContentsMargins(0, 0, 0, 0)
        self.txt_cw = QLineEdit("150")
        self.txt_ch = QLineEdit("150")
        self.txt_cw.setValidator(QDoubleValidator(10.0, 1000.0, 1))
        self.txt_ch.setValidator(QDoubleValidator(10.0, 1000.0, 1))
        self.txt_cw.textChanged.connect(self.update_paper_dims)
        self.txt_ch.textChanged.connect(self.update_paper_dims)
        h_custom.addWidget(QLabel("W (mm):"));
        h_custom.addWidget(self.txt_cw)
        h_custom.addWidget(QLabel("H (mm):"));
        h_custom.addWidget(self.txt_ch)
        self.widget_custom.setVisible(False)
        vbox.addWidget(self.widget_custom)

        self.btn_orientation = QPushButton("Toggle Portrait/Landscape")
        self.btn_orientation.clicked.connect(self.toggle_orientation)
        vbox.addWidget(self.btn_orientation)

        line2 = QFrame();
        line2.setFrameShape(QFrame.Shape.HLine);
        vbox.addWidget(line2)

        # Topo Settings
        vbox.addWidget(QLabel("<b>Map Options</b>"))
        self.chk_ocean = QCheckBox("Plot Ocean/Crater Depths")
        self.chk_ocean.setChecked(True)
        self.chk_ocean.toggled.connect(self.toggle_ocean)
        self.chk_borders = QCheckBox("Show Reference Borders")
        self.chk_borders.setChecked(True)
        self.chk_borders.toggled.connect(self.toggle_borders)
        vbox.addWidget(self.chk_ocean)
        vbox.addWidget(self.chk_borders)

        vbox.addSpacing(10)
        vbox.addWidget(QLabel("<b>Contour Interval (ft)</b>"))
        h1 = QHBoxLayout()
        self.slider_int = QSlider(Qt.Orientation.Horizontal)
        self.slider_int.setRange(20, 10000)
        self.slider_int.setSingleStep(20)
        self.slider_int.setValue(self.interval_ft)
        self.txt_int = QLineEdit(str(self.interval_ft))
        self.txt_int.setValidator(QIntValidator(1, 20000))
        self.txt_int.setFixedWidth(60)
        self.slider_int.valueChanged.connect(lambda v: self.txt_int.setText(str(v)))
        self.txt_int.textChanged.connect(self.update_interval)
        h1.addWidget(self.slider_int);
        h1.addWidget(self.txt_int)
        vbox.addLayout(h1)

        vbox.addWidget(QLabel("<b>Pen Thickness (mm)</b>"))
        h2 = QHBoxLayout()
        self.slider_pen = QSlider(Qt.Orientation.Horizontal)
        self.slider_pen.setRange(10, 200)
        self.slider_pen.setValue(int(self.pen_mm * 100))
        self.txt_pen = QLineEdit(str(self.pen_mm))
        self.txt_pen.setValidator(QDoubleValidator(0.01, 5.0, 2))
        self.txt_pen.setFixedWidth(60)
        self.slider_pen.valueChanged.connect(lambda v: self.txt_pen.setText(f"{v / 100.0:.2f}"))
        self.txt_pen.textChanged.connect(self.update_pen)
        h2.addWidget(self.slider_pen);
        h2.addWidget(self.txt_pen)
        vbox.addLayout(h2)

        # Adaptive Guidance Box
        self.frame_adaptive = QFrame()
        self.frame_adaptive.setStyleSheet("background-color: #EBF5FB; border: 1px solid #AED6F6; border-radius: 5px; color: black;")
        v_adapt = QVBoxLayout(self.frame_adaptive)
        self.lbl_adaptive = QLabel("<b>Adaptive Guidance</b><br>Local Relief: --- ft<br>Suggested: --- ft")
        self.btn_apply_adaptive = QPushButton("Use Suggested Interval")
        self.btn_apply_adaptive.setEnabled(False)
        self.btn_apply_adaptive.clicked.connect(self.apply_adaptive)
        v_adapt.addWidget(self.lbl_adaptive)
        v_adapt.addWidget(self.btn_apply_adaptive)
        vbox.addWidget(self.frame_adaptive)

        # Actions
        vbox.addStretch()
        self.lbl_status = QLabel("Ready.")
        self.lbl_status.setStyleSheet("color: gray; font-style: italic;")
        vbox.addWidget(self.lbl_status)

        self.btn_draw = QPushButton("DRAW CONTOURS")
        self.btn_draw.setStyleSheet("background-color: #2E86C1; color: white; font-weight: bold; padding: 12px;")
        self.btn_draw.clicked.connect(self.draw_action)
        vbox.addWidget(self.btn_draw)

        self.btn_export = QPushButton("Export SVG & Copy G-Code")
        self.btn_export.setStyleSheet("background-color: #28B463; color: white; font-weight: bold; padding: 12px;")
        self.btn_export.clicked.connect(self.export_action)
        vbox.addWidget(self.btn_export)

        # --- RIGHT PANELS: CANVASES ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Map Container
        map_widget = QWidget()
        v_map = QVBoxLayout(map_widget)
        v_map.setContentsMargins(0, 0, 0, 0)

        self.fig_map = Figure(figsize=(5, 5), dpi=100)
        self.ax_map = self.fig_map.add_subplot(111)
        self.canvas_map = FigureCanvas(self.fig_map)
        v_map.addWidget(self.canvas_map)

        # Map Toolbar (Below Map)
        h_toolbar = QHBoxLayout()
        self.btn_home = QPushButton("🏠 Reset View")
        self.btn_home.clicked.connect(self.reset_home_view)

        self.btn_zoom_tool = QPushButton("🔍 Zoom Tool")
        self.btn_zoom_tool.setCheckable(True)
        self.btn_zoom_tool.toggled.connect(self.toggle_zoom_tool)

        self.btn_clear_sel = QPushButton("❌ Clear Selection")
        self.btn_clear_sel.clicked.connect(self.clear_selection)

        h_toolbar.addWidget(self.btn_home)
        h_toolbar.addWidget(self.btn_zoom_tool)
        h_toolbar.addWidget(QLabel("<i>(Middle-click & drag to pan)</i>"))
        h_toolbar.addStretch()
        h_toolbar.addWidget(self.btn_clear_sel)
        v_map.addLayout(h_toolbar)
        splitter.addWidget(map_widget)

        # Preview Canvas
        self.fig_prev = Figure(figsize=(5, 5), dpi=100)
        self.ax_prev = self.fig_prev.add_subplot(111)
        self.canvas_prev = FigureCanvas(self.fig_prev)
        splitter.addWidget(self.canvas_prev)

        # Selection Tools
        self.selector = RectangleSelector(self.ax_map, self.on_select, useblit=True,
                                          button=[1], interactive=True,
                                          props=dict(facecolor='red', edgecolor='red', alpha=0.3, fill=True))

        self.zoom_selector = RectangleSelector(self.ax_map, self.on_zoom_select, useblit=True,
                                               button=[1], interactive=False,
                                               props=dict(facecolor='blue', edgecolor='blue', alpha=0.2, fill=True))
        self.zoom_selector.set_active(False)

        # Panning Events
        self.canvas_map.mpl_connect('scroll_event', self.zoom_map)
        self.canvas_map.mpl_connect('button_press_event', self.on_mouse_press)
        self.canvas_map.mpl_connect('motion_notify_event', self.on_mouse_motion)
        self.canvas_map.mpl_connect('button_release_event', self.on_mouse_release)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(splitter)

        self.update_paper_dims()

    # --- MAP TOOLBAR LOGIC ---
    def toggle_zoom_tool(self, checked):
        if checked:
            self.selector.set_active(False)
            self.zoom_selector.set_active(True)
            self.btn_zoom_tool.setStyleSheet("background-color: lightblue; font-weight: bold;")
        else:
            self.selector.set_active(True)
            self.zoom_selector.set_active(False)
            self.btn_zoom_tool.setStyleSheet("")

    def on_zoom_select(self, eclick, erelease):
        x1, x2 = sorted([eclick.xdata, erelease.xdata])
        y1, y2 = sorted([eclick.ydata, erelease.ydata])
        self.ax_map.set_xlim(x1, x2)
        self.ax_map.set_ylim(y1, y2)
        self.canvas_map.draw_idle()
        self.btn_zoom_tool.setChecked(False)  # Auto turn off

    def reset_home_view(self):
        self.ax_map.set_xlim(-180, 180)
        self.ax_map.set_ylim(-90, 90)
        self.canvas_map.draw_idle()

    def clear_selection(self):
        self.last_drag_coords = None
        self.has_drawn = False
        self.selector.extents = (0, 0, 0, 0)
        self.ax_prev.clear()
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])
        self.ax_prev.text(0.5, 0.5, "Selection cleared.", ha='center', va='center')
        self.canvas_prev.draw_idle()
        self.canvas_map.draw_idle()
        self.lbl_adaptive.setText("<b>Adaptive Guidance</b><br>Local Relief: --- ft<br>Suggested: --- ft")
        self.btn_apply_adaptive.setEnabled(False)

    def on_mouse_press(self, event):
        if event.button == 2:  # Middle mouse button
            self.pan_active = True
            self.pan_start_x = event.x
            self.pan_start_y = event.y
            self.pan_xlim = self.ax_map.get_xlim()
            self.pan_ylim = self.ax_map.get_ylim()

    def on_mouse_motion(self, event):
        if self.pan_active and event.inaxes == self.ax_map:
            inv = self.ax_map.transData.inverted()
            start_data = inv.transform((self.pan_start_x, self.pan_start_y))
            end_data = inv.transform((event.x, event.y))
            dx = end_data[0] - start_data[0]
            dy = end_data[1] - start_data[1]

            self.ax_map.set_xlim(self.pan_xlim[0] - dx, self.pan_xlim[1] - dx)
            self.ax_map.set_ylim(self.pan_ylim[0] - dy, self.pan_ylim[1] - dy)
            self.canvas_map.draw_idle()

    def on_mouse_release(self, event):
        if event.button == 2:
            self.pan_active = False

    # --- UI UPDATES & LOGIC ---
    def update_paper_dims(self):
        if self.paper_name == 'Custom (mm)':
            self.widget_custom.setVisible(True)
            try:
                w, h = float(self.txt_cw.text()), float(self.txt_ch.text())
            except ValueError:
                w, h = 150.0, 150.0
        else:
            self.widget_custom.setVisible(False)
            w, h = PAPER_SIZES[self.paper_name]

        self.pw = w if self.is_landscape else h
        self.ph = h if self.is_landscape else w
        self.paper_aspect = self.pw / self.ph
        self.ax_prev.set_box_aspect(self.ph / self.pw)
        self.canvas_prev.draw_idle()
        if self.last_drag_coords: self.process_selection(*self.last_drag_coords)

    def change_paper(self, name):
        self.paper_name = name
        self.update_paper_dims()

    def toggle_orientation(self):
        self.is_landscape = not self.is_landscape
        self.update_paper_dims()

    def toggle_ocean(self, checked):
        self.show_ocean = checked
        if self.has_drawn: self.redraw_contours()

    def toggle_borders(self, checked):
        self.show_borders = checked
        for artist in self.border_artists: artist.remove()
        self.border_artists = []
        if self.show_borders and HAS_GPD and self.active_planet == 'Earth':
            b1 = world_borders.boundary.plot(ax=self.ax_map, edgecolor='cyan', linewidth=0.6, alpha=0.5, zorder=2)
            b2 = state_borders.boundary.plot(ax=self.ax_map, edgecolor='cyan', linewidth=0.2, alpha=0.3, zorder=2)
            self.border_artists.extend(b1.collections + b2.collections)
        self.canvas_map.draw_idle()

    def update_interval(self, text):
        if text.isdigit() and int(text) > 0:
            self.interval_ft = int(text)
            self.slider_int.blockSignals(True)
            self.slider_int.setValue(self.interval_ft)
            self.slider_int.blockSignals(False)
            if self.has_drawn: self.redraw_contours()

    def update_pen(self, text):
        try:
            val = float(text)
            if val > 0:
                self.pen_mm = val
                self.slider_pen.blockSignals(True)
                self.slider_pen.setValue(int(val * 100))
                self.slider_pen.blockSignals(False)
                if self.has_drawn: self.redraw_contours()
        except ValueError:
            pass

    def apply_adaptive(self):
        if hasattr(self, 'suggested_interval'):
            self.txt_int.setText(str(self.suggested_interval))

    def switch_planet(self):
        self.active_planet = 'Earth' if self.radio_earth.isChecked() else 'Moon'
        self.current_dem_src, base_img = load_planet_data(self.active_planet)

        self.ax_map.clear()
        self.ax_map.imshow(base_img, extent=[-180, 180, -90, 90], origin='upper', zorder=0)
        self.ax_map.set_title(f"Select Region on {self.active_planet}")

        self.border_artists = []
        if self.chk_borders.isChecked(): self.toggle_borders(True)

        self.clear_selection()
        self.reset_home_view()

    def zoom_map(self, event):
        if event.inaxes != self.ax_map: return
        scale = 1 / 1.2 if event.button == 'up' else 1.2
        cur_xlim, cur_ylim = self.ax_map.get_xlim(), self.ax_map.get_ylim()
        xdata, ydata = event.xdata, event.ydata
        if xdata is None or ydata is None: return
        nw = (cur_xlim[1] - cur_xlim[0]) * scale
        nh = (cur_ylim[1] - cur_ylim[0]) * scale
        relx, rely = (cur_xlim[1] - xdata) / (cur_xlim[1] - cur_xlim[0]), (cur_ylim[1] - ydata) / (
                    cur_ylim[1] - cur_ylim[0])
        self.ax_map.set_xlim([max(-180, xdata - nw * (1 - relx)), min(180, xdata + nw * relx)])
        self.ax_map.set_ylim([max(-90, ydata - nh * (1 - rely)), min(90, ydata + nh * rely)])
        self.canvas_map.draw_idle()

    def on_select(self, eclick, erelease):
        x1, x2 = sorted([eclick.xdata, erelease.xdata])
        y1, y2 = sorted([eclick.ydata, erelease.ydata])
        self.last_drag_coords = (x1, x2, y1, y2)
        self.process_selection(x1, x2, y1, y2)

    def process_selection(self, x1, x2, y1, y2):
        if self.current_dem_src is None: return
        gw, gh = x2 - x1, y2 - y1
        if gw == 0 or gh == 0: return

        mid_lat = np.radians((y1 + y2) / 2)
        tw = gw * np.cos(mid_lat)
        ta = tw / gh

        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if ta > self.paper_aspect:
            pad_geo_h = tw / self.paper_aspect
            pad_geo_w = gw
        else:
            pad_geo_h = gh
            pad_geo_w = (gh * self.paper_aspect) / np.cos(mid_lat)

        ex1, ex2 = cx - pad_geo_w / 2, cx + pad_geo_w / 2
        ey1, ey2 = cy - pad_geo_h / 2, cy + pad_geo_h / 2
        self.current_view_extent = [ex1, ex2, ey1, ey2]

        h, w = self.current_dem_src.shape
        px_x1, px_x2 = int((ex1 + 180) / 360 * w), int((ex2 + 180) / 360 * w)
        px_y1, px_y2 = int((90 - ey2) / 180 * h), int((90 - ey1) / 180 * h)

        px_x1_clip, px_x2_clip = max(0, px_x1), min(w, px_x2)
        px_y1_clip, px_y2_clip = max(0, px_y1), min(h, px_y2)

        if px_x2_clip <= px_x1_clip or px_y2_clip <= px_y1_clip: return

        self.pending_bounds = [px_x1_clip, px_y1_clip, px_x2_clip, px_y2_clip]
        self.has_drawn = False
        self.ax_prev.clear();
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])
        self.ax_prev.text(0.5, 0.5, "Ready.\nClick 'DRAW CONTOURS'.", ha='center', va='center')
        self.canvas_prev.draw_idle()
        self.lbl_status.setText("Selection captured. Waiting for Draw.")

    def draw_action(self):
        if not self.last_drag_coords or self.current_dem_src is None: return
        self.btn_draw.setEnabled(False)
        self.lbl_status.setText("Reading local high-res DEM data... Please wait.")
        self.ax_prev.clear()
        self.ax_prev.text(0.5, 0.5, "Extracting & Processing...", ha='center', va='center')
        self.canvas_prev.draw()

        self.worker = DataFetchWorker(self.active_planet, self.current_dem_src, self.pending_bounds)
        self.worker.finished.connect(self.on_fetch_success)
        self.worker.error.connect(self.on_fetch_error)
        self.worker.start()

    def on_fetch_success(self, dem_data, extent, min_e, max_e):
        self.current_dem_crop = dem_data
        self.current_data_extent = extent

        # Adaptive Guidance Math (~35 lines target)
        relief = max_e - min_e
        if relief > 0:
            target_lines = 35
            raw_interval = relief / target_lines
            nice_steps = [10, 20, 50, 100, 200, 250, 500, 1000, 2000, 5000]
            best_step = next((s for s in nice_steps if s >= raw_interval), nice_steps[-1])
            self.suggested_interval = best_step
            self.lbl_adaptive.setText(
                f"<b>Adaptive Guidance</b><br>Local Relief: {int(relief):,} ft<br>Suggested: {best_step} ft")
            self.btn_apply_adaptive.setEnabled(True)

        self.has_drawn = True
        self.redraw_contours()
        self.btn_draw.setEnabled(True)
        self.lbl_status.setText("Render complete!")

    def on_fetch_error(self, err_msg):
        self.btn_draw.setEnabled(True)
        self.lbl_status.setText("Error reading data.")
        QMessageBox.critical(self, "Read Error", f"Failed to fetch data:\n{err_msg}")

    def redraw_contours(self):
        if not self.has_drawn or self.current_dem_crop is None: return
        self.ax_prev.clear();
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])

        mult = DATA_FILES[self.active_planet]['multiplier']
        crop_ft = self.current_dem_crop * mult
        if np.isnan(crop_ft).all():
            self.ax_prev.text(0.5, 0.5, "No valid data here.", ha='center')
            self.canvas_prev.draw_idle()
            return

        min_e, max_e = np.nanmin(crop_ft), np.nanmax(crop_ft)
        s_lvl = np.floor(min_e / self.interval_ft) * self.interval_ft
        e_lvl = np.ceil(max_e / self.interval_ft) * self.interval_ft
        levels = np.arange(s_lvl, e_lvl + self.interval_ft, self.interval_ft)

        if self.active_planet == 'Earth' and not self.show_ocean:
            levels = [l for l in levels if l >= 0]
            if not levels and max_e >= 0: levels = [0]

        if len(levels) > 300: levels = np.linspace(max(0, min_e) if not self.show_ocean else min_e, max_e, 300)

        if len(levels) > 0:
            mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
            self.ax_prev.set_aspect(1 / np.cos(mid_lat))
            self.ax_prev.contour(crop_ft, levels=levels, colors='black',
                                 linewidths=(self.pen_mm * PT_PER_MM),
                                 extent=self.current_data_extent, origin='upper')
            self.ax_prev.set_xlim(self.current_view_extent[0], self.current_view_extent[1])
            self.ax_prev.set_ylim(self.current_view_extent[2], self.current_view_extent[3])

        self.canvas_prev.draw_idle()

    def export_action(self):
        if not self.has_drawn:
            QMessageBox.warning(self, "Not Ready", "Draw a selection first!")
            return

        orient = "landscape" if self.is_landscape else "portrait"
        default_name = f"{self.active_planet.lower()}_{orient}_{int(self.pw)}x{int(self.ph)}_{self.interval_ft}ft.svg"

        filepath, _ = QFileDialog.getSaveFileName(self, "Export SVG", default_name, "SVG Files (*.svg)")
        if not filepath: return

        self.lbl_status.setText("Exporting SVG...")
        QApplication.processEvents()

        mult = DATA_FILES[self.active_planet]['multiplier']
        crop_ft = self.current_dem_crop * mult
        min_e, max_e = np.nanmin(crop_ft), np.nanmax(crop_ft)
        s_lvl = np.floor(min_e / self.interval_ft) * self.interval_ft
        e_lvl = np.ceil(max_e / self.interval_ft) * self.interval_ft
        levels = np.arange(s_lvl, e_lvl + self.interval_ft, self.interval_ft)
        if self.active_planet == 'Earth' and not self.show_ocean:
            levels = [l for l in levels if l >= 0]

        export_fig = Figure(figsize=(self.pw / 25.4, self.ph / 25.4))
        export_ax = export_fig.add_axes([0, 0, 1, 1]);
        export_ax.axis('off')

        mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
        export_ax.set_aspect(1 / np.cos(mid_lat))

        if len(levels) > 0:
            export_ax.contour(crop_ft, levels=levels, colors='black',
                              linewidths=(self.pen_mm * PT_PER_MM),
                              extent=self.current_data_extent, origin='upper')

        export_ax.set_xlim(self.current_view_extent[0], self.current_view_extent[1])
        export_ax.set_ylim(self.current_view_extent[2], self.current_view_extent[3])
        export_fig.savefig(filepath, format='svg')

        vpype_cmd = f'vpype read "{filepath}" linesimplify -t 0.1mm linemerge -t 0.3mm linesort gwrite --profile ender3 output.gcode'
        QApplication.clipboard().setText(vpype_cmd)

        self.lbl_status.setText("Saved! vpype command copied to clipboard.")
        QMessageBox.information(self, "Success",
                                f"SVG exported to:\n{filepath}\n\nvpype G-code command copied to clipboard!")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = PlotterApp()
    window.show()
    sys.exit(app.exec())