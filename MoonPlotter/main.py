import sys
import os
import tempfile
import json
import copy
import re
import numpy as np
import rasterio
from rasterio.windows import Window
from PIL import Image
import platform

# PyQt6 Imports
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QRadioButton, QCheckBox, QComboBox, QSplitter,
                             QFileDialog, QMessageBox, QFrame, QButtonGroup,
                             QInputDialog, QColorDialog, QSlider, QScrollArea, QSpinBox, QCompleter)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QDoubleValidator, QIntValidator, QShortcut, QKeySequence, QIcon

# Matplotlib Imports
import matplotlib

matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.widgets import RectangleSelector
from matplotlib.textpath import TextPath
from matplotlib.transforms import Affine2D
from matplotlib.collections import LineCollection
import matplotlib.patches as patches

# Math / Geometry Imports
import scipy.ndimage
from shapely.geometry import box as shp_box, LineString

import warnings

warnings.filterwarnings('ignore', r'All-NaN (slice|axis) encountered')
Image.MAX_IMAGE_PIXELS = None

# Attempt to load vpype
try:
    import vpype_cli

    HAS_VPYPE = True
except ImportError:
    HAS_VPYPE = False
    print("WARNING: vpype API not found. Please 'pip install vpype vpype-gcode'")

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
# 0. CHAIKIN SMOOTHING ALGORITHM
# ==========================================
def chaikin_smooth(coords, iterations=2):
    """Vectorized Chaikin corner-cutting for sweeping cartographic curves."""
    if len(coords) < 3 or iterations == 0:
        return coords
    for _ in range(iterations):
        p0, p1 = coords[:-1], coords[1:]
        q = 0.75 * p0 + 0.25 * p1
        r = 0.25 * p0 + 0.75 * p1
        new_coords = np.empty((len(coords) * 2 - 2, 2))
        new_coords[0::2] = q
        new_coords[1::2] = r
        coords = np.vstack((coords[0], new_coords, coords[-1]))
    return coords


