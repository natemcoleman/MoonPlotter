import sys
import os
import tempfile
import copy
import re
import numpy as np
import rasterio
from PIL import Image
import platform

# PyQt6 Imports
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QRadioButton, QCheckBox, QComboBox, QSplitter,
                             QFileDialog, QMessageBox, QFrame, QButtonGroup,
                             QInputDialog, QColorDialog, QSlider, QScrollArea, QSpinBox, QCompleter,
                             QProgressBar)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
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

# Geometry & Local Imports
from shapely.geometry import box as shp_box, LineString
from config import DATA_FILES, PAPER_SIZES, PT_PER_MM, load_pens, save_pens
from workers import RenderWorker

import warnings
from rasterio.errors import NotGeoreferencedWarning

warnings.filterwarnings('ignore', r'All-NaN (slice|axis) encountered')
warnings.filterwarnings('ignore', category=NotGeoreferencedWarning)
Image.MAX_IMAGE_PIXELS = None

# Optional Integrations
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
    world_borders = None
    state_borders = None

# Cache for UI map previews
loaded_maps_cache = {}


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

    loaded_maps_cache[planet] = (None, base_img)
    return None, base_img


# ==========================================
# MAIN PYQT6 GUI
# ==========================================
class PlotterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Topo Plotter Studio - Cartographer Edition")
        self.setWindowIcon(QIcon("icon.jpg"))
        self.resize(1000, 1200)

        self.active_planet = 'Earth'
        self.paper_name = '100x100mm (Default)'
        self.is_landscape = True
        self.interval_ft = 200

        self.current_dem_crop = None
        self.current_data_extent = None
        self.current_view_extent = None
        self.last_drag_coords = None
        self.has_drawn = False
        self.is_calculating = False

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

        # Rendering Mode Settings
        vbox.addSpacing(10)
        vbox.addWidget(QLabel("<b>Plot Rendering Mode</b>"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(
            ["Topography (DEM)", "Monochrome Hatch (Color Map)", "CMY Color Separation (Color Map)"])
        self.combo_mode.currentTextChanged.connect(self.context_ui_update)
        vbox.addWidget(self.combo_mode)

        self.lbl_hatch = QLabel("Hatching Strategy:")
        self.combo_hatch = QComboBox()
        self.combo_hatch.addItems(["Multi-Pass Thresholded", "Variable Spacing Scanlines", "Modulated Sine Squiggles"])
        vbox.addWidget(self.lbl_hatch)
        vbox.addWidget(self.combo_hatch)

        vbox.addSpacing(10)
        vbox.addWidget(QLabel("<b>Map Options</b>"))

        self.chk_ocean = QCheckBox("Plot Ocean/Crater Depths")
        self.chk_ocean.setChecked(True)
        self.chk_ocean.toggled.connect(self.trigger_recalc)

        self.chk_borders = QCheckBox("Show Color Map Borders")
        self.chk_borders.setChecked(True)
        self.chk_borders.toggled.connect(self.toggle_borders)

        self.chk_apollo = QCheckBox("Show Apollo Landing Sites")
        self.chk_apollo.setChecked(False)
        self.chk_apollo.setStyleSheet("color: #8E44AD; font-weight: bold;")
        self.chk_apollo.toggled.connect(self.toggle_apollo)

        self.chk_plot_borders = QCheckBox("Plot Borders (Index Pen)")
        self.chk_plot_borders.setChecked(True)
        self.chk_plot_borders.setStyleSheet("color: #2E86C1; font-weight: bold;")
        self.chk_plot_borders.toggled.connect(self.trigger_recalc)

        self.chk_circle = QCheckBox("Circle Mode (Hemisphere)")
        self.chk_circle.setChecked(False)
        self.chk_circle.setStyleSheet("color: #D35400; font-weight: bold;")
        self.chk_circle.toggled.connect(self.toggle_circle_mode)

        self.chk_blue_water = QCheckBox("Use blue pen for underwater (<0m)")
        self.chk_blue_water.setChecked(True)
        self.chk_blue_water.toggled.connect(self.trigger_recalc)

        self.chk_mask_text = QCheckBox("Clear background behind Text")
        self.chk_mask_text.setChecked(True)
        self.chk_mask_text.setStyleSheet("color: #2E86C1; font-weight: bold;")
        self.chk_mask_text.toggled.connect(self.render_canvas)

        vbox.addWidget(self.chk_ocean)
        vbox.addWidget(self.chk_borders)
        vbox.addWidget(self.chk_apollo)
        vbox.addWidget(self.chk_plot_borders)
        vbox.addWidget(self.chk_circle)
        vbox.addWidget(self.chk_blue_water)
        vbox.addWidget(self.chk_mask_text)

        # Region Masking UI
        self.lbl_region = QLabel("<b>Region Masking (Clip to border)</b>")
        vbox.addWidget(self.lbl_region)
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
            completer = QCompleter(regions)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self.combo_region.setCompleter(completer)

        self.combo_region.currentTextChanged.connect(self.on_region_changed)
        vbox.addWidget(self.combo_region)

        # Paper Size
        vbox.addSpacing(10)
        vbox.addWidget(QLabel("<b>Paper / Bed Size</b>"))
        self.combo_paper = QComboBox()
        self.combo_paper.addItems(list(PAPER_SIZES.keys()))
        self.combo_paper.currentTextChanged.connect(self.change_paper)
        vbox.addWidget(self.combo_paper)

        self.widget_custom = QWidget()
        h_custom = QHBoxLayout(self.widget_custom)
        h_custom.setContentsMargins(0, 0, 0, 0)
        self.txt_cw = QLineEdit("150")
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

        # --- CONTOUR SPECIFIC CONTROLS ---
        line2 = QFrame();
        line2.setFrameShape(QFrame.Shape.HLine);
        vbox.addWidget(line2)

        self.lbl_contour_hdr = QLabel("<b>Contour Generation</b>")
        vbox.addWidget(self.lbl_contour_hdr)

        self.lbl_usgs_interval = QLabel("Interval: Auto (USGS Scale)")
        self.lbl_usgs_interval.setStyleSheet("color: #8E44AD; font-weight: bold;")
        vbox.addWidget(self.lbl_usgs_interval)

        self.widget_interval = QWidget()
        h_int = QHBoxLayout(self.widget_interval)
        h_int.setContentsMargins(0, 0, 0, 0)
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
        vbox.addWidget(self.widget_interval)

        self.widget_index = QWidget()
        h_idx = QHBoxLayout(self.widget_index)
        h_idx.setContentsMargins(0, 0, 0, 0)
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
        vbox.addWidget(self.widget_index)

        self.lbl_smooth_hdr = QLabel("<b>Vector Smoothing (USGS Parameters)</b>")
        vbox.addWidget(self.lbl_smooth_hdr)

        self.widget_presets = QWidget()
        h_presets = QHBoxLayout(self.widget_presets)
        h_presets.setContentsMargins(0, 0, 0, 0)
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
        vbox.addWidget(self.widget_presets)

        self.widget_blur = QWidget()
        h_blur = QHBoxLayout(self.widget_blur)
        h_blur.setContentsMargins(0, 0, 0, 0)
        h_blur.addWidget(QLabel("Raster Blur \u03c3:"))
        self.slider_blur = QSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(0, 50)
        self.slider_blur.setValue(0)
        self.lbl_blur_val = QLabel("0.0")
        self.slider_blur.valueChanged.connect(
            lambda v: (self.lbl_blur_val.setText(f"{v / 10.0:.1f}"), self.recalc_timer.start(150)))
        h_blur.addWidget(self.slider_blur);
        h_blur.addWidget(self.lbl_blur_val)
        vbox.addWidget(self.widget_blur)

        self.widget_simplify = QWidget()
        h_simp = QHBoxLayout(self.widget_simplify)
        h_simp.setContentsMargins(0, 0, 0, 0)
        h_simp.addWidget(QLabel("Simplify Tol:"))
        self.slider_simp = QSlider(Qt.Orientation.Horizontal)
        self.slider_simp.setRange(0, 250)
        self.slider_simp.setValue(40)
        self.lbl_simp_val = QLabel("4.0m")
        self.slider_simp.valueChanged.connect(
            lambda v: (self.lbl_simp_val.setText(f"{v / 10.0:.1f}m"), self.recalc_timer.start(150)))
        h_simp.addWidget(self.slider_simp);
        h_simp.addWidget(self.lbl_simp_val)
        vbox.addWidget(self.widget_simplify)

        self.widget_smooth = QWidget()
        h_smooth = QHBoxLayout(self.widget_smooth)
        h_smooth.setContentsMargins(0, 0, 0, 0)
        h_smooth.addWidget(QLabel("Smoothing:"))
        self.slider_smooth = QSlider(Qt.Orientation.Horizontal)
        self.slider_smooth.setRange(0, 3000)
        self.slider_smooth.setValue(640)
        self.lbl_smooth_val = QLabel("64.0m")
        self.slider_smooth.valueChanged.connect(
            lambda v: (self.lbl_smooth_val.setText(f"{v / 10.0:.1f}m"), self.recalc_timer.start(150)))
        h_smooth.addWidget(self.slider_smooth);
        h_smooth.addWidget(self.lbl_smooth_val)
        vbox.addWidget(self.widget_smooth)

        vbox.addStretch()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        vbox.addWidget(self.progress_bar)

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

        h_tools = QHBoxLayout()
        btn_style = "background-color: #E5E7E9; color: black; font-weight: bold; padding: 4px; border-radius: 3px;"
        self.btn_undo = QPushButton("Undo (Ctrl+Z)");
        self.btn_undo.setStyleSheet(btn_style);
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self.undo_action)
        self.btn_redo = QPushButton("Redo (Ctrl+Y)");
        self.btn_redo.setStyleSheet(btn_style);
        self.btn_redo.setEnabled(False)
        self.btn_redo.clicked.connect(self.redo_action)
        self.btn_add_pen = QPushButton("Add Custom Pen");
        self.btn_add_pen.setStyleSheet(btn_style)
        self.btn_add_pen.clicked.connect(self.add_custom_pen)
        h_tools.addWidget(self.btn_undo);
        h_tools.addWidget(self.btn_redo);
        h_tools.addStretch();
        h_tools.addWidget(self.btn_add_pen)
        v_pal.addLayout(h_tools)

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
        self.context_ui_update()

    def context_ui_update(self):
        """Dynamically hides/shows UI options based on active planet and mode."""
        planet = 'Earth' if self.radio_earth.isChecked() else 'Moon'
        mode = self.combo_mode.currentText()
        is_earth = (planet == 'Earth')
        is_topo = "Topography" in mode

        self.chk_plot_borders.setVisible(is_earth)
        self.chk_blue_water.setVisible(is_earth)
        self.lbl_region.setVisible(is_earth)
        self.combo_region.setVisible(is_earth)

        self.chk_apollo.setVisible(not is_earth)

        self.lbl_hatch.setVisible(not is_topo)
        self.combo_hatch.setVisible(not is_topo)

        self.lbl_usgs_interval.setVisible(is_topo)
        self.widget_interval.setVisible(is_topo)
        self.widget_index.setVisible(is_topo)
        self.widget_simplify.setVisible(is_topo)
        self.widget_smooth.setVisible(is_topo)
        self.widget_blur.setVisible(is_topo)
        self.widget_presets.setVisible(is_topo)
        self.lbl_smooth_hdr.setVisible(is_topo)
        self.lbl_contour_hdr.setVisible(is_topo)

    def on_region_changed(self, text):
        if "Tahoe" in text:
            self.slider_int.blockSignals(True);
            self.txt_int.blockSignals(True)
            self.slider_int.setRange(25, 500);
            self.slider_int.setSingleStep(25)
            self.slider_int.setValue(75);
            self.txt_int.setText("75")
            self.interval_ft = 75
            self.lbl_usgs_interval.setText("Interval: Tahoe Bathymetry (75 ft)")
            self.slider_int.blockSignals(False);
            self.txt_int.blockSignals(False)
        else:
            self.slider_int.blockSignals(True);
            self.txt_int.blockSignals(True)
            self.slider_int.setRange(50, 10000);
            self.slider_int.setSingleStep(50)
            val = max(50, self.interval_ft)
            self.slider_int.setValue(val);
            self.txt_int.setText(str(val))
            self.lbl_usgs_interval.setText("Interval: Auto (USGS Scale)")
            self.slider_int.blockSignals(False);
            self.txt_int.blockSignals(False)
        self.recalc_timer.start(500)

    # --- PEN PALETTE & STATES ---
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

    # --- MAP & DATA CALCULATION ---
    def trigger_recalc(self):
        if self.is_calculating:
            self.recalc_pending = True
            return
        if self.has_drawn and self.current_view_extent:
            self.push_undo_state()
            self.draw_action()

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

    def get_ui_params(self):
        return {
            'active_planet': self.active_planet,
            'selected_region': self.combo_region.currentText(),
            'circle_mode': self.chk_circle.isChecked(),
            'show_ocean': self.chk_ocean.isChecked(),
            'show_borders': self.chk_plot_borders.isChecked(),
            'show_apollo': self.chk_apollo.isChecked(),
            'blue_water': self.chk_blue_water.isChecked(),
            'interval_ft': self.interval_ft,
            'auto_index': self.chk_auto_index.isChecked(),
            'index_interval': self.spin_idx.value(),
            'simp_tol': self.slider_simp.value() / 10.0,
            'smooth_val': self.slider_smooth.value() / 10.0,
            'blur_sigma': self.slider_blur.value() / 10.0,
            'plot_mode': self.combo_mode.currentText(),
            'hatch_strategy': self.combo_hatch.currentText()
        }

    def draw_action(self):
        if not self.current_view_extent: return
        self.btn_draw.setEnabled(False)
        self.is_calculating = True
        self.progress_bar.setVisible(True)

        params = self.get_ui_params()
        dem_path = DATA_FILES[self.active_planet]['dem']
        if self.active_planet == 'Earth' and "Tahoe" in params['selected_region']:
            dem_path = "tahoe2.tif"

        if not os.path.exists(dem_path):
            QMessageBox.critical(self, "Missing File", f"Cannot find {dem_path}")
            self.btn_draw.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.lbl_status.setText("Ready.")
            self.is_calculating = False
            return

        _, base_img = load_planet_data(self.active_planet)
        self.worker = RenderWorker(self.active_planet, dem_path, self.current_view_extent, params, base_img=base_img)
        # self.worker = RenderWorker(self.active_planet, dem_path, self.current_view_extent, params)
        self.worker.finished.connect(self.on_render_success)
        self.worker.error.connect(self.on_render_error)
        self.worker.progress.connect(self.on_render_progress)
        self.worker.start()

    def on_render_progress(self, msg):
        self.lbl_status.setText(msg)

    def on_render_success(self, segments, dem_data, extent, min_e, max_e):
        self.current_dem_crop = dem_data
        self.current_data_extent = extent
        self.dem_segments = segments

        relief = max_e - min_e
        is_tahoe = (self.active_planet == 'Earth' and "Tahoe" in self.combo_region.currentText())

        # Auto-USGS Interval Feedback (for Topo mode only)
        if relief > 0 and not is_tahoe and "Topography" in self.combo_mode.currentText():
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
                best_step = 120
            best_step*=100
            if self.interval_ft != best_step and not getattr(self, 'recalc_pending', False):
                self.slider_int.blockSignals(True);
                self.txt_int.blockSignals(True)
                self.interval_ft = best_step
                self.slider_int.setValue(best_step);
                self.txt_int.setText(str(best_step))
                self.lbl_usgs_interval.setText(f"Interval: Auto USGS ({best_step} ft)")
                self.slider_int.blockSignals(False);
                self.txt_int.blockSignals(False)
                # Restart calculation with newly adopted optimal step size
                self.is_calculating = False
                self.trigger_recalc()
                return

        self.has_drawn = True
        if not getattr(self, 'recalc_pending', False):
            self.custom_items = []
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.update_undo_redo_btns()

        self.ax_prev.set_xlim(0.0, 1.0);
        self.ax_prev.set_ylim(0.0, 1.0)
        self.render_canvas()

        self.btn_draw.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.lbl_status.setText("Render complete!")
        self.is_calculating = False

        if getattr(self, 'recalc_pending', False):
            self.recalc_pending = False
            self.recalc_timer.start(100)

    def on_render_error(self, err_msg):
        self.btn_draw.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.lbl_status.setText("Ready.")
        self.is_calculating = False
        QMessageBox.critical(self, "Read Error", err_msg)

    # --- HIGH PERFORMANCE RENDERER ---
    def render_canvas(self):
        xlim, ylim = self.ax_prev.get_xlim(), self.ax_prev.get_ylim()
        is_fresh = (xlim == (0.0, 1.0) and ylim == (0.0, 1.0))

        self.ax_prev.clear()
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])

        # Apply CMY Paper Background if needed
        if "CMY" in self.combo_mode.currentText():
            self.ax_prev.set_facecolor('#FFFDE7')
        else:
            self.ax_prev.set_facecolor('white')

        if not self.has_drawn: return

        mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
        self.ax_prev.set_aspect(1 / np.cos(mid_lat))

        if getattr(self, 'dem_segments', []):
            segs = [item['seg'] for item in self.dem_segments]
            colors = [self.pens.get(item['pen_id'], self.pens['base'])['color'] for item in self.dem_segments]
            lws = [self.pens.get(item['pen_id'], self.pens['base'])['width'] * PT_PER_MM for item in self.dem_segments]

            self.dem_collection = LineCollection(segs, colors=colors, linewidths=lws, picker=True, pickradius=5,
                                                 zorder=10)
            self.ax_prev.add_collection(self.dem_collection)

        for idx, item in enumerate(getattr(self, 'custom_items', [])):
            pen = self.pens.get(item['pen_id'], self.pens['base'])
            if item['type'] == 'line':
                seg = np.array(item['points'])
                line, = self.ax_prev.plot(seg[:, 0], seg[:, 1], color=pen['color'],
                                          linewidth=pen['width'] * PT_PER_MM, picker=True, pickradius=5, zorder=11)
                line.item_ref = ('custom', idx)

            elif item['type'] == 'text':
                tp = TextPath((0, 0), item['text'], size=1)
                trans = Affine2D().scale(item['scale']).rotate_deg(item['rot']).translate(item['x'], item['y'])

                if self.chk_mask_text.isChecked():
                    bbox = tp.transformed(trans).get_extents()
                    pad = item['scale'] * 0.2
                    rect = patches.Rectangle((bbox.x0 - pad, bbox.y0 - pad),
                                             bbox.width + 2 * pad, bbox.height + 2 * pad,
                                             facecolor=self.ax_prev.get_facecolor(), edgecolor='none', zorder=14)
                    self.ax_prev.add_patch(rect)

                for poly in tp.transformed(trans).to_polygons():
                    line, = self.ax_prev.plot(poly[:, 0], poly[:, 1], color=pen['color'],
                                              linewidth=pen['width'] * PT_PER_MM, picker=True, pickradius=5, zorder=15)
                    line.item_ref = ('custom', idx)

        if is_fresh and self.current_view_extent:
            if self.chk_circle.isChecked():
                if self.active_planet == 'Earth':
                    R = 6371000.0
                elif self.active_planet == 'Moon':
                    R = 1737400.0
                else:
                    R = 3389500.0
                self.ax_prev.set_xlim(-R * 1.05, R * 1.05)
                self.ax_prev.set_ylim(-R * 1.05, R * 1.05)
                self.ax_prev.set_aspect('equal')
            else:
                self.ax_prev.set_xlim(self.current_view_extent[0], self.current_view_extent[1])
                self.ax_prev.set_ylim(self.current_view_extent[2], self.current_view_extent[3])
                mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
                self.ax_prev.set_aspect(1 / np.cos(mid_lat))
        else:
            self.ax_prev.set_xlim(xlim)
            self.ax_prev.set_ylim(ylim)

        self.canvas_prev.draw_idle()

    # --- PREVIEW ZOOM & PAN ---
    def reset_prev_view(self):
        if not self.has_drawn: return
        self.ax_prev.set_xlim(0.0, 1.0)
        self.ax_prev.set_ylim(0.0, 1.0)
        self.render_canvas()

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
                    if (dx * dx + dy * dy) < min_dist: min_dist = dx * dx + dy * dy; target = idx
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
        self.last_drag_coords = None
        self.has_drawn = False
        self.selector.extents = (-200, -200, -100, -100)
        self.ax_prev.clear();
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])
        self.canvas_prev.draw_idle()
        self.canvas_map.draw_idle()
        self.custom_items = []
        self.dem_segments = []
        self.undo_stack.clear();
        self.redo_stack.clear()
        self.update_undo_redo_btns()

    def toggle_circle_mode(self, checked):
        if checked:
            self.selector.set_active(False)
            self.selector.extents = (-200, -200, -100, -100)
            self.zoom_selector.set_active(False)
            self.canvas_map.setCursor(Qt.CursorShape.OpenHandCursor)
            self.circle_center_lon = getattr(self, 'circle_center_lon', 0.0)
            self.update_circle_patches()
            self.ax_map.set_xlim(-180, 180);
            self.ax_map.set_ylim(-90, 90)
            self.has_drawn = False
        else:
            self.selector.set_active(True)
            self.circle_patch_1.set_visible(False)
            self.circle_patch_2.set_visible(False)
            self.canvas_map.setCursor(Qt.CursorShape.ArrowCursor)
            self.clear_selection()
        self.canvas_map.draw_idle()

    def toggle_zoom_tool(self, checked):
        if self.chk_circle.isChecked():
            self.btn_zoom_tool.setChecked(False)
            return
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
        elif event.button == 1 and self.chk_circle.isChecked() and event.inaxes == self.ax_map:
            self.circle_dragging = True
            self.circle_drag_start = event.xdata
            self.circle_start_lon = getattr(self, 'circle_center_lon', 0.0)
            self.canvas_map.setCursor(Qt.CursorShape.ClosedHandCursor)

            self.circle_patch_1.set_visible(False)
            self.circle_patch_2.set_visible(False)
            self.canvas_map.draw()
            self.bg_cache = self.canvas_map.copy_from_bbox(self.ax_map.bbox)

            self.circle_patch_1.set_visible(True)
            self.circle_patch_2.set_visible(True)
            self.ax_map.draw_artist(self.circle_patch_1)
            self.ax_map.draw_artist(self.circle_patch_2)
            self.canvas_map.blit(self.ax_map.bbox)

    def on_mouse_motion_map(self, event):
        if self.pan_active and event.inaxes == self.ax_map:
            inv = self.ax_map.transData.inverted()
            sx, sy = inv.transform((self.pan_start_x, self.pan_start_y))
            ex, ey = inv.transform((event.x, event.y))
            self.ax_map.set_xlim(self.pan_xlim[0] - (ex - sx), self.pan_xlim[1] - (ex - sx))
            self.ax_map.set_ylim(self.pan_ylim[0] - (ey - sy), self.pan_ylim[1] - (ey - sy))
            self.canvas_map.draw_idle()
        elif getattr(self, 'circle_dragging', False) and event.inaxes == self.ax_map:
            dx = event.xdata - self.circle_drag_start
            self.circle_center_lon = (self.circle_start_lon + dx + 180) % 360 - 180
            if hasattr(self, 'bg_cache'):
                self.canvas_map.restore_region(self.bg_cache)
                self.update_circle_patches()
                if self.circle_patch_1.get_visible(): self.ax_map.draw_artist(self.circle_patch_1)
                if self.circle_patch_2.get_visible(): self.ax_map.draw_artist(self.circle_patch_2)
                self.canvas_map.blit(self.ax_map.bbox)

    def on_mouse_release_map(self, event):
        if event.button == 2:
            self.pan_active = False
        elif event.button == 1 and self.chk_circle.isChecked():
            self.circle_dragging = False
            self.canvas_map.setCursor(Qt.CursorShape.OpenHandCursor)
            self.canvas_map.draw_idle()

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
        self.context_ui_update()

        _, base_img = load_planet_data(self.active_planet)
        self.ax_map.clear()
        self.ax_map.imshow(base_img, extent=[-180, 180, -90, 90], origin='upper', zorder=0)
        self.ax_map.set_title(f"Select Region on {self.active_planet}")

        # Preload Geopandas Borders invisibly
        self.border_artists = []
        if HAS_GPD and self.active_planet == 'Earth':
            b1 = world_borders.boundary.plot(ax=self.ax_map, edgecolor='cyan', linewidth=0.6, alpha=0.5, zorder=2)
            b2 = state_borders.boundary.plot(ax=self.ax_map, edgecolor='cyan', linewidth=0.2, alpha=0.3, zorder=2)
            self.border_artists.extend(b1.collections + b2.collections)
        for artist in self.border_artists:
            artist.set_visible(self.chk_borders.isChecked())

        # Preload Apollo Sites invisibly
        self.apollo_artists = []
        if self.active_planet == 'Moon':
            apollo_sites = {
                "Apollo 11": (0.67416, 23.47314), "Apollo 12": (-3.0128, -23.4219),
                "Apollo 14": (-3.64589, -17.47194), "Apollo 15": (26.13239, 3.63330),
                "Apollo 16": (-8.9734, 15.5011), "Apollo 17": (20.1911, 30.7723)
            }
            for name, (lat, lon) in apollo_sites.items():
                marker, = self.ax_map.plot(lon, lat, marker='X', markersize=6, color='yellow', markeredgecolor='black',
                                           zorder=10)
                self.apollo_artists.append(marker)
        for artist in self.apollo_artists:
            artist.set_visible(self.chk_apollo.isChecked())

        self.circle_patch_1 = patches.Rectangle((0, 0), 0, 0, facecolor='blue', alpha=0.2, edgecolor='blue',
                                                linewidth=1.5, zorder=5)
        self.circle_patch_2 = patches.Rectangle((0, 0), 0, 0, facecolor='blue', alpha=0.2, edgecolor='blue',
                                                linewidth=1.5, zorder=5)
        self.ax_map.add_patch(self.circle_patch_1);
        self.ax_map.add_patch(self.circle_patch_2)

        self.clear_selection()
        if getattr(self, 'circle_mode_active', False): self.update_circle_patches()
        self.reset_home_view()

    def update_circle_patches(self):
        lon = getattr(self, 'circle_center_lon', 0.0)
        l_min, l_max = lon - 90, lon + 90

        if l_min < -180:
            self.circle_patch_1.set_bounds(l_min + 360, -90, -180 - l_min, 180)
            self.circle_patch_2.set_bounds(-180, -90, l_max - (-180), 180)
            self.circle_patch_1.set_visible(True);
            self.circle_patch_2.set_visible(True)
        elif l_max > 180:
            self.circle_patch_1.set_bounds(l_min, -90, 180 - l_min, 180)
            self.circle_patch_2.set_bounds(-180, -90, l_max - 180, 180)
            self.circle_patch_1.set_visible(True);
            self.circle_patch_2.set_visible(True)
        else:
            self.circle_patch_1.set_bounds(l_min, -90, 180, 180)
            self.circle_patch_1.set_visible(True);
            self.circle_patch_2.set_visible(False)

        self.current_view_extent = [l_min, l_max, -90, 90]

    def toggle_borders(self, checked):
        for artist in self.border_artists: artist.set_visible(checked)
        self.canvas_map.draw_idle()

    def toggle_apollo(self, checked):
        for artist in self.apollo_artists: artist.set_visible(checked)
        self.canvas_map.draw_idle()
        self.render_canvas()

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
        gw, gh = x2 - x1, y2 - y1
        if gw == 0 or gh == 0: return
        mid_lat = np.radians((y1 + y2) / 2)
        tw = gw * np.cos(mid_lat);
        ta = tw / gh
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if ta > self.paper_aspect:
            pad_geo_h, pad_geo_w = tw / self.paper_aspect, gw
        else:
            pad_geo_h, pad_geo_w = gh, (gh * self.paper_aspect) / np.cos(mid_lat)
        ex1, ex2 = cx - pad_geo_w / 2, cx + pad_geo_w / 2
        ey1, ey2 = cy - pad_geo_h / 2, cy + pad_geo_h / 2
        self.current_view_extent = [ex1, ex2, ey1, ey2]
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

        if self.chk_circle.isChecked():
            if self.active_planet == 'Earth':
                R_p = 6371000.0
            elif self.active_planet == 'Moon':
                R_p = 1737400.0
            else:
                R_p = 3389500.0
            export_ax.set_aspect('equal')
            export_ax.set_xlim(-R_p * 1.05, R_p * 1.05)
            export_ax.set_ylim(-R_p * 1.05, R_p * 1.05)
        else:
            mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
            export_ax.set_aspect(1 / np.cos(mid_lat))
            export_ax.set_xlim(self.current_view_extent[0], self.current_view_extent[1])
            export_ax.set_ylim(self.current_view_extent[2], self.current_view_extent[3])

        text_boxes = []
        if self.chk_mask_text.isChecked():
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
        if not self.has_drawn:
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

        text_boxes = []
        if self.chk_mask_text.isChecked():
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

            if self.chk_circle.isChecked():
                if self.active_planet == 'Earth':
                    R_p = 6371000.0
                elif self.active_planet == 'Moon':
                    R_p = 1737400.0
                else:
                    R_p = 3389500.0
                export_ax.set_aspect('equal')
                export_ax.set_xlim(-R_p * 1.05, R_p * 1.05)
                export_ax.set_ylim(-R_p * 1.05, R_p * 1.05)
            else:
                mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
                export_ax.set_aspect(1 / np.cos(mid_lat))
                export_ax.set_xlim(self.current_view_extent[0], self.current_view_extent[1])
                export_ax.set_ylim(self.current_view_extent[2], self.current_view_extent[3])

            has_data = False

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
                f"; --- START ROUTINE: {pen['name']} ---", "G21", "G90", "G28", "G92 X-50.0 Y-50.0 Z-4.0",
                "G0 Z1.0 F1500", "M400"
            ]

            with open(t_gcode, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    if line.startswith("G0 X") or line.startswith("G00 X"):
                        master_gcode.extend(["G0 Z1.0 F1500", "M400", f"{line} F3000", "M400", "G1 Z0.0 F1000", "M400"])
                    elif line.startswith("G1 X") or line.startswith("G01 X"):
                        master_gcode.append(f"{line} F1500")

            master_gcode.extend(["G0 Z1.0 F1500", "M400", "; --- END OF PLOT ---", "G0 X0 Y0 F3000", "G0 Z25.0 F1500",
                                 f"M117 {pen['name']} Complete!", "M84"])

            try:
                with open(final_out_gcode, "w") as f:
                    f.write("\n".join(master_gcode))

                total_time_min = 0.0
                cx, cy, cz = -50.0, -50.0, -4.0
                cf = 1500.0

                for line in master_gcode:
                    line = line.split(';')[0].strip()
                    if not line: continue
                    parts = line.split()
                    cmd = parts[0]

                    if cmd in ['G0', 'G00', 'G1', 'G01']:
                        nx, ny, nz = cx, cy, cz
                        for p in parts[1:]:
                            if p.startswith('X'):
                                nx = float(p[1:])
                            elif p.startswith('Y'):
                                ny = float(p[1:])
                            elif p.startswith('Z'):
                                nz = float(p[1:])
                            elif p.startswith('F'):
                                cf = float(p[1:])

                        dist = np.sqrt((nx - cx) ** 2 + (ny - cy) ** 2 + (nz - cz) ** 2)
                        if cf > 0: total_time_min += dist / cf
                        cx, cy, cz = nx, ny, nz

                total_time_min *= 1.15
                h = int(total_time_min // 60);
                m = int(total_time_min % 60);
                s = int((total_time_min * 60) % 60)
                time_str = f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s"

                exported_files.append(
                    {'path': final_out_gcode, 'name': pen['name'], 'time': time_str, 'mins': total_time_min})

            except Exception as e:
                print(f"Error saving {final_out_gcode}: {e}")

            try:
                if os.path.exists(t_svg): os.remove(t_svg)
                if os.path.exists(t_gcode): os.remove(t_gcode)
            except:
                pass

        if exported_files:
            total_mins = sum(f['mins'] for f in exported_files)
            th = int(total_mins // 60);
            tm = int(total_mins % 60);
            ts = int((total_mins * 60) % 60)
            total_time_str = f"{th}h {tm}m {ts}s" if th > 0 else f"{tm}m {ts}s"

            self.lbl_status.setText(f"Successfully generated {len(exported_files)} files. (Est: {total_time_str})")
            msg = f"Generated {len(exported_files)} G-Code files (Total Est. Time: {total_time_str}):\n\n"
            for f in exported_files: msg += f"• {os.path.basename(f['path'])}  ->  {f['time']}\n"
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.warning(self, "No Data", "No vector elements found for any active pen!")
            self.lbl_status.setText("Ready.")


if __name__ == '__main__':
    if platform.system() == 'Windows':
        import ctypes

        myappid = 'tps.cartographer.studio.1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("icon.jpg"))
    app.setStyle("Fusion")
    window = PlotterApp()
    window.show()
    sys.exit(app.exec())