# ==========================================
# 1. CONFIGURATION & PERSISTENCE
# ==========================================
DATA_FILES = {
    'Earth': {'color': 'earth_color.tif', 'dem': 'BE_dem_v2.tif', 'multiplier': 3.28084},
    'Moon': {'color': 'lroc_color_16bit_srgb_4k.tif', 'dem': 'ldem_16.tif', 'multiplier': 3280.84},
    'Mars':  {'color': 'mars_viking_color.tif', 'dem': 'mars_mola_dem_463m.tif', 'multiplier': 3.28084}
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
PENS_FILE = "plotter_pens.json"


def load_pens():
    if os.path.exists(PENS_FILE):
        try:
            with open(PENS_FILE, 'r') as f:
                return json.load(f)
        except:
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
            json.dump(pens, f)
    except:
        pass


def load_planet_data(planet):
    if planet in loaded_maps_cache: return loaded_maps_cache[planet]
    files = DATA_FILES[planet]
    if not os.path.exists(files['color']):
        base_img = np.zeros((2048, 4096, 3), dtype=np.uint8)
    else:
        if files['color'].endswith('.tif'):
            with rasterio.open(files['color']) as src:
                scale = max(1.0, src.width / 4096.0)
                new_w, new_h = int(src.width / scale), int(src.height / scale)
                base_img = src.read(out_shape=(src.count, new_h, new_w),
                                    resampling=rasterio.enums.Resampling.bilinear).transpose(1, 2, 0)
                if base_img.dtype == np.uint16: base_img = (base_img / 256).astype(np.uint8)
        else:
            base_img = np.array(Image.open(files['color']))

    dem_path = files['dem']
    dem_src = rasterio.open(dem_path) if os.path.exists(dem_path) else None
    loaded_maps_cache[planet] = (dem_src, base_img)
    return dem_src, base_img


# ==========================================
# 2. BACKGROUND WORKER THREAD
# ==========================================
    # ==========================================
    # 2. BACKGROUND WORKER THREAD
    # ==========================================
# ==========================================
# 2. BACKGROUND WORKER THREAD
# ==========================================
class DataFetchWorker(QThread):
    finished = pyqtSignal(object, list, float, float)
    error = pyqtSignal(str)

    def __init__(self, planet, dem_path, geo_bounds):
        super().__init__()
        self.planet = planet
        self.dem_path = dem_path
        self.geo_bounds = geo_bounds

    def run(self):
        try:
            from rasterio.transform import from_bounds as transform_from_bounds

            with rasterio.open(self.dem_path) as src:
                lon_min, lon_max, lat_min, lat_max = self.geo_bounds

                # Check if this is one of our global equirectangular maps
                global_dems = [DATA_FILES['Earth']['dem'], DATA_FILES['Moon']['dem'], DATA_FILES['Mars']['dem']]
                is_global = self.dem_path in global_dems

                if is_global:
                    # Force a strict -180 to +180 geographic transform
                    # This bypasses missing transforms or 0-360 Moon datasets
                    active_transform = transform_from_bounds(-180, -90, 180, 90, src.width, src.height)
                else:
                    # Use the dataset's native transform (e.g., for Tahoe)
                    active_transform = src.transform

                # Safely calculate the exact pixel window
                window = rasterio.windows.from_bounds(lon_min, lat_min, lon_max, lat_max, transform=active_transform)

                # Read only that window, filling any out-of-bounds areas with NaN
                dem_data = src.read(1, window=window, boundless=True, fill_value=np.nan).astype(np.float32)

                # Get the exact geographic extent of this specific slice for matplotlib
                win_bounds = rasterio.windows.bounds(window, active_transform)
                extent = [win_bounds[0], win_bounds[2], win_bounds[1], win_bounds[3]]

            # Mask out GDAL NoData values before any math is done
            dem_data[dem_data < -100000] = np.nan

            mult = DATA_FILES[self.planet]['multiplier']
            crop_ft = dem_data * mult

            # Safely calculate max/min relief
            if np.isnan(crop_ft).all():
                self.finished.emit(crop_ft, extent, 0.0, 0.0)
            else:
                self.finished.emit(crop_ft, extent, float(np.nanmin(crop_ft)), float(np.nanmax(crop_ft)))

        except Exception as e:
            self.error.emit(str(e))

# ==========================================
# 3. MAIN PYQT6 GUI
# ==========================================
class PlotterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Topo Plotter Studio - Cartographer Edition")
        self.resize(1650, 900)

        self.active_planet = 'Earth'
        self.paper_name = '100x100mm (Default)'
        self.is_landscape = True
        self.show_ocean = True
        self.show_borders = True
        self.interval_ft = 200

        self.current_dem_src = None
        self.current_dem_crop = None
        self.current_data_extent = None
        self.current_view_extent = None
        self.last_drag_coords = None
        self.has_drawn = False

        self.pan_active = False
        self.prev_pan_active = False

        self.undo_stack = []
        self.redo_stack = []

        self.pens = load_pens()
        self.active_pen_id = 'base'
        self.canvas_mode = 'paint'

        self.dem_segments = []
        self.custom_items = []
        self.dem_collection = None

        self.drawing_active = False
        self.current_freehand = []
        self.temp_draw_line = None

        self.transform_target_idx = None
        self.transform_start_state = None

        # Debounce timer for sliders
        self.recalc_timer = QTimer()
        self.recalc_timer.setSingleShot(True)
        self.recalc_timer.timeout.connect(self.trigger_recalc)

        self.init_ui()
        self.setup_hotkeys()
        self.switch_planet()

    def setup_hotkeys(self):
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self.undo_action)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self.redo_action)
        QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self.redo_action)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # --- LEFT PANEL: CONTROLS ---
        left_panel = QWidget()
        left_panel.setFixedWidth(340)
        vbox = QVBoxLayout(left_panel)
        vbox.setAlignment(Qt.AlignmentFlag.AlignTop)

        vbox.addWidget(QLabel("<b>Planet Source</b>"))
        self.radio_earth = QRadioButton("Earth (Local)")
        self.radio_moon = QRadioButton("Moon (Local)")
        self.radio_earth.setChecked(True)
        self.radio_earth.toggled.connect(self.switch_planet)
        h_planet = QHBoxLayout()
        h_planet.addWidget(self.radio_earth)
        h_planet.addWidget(self.radio_moon)
        vbox.addLayout(h_planet)

        vbox.addWidget(QLabel("<b>Map Options</b>"))
        self.chk_ocean = QCheckBox("Plot Ocean/Crater Depths")
        self.chk_ocean.setChecked(True)
        self.chk_ocean.toggled.connect(self.toggle_ocean)
        self.chk_borders = QCheckBox("Show Reference Borders")
        self.chk_borders.setChecked(True)
        self.chk_borders.toggled.connect(self.toggle_borders)

        # New Mask/Border features
        self.chk_plot_borders = QCheckBox("Plot Borders (Index Pen)")
        self.chk_plot_borders.setChecked(True)
        self.chk_plot_borders.setStyleSheet("color: #2E86C1; font-weight: bold;")
        self.chk_plot_borders.toggled.connect(self.trigger_recalc)

        self.chk_mask_text = QCheckBox("Clear background behind Text")
        self.chk_mask_text.setChecked(True)
        self.chk_mask_text.setStyleSheet("color: #2E86C1; font-weight: bold;")
        self.chk_mask_text.toggled.connect(self.render_canvas)

        vbox.addWidget(self.chk_ocean)
        vbox.addWidget(self.chk_borders)
        vbox.addWidget(self.chk_plot_borders)
        vbox.addWidget(self.chk_mask_text)

        # --- REGION MASKING UI ---
        vbox.addSpacing(10)
        vbox.addWidget(QLabel("<b>Region Masking (Clip to border)</b>"))
        self.combo_region = QComboBox()
        self.combo_region.setEditable(True)
        self.combo_region.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo_region.addItem("None (Plot Full Selection)")
        self.combo_region.addItem("Lake Tahoe (High-Res Bathymetry)")

        if HAS_GPD:
            regions = []
            w_col = 'NAME' if 'NAME' in world_borders.columns else 'name' if 'name' in world_borders.columns else None
            s_col = 'name' if 'name' in state_borders.columns else None

            if w_col: regions.extend(world_borders[w_col].dropna().tolist())
            if s_col: regions.extend(state_borders[s_col].dropna().tolist())

            regions = sorted(list(set(regions)))
            self.combo_region.addItems(regions)

            # Setup Case-Insensitive "Contains" Search
            completer = QCompleter(regions)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self.combo_region.setCompleter(completer)

        # Trigger a delayed recalculation when a valid region is typed/selected
        self.combo_region.currentTextChanged.connect(lambda t: self.recalc_timer.start(500))
        vbox.addWidget(self.combo_region)
        # -------------------------

        vbox.addSpacing(10)
        vbox.addWidget(QLabel("<b>Paper / Bed Size</b>"))
        self.combo_paper = QComboBox()
        self.combo_paper.addItems(list(PAPER_SIZES.keys()))
        self.combo_paper.currentTextChanged.connect(self.change_paper)
        vbox.addWidget(self.combo_paper)

        self.widget_custom = QWidget()
        h_custom = QHBoxLayout(self.widget_custom)
        h_custom.setContentsMargins(0, 0, 0, 0)
        self.txt_cw = QLineEdit("150");
        self.txt_ch = QLineEdit("150")
        self.txt_cw.textChanged.connect(self.update_paper_dims)
        self.txt_ch.textChanged.connect(self.update_paper_dims)
        h_custom.addWidget(QLabel("W:"));
        h_custom.addWidget(self.txt_cw)
        h_custom.addWidget(QLabel("H:"));
        h_custom.addWidget(self.txt_ch)
        self.widget_custom.setVisible(False)
        vbox.addWidget(self.widget_custom)

        self.btn_orientation = QPushButton("Toggle Portrait/Landscape")
        self.btn_orientation.clicked.connect(self.toggle_orientation)
        vbox.addWidget(self.btn_orientation)

        line2 = QFrame();
        line2.setFrameShape(QFrame.Shape.HLine);
        vbox.addWidget(line2)

        vbox.addWidget(QLabel("<b>Contour Generation</b>"))

        # USGS Interval logic display
        self.lbl_usgs_interval = QLabel("Interval: Auto (USGS Scale)")
        self.lbl_usgs_interval.setStyleSheet("color: #8E44AD; font-weight: bold;")
        vbox.addWidget(self.lbl_usgs_interval)

        h_int = QHBoxLayout()
        h_int.addWidget(QLabel("Override (ft):"))
        self.slider_int = QSlider(Qt.Orientation.Horizontal)
        self.slider_int.setRange(50, 10000)
        self.slider_int.setSingleStep(50)
        self.slider_int.setValue(self.interval_ft)
        self.txt_int = QLineEdit(str(self.interval_ft))
        self.txt_int.setValidator(QIntValidator(1, 20000))
        self.txt_int.setFixedWidth(55)
        self.slider_int.valueChanged.connect(lambda v: self.txt_int.setText(str(v)))
        self.txt_int.textChanged.connect(self.update_interval)
        h_int.addWidget(self.slider_int);
        h_int.addWidget(self.txt_int)
        vbox.addLayout(h_int)

        h_idx = QHBoxLayout()
        self.chk_auto_index = QCheckBox("Auto-Index lines every")
        self.chk_auto_index.setChecked(True)
        self.spin_idx = QSpinBox()
        self.spin_idx.setRange(2, 20)
        self.spin_idx.setValue(5)
        self.spin_idx.setFixedWidth(40)
        h_idx.addWidget(self.chk_auto_index);
        h_idx.addWidget(self.spin_idx);
        h_idx.addWidget(QLabel("intervals"))
        h_idx.addStretch()
        self.chk_auto_index.toggled.connect(self.trigger_recalc)
        self.spin_idx.valueChanged.connect(self.trigger_recalc)
        vbox.addLayout(h_idx)

        line3 = QFrame();
        line3.setFrameShape(QFrame.Shape.HLine);
        vbox.addWidget(line3)

        # --- GENERALIZATION & SMOOTHING UI ---
        vbox.addWidget(QLabel("<b>Vector Smoothing (USGS Parameters)</b>"))

        h_presets = QHBoxLayout()
        btn_low = QPushButton("Low");
        btn_low.clicked.connect(lambda: self.apply_usgs_preset(8, 128))
        btn_med = QPushButton("Medium");
        btn_med.clicked.connect(lambda: self.apply_usgs_preset(40, 640))
        btn_high = QPushButton("High");
        btn_high.clicked.connect(lambda: self.apply_usgs_preset(160, 2560))
        h_presets.addWidget(QLabel("Presets:"));
        h_presets.addWidget(btn_low);
        h_presets.addWidget(btn_med);
        h_presets.addWidget(btn_high)
        vbox.addLayout(h_presets)

        # Raster Blur Slider
        h_blur = QHBoxLayout()
        h_blur.addWidget(QLabel("Raster Blur \u03c3:"))
        self.slider_blur = QSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(0, 50)
        self.slider_blur.setValue(0)
        self.lbl_blur_val = QLabel("0.0")
        self.slider_blur.valueChanged.connect(
            lambda v: (self.lbl_blur_val.setText(f"{v / 10.0:.1f}"), self.recalc_timer.start(150)))
        h_blur.addWidget(self.slider_blur);
        h_blur.addWidget(self.lbl_blur_val)
        vbox.addLayout(h_blur)

        # Vector Simplify Slider
        h_simp = QHBoxLayout()
        h_simp.addWidget(QLabel("Simplify Tol:"))
        self.slider_simp = QSlider(Qt.Orientation.Horizontal)
        self.slider_simp.setRange(0, 250)
        self.slider_simp.setValue(40)  # Medium Default (4.0m)
        self.lbl_simp_val = QLabel("4.0m")
        self.slider_simp.valueChanged.connect(
            lambda v: (self.lbl_simp_val.setText(f"{v / 10.0:.1f}m"), self.recalc_timer.start(150)))
        h_simp.addWidget(self.slider_simp);
        h_simp.addWidget(self.lbl_simp_val)
        vbox.addLayout(h_simp)

        # Vector Smoothing Slider
        h_smooth = QHBoxLayout()
        h_smooth.addWidget(QLabel("Smoothing:"))
        self.slider_smooth = QSlider(Qt.Orientation.Horizontal)
        self.slider_smooth.setRange(0, 3000)
        self.slider_smooth.setValue(640)  # Medium Default (64.0m)
        self.lbl_smooth_val = QLabel("64.0m")
        self.slider_smooth.valueChanged.connect(
            lambda v: (self.lbl_smooth_val.setText(f"{v / 10.0:.1f}m"), self.recalc_timer.start(150)))
        h_smooth.addWidget(self.slider_smooth);
        h_smooth.addWidget(self.lbl_smooth_val)
        vbox.addLayout(h_smooth)

        vbox.addStretch()
        self.lbl_status = QLabel("Ready.")
        self.lbl_status.setStyleSheet("color: gray; font-style: italic;")
        vbox.addWidget(self.lbl_status)

        self.btn_draw = QPushButton("1. DRAW MAP")
        self.btn_draw.setStyleSheet("background-color: #2E86C1; color: white; font-weight: bold; padding: 10px;")
        self.btn_draw.clicked.connect(self.draw_action)
        vbox.addWidget(self.btn_draw)

        self.btn_export_svg = QPushButton("2. SAVE SVG (Editable)")
        self.btn_export_svg.setStyleSheet("background-color: #8E44AD; color: white; font-weight: bold; padding: 10px;")
        self.btn_export_svg.clicked.connect(self.export_svg_action)
        vbox.addWidget(self.btn_export_svg)

        self.btn_export = QPushButton("3. GENERATE MULTI-PEN G-CODE")
        self.btn_export.setStyleSheet("background-color: #28B463; color: white; font-weight: bold; padding: 10px;")
        self.btn_export.clicked.connect(self.export_action)
        vbox.addWidget(self.btn_export)

        # --- RIGHT PANELS: CANVASES ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Color Map
        map_widget = QWidget();
        v_map = QVBoxLayout(map_widget);
        v_map.setContentsMargins(0, 0, 0, 0)
        self.fig_map = Figure(figsize=(5, 5), dpi=100);
        self.ax_map = self.fig_map.add_subplot(111)
        self.canvas_map = FigureCanvas(self.fig_map)
        v_map.addWidget(self.canvas_map)

        h_toolbar = QHBoxLayout()
        self.btn_home = QPushButton("Reset View")
        self.btn_home.clicked.connect(self.reset_home_view)
        self.btn_zoom_tool = QPushButton("Zoom Map")
        self.btn_zoom_tool.setCheckable(True)
        self.btn_zoom_tool.toggled.connect(self.toggle_zoom_tool)
        self.btn_clear_sel = QPushButton("Clear Map")
        self.btn_clear_sel.clicked.connect(self.clear_selection)
        h_toolbar.addWidget(self.btn_home);
        h_toolbar.addWidget(self.btn_zoom_tool)
        h_toolbar.addWidget(QLabel("<i>(Mid-click to pan)</i>"));
        h_toolbar.addStretch()
        h_toolbar.addWidget(self.btn_clear_sel)
        v_map.addLayout(h_toolbar)
        splitter.addWidget(map_widget)

        # Preview Container & Paint Palette
        prev_widget = QWidget();
        v_prev = QVBoxLayout(prev_widget);
        v_prev.setContentsMargins(0, 0, 0, 0)
        self.fig_prev = Figure(figsize=(5, 5), dpi=100);
        self.ax_prev = self.fig_prev.add_subplot(111)
        self.canvas_prev = FigureCanvas(self.fig_prev)
        v_prev.addWidget(self.canvas_prev)

        # Preview Zoom Toolbar
        h_prev_toolbar = QHBoxLayout()
        self.btn_prev_home = QPushButton("Reset View")
        self.btn_prev_home.clicked.connect(self.reset_prev_view)
        self.btn_prev_zoom = QPushButton("Zoom Preview")
        self.btn_prev_zoom.setCheckable(True)
        self.btn_prev_zoom.toggled.connect(self.toggle_prev_zoom)
        h_prev_toolbar.addWidget(self.btn_prev_home);
        h_prev_toolbar.addWidget(self.btn_prev_zoom)
        h_prev_toolbar.addWidget(QLabel("<i>(Scroll to zoom, Mid-click to pan)</i>"));
        h_prev_toolbar.addStretch()
        v_prev.addLayout(h_prev_toolbar)

        # Tool Palette
        self.frame_palette = QFrame()
        self.frame_palette.setStyleSheet("background-color: #FDFEFE; border: 1px solid #D5D8DC; border-radius: 5px;")
        v_pal = QVBoxLayout(self.frame_palette)
        v_pal.setContentsMargins(8, 8, 8, 8)

        # Top Tool Modes
        h_modes = QHBoxLayout()
        self.mode_group = QButtonGroup()
        modes = [('paint', 'Paint'), ('draw', 'Draw Line'), ('text', 'Add Text'), ('transform', 'Move/Rotate Text')]
        for mode, title in modes:
            btn = QPushButton(title)
            btn.setCheckable(True)
            btn.setProperty("mode", mode)
            if mode == 'paint': btn.setChecked(True)
            self.mode_group.addButton(btn)
            h_modes.addWidget(btn)
        self.mode_group.buttonClicked.connect(self.change_mode)
        v_pal.addLayout(h_modes)

        # Undo / Redo / Add Pen
        h_tools = QHBoxLayout()
        btn_style = "background-color: #E5E7E9; color: black; font-weight: bold; padding: 4px; border-radius: 3px;"

        self.btn_undo = QPushButton("Undo (Ctrl+Z)")
        self.btn_undo.setStyleSheet(btn_style);
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self.undo_action)

        self.btn_redo = QPushButton("Redo (Ctrl+Y)")
        self.btn_redo.setStyleSheet(btn_style);
        self.btn_redo.setEnabled(False)
        self.btn_redo.clicked.connect(self.redo_action)

        self.btn_add_pen = QPushButton("Add Custom Pen")
        self.btn_add_pen.setStyleSheet(btn_style)
        self.btn_add_pen.clicked.connect(self.add_custom_pen)

        h_tools.addWidget(self.btn_undo);
        h_tools.addWidget(self.btn_redo)
        h_tools.addStretch();
        h_tools.addWidget(self.btn_add_pen)
        v_pal.addLayout(h_tools)

        # Vertical Scroll Area for Pens
        self.scroll_pens = QScrollArea()
        self.scroll_pens.setWidgetResizable(True)
        self.scroll_pens.setFixedHeight(100)
        self.scroll_pens.setStyleSheet("border: None;")
        self.scroll_content = QWidget()
        self.v_palette_btns = QVBoxLayout(self.scroll_content)
        self.v_palette_btns.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.pen_btn_group = QButtonGroup()
        self.pen_btn_group.buttonClicked.connect(self.on_palette_selected)
        self.scroll_pens.setWidget(self.scroll_content)
        v_pal.addWidget(self.scroll_pens)

        self.rebuild_palette_ui()
        self.update_mode_button_styles()
        v_prev.addWidget(self.frame_palette)

        splitter.addWidget(prev_widget)
        main_layout.addWidget(left_panel)
        main_layout.addWidget(splitter)

        # Map Canvas Events
        self.selector = RectangleSelector(self.ax_map, self.on_select, useblit=True, button=[1], interactive=True,
                                          props=dict(facecolor='red', edgecolor='red', alpha=0.3, fill=True))
        self.zoom_selector = RectangleSelector(self.ax_map, self.on_zoom_select, useblit=True, button=[1],
                                               interactive=False,
                                               props=dict(facecolor='blue', edgecolor='blue', alpha=0.2, fill=True))
        self.canvas_map.mpl_connect('scroll_event', self.zoom_map)
        self.canvas_map.mpl_connect('button_press_event', self.on_mouse_press_map)
        self.canvas_map.mpl_connect('motion_notify_event', self.on_mouse_motion_map)
        self.canvas_map.mpl_connect('button_release_event', self.on_mouse_release_map)

        # Preview Canvas Events
        self.prev_zoom_selector = RectangleSelector(self.ax_prev, self.on_prev_zoom_select, useblit=True, button=[1],
                                                    interactive=False,
                                                    props=dict(facecolor='blue', edgecolor='blue', alpha=0.2,
                                                               fill=True))
        self.prev_zoom_selector.set_active(False)
        self.canvas_prev.mpl_connect('button_press_event', self.on_prev_press)
        self.canvas_prev.mpl_connect('motion_notify_event', self.on_prev_motion)
        self.canvas_prev.mpl_connect('button_release_event', self.on_prev_release)
        self.canvas_prev.mpl_connect('scroll_event', self.on_prev_scroll)
        self.canvas_prev.mpl_connect('pick_event', self.on_prev_pick)

        self.update_paper_dims()

    # --- MODE & STATE LOGIC ---
    def apply_usgs_preset(self, simp, smooth):
        self.slider_simp.setValue(simp)
        self.slider_smooth.setValue(smooth)
        self.trigger_recalc()

    def update_mode_button_styles(self):
        for btn in self.mode_group.buttons():
            if btn.isChecked():
                btn.setStyleSheet(
                    "background-color: #2E86C1; color: white; font-weight: bold; border-radius: 4px; padding: 6px;")
            else:
                btn.setStyleSheet("background-color: #E5E7E9; color: black; border-radius: 4px; padding: 6px;")

    def change_mode(self, btn):
        self.canvas_mode = btn.property("mode")
        self.update_mode_button_styles()
        self.btn_prev_zoom.setChecked(False)
        self.prev_zoom_selector.set_active(False)
        if self.canvas_mode == 'text':
            self.lbl_status.setText("Text Mode: Click on the preview map to place text.")
        elif self.canvas_mode == 'draw':
            self.lbl_status.setText("Draw Mode: Click and drag on the preview map to draw contours.")
        elif self.canvas_mode == 'transform':
            self.lbl_status.setText("Transform Mode: Click & Drag to move. Scroll to Rotate. Shift+Scroll to Scale.")
        else:
            self.lbl_status.setText(
                "Paint Mode: Click a contour to change its pen (Shift+Click to change all at elevation).")

    def push_undo_state(self):
        state = {'pens': [s['pen_id'] for s in self.dem_segments], 'custom': copy.deepcopy(self.custom_items)}
        self.undo_stack.append(state);
        self.redo_stack.clear();
        self.update_undo_redo_btns()

    def update_undo_redo_btns(self):
        self.btn_undo.setEnabled(len(self.undo_stack) > 0)
        self.btn_redo.setEnabled(len(self.redo_stack) > 0)

    def undo_action(self):
        if not self.undo_stack: return
        self.redo_stack.append(
            {'pens': [s['pen_id'] for s in self.dem_segments], 'custom': copy.deepcopy(self.custom_items)})
        state = self.undo_stack.pop()
        self._apply_state(state);
        self.update_undo_redo_btns()

    def redo_action(self):
        if not self.redo_stack: return
        self.undo_stack.append(
            {'pens': [s['pen_id'] for s in self.dem_segments], 'custom': copy.deepcopy(self.custom_items)})
        state = self.redo_stack.pop()
        self._apply_state(state);
        self.update_undo_redo_btns()

    def _apply_state(self, state):
        for i, p_id in enumerate(state['pens']):
            if i < len(self.dem_segments): self.dem_segments[i]['pen_id'] = p_id
        self.custom_items = copy.deepcopy(state['custom'])
        self.render_canvas()

    # --- PEN PALETTE ---
    def rebuild_palette_ui(self):
        while self.v_palette_btns.count():
            item = self.v_palette_btns.takeAt(0)
            if item.widget():
                self.pen_btn_group.removeButton(item.widget())
                item.widget().deleteLater()
        for p_id, pen in self.pens.items():
            btn = QRadioButton(f"{pen['name']} ({pen['width']}mm)")
            btn.setStyleSheet(f"QRadioButton {{ color: {pen['color']}; font-weight: bold; padding: 4px; }}")
            btn.setProperty("pen_id", p_id)
            if p_id == self.active_pen_id: btn.setChecked(True)
            self.pen_btn_group.addButton(btn)
            self.v_palette_btns.addWidget(btn)

    def on_palette_selected(self, btn):
        self.active_pen_id = btn.property("pen_id")

    def add_custom_pen(self):
        name, ok1 = QInputDialog.getText(self, "New Pen", "Enter tool name:")
        if not ok1 or not name: return
        width, ok2 = QInputDialog.getDouble(self, "New Pen", "Enter line width (mm):", 0.5, 0.01, 5.0, 2)
        if not ok2: return
        color_dlg = QColorDialog.getColor(Qt.GlobalColor.black, self, "Select Preview Color")
        if not color_dlg.isValid(): return
        p_id = f"custom_{len(self.pens)}"
        self.pens[p_id] = {'id': p_id, 'name': name, 'width': width, 'color': color_dlg.name()}
        save_pens(self.pens)
        self.rebuild_palette_ui()

    # --- MAP & DATA CALCULATION ---
    def trigger_recalc(self):
        if self.has_drawn:
            self.push_undo_state()
            self.calculate_dem_contours()
            self.render_canvas()

    def update_interval(self, text):
        try:
            val = int(text)
            if val > 0:
                self.interval_ft = val
                self.slider_int.blockSignals(True)
                self.slider_int.setValue(val)
                self.slider_int.blockSignals(False)
                self.recalc_timer.start(250)
        except ValueError:
            pass

    def draw_action(self):
        if not self.current_view_extent: return
        self.btn_draw.setEnabled(False)
        self.lbl_status.setText("Extracting DEM data... Please wait.")

        # Default to the global DEM
        dem_path = DATA_FILES[self.active_planet]['dem']
        selected_region = self.combo_region.currentText()

        # Swap to local high-res dataset if selected
        if self.active_planet == 'Earth' and selected_region == "Lake Tahoe (High-Res Bathymetry)":
            dem_path = "tahoe2.tif"

        if not os.path.exists(dem_path):
            QMessageBox.critical(self, "Missing File", f"Cannot find {dem_path}")
            self.btn_draw.setEnabled(True)
            return

        # Pass the path and the geographic bounds directly to the worker
        self.worker = DataFetchWorker(self.active_planet, dem_path, self.current_view_extent)
        self.worker.finished.connect(self.on_fetch_success)
        self.worker.error.connect(self.on_fetch_error)
        self.worker.start()

    def on_fetch_success(self, dem_data, extent, min_e, max_e):
        self.current_dem_crop = dem_data
        self.current_data_extent = extent
        relief = max_e - min_e

        # USGS Dynamic Interval Selection
        if relief > 0:
            if relief < 200:
                best_step = 5
            elif relief <= 725:
                best_step = 10
            elif relief <= 2075:
                best_step = 20
            elif relief <= 3500:
                best_step = 40
            elif relief <= 5500:
                best_step = 80
            elif relief <= 7100:
                best_step = 100
            else:
                # best_step = 120
                best_step = 1000
            best_step *= 1000
            self.interval_ft = best_step
            self.lbl_usgs_interval.setText(f"Interval: Auto USGS ({best_step} ft)")

            self.slider_int.blockSignals(True)
            self.txt_int.blockSignals(True)
            self.slider_int.setValue(best_step)
            self.txt_int.setText(str(best_step))
            self.slider_int.blockSignals(False)
            self.txt_int.blockSignals(False)

        self.has_drawn = True
        self.custom_items = []
        self.undo_stack.clear();
        self.redo_stack.clear();
        self.update_undo_redo_btns()
        self.calculate_dem_contours()

        self.ax_prev.set_xlim(0.0, 1.0);
        self.ax_prev.set_ylim(0.0, 1.0)
        self.render_canvas()
        self.btn_draw.setEnabled(True)
        self.lbl_status.setText("Render complete!")

    def on_fetch_error(self, err_msg):
        self.btn_draw.setEnabled(True);
        QMessageBox.critical(self, "Read Error", err_msg)

    def calculate_dem_contours(self):
        self.dem_segments = []
        if not self.has_drawn or self.current_dem_crop is None: return

        selected_region = self.combo_region.currentText()
        mult = DATA_FILES[self.active_planet]['multiplier']

        # Use .copy() so we don't permanently alter the original array in memory during slider drags
        crop_ft = self.current_dem_crop.copy() * mult

        # --- TAHOE SHORELINE ISOLATION ---
        if self.active_planet == 'Earth' and selected_region == "Lake Tahoe (High-Res Bathymetry)":
            # Tahoe's surface is ~6225 ft. This turns any surrounding land mountains into NaN.
            crop_ft[crop_ft > 6226.0] = np.nan

        if np.isnan(crop_ft).all(): return

        # Capture the footprint of the valid data (the lake surface) before blurring
        valid_mask = ~np.isnan(crop_ft)

        # 1. Raster-Level Gaussian Blur (with NaN protection)
        sigma = self.slider_blur.value() / 10.0
        if sigma > 0:
            # Replace NaNs with the surface elevation so the edges don't artificially deepen during the blur
            safe_crop = np.where(valid_mask, crop_ft, np.nanmax(crop_ft))
            blurred = scipy.ndimage.gaussian_filter(safe_crop, sigma=sigma, mode='nearest')
            crop_ft = np.where(valid_mask, blurred, np.nan)

        min_e, max_e = np.nanmin(crop_ft), np.nanmax(crop_ft)
        s_lvl = np.floor(min_e / self.interval_ft) * self.interval_ft
        e_lvl = np.ceil(max_e / self.interval_ft) * self.interval_ft
        levels = np.arange(s_lvl, e_lvl + self.interval_ft, self.interval_ft)

        if self.active_planet == 'Earth' and not self.show_ocean:
            levels = [l for l in levels if l >= 0]
        if len(levels) == 0: return

        # --- SETUP POLYGON REGION MASK ---
        mask_polygon = None
        if HAS_GPD and self.active_planet == 'Earth' and selected_region not in ["None (Plot Full Selection)",
                                                                                 "Lake Tahoe (High-Res Bathymetry)"]:
            w_col = 'NAME' if 'NAME' in world_borders.columns else 'name' if 'name' in world_borders.columns else None
            s_col = 'name' if 'name' in state_borders.columns else None

            geom = None
            if s_col and selected_region in state_borders[s_col].values:
                geom = state_borders[state_borders[s_col] == selected_region].geometry.unary_union
            elif w_col and selected_region in world_borders[w_col].values:
                geom = world_borders[world_borders[w_col] == selected_region].geometry.unary_union

            if geom:
                view_box = shp_box(self.current_data_extent[0], self.current_data_extent[2],
                                   self.current_data_extent[1], self.current_data_extent[3])
                mask_polygon = geom.intersection(view_box)

        # 2. Extract Base Vector Polylines
        fig_temp = Figure();
        ax_temp = fig_temp.add_subplot(111)
        cs = ax_temp.contour(crop_ft, levels=levels, extent=self.current_data_extent, origin='upper')

        # 3. Preparation for Metric Projection
        # R_planet = 6371000.0 if self.active_planet == 'Earth' else 1737400.0
        if self.active_planet == 'Earth':
            R_planet = 6371000.0
        elif self.active_planet == 'Moon':
            R_planet = 1737400.0
        elif self.active_planet == 'Mars':
            R_planet = 3389500.0
        mid_lat = np.radians((self.current_data_extent[2] + self.current_data_extent[3]) / 2)
        mid_lon = (self.current_data_extent[0] + self.current_data_extent[1]) / 2
        cos_lat = np.cos(mid_lat)
        deg2rad = np.pi / 180.0

        simp_tol = self.slider_simp.value() / 10.0
        smooth_val = self.slider_smooth.value() / 10.0

        chaikin_iters = 0
        if smooth_val > 10.0: chaikin_iters = 1
        if smooth_val > 40.0: chaikin_iters = 2
        if smooth_val > 150.0: chaikin_iters = 3

        # HELPER: Apply USGS generalization parameters to any line segment
        def generalize_line(line_seg):
            xs = (line_seg[:, 0] - mid_lon) * deg2rad * R_planet * cos_lat
            ys = (line_seg[:, 1] - mid_lat) * deg2rad * R_planet
            m_seg = np.column_stack((xs, ys))

            if simp_tol > 0:
                ls = LineString(m_seg)
                m_seg = np.array(ls.simplify(simp_tol, preserve_topology=True).coords)

            if chaikin_iters > 0 and len(m_seg) >= 3:
                m_seg = chaikin_smooth(m_seg, chaikin_iters)

            out_seg = np.empty_like(m_seg)
            out_seg[:, 0] = mid_lon + m_seg[:, 0] / (deg2rad * R_planet * cos_lat)
            out_seg[:, 1] = mid_lat + m_seg[:, 1] / (deg2rad * R_planet)
            return out_seg

        for lvl_idx, level in enumerate(cs.levels):
            segs = cs.allsegs[lvl_idx]
            is_index = False
            if self.chk_auto_index.isChecked() and round(level / self.interval_ft) % self.spin_idx.value() == 0:
                is_index = True

            pen_id = 'pen_1' if is_index else 'base'
            for seg in segs:
                if len(seg) < 2: continue

                smoothed_seg = generalize_line(seg)

                # --- REGION CLIPPING ---
                if mask_polygon and not mask_polygon.is_empty:
                    line_geom = LineString(smoothed_seg)
                    clipped = line_geom.intersection(mask_polygon)
                    valid_lines = []

                    if clipped.is_empty:
                        continue
                    elif clipped.geom_type == 'LineString':
                        valid_lines.append(clipped)
                    elif clipped.geom_type == 'MultiLineString':
                        valid_lines.extend(list(clipped.geoms))
                    elif clipped.geom_type == 'GeometryCollection':
                        for g in clipped.geoms:
                            if g.geom_type == 'LineString':
                                valid_lines.append(g)
                            elif g.geom_type == 'MultiLineString':
                                valid_lines.extend(list(g.geoms))

                    for v_line in valid_lines:
                        if len(v_line.coords) >= 2:
                            self.dem_segments.append(
                                {'type': 'dem', 'seg': np.array(v_line.coords), 'level': level, 'pen_id': pen_id})
                else:
                    self.dem_segments.append({'type': 'dem', 'seg': smoothed_seg, 'level': level, 'pen_id': pen_id})

        # 4. Extract Reference Borders (Index Pen)
        if self.chk_plot_borders.isChecked() and self.active_planet == 'Earth':

            # 4A. Trace the Shoreline for Lake Tahoe
            if selected_region == "Lake Tahoe (High-Res Bathymetry)":
                fig_mask = Figure();
                ax_mask = fig_mask.add_subplot(111)
                # Find the boundary of the valid data mask
                cs_mask = ax_mask.contour(valid_mask.astype(float), levels=[0.5], extent=self.current_data_extent,
                                          origin='upper')
                for segs in cs_mask.allsegs:
                    for seg in segs:
                        if len(seg) < 2: continue
                        smoothed_seg = generalize_line(seg)
                        self.dem_segments.append({'type': 'border', 'seg': smoothed_seg, 'level': 0, 'pen_id': 'pen_1'})

            # 4B. Trace State / Country Borders
            elif HAS_GPD:
                if mask_polygon and not mask_polygon.is_empty:
                    bound_geom = mask_polygon.boundary
                    if bound_geom.geom_type == 'LineString':
                        self.dem_segments.append(
                            {'type': 'border', 'seg': np.array(bound_geom.coords), 'level': 0, 'pen_id': 'pen_1'})
                    elif bound_geom.geom_type == 'MultiLineString':
                        for line in bound_geom.geoms:
                            self.dem_segments.append(
                                {'type': 'border', 'seg': np.array(line.coords), 'level': 0, 'pen_id': 'pen_1'})
                else:
                    view_box = shp_box(self.current_view_extent[0], self.current_view_extent[2],
                                       self.current_view_extent[1], self.current_view_extent[3])
                    for gdf in [world_borders, state_borders]:
                        try:
                            clipped = gpd.clip(gdf, view_box)
                            for geom in clipped.geometry:
                                if geom.geom_type == 'Polygon':
                                    self.dem_segments.append(
                                        {'type': 'border', 'seg': np.array(geom.exterior.coords), 'level': 0,
                                         'pen_id': 'pen_1'})
                                    for ring in geom.interiors:
                                        self.dem_segments.append(
                                            {'type': 'border', 'seg': np.array(ring.coords), 'level': 0,
                                             'pen_id': 'pen_1'})
                                elif geom.geom_type == 'MultiPolygon':
                                    for poly in geom.geoms:
                                        self.dem_segments.append(
                                            {'type': 'border', 'seg': np.array(poly.exterior.coords), 'level': 0,
                                             'pen_id': 'pen_1'})
                                        for ring in poly.interiors:
                                            self.dem_segments.append(
                                                {'type': 'border', 'seg': np.array(ring.coords), 'level': 0,
                                                 'pen_id': 'pen_1'})
                                elif geom.geom_type == 'LineString':
                                    self.dem_segments.append(
                                        {'type': 'border', 'seg': np.array(geom.coords), 'level': 0, 'pen_id': 'pen_1'})
                                elif geom.geom_type == 'MultiLineString':
                                    for line in geom.geoms:
                                        self.dem_segments.append(
                                            {'type': 'border', 'seg': np.array(line.coords), 'level': 0,
                                             'pen_id': 'pen_1'})
                        except Exception as e:
                            print(f"Border clip skipped: {e}")
    # --- HIGH PERFORMANCE RENDERER ---
    def render_canvas(self):
        xlim, ylim = self.ax_prev.get_xlim(), self.ax_prev.get_ylim()
        is_fresh = (xlim == (0.0, 1.0) and ylim == (0.0, 1.0))

        self.ax_prev.clear();
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])
        if not getattr(self, 'has_drawn', False): return

        mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
        self.ax_prev.set_aspect(1 / np.cos(mid_lat))

        if getattr(self, 'dem_segments', []):
            segs = [item['seg'] for item in self.dem_segments]
            colors = [self.pens[item['pen_id']]['color'] for item in self.dem_segments]
            lws = [self.pens[item['pen_id']]['width'] * PT_PER_MM for item in self.dem_segments]

            # zorder=10 keeps DEM lines behind Text Masks (zorder=14)
            self.dem_collection = LineCollection(segs, colors=colors, linewidths=lws, picker=True, pickradius=5,
                                                 zorder=10)
            self.ax_prev.add_collection(self.dem_collection)

        for idx, item in enumerate(getattr(self, 'custom_items', [])):
            pen = self.pens[item['pen_id']]
            if item['type'] == 'line':
                seg = np.array(item['points'])
                line, = self.ax_prev.plot(seg[:, 0], seg[:, 1], color=pen['color'],
                                          linewidth=pen['width'] * PT_PER_MM, picker=True, pickradius=5, zorder=11)
                line.item_ref = ('custom', idx)

            elif item['type'] == 'text':
                tp = TextPath((0, 0), item['text'], size=1)
                trans = Affine2D().scale(item['scale']).rotate_deg(item['rot']).translate(item['x'], item['y'])

                # Draw Visual Blanking Mask Behind Text
                if hasattr(self, 'chk_mask_text') and self.chk_mask_text.isChecked():
                    bbox = tp.transformed(trans).get_extents()
                    pad = item['scale'] * 0.2
                    rect = patches.Rectangle((bbox.x0 - pad, bbox.y0 - pad),
                                             bbox.width + 2 * pad, bbox.height + 2 * pad,
                                             facecolor='white', edgecolor='none', zorder=14)
                    self.ax_prev.add_patch(rect)

                polys = tp.transformed(trans).to_polygons()
                for poly in polys:
                    line, = self.ax_prev.plot(poly[:, 0], poly[:, 1], color=pen['color'],
                                              linewidth=pen['width'] * PT_PER_MM, picker=True, pickradius=5, zorder=15)
                    line.item_ref = ('custom', idx)

        if is_fresh and self.current_view_extent:
            self.ax_prev.set_xlim(self.current_view_extent[0], self.current_view_extent[1])
            self.ax_prev.set_ylim(self.current_view_extent[2], self.current_view_extent[3])
        else:
            self.ax_prev.set_xlim(xlim);
            self.ax_prev.set_ylim(ylim)
        self.canvas_prev.draw_idle()

    # --- PREVIEW ZOOM & PAN ---
    def reset_prev_view(self):
        if not self.has_drawn: return
        self.ax_prev.set_xlim(self.current_view_extent[0], self.current_view_extent[1])
        self.ax_prev.set_ylim(self.current_view_extent[2], self.current_view_extent[3])
        self.canvas_prev.draw_idle()

    def toggle_prev_zoom(self, checked):
        if checked:
            self.mode_group.setExclusive(False)
            for btn in self.mode_group.buttons(): btn.setChecked(False)
            self.mode_group.setExclusive(True)
            self.update_mode_button_styles()
            self.btn_prev_zoom.setStyleSheet(
                "background-color: lightblue; font-weight: bold; color: black; border-radius: 4px; padding: 6px;")
        else:
            self.btn_prev_zoom.setStyleSheet("")
        self.prev_zoom_selector.set_active(checked)

    def on_prev_zoom_select(self, eclick, erelease):
        x1, x2 = sorted([eclick.xdata, erelease.xdata]);
        y1, y2 = sorted([eclick.ydata, erelease.ydata])
        self.ax_prev.set_xlim(x1, x2);
        self.ax_prev.set_ylim(y1, y2)
        self.canvas_prev.draw_idle();
        self.btn_prev_zoom.setChecked(False)

    # --- INTERACTIVE MOUSE CONTROLS (Preview Canvas) ---
    def on_prev_press(self, event):
        if event.inaxes != self.ax_prev: return
        if event.button == 2:
            self.prev_pan_active = True
            self.prev_pan_start_x, self.prev_pan_start_y = event.x, event.y
            self.prev_pan_xlim, self.prev_pan_ylim = self.ax_prev.get_xlim(), self.ax_prev.get_ylim()
            return
        if event.button != 1 or self.btn_prev_zoom.isChecked(): return

        if self.canvas_mode == 'draw':
            self.drawing_active = True
            self.current_freehand = [(event.xdata, event.ydata)]
            pen = self.pens[self.active_pen_id]
            self.temp_draw_line, = self.ax_prev.plot([event.xdata], [event.ydata], color=pen['color'],
                                                     lw=pen['width'] * PT_PER_MM, zorder=20)
            self.canvas_prev.draw_idle()

        elif self.canvas_mode == 'text':
            text, ok = QInputDialog.getText(self, "Add Text", "Enter text to plot:")
            if ok and text:
                self.push_undo_state()
                vp_width = self.current_view_extent[1] - self.current_view_extent[0]
                init_scale = vp_width * 0.05
                self.custom_items.append({'type': 'text', 'text': text, 'x': event.xdata, 'y': event.ydata,
                                          'rot': 0.0, 'scale': init_scale, 'pen_id': self.active_pen_id})
                self.render_canvas()

        elif self.canvas_mode == 'transform':
            min_dist = float('inf');
            target = None
            for idx, item in enumerate(self.custom_items):
                if item['type'] == 'text':
                    dx = event.xdata - item['x'];
                    dy = event.ydata - item['y']
                    dist = dx * dx + dy * dy
                    if dist < min_dist: min_dist = dist; target = idx
            if target is not None:
                self.transform_target_idx = target
                self.transform_start_state = copy.deepcopy(self.custom_items[target])

    def on_prev_motion(self, event):
        if event.inaxes != self.ax_prev: return
        if self.prev_pan_active:
            inv = self.ax_prev.transData.inverted()
            sx, sy = inv.transform((self.prev_pan_start_x, self.prev_pan_start_y))
            ex, ey = inv.transform((event.x, event.y))
            self.ax_prev.set_xlim(self.prev_pan_xlim[0] - (ex - sx), self.prev_pan_xlim[1] - (ex - sx))
            self.ax_prev.set_ylim(self.prev_pan_ylim[0] - (ey - sy), self.prev_pan_ylim[1] - (ey - sy))
            self.canvas_prev.draw_idle()
            return

        if self.canvas_mode == 'draw' and self.drawing_active:
            self.current_freehand.append((event.xdata, event.ydata))
            xs, ys = zip(*self.current_freehand)
            self.temp_draw_line.set_data(xs, ys)
            self.canvas_prev.draw_idle()

        elif self.canvas_mode == 'transform' and self.transform_target_idx is not None:
            item = self.custom_items[self.transform_target_idx]
            item['x'] = event.xdata;
            item['y'] = event.ydata
            self.render_canvas()

    def on_prev_release(self, event):
        if event.button == 2: self.prev_pan_active = False; return
        if event.button != 1: return

        if self.canvas_mode == 'draw' and self.drawing_active:
            self.drawing_active = False
            if len(self.current_freehand) > 1:
                self.push_undo_state()
                self.custom_items.append(
                    {'type': 'line', 'points': self.current_freehand, 'pen_id': self.active_pen_id})
            if self.temp_draw_line:
                self.temp_draw_line.remove();
                self.temp_draw_line = None
            self.render_canvas()

        elif self.canvas_mode == 'transform' and self.transform_target_idx is not None:
            start = self.transform_start_state;
            cur = self.custom_items[self.transform_target_idx]
            if start['x'] != cur['x'] or start['y'] != cur['y']:
                self.custom_items[self.transform_target_idx] = start
                self.push_undo_state()
                self.custom_items[self.transform_target_idx] = cur
            self.transform_target_idx = None;
            self.transform_start_state = None

    def on_prev_scroll(self, event):
        if event.inaxes != self.ax_prev: return
        if self.canvas_mode == 'transform':
            min_dist = float('inf');
            target = None
            for idx, item in enumerate(self.custom_items):
                if item['type'] == 'text':
                    dx = event.xdata - item['x'];
                    dy = event.ydata - item['y']
                    if (dx * dx + dy * dy) < min_dist: min_dist = (dx * dx + dy * dy); target = idx
            if target is not None:
                self.push_undo_state()
                item = self.custom_items[target]
                if event.key == 'shift':
                    item['scale'] *= (1.1 if event.step > 0 else 0.9)
                else:
                    item['rot'] += event.step * 5.0
                self.render_canvas()
            return

        scale = 1 / 1.2 if event.step > 0 else 1.2
        cx, cy = self.ax_prev.get_xlim(), self.ax_prev.get_ylim()
        x, y = event.xdata, event.ydata
        if x is None or y is None: return
        nw, nh = (cx[1] - cx[0]) * scale, (cy[1] - cy[0]) * scale
        rx, ry = (cx[1] - x) / (cx[1] - cx[0]), (cy[1] - y) / (cy[1] - cy[0])
        self.ax_prev.set_xlim([x - nw * (1 - rx), x + nw * rx])
        self.ax_prev.set_ylim([y - nh * (1 - ry), y + nh * ry])
        self.canvas_prev.draw_idle()

    def on_prev_pick(self, event):
        if event.mouseevent.button != 1 or self.canvas_mode != 'paint' or self.btn_prev_zoom.isChecked(): return
        artist = event.artist
        if hasattr(self, 'dem_collection') and artist == self.dem_collection:
            idx = event.ind[0]
            target_level = self.dem_segments[idx]['level']
            shift_held = event.mouseevent.key == 'shift'
            self.push_undo_state()
            for i, item in enumerate(self.dem_segments):
                if (shift_held and item['level'] == target_level) or (not shift_held and i == idx):
                    item['pen_id'] = self.active_pen_id
            self.render_canvas()
            return
        if hasattr(artist, 'item_ref'):
            group, idx = artist.item_ref
            if group == 'custom':
                self.push_undo_state()
                self.custom_items[idx]['pen_id'] = self.active_pen_id
                self.render_canvas()

    # --- MAP LOGIC (Panning & Zooming) ---
    def reset_home_view(self):
        self.ax_map.set_xlim(-180, 180);
        self.ax_map.set_ylim(-90, 90)
        self.canvas_map.draw_idle()

    def clear_selection(self):
        self.last_drag_coords = None;
        self.has_drawn = False;
        self.selector.extents = (0, 0, 0, 0)
        self.ax_prev.clear();
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])
        self.canvas_prev.draw_idle();
        self.canvas_map.draw_idle()
        self.custom_items = [];
        self.dem_segments = []
        self.undo_stack.clear();
        self.redo_stack.clear();
        self.update_undo_redo_btns()

    def toggle_zoom_tool(self, checked):
        self.selector.set_active(not checked)
        self.zoom_selector.set_active(checked)
        self.btn_zoom_tool.setStyleSheet("background-color: lightblue;" if checked else "")

    def on_zoom_select(self, eclick, erelease):
        x1, x2 = sorted([eclick.xdata, erelease.xdata]);
        y1, y2 = sorted([eclick.ydata, erelease.ydata])
        self.ax_map.set_xlim(x1, x2);
        self.ax_map.set_ylim(y1, y2)
        self.canvas_map.draw_idle();
        self.btn_zoom_tool.setChecked(False)

    def on_mouse_press_map(self, event):
        if event.button == 2:
            self.pan_active = True
            self.pan_start_x, self.pan_start_y = event.x, event.y
            self.pan_xlim, self.pan_ylim = self.ax_map.get_xlim(), self.ax_map.get_ylim()

    def on_mouse_motion_map(self, event):
        if self.pan_active and event.inaxes == self.ax_map:
            inv = self.ax_map.transData.inverted()
            sx, sy = inv.transform((self.pan_start_x, self.pan_start_y))
            ex, ey = inv.transform((event.x, event.y))
            self.ax_map.set_xlim(self.pan_xlim[0] - (ex - sx), self.pan_xlim[1] - (ex - sx))
            self.ax_map.set_ylim(self.pan_ylim[0] - (ey - sy), self.pan_ylim[1] - (ey - sy))
            self.canvas_map.draw_idle()

    def on_mouse_release_map(self, event):
        if event.button == 2: self.pan_active = False

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
        self.pw, self.ph = (w, h) if self.is_landscape else (h, w)
        self.paper_aspect = self.pw / self.ph
        self.ax_prev.set_box_aspect(self.ph / self.pw)
        self.canvas_prev.draw_idle()
        if self.last_drag_coords: self.process_selection(*self.last_drag_coords)

    def change_paper(self, name):
        self.paper_name = name;
        self.update_paper_dims()

    def toggle_orientation(self):
        self.is_landscape = not self.is_landscape;
        self.update_paper_dims()

    def switch_planet(self):
        self.active_planet = 'Earth' if self.radio_earth.isChecked() else 'Moon'
        self.current_dem_src, base_img = load_planet_data(self.active_planet)
        self.ax_map.clear()
        self.ax_map.imshow(base_img, extent=[-180, 180, -90, 90], origin='upper', zorder=0)
        self.ax_map.set_title(f"Select Region on {self.active_planet}")
        self.border_artists = []
        if self.show_borders and HAS_GPD and self.active_planet == 'Earth':
            b1 = world_borders.boundary.plot(ax=self.ax_map, edgecolor='cyan', linewidth=0.6, alpha=0.5, zorder=2)
            b2 = state_borders.boundary.plot(ax=self.ax_map, edgecolor='cyan', linewidth=0.2, alpha=0.3, zorder=2)
            self.border_artists.extend(b1.collections + b2.collections)
        self.clear_selection();
        self.reset_home_view()

    def toggle_ocean(self, checked):
        self.show_ocean = checked;
        self.trigger_recalc()

    def toggle_borders(self, checked):
        self.show_borders = checked;
        self.switch_planet()

    def zoom_map(self, event):
        if event.inaxes != self.ax_map: return
        scale = 1 / 1.2 if event.button == 'up' else 1.2
        cx, cy = self.ax_map.get_xlim(), self.ax_map.get_ylim()
        x, y = event.xdata, event.ydata
        if x is None or y is None: return
        nw, nh = (cx[1] - cx[0]) * scale, (cy[1] - cy[0]) * scale
        rx, ry = (cx[1] - x) / (cx[1] - cx[0]), (cy[1] - y) / (cy[1] - cy[0])
        self.ax_map.set_xlim([max(-180, x - nw * (1 - rx)), min(180, x + nw * rx)])
        self.ax_map.set_ylim([max(-90, y - nh * (1 - ry)), min(90, y + nh * ry)])
        self.canvas_map.draw_idle()

    def on_select(self, eclick, erelease):
        x1, x2 = sorted([eclick.xdata, erelease.xdata]);
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
            pad_geo_h, pad_geo_w = tw / self.paper_aspect, gw
        else:
            pad_geo_h, pad_geo_w = gh, (gh * self.paper_aspect) / np.cos(mid_lat)
        ex1, ex2 = cx - pad_geo_w / 2, cx + pad_geo_w / 2
        ey1, ey2 = cy - pad_geo_h / 2, cy + pad_geo_h / 2
        self.current_view_extent = [ex1, ex2, ey1, ey2]

        h, w = self.current_dem_src.shape
        px_x1, px_x2 = int((ex1 + 180) / 360 * w), int((ex2 + 180) / 360 * w)
        px_y1, px_y2 = int((90 - ey2) / 180 * h), int((90 - ey1) / 180 * h)
        c_x1, c_x2 = max(0, px_x1), min(w, px_x2)
        c_y1, c_y2 = max(0, px_y1), min(h, px_y2)
        if c_x2 <= c_x1 or c_y2 <= c_y1: return
        self.pending_bounds = [c_x1, c_y1, c_x2, c_y2]
        self.has_drawn = False
        self.ax_prev.clear();
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])
        self.canvas_prev.draw_idle()
        self.lbl_status.setText("Selection captured. Click '1. DRAW MAP'.")

    # --- EXPORT PIPELINES ---
    def export_svg_action(self):
        if not self.has_drawn:
            QMessageBox.warning(self, "Not Ready", "Draw a map first!")
            return
        orient = "landscape" if self.is_landscape else "portrait"
        default_name = f"{self.active_planet.lower()}_{orient}_{int(self.pw)}x{int(self.ph)}.svg"
        out_svg, _ = QFileDialog.getSaveFileName(self, "Save Editable SVG", default_name, "SVG Files (*.svg)")
        if not out_svg: return

        self.lbl_status.setText("Generating Master SVG...")
        QApplication.processEvents()

        export_fig = Figure(figsize=(self.pw / 25.4, self.ph / 25.4))
        export_ax = export_fig.add_axes([0, 0, 1, 1]);
        export_ax.axis('off')
        mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
        export_ax.set_aspect(1 / np.cos(mid_lat))
        export_ax.set_xlim(self.current_view_extent[0], self.current_view_extent[1])
        export_ax.set_ylim(self.current_view_extent[2], self.current_view_extent[3])

        # Prepare Text Subtraction Masks
        text_boxes = []
        if hasattr(self, 'chk_mask_text') and self.chk_mask_text.isChecked():
            for item in getattr(self, 'custom_items', []):
                if item['type'] == 'text':
                    tp = TextPath((0, 0), item['text'], size=1)
                    trans = Affine2D().scale(item['scale']).rotate_deg(item['rot']).translate(item['x'], item['y'])
                    bbox = tp.transformed(trans).get_extents()
                    pad = item['scale'] * 0.2
                    text_boxes.append(shp_box(bbox.x0 - pad, bbox.y0 - pad, bbox.x1 + pad, bbox.y1 + pad))

        for p_id, pen in self.pens.items():
            for item in self.dem_segments:
                if item['pen_id'] == p_id:
                    line_geom = LineString(item['seg'])
                    valid_geoms = [line_geom]
                    for tbox in text_boxes:
                        next_geoms = []
                        for g in valid_geoms:
                            diff = g.difference(tbox)
                            if diff.is_empty: continue
                            if diff.geom_type == 'LineString':
                                next_geoms.append(diff)
                            elif diff.geom_type == 'MultiLineString':
                                next_geoms.extend(list(diff.geoms))
                        valid_geoms = next_geoms
                    for g in valid_geoms:
                        if len(g.coords) >= 2:
                            export_ax.plot(np.array(g.coords)[:, 0], np.array(g.coords)[:, 1], color=pen['color'],
                                           linewidth=pen['width'] * PT_PER_MM)

            for item in self.custom_items:
                if item['pen_id'] == p_id:
                    if item['type'] == 'line':
                        line_geom = LineString(item['points'])
                        valid_geoms = [line_geom]
                        for tbox in text_boxes:
                            next_geoms = []
                            for g in valid_geoms:
                                diff = g.difference(tbox)
                                if diff.is_empty: continue
                                if diff.geom_type == 'LineString':
                                    next_geoms.append(diff)
                                elif diff.geom_type == 'MultiLineString':
                                    next_geoms.extend(list(diff.geoms))
                            valid_geoms = next_geoms
                        for g in valid_geoms:
                            if len(g.coords) >= 2:
                                export_ax.plot(np.array(g.coords)[:, 0], np.array(g.coords)[:, 1], color=pen['color'],
                                               linewidth=pen['width'] * PT_PER_MM)
                    elif item['type'] == 'text':
                        tp = TextPath((0, 0), item['text'], size=1)
                        trans = Affine2D().scale(item['scale']).rotate_deg(item['rot']).translate(item['x'], item['y'])
                        for poly in tp.transformed(trans).to_polygons():
                            export_ax.plot(poly[:, 0], poly[:, 1], color=pen['color'],
                                           linewidth=pen['width'] * PT_PER_MM)

        export_fig.savefig(out_svg, format='svg')
        import matplotlib.pyplot as plt
        plt.close(export_fig)
        self.lbl_status.setText("SVG Generated Successfully!")
        QMessageBox.information(self, "Success", f"Master SVG saved to:\n{out_svg}")

    def export_action(self):
        if not HAS_VPYPE:
            QMessageBox.critical(self, "Missing Library", "Please run: pip install vpype vpype-gcode")
            return
        if not getattr(self, 'has_drawn', False):
            QMessageBox.warning(self, "Not Ready", "Draw a map first!")
            return

        orient = "landscape" if self.is_landscape else "portrait"
        default_base_name = f"{self.active_planet.lower()}_{orient}_{int(self.pw)}x{int(self.ph)}.gcode"

        base_filepath, _ = QFileDialog.getSaveFileName(self, "Select Base Name for G-Code Files", default_base_name,
                                                       "G-Code Files (*.gcode)")
        if not base_filepath: return
        base_filepath = base_filepath.replace("\\", "/")
        dir_path = os.path.dirname(base_filepath)
        base_name = os.path.splitext(os.path.basename(base_filepath))[0]

        self.lbl_status.setText("Generating individual multi-pen G-Code files...")
        QApplication.processEvents()

        temp_dir = tempfile.gettempdir()
        exported_files = []

        # Prepare Text Masks
        text_boxes = []
        if hasattr(self, 'chk_mask_text') and self.chk_mask_text.isChecked():
            for item in getattr(self, 'custom_items', []):
                if item['type'] == 'text':
                    tp = TextPath((0, 0), item['text'], size=1)
                    trans = Affine2D().scale(item['scale']).rotate_deg(item['rot']).translate(item['x'], item['y'])
                    bbox = tp.transformed(trans).get_extents()
                    pad = item['scale'] * 0.2
                    text_boxes.append(shp_box(bbox.x0 - pad, bbox.y0 - pad, bbox.x1 + pad, bbox.y1 + pad))

        for p_id, pen in self.pens.items():
            safe_pen_name = "".join([c if c.isalnum() else "_" for c in pen['name']])
            safe_pen_name = re.sub(r'_+', '_', safe_pen_name).strip('_')
            final_out_gcode = f"{dir_path}/{safe_pen_name}_{base_name}.gcode"

            t_svg = os.path.join(temp_dir, f"temp_{p_id}.svg").replace("\\", "/")
            t_gcode = os.path.join(temp_dir, f"temp_{p_id}.gcode").replace("\\", "/")

            export_fig = Figure(figsize=(self.pw / 25.4, self.ph / 25.4))
            export_ax = export_fig.add_axes([0, 0, 1, 1]);
            export_ax.axis('off')
            mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
            export_ax.set_aspect(1 / np.cos(mid_lat))
            export_ax.set_xlim(self.current_view_extent[0], self.current_view_extent[1])
            export_ax.set_ylim(self.current_view_extent[2], self.current_view_extent[3])

            has_data = False

            # Mask and Plot DEM & Borders
            for item in getattr(self, 'dem_segments', []):
                if item.get('pen_id') == p_id:
                    line_geom = LineString(item['seg'])
                    valid_geoms = [line_geom]
                    for tbox in text_boxes:
                        next_geoms = []
                        for g in valid_geoms:
                            diff = g.difference(tbox)
                            if diff.is_empty: continue
                            if diff.geom_type == 'LineString':
                                next_geoms.append(diff)
                            elif diff.geom_type == 'MultiLineString':
                                next_geoms.extend(list(diff.geoms))
                        valid_geoms = next_geoms
                    for g in valid_geoms:
                        if len(g.coords) >= 2:
                            export_ax.plot(np.array(g.coords)[:, 0], np.array(g.coords)[:, 1], color='black',
                                           linewidth=1)
                            has_data = True

            # Mask and Plot Custom Items
            for item in getattr(self, 'custom_items', []):
                if item.get('pen_id') == p_id:
                    if item['type'] == 'line':
                        line_geom = LineString(item['points'])
                        valid_geoms = [line_geom]
                        for tbox in text_boxes:
                            next_geoms = []
                            for g in valid_geoms:
                                diff = g.difference(tbox)
                                if diff.is_empty: continue
                                if diff.geom_type == 'LineString':
                                    next_geoms.append(diff)
                                elif diff.geom_type == 'MultiLineString':
                                    next_geoms.extend(list(diff.geoms))
                            valid_geoms = next_geoms
                        for g in valid_geoms:
                            if len(g.coords) >= 2:
                                export_ax.plot(np.array(g.coords)[:, 0], np.array(g.coords)[:, 1], color='black',
                                               linewidth=1)
                                has_data = True
                    elif item['type'] == 'text':
                        tp = TextPath((0, 0), item['text'], size=1)
                        trans = Affine2D().scale(item['scale']).rotate_deg(item['rot']).translate(item['x'], item['y'])
                        for poly in tp.transformed(trans).to_polygons():
                            export_ax.plot(poly[:, 0], poly[:, 1], color='black', linewidth=1)
                            has_data = True

            if not has_data:
                import matplotlib.pyplot as plt
                plt.close(export_fig)
                continue

            export_fig.savefig(t_svg, format='svg')
            import matplotlib.pyplot as plt
            plt.close(export_fig)

            try:
                vpype_cmd = f'read "{t_svg}" linesimplify -t 0.1mm linemerge -t 0.3mm filter --min-length 2mm linesort gwrite --profile gcodemm "{t_gcode}"'
                vpype_cli.execute(vpype_cmd)
            except Exception as e:
                print(f"Skipping {pen['name']} optimization: {e}")
                continue

            master_gcode = [
                f"; --- START ROUTINE: {pen['name']} ---",
                "G21",
                "G90",
                "G28",
                "G92 X-50.0 Y-50.0 Z-4.0",
                "G0 Z1.0 F1500",
                "M400"
            ]

            with open(t_gcode, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    if line.startswith("G0 X") or line.startswith("G00 X"):
                        master_gcode.extend([
                            "G0 Z1.0 F1500", "M400",
                            f"{line} F3000", "M400",
                            "G1 Z0.0 F1000", "M400"
                        ])
                    elif line.startswith("G1 X") or line.startswith("G01 X"):
                        master_gcode.append(f"{line} F1500")

            master_gcode.extend([
                "G0 Z1.0 F1500",  # Immediate 1mm vertical hop
                "M400",  # Wait for physical lift to complete
                "; --- END OF PLOT ---",
                "G0 X0 Y0 F3000",  # Slide back to origin while hovering 1mm above the page
                "G0 Z25.0 F1500",  # Final clearance lift to remove the page
                f"M117 {pen['name']} Complete!",
                "M84"
            ])

            try:
                with open(final_out_gcode, "w") as f:
                    f.write("\n".join(master_gcode))
                exported_files.append(final_out_gcode)
            except Exception as e:
                print(f"Error saving {final_out_gcode}: {e}")

            try:
                if os.path.exists(t_svg): os.remove(t_svg)
                if os.path.exists(t_gcode): os.remove(t_gcode)
            except:
                pass

        if exported_files:
            self.lbl_status.setText(f"Successfully generated {len(exported_files)} G-Code files!")
            files_list_str = "\n".join([os.path.basename(f) for f in exported_files])
            QMessageBox.information(self, "Success",
                                    f"Generated {len(exported_files)} individual G-Code files:\n\n{files_list_str}")
        else:
            QMessageBox.warning(self, "No Data", "No vector elements found for any active pen!")
            self.lbl_status.setText("Ready.")


if __name__ == '__main__':
    if platform.system() == 'Windows':
        import ctypes

        myappid = 'tps.cartographer.studio.1'  # Arbitrary unique ID string
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("icon.jpg"))
    app.setStyle("Fusion")
    window = PlotterApp()
    window.show()
    sys.exit(app.exec())