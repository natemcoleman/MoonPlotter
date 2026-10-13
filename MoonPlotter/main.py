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
                             QProgressBar, QDoubleSpinBox, QGridLayout)
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
from config import (DATA_FILES, PAPER_SIZES, PT_PER_MM, PLANET_RADII, INK_SETS, DEFAULT_INK_SET, MONO_INK_SETS,
                    DEFAULT_MONO_INK_SET, TAHOE, MIN_SEGMENT_MM, DEFAULT_HATCH_SPACING_MM, load_pens, save_pens)
from workers import RenderWorker
from hatch_engine import composite_ink_layers, STRATEGIES
from theme import apply_theme, style_axes, SEPIA, ACCENT, TEXT, MUTED
from ui_widgets import CollapsibleSection, LayerPanel, swatch_icon
from sites import sites_for, CATEGORY_STYLE
from contour_engine import auto_contour_interval
from config import AUTO_CONTOUR_TARGET_LINES

SEL_FILL = SEPIA        # selection rectangle on the source map
SEL_EDGE = SEPIA
ZOOM_FILL = ACCENT
LAYER_LABELS = {'contours': 'Contours', 'borders': 'Borders & outlines', 'sites': 'Points of interest',
                'annotations': 'Text & drawn lines'}

# Rough plotter model for the live time estimate (matches the G-code writer / estimator):
DRAW_FEED = 1500.0      # mm/min (G1)
TRAVEL_FEED = 3000.0    # mm/min (G0)
PEN_UP_DOWN_MIN = 1.0 / 1500.0 + 1.0 / 1000.0   # 1 mm Z up at F1500 + 1 mm down at F1000, per stroke
ACCEL_FACTOR = 1.15


def fmt_minutes(m):
    if m < 1:
        return "< 1 min"
    h, mm = int(m // 60), int(round(m % 60))
    return f"{h}h {mm:02d}m" if h else f"{mm} min"
from matplotlib.backends.backend_agg import FigureCanvasAgg

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def app_icon():
    """Multi-size icon resolved next to this script (not the current working directory)."""
    icon = QIcon()
    for name in ("icon.ico", "icon.png", "icon.jpg"):
        path = os.path.join(APP_DIR, name)
        if os.path.exists(path):
            icon = QIcon(path)
            if not icon.isNull():
                break
    return icon

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

# Natural Earth borders are downloaded once, in contour_engine (shared with the worker).
from contour_engine import HAS_GPD, world_borders, state_borders

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
        self.setWindowIcon(app_icon())
        self.resize(1500, 500)

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
        self.recalc_pending = False
        self.pending_fresh = False
        self.interval_auto_done = False   # an auto interval has been picked for the current selection
        self.layer_visible = {}           # layer key -> bool (missing = visible)
        self.selected_sites = {}          # planet -> set of site names to plot
        self.site_artists = []            # (artist, site tuple) on the source map
        self.site_annot = None
        self.last_relief_ft = None
        self._visible_idx = []            # preview collection index -> dem_segments index
        self._workers = set()      # keeps QThread objects alive until they have fully exited
        self._render_token = 0     # bumped when the selection/planet changes; stale results are dropped
        self.worker = None

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
        # Debounced re-render of the subtractive ink preview (after zoom/pan/resize)
        self.ink_image = None
        self.ink_timer = QTimer()
        self.ink_timer.setSingleShot(True)
        self.ink_timer.timeout.connect(self._refresh_ink_sim)

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

    # ------------------------------------------------------------------
    # UI CONSTRUCTION
    # ------------------------------------------------------------------
    @staticmethod
    def _field(label_text, widget, hint=None):
        """Label above a control (sidebar form row)."""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        lbl = QLabel(label_text)
        lbl.setObjectName("fieldLabel")
        v.addWidget(lbl)
        if isinstance(widget, QHBoxLayout):
            v.addLayout(widget)
        else:
            v.addWidget(widget)
        if hint:
            h = QLabel(hint)
            h.setObjectName("hint")
            h.setWordWrap(True)
            v.addWidget(h)
        return box

    @staticmethod
    def _slider_row(slider, value_label):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(slider, 1)
        value_label.setObjectName("value")
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(value_label)
        return w

    def init_ui(self):
        main_widget = QWidget()
        main_widget.setObjectName("central")
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ================= SIDEBAR =================
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(330)
        side_v = QVBoxLayout(sidebar)
        side_v.setContentsMargins(0, 0, 0, 0)
        side_v.setSpacing(0)

        header = QWidget()
        hv = QVBoxLayout(header)
        hv.setContentsMargins(16, 14, 16, 8)
        hv.setSpacing(0)
        t = QLabel("Topo Plotter Studio")
        t.setObjectName("appTitle")
        st = QLabel("Cartographer Edition")
        st.setObjectName("appSubtitle")
        hv.addWidget(t)
        hv.addWidget(st)
        side_v.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("sidebarBody")
        vbox = QVBoxLayout(body)
        vbox.setContentsMargins(14, 4, 14, 8)
        vbox.setSpacing(4)
        vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)
        side_v.addWidget(scroll, 1)

        # ---------- SOURCE ----------
        sec_src = CollapsibleSection("Source")
        self.radio_earth = QRadioButton("Earth")
        self.radio_moon = QRadioButton("Moon")
        self.radio_mars = QRadioButton("Mars")
        self.radio_earth.setChecked(True)
        mars_files = DATA_FILES['Mars']
        if not (os.path.exists(mars_files['dem']) or os.path.exists(mars_files['color'])):
            self.radio_mars.setEnabled(False)
            self.radio_mars.setToolTip("Download the USGS Mars files into the project folder:\n"
                                       f"{mars_files['dem']}\n{mars_files['color']}")
        for r in (self.radio_earth, self.radio_moon, self.radio_mars):
            r.toggled.connect(lambda checked: checked and self.switch_planet())
        h_planet = QHBoxLayout()
        for r in (self.radio_earth, self.radio_moon, self.radio_mars):
            h_planet.addWidget(r)
        h_planet.addStretch()
        sec_src.add(self._field("Planet", h_planet))

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
        self.lbl_region = sec_src.add(self._field("Clip to region", self.combo_region))

        self.chk_circle = QCheckBox("Circle mode (whole hemisphere)")
        self.chk_circle.toggled.connect(self.toggle_circle_mode)
        sec_src.add(self.chk_circle)
        self.chk_borders = QCheckBox("Show borders on the source map")
        self.chk_borders.setChecked(True)
        self.chk_borders.toggled.connect(self.toggle_borders)
        sec_src.add(self.chk_borders)
        self.chk_sites = QCheckBox("Show points of interest")
        self.chk_sites.setChecked(True)
        self.chk_sites.setToolTip("Landing sites, mountains, craters and other good plot subjects.")
        self.chk_sites.toggled.connect(self.toggle_sites)
        sec_src.add(self.chk_sites)
        self.chk_site_labels = QCheckBox("Label plotted sites")
        self.chk_site_labels.setChecked(True)
        self.chk_site_labels.toggled.connect(self.trigger_recalc)
        sec_src.add(self.chk_site_labels)
        self.lbl_sites_hint = QLabel("Hover a marker for details \u00b7 click to add it to the plot "
                                     "(filled = plotted) \u00b7 double-click to frame the area")
        self.lbl_sites_hint.setObjectName("hint")
        self.lbl_sites_hint.setWordWrap(True)
        sec_src.add(self.lbl_sites_hint)
        vbox.addWidget(sec_src)

        # ---------- PAPER ----------
        sec_paper = CollapsibleSection("Paper")
        self.combo_paper = QComboBox()
        self.combo_paper.addItems(list(PAPER_SIZES.keys()))
        self.combo_paper.currentTextChanged.connect(self.change_paper)
        self.btn_orientation = QPushButton("Rotate")
        self.btn_orientation.setToolTip("Toggle portrait / landscape")
        self.btn_orientation.clicked.connect(self.toggle_orientation)
        h_paper = QHBoxLayout()
        h_paper.addWidget(self.combo_paper, 1)
        h_paper.addWidget(self.btn_orientation)
        sec_paper.add(self._field("Bed / paper size", h_paper))
        self.widget_custom = QWidget()
        h_custom = QHBoxLayout(self.widget_custom)
        h_custom.setContentsMargins(0, 0, 0, 0)
        self.txt_cw = QLineEdit("150")
        self.txt_ch = QLineEdit("150")
        self.txt_cw.textChanged.connect(self.update_paper_dims)
        self.txt_ch.textChanged.connect(self.update_paper_dims)
        h_custom.addWidget(QLabel("W"))
        h_custom.addWidget(self.txt_cw)
        h_custom.addWidget(QLabel("H"))
        h_custom.addWidget(self.txt_ch)
        h_custom.addWidget(QLabel("mm"))
        self.widget_custom.setVisible(False)
        sec_paper.add(self.widget_custom)
        vbox.addWidget(sec_paper)

        # ---------- CONTOURS LAYER ----------
        self.sec_contours = CollapsibleSection("Contours", checkable=True, checked=True)
        self.sec_contours.enabled_changed.connect(self.on_layers_changed)
        self.lbl_usgs_interval = QLabel("Interval: Auto")
        self.lbl_usgs_interval.setObjectName("intervalBadge")
        self.sec_contours.add(self.lbl_usgs_interval)

        self.widget_interval = QWidget()
        h_int = QHBoxLayout(self.widget_interval)
        h_int.setContentsMargins(0, 0, 0, 0)
        self.slider_int = QSlider(Qt.Orientation.Horizontal)
        self.slider_int.setRange(50, 10000)
        self.slider_int.setSingleStep(50)
        self.slider_int.setValue(self.interval_ft)
        self.txt_int = QLineEdit(str(self.interval_ft))
        self.txt_int.setValidator(QIntValidator(1, 50000))
        self.txt_int.setFixedWidth(60)
        self.slider_int.valueChanged.connect(lambda v: self.txt_int.setText(str(v)))
        self.txt_int.textChanged.connect(self.update_interval)
        h_int.addWidget(self.slider_int, 1)
        h_int.addWidget(self.txt_int)
        h_int.addWidget(QLabel("ft"))
        self.sec_contours.add(self._field("Interval override", self.widget_interval))

        self.slider_density = QSlider(Qt.Orientation.Horizontal)
        self.slider_density.setRange(-100, 100)
        self.slider_density.setValue(0)
        self.lbl_density_val = QLabel("\u00d71.0")
        self.density_timer = QTimer()
        self.density_timer.setSingleShot(True)
        self.density_timer.timeout.connect(self.apply_contour_density)
        self.slider_density.valueChanged.connect(
            lambda v: (self.lbl_density_val.setText(f"\u00d7{2 ** (v / 50.0):.1f}"), self.density_timer.start(250)))
        self.sec_contours.add(self._field(
            "Contour density  (sparser \u2190 \u2192 denser)", self._slider_row(self.slider_density, self.lbl_density_val),
            "Picks the nearest standard interval for this selection's relief."))

        self.widget_index = QWidget()
        h_idx = QHBoxLayout(self.widget_index)
        h_idx.setContentsMargins(0, 0, 0, 0)
        self.chk_auto_index = QCheckBox("Index line every")
        self.chk_auto_index.setChecked(True)
        self.spin_idx = QSpinBox()
        self.spin_idx.setRange(2, 20)
        self.spin_idx.setValue(5)
        self.spin_idx.setFixedWidth(52)
        h_idx.addWidget(self.chk_auto_index)
        h_idx.addWidget(self.spin_idx)
        h_idx.addWidget(QLabel("lines"))
        h_idx.addStretch()
        self.chk_auto_index.toggled.connect(self.trigger_recalc)
        self.spin_idx.valueChanged.connect(self.trigger_recalc)
        self.sec_contours.add(self.widget_index)

        self.lbl_smooth_hdr = QLabel("Generalisation")
        self.lbl_smooth_hdr.setObjectName("fieldLabel")
        self.sec_contours.add(self.lbl_smooth_hdr)
        self.widget_presets = QWidget()
        h_presets = QHBoxLayout(self.widget_presets)
        h_presets.setContentsMargins(0, 0, 0, 0)
        for name, simp, smooth in (("Low", 8, 128), ("Medium", 40, 640), ("High", 160, 2560)):
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, a=simp, c=smooth: self.apply_usgs_preset(a, c))
            h_presets.addWidget(b)
        self.sec_contours.add(self.widget_presets)

        self.slider_blur = QSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(0, 50)
        self.lbl_blur_val = QLabel("0.0")
        self.slider_blur.valueChanged.connect(
            lambda v: (self.lbl_blur_val.setText(f"{v / 10.0:.1f}"), self.recalc_timer.start(150)))
        self.widget_blur = self.sec_contours.add(
            self._field("Raster blur σ (px)", self._slider_row(self.slider_blur, self.lbl_blur_val)))

        self.slider_simp = QSlider(Qt.Orientation.Horizontal)
        self.slider_simp.setRange(0, 250)
        self.slider_simp.setValue(40)
        self.lbl_simp_val = QLabel("4.0 m")
        self.slider_simp.valueChanged.connect(
            lambda v: (self.lbl_simp_val.setText(f"{v / 10.0:.1f} m"), self.recalc_timer.start(150)))
        self.widget_simplify = self.sec_contours.add(
            self._field("Simplify tolerance", self._slider_row(self.slider_simp, self.lbl_simp_val)))

        self.slider_smooth = QSlider(Qt.Orientation.Horizontal)
        self.slider_smooth.setRange(0, 3000)
        self.slider_smooth.setValue(640)
        self.lbl_smooth_val = QLabel("64.0 m")
        self.slider_smooth.valueChanged.connect(
            lambda v: (self.lbl_smooth_val.setText(f"{v / 10.0:.1f} m"), self.recalc_timer.start(150)))
        self.widget_smooth = self.sec_contours.add(
            self._field("Smoothing", self._slider_row(self.slider_smooth, self.lbl_smooth_val)))

        self.chk_tanaka = QCheckBox("Illuminated contours (Tanaka)")
        self.chk_tanaka.setToolTip("Thin pen on slopes facing a north-west light, thick pen on the shadowed side.")
        self.chk_tanaka.toggled.connect(self.trigger_recalc)
        self.sec_contours.add(self.chk_tanaka)
        self.chk_ocean = QCheckBox("Plot ocean / crater depths")
        self.chk_ocean.setChecked(True)
        self.chk_ocean.toggled.connect(self.trigger_recalc)
        self.sec_contours.add(self.chk_ocean)
        self.chk_blue_water = QCheckBox("Blue pen below sea level")
        self.chk_blue_water.setChecked(True)
        self.chk_blue_water.toggled.connect(self.trigger_recalc)
        self.sec_contours.add(self.chk_blue_water)
        self.chk_plot_borders = QCheckBox("Plot country / state borders")
        self.chk_plot_borders.setChecked(True)
        self.chk_plot_borders.toggled.connect(self.trigger_recalc)
        self.sec_contours.add(self.chk_plot_borders)
        self.lbl_contour_hdr = self.sec_contours      # kept for older code paths
        vbox.addWidget(self.sec_contours)

        # ---------- FILL / HATCHING LAYER ----------
        self.sec_fill = CollapsibleSection("Fill / Hatching", checkable=True, checked=False)
        self.sec_fill.enabled_changed.connect(self.on_layers_changed)
        self.combo_hatch = QComboBox()
        self.combo_hatch.addItems(STRATEGIES)
        self.combo_hatch.currentTextChanged.connect(lambda _: (self.context_ui_update(), self.recalc_timer.start(150)))
        self.lbl_hatch = self.sec_fill.add(self._field("Pattern", self.combo_hatch))

        self.combo_source = QComboBox()
        self.combo_source.addItems(["Color map", "Hillshade", "Color map × Hillshade"])
        self.combo_source.setToolTip("What drives the tone: the colour image, light/shadow computed from\n"
                                     "the elevation data, or both multiplied together.")
        self.combo_source.currentTextChanged.connect(lambda _: self.recalc_timer.start(150))
        self.lbl_source = self.sec_fill.add(self._field("Tone source", self.combo_source))

        self.combo_inks = QComboBox()
        self.combo_inks.addItems(["Monochrome", "Color separation"])
        self.combo_inks.currentTextChanged.connect(lambda _: (self.context_ui_update(), self.recalc_timer.start(150)))
        self.lbl_inks = self.sec_fill.add(self._field("Inks", self.combo_inks))

        self.combo_mono_pen = QComboBox()
        self.combo_mono_pen.addItems(list(MONO_INK_SETS.keys()))
        self.combo_mono_pen.setCurrentText(DEFAULT_MONO_INK_SET)
        self.combo_mono_pen.currentTextChanged.connect(lambda _: self.recalc_timer.start(150))
        self.lbl_mono_pen = self.sec_fill.add(self._field("Pen", self.combo_mono_pen))

        self.combo_ink_set = QComboBox()
        self.combo_ink_set.addItems(list(INK_SETS.keys()))
        self.combo_ink_set.setCurrentText(DEFAULT_INK_SET)
        self.combo_ink_set.currentTextChanged.connect(lambda _: self.recalc_timer.start(150))
        self.lbl_ink_set = self.sec_fill.add(self._field("Ink set / paper", self.combo_ink_set))

        self.spin_spacing = QDoubleSpinBox()
        self.spin_spacing.setRange(0.3, 5.0)
        self.spin_spacing.setSingleStep(0.1)
        self.spin_spacing.setDecimals(2)
        self.spin_spacing.setSuffix(" mm")
        self.spin_spacing.setValue(DEFAULT_HATCH_SPACING_MM)
        self.spin_spacing.valueChanged.connect(lambda _: self.recalc_timer.start(400))
        self.widget_hatch_opts = self.sec_fill.add(self._field(
            "Line / dot spacing", self.spin_spacing, "Wider spacing gives thick pens more tonal steps."))

        self.slider_tone = QSlider(Qt.Orientation.Horizontal)
        self.slider_tone.setRange(-100, 100)
        self.slider_tone.setValue(0)
        self.lbl_tone_val = QLabel("0")
        self.slider_tone.valueChanged.connect(
            lambda v: (self.lbl_tone_val.setText(f"{v:+d}" if v else "0"), self.recalc_timer.start(300)))
        tone_row = self._slider_row(self.slider_tone, self.lbl_tone_val)
        self.widget_tone = self.sec_fill.add(self._field(
            "Tone  (lighter ← → darker)", tone_row,
            "Gel ink spreads into paper; pull this left if plots come out too dark."))

        self.chk_auto_contrast = QCheckBox("Auto contrast")
        self.chk_auto_contrast.setChecked(True)
        self.chk_auto_contrast.toggled.connect(self.trigger_recalc)
        self.sec_fill.add(self.chk_auto_contrast)
        self.chk_join = QCheckBox("Join neighbouring lines (zigzag)")
        self.chk_join.setChecked(True)
        self.chk_join.setToolTip("Links the end of each stroke to the next line - far fewer pen lifts.")
        self.chk_join.toggled.connect(self.trigger_recalc)
        self.sec_fill.add(self.chk_join)
        vbox.addWidget(self.sec_fill)
        vbox.addStretch()
        # Long combo entries (ink-set names) must not widen the sidebar past its scroll area.
        for cb in body.findChildren(QComboBox):
            cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(8)

        # ---------- FOOTER (always visible) ----------
        footer = QWidget()
        footer.setObjectName("footer")
        fv = QVBoxLayout(footer)
        fv.setContentsMargins(14, 10, 14, 14)
        fv.setSpacing(6)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        fv.addWidget(self.progress_bar)
        self.lbl_status = QLabel("Drag a box on the map to choose an area.")
        self.lbl_status.setObjectName("status")
        self.lbl_status.setWordWrap(True)
        fv.addWidget(self.lbl_status)
        self.btn_draw = QPushButton("Draw Map")
        self.btn_draw.setObjectName("primary")
        self.btn_draw.setShortcut("Ctrl+Return")
        self.btn_draw.setToolTip("Render the selection (Ctrl+Enter)")
        self.btn_draw.clicked.connect(self.draw_action)
        fv.addWidget(self.btn_draw)
        h_exp = QHBoxLayout()
        self.btn_export_svg = QPushButton("Save SVG")
        self.btn_export_svg.clicked.connect(self.export_svg_action)
        self.btn_export = QPushButton("Export G-code")
        self.btn_export.clicked.connect(self.export_action)
        h_exp.addWidget(self.btn_export_svg)
        h_exp.addWidget(self.btn_export)
        fv.addLayout(h_exp)
        side_v.addWidget(footer)

        # ================= WORKSPACE =================
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("workSplit")
        splitter.setChildrenCollapsible(False)

        # --- Source map card ---
        map_card = QFrame()
        map_card.setObjectName("card")
        v_map = QVBoxLayout(map_card)
        v_map.setContentsMargins(10, 10, 10, 8)
        map_title = QLabel("SOURCE MAP")
        map_title.setObjectName("panelTitle")
        v_map.addWidget(map_title)
        self.fig_map = Figure(figsize=(5, 5), dpi=100)
        self.ax_map = self.fig_map.add_subplot(111)
        self.canvas_map = FigureCanvas(self.fig_map)
        v_map.addWidget(self.canvas_map, 1)
        h_toolbar = QHBoxLayout()
        self.btn_home = QPushButton("Reset view")
        self.btn_home.setObjectName("ghost")
        self.btn_home.clicked.connect(self.reset_home_view)
        self.btn_zoom_tool = QPushButton("Zoom box")
        self.btn_zoom_tool.setCheckable(True)
        self.btn_zoom_tool.toggled.connect(self.toggle_zoom_tool)
        self.btn_clear_sel = QPushButton("Clear")
        self.btn_clear_sel.setObjectName("ghost")
        self.btn_clear_sel.clicked.connect(self.clear_selection)
        hint_map = QLabel("Drag to select · scroll to zoom · middle-drag to pan")
        hint_map.setObjectName("hint")
        h_toolbar.addWidget(self.btn_zoom_tool)
        h_toolbar.addWidget(self.btn_home)
        h_toolbar.addWidget(hint_map, 1)
        h_toolbar.addWidget(self.btn_clear_sel)
        v_map.addLayout(h_toolbar)
        splitter.addWidget(map_card)

        # --- Preview card (canvas + tools) and layers panel ---
        prev_card = QFrame()
        prev_card.setObjectName("card")
        prev_outer = QHBoxLayout(prev_card)
        prev_outer.setContentsMargins(10, 10, 10, 8)
        prev_outer.setSpacing(12)
        prev_col = QVBoxLayout()
        prev_title = QLabel("PLOT PREVIEW")
        prev_title.setObjectName("panelTitle")
        prev_col.addWidget(prev_title)
        self.fig_prev = Figure(figsize=(5, 5), dpi=100)
        self.ax_prev = self.fig_prev.add_subplot(111)
        self.canvas_prev = FigureCanvas(self.fig_prev)
        prev_top = QWidget()
        prev_top_l = QVBoxLayout(prev_top)
        prev_top_l.setContentsMargins(0, 0, 0, 0)
        prev_top_l.addWidget(self.canvas_prev, 1)

        h_prev_toolbar = QHBoxLayout()
        self.btn_prev_zoom = QPushButton("Zoom box")
        self.btn_prev_zoom.setCheckable(True)
        self.btn_prev_zoom.toggled.connect(self.toggle_prev_zoom)
        self.btn_prev_home = QPushButton("Fit page")
        self.btn_prev_home.setObjectName("ghost")
        self.btn_prev_home.clicked.connect(self.reset_prev_view)
        hint_prev = QLabel("Scroll to zoom · middle-drag to pan")
        hint_prev.setObjectName("hint")
        h_prev_toolbar.addWidget(self.btn_prev_zoom)
        h_prev_toolbar.addWidget(self.btn_prev_home)
        h_prev_toolbar.addWidget(hint_prev, 1)
        prev_top_l.addLayout(h_prev_toolbar)

        # Tool palette
        self.frame_palette = QWidget()
        v_pal = QVBoxLayout(self.frame_palette)
        v_pal.setContentsMargins(0, 8, 0, 0)
        v_pal.setSpacing(8)
        tools_title = QLabel("EDITING TOOLS \u00b7 PEN")
        tools_title.setObjectName("panelTitle")
        v_pal.addWidget(tools_title)
        h_modes = QHBoxLayout()
        self.mode_group = QButtonGroup()
        modes = [('paint', 'Paint'), ('draw', 'Draw line'), ('text', 'Add text'), ('transform', 'Move / rotate text')]
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
        self.btn_undo = QPushButton("Undo")
        self.btn_undo.setObjectName("ghost")
        self.btn_undo.setToolTip("Ctrl+Z")
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self.undo_action)
        self.btn_redo = QPushButton("Redo")
        self.btn_redo.setObjectName("ghost")
        self.btn_redo.setToolTip("Ctrl+Y")
        self.btn_redo.setEnabled(False)
        self.btn_redo.clicked.connect(self.redo_action)
        self.chk_mask_text = QCheckBox("Blank lines behind text")
        self.chk_mask_text.setChecked(True)
        self.chk_mask_text.toggled.connect(self.render_canvas)
        self.btn_add_pen = QPushButton("+ Pen")
        self.btn_add_pen.setObjectName("ghost")
        self.btn_add_pen.setToolTip("Add a custom pen")
        self.btn_add_pen.clicked.connect(self.add_custom_pen)
        h_tools.addWidget(self.btn_undo)
        h_tools.addWidget(self.btn_redo)
        h_tools.addWidget(self.chk_mask_text)
        h_tools.addStretch()
        h_tools.addWidget(self.btn_add_pen)
        v_pal.addLayout(h_tools)

        self.scroll_pens = QScrollArea()
        self.scroll_pens.setWidgetResizable(True)
        self.scroll_pens.setMinimumHeight(96)
        self.scroll_pens.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_content = QWidget()
        self.v_palette_btns = QGridLayout(self.scroll_content)
        self.v_palette_btns.setContentsMargins(0, 0, 0, 0)
        self.v_palette_btns.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.pen_btn_group = QButtonGroup()
        self.pen_btn_group.buttonClicked.connect(self.on_palette_selected)
        self.scroll_pens.setWidget(self.scroll_content)
        v_pal.addWidget(self.scroll_pens, 1)
        self.rebuild_palette_ui()
        self.update_mode_button_styles()
        self.frame_palette.setMinimumHeight(210)

        # Drag the handle between the preview and the tools to give either more room.
        prev_split = QSplitter(Qt.Orientation.Vertical)
        prev_split.setObjectName("previewSplit")
        prev_split.setChildrenCollapsible(False)
        prev_split.addWidget(prev_top)
        prev_split.addWidget(self.frame_palette)
        prev_split.setStretchFactor(0, 3)
        prev_split.setStretchFactor(1, 2)
        prev_split.setSizes([600, 320])         # proportions; the preview is square-ish, tools get the rest
        prev_col.addWidget(prev_split, 1)
        prev_outer.addLayout(prev_col, 1)

        self.layer_panel = LayerPanel()
        self.layer_panel.setFixedWidth(210)
        self.layer_panel.visibility_changed.connect(self.on_layer_visibility)
        prev_outer.addWidget(self.layer_panel)
        splitter.addWidget(prev_card)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        splitter.setSizes([420, 680])           # map : preview+layers, kept as proportions on resize

        work = QWidget()
        wl = QHBoxLayout(work)
        wl.setContentsMargins(10, 10, 10, 10)
        wl.addWidget(splitter)
        main_layout.addWidget(sidebar)
        main_layout.addWidget(work, 1)

        style_axes(self.fig_map, self.ax_map)
        style_axes(self.fig_prev, self.ax_prev)
        # Let both canvases use their whole card.
        self.fig_map.subplots_adjust(left=0.08, right=0.96, top=0.97, bottom=0.07)
        self.fig_prev.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)

        # Map Canvas Events
        self.selector = RectangleSelector(self.ax_map, self.on_select, useblit=True, button=[1], interactive=True,
                                          props=dict(facecolor=SEL_FILL, edgecolor=SEL_EDGE, alpha=0.25, fill=True,
                                                     linewidth=1.5))
        self.zoom_selector = RectangleSelector(self.ax_map, self.on_zoom_select, useblit=True, button=[1],
                                               interactive=False,
                                               props=dict(facecolor=ZOOM_FILL, edgecolor=ZOOM_FILL, alpha=0.2,
                                                          fill=True))
        self.zoom_selector.set_active(False)
        self.canvas_map.mpl_connect('scroll_event', self.zoom_map)
        self.canvas_map.mpl_connect('button_press_event', self.on_mouse_press_map)
        self.canvas_map.mpl_connect('motion_notify_event', self.on_mouse_motion_map)
        self.canvas_map.mpl_connect('button_release_event', self.on_mouse_release_map)

        # Preview Canvas Events
        self.prev_zoom_selector = RectangleSelector(self.ax_prev, self.on_prev_zoom_select, useblit=True, button=[1],
                                                    interactive=False,
                                                    props=dict(facecolor=ZOOM_FILL, edgecolor=ZOOM_FILL, alpha=0.2,
                                                               fill=True))
        self.prev_zoom_selector.set_active(False)
        self.canvas_prev.mpl_connect('button_press_event', self.on_prev_press)
        self.canvas_prev.mpl_connect('motion_notify_event', self.on_prev_motion)
        self.canvas_prev.mpl_connect('button_release_event', self.on_prev_release)
        self.canvas_prev.mpl_connect('scroll_event', self.on_prev_scroll)
        self.canvas_prev.mpl_connect('pick_event', self.on_prev_pick)
        self.canvas_prev.mpl_connect('resize_event', lambda e: self.schedule_ink_sim(150))

        self.update_paper_dims()
        self.context_ui_update()

    def context_ui_update(self):
        """Shows only the options that apply to the current planet, layers and fill pattern."""
        planet = self.selected_planet()
        is_earth = (planet == 'Earth')
        strategy = self.combo_hatch.currentText()
        is_hachure = "Hachure" in strategy
        is_color = self.combo_inks.currentText() == "Color separation" and not is_hachure

        self.lbl_region.setVisible(is_earth)
        self.chk_borders.setVisible(is_earth)
        self.chk_plot_borders.setVisible(is_earth)
        self.chk_blue_water.setVisible(is_earth)
        self.chk_ocean.setText("Plot ocean depths" if is_earth else "Plot all depths (below datum)")

        self.lbl_source.setVisible(not is_hachure)
        self.lbl_inks.setVisible(not is_hachure)
        self.lbl_ink_set.setVisible(is_color)
        self.lbl_mono_pen.setVisible(not is_color)
        self.chk_auto_contrast.setVisible(not is_hachure)
        self.chk_join.setVisible(not ("Stipp" in strategy or is_hachure))

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
            self.select_tahoe_extent()
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
        # A different region is a different map: re-render fresh (auto interval unless Tahoe).
        self.pending_fresh = True
        self.recalc_timer.start(500)

    def select_tahoe_extent(self):
        """Frames the selection on the lake (padded to the paper aspect) and zooms the map to it."""
        if self.active_planet != 'Earth' or self.chk_circle.isChecked():
            return
        left, bottom, right, top = TAHOE['bounds']
        self.last_drag_coords = (left, right, bottom, top)
        self.process_selection(left, right, bottom, top)
        self.selector.extents = (left, right, bottom, top)
        pad = 0.6
        self.ax_map.set_xlim(left - pad, right + pad)
        self.ax_map.set_ylim(bottom - pad, top + pad)
        self.canvas_map.draw_idle()
        self.lbl_status.setText("Lake Tahoe framed - click Draw Map.")

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
        # Pen paint is stored per segment index; it only applies to the same render it came from.
        if len(state['pens']) == len(self.dem_segments):
            for i, p_id in enumerate(state['pens']):
                self.dem_segments[i]['pen_id'] = p_id
        self.custom_items = copy.deepcopy(state['custom'])
        self.render_canvas()
        self.refresh_layer_panel()

    def rebuild_palette_ui(self):
        while self.v_palette_btns.count():
            item = self.v_palette_btns.takeAt(0)
            if item.widget():
                self.pen_btn_group.removeButton(item.widget())
                item.widget().deleteLater()
        for i, (p_id, pen) in enumerate(self.pens.items()):
            btn = QRadioButton(f"{pen['name']}")
            btn.setIcon(swatch_icon([pen['color']]))
            btn.setToolTip(f"{pen['name']} - {pen['width']} mm")
            btn.setProperty("pen_id", p_id)
            if p_id == self.active_pen_id: btn.setChecked(True)
            self.pen_btn_group.addButton(btn)
            self.v_palette_btns.addWidget(btn, i // 2, i % 2)

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
        # Checked / unchecked looks come from the theme stylesheet (QPushButton:checked).
        for btn in self.mode_group.buttons():
            btn.update()

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
    def on_layers_changed(self, _checked=None):
        self.context_ui_update()
        if self.has_drawn:
            self.recalc_timer.start(100)
        else:
            self.render_canvas()

    def contour_target_lines(self):
        return max(3, int(round(AUTO_CONTOUR_TARGET_LINES * 2 ** (self.slider_density.value() / 50.0))))

    def apply_contour_density(self):
        """Density slider -> nearest standard interval for the last rendered relief."""
        if "Tahoe" in self.combo_region.currentText():
            self.lbl_status.setText("Tahoe uses a fixed 75 ft bathymetry interval - use the override box.")
            return
        if not self.last_relief_ft:
            self.interval_auto_done = False          # the next draw picks the interval with this density
            return
        step = auto_contour_interval(self.last_relief_ft, self.contour_target_lines())
        if not step:
            return
        self.slider_int.blockSignals(True)
        self.txt_int.blockSignals(True)
        self.interval_ft = step
        self.slider_int.setValue(min(max(step, self.slider_int.minimum()), self.slider_int.maximum()))
        self.txt_int.setText(str(step))
        self.slider_int.blockSignals(False)
        self.txt_int.blockSignals(False)
        n = int(self.last_relief_ft // step) + 1
        self.lbl_usgs_interval.setText(f"Interval: Auto \u00b7 {step:,} ft  (~{n} levels)")
        self.interval_auto_done = True
        self.trigger_recalc()

    def tone_gamma(self):
        return float(2.0 ** (self.slider_tone.value() / 100.0))

    def trigger_recalc(self):
        # NOTE: no extra parameters - this slot is connected to toggled(bool)/valueChanged(int),
        # and PyQt would pass those values in as arguments.
        if self.is_calculating:
            self.recalc_pending = True
            return
        if self.has_drawn and self.current_view_extent:
            fresh = self.pending_fresh
            self.pending_fresh = False
            if not fresh:
                self.push_undo_state()
            self._start_render(fresh=fresh)

    def update_interval(self, text):
        try:
            val = int(text)
            if val > 0:
                self.interval_ft = val
                self.slider_int.blockSignals(True)
                self.slider_int.setValue(val)
                self.slider_int.blockSignals(False)
                if "Tahoe" not in self.combo_region.currentText():
                    self.lbl_usgs_interval.setText(f"Interval: Manual ({val} ft)")
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
            'show_apollo': False,
            'sites': sorted(self.selected_sites.get(self.active_planet, set())),
            'site_labels': self.chk_site_labels.isChecked(),
            'contour_target_lines': self.contour_target_lines(),
            'blue_water': self.chk_blue_water.isChecked(),
            'interval_ft': self.interval_ft,
            'auto_index': self.chk_auto_index.isChecked(),
            'index_interval': self.spin_idx.value(),
            'simp_tol': self.slider_simp.value() / 10.0,
            'smooth_val': self.slider_smooth.value() / 10.0,
            'blur_sigma': self.slider_blur.value() / 10.0,
            'layer_contours': self.sec_contours.is_enabled(),
            'layer_fill': self.sec_fill.is_enabled(),
            'force_auto_interval': not self.interval_auto_done,
            'tanaka': self.chk_tanaka.isChecked(),
            'fill_source': self.combo_source.currentText(),
            'fill_inks': 'color' if self.combo_inks.currentText() == "Color separation" else 'mono',
            'tone_gamma': self.tone_gamma(),
            'join_lines': self.chk_join.isChecked(),
            'hatch_strategy': self.combo_hatch.currentText(),
            'ink_set': self.combo_ink_set.currentText(),
            'mono_ink_set': self.combo_mono_pen.currentText(),
            'hatch_spacing_mm': self.spin_spacing.value(),
            'auto_contrast': self.chk_auto_contrast.isChecked(),
            'paper_w_mm': self.pw,
            'paper_h_mm': self.ph,
            'pens': copy.deepcopy(self.pens),
        }

    def draw_action(self):
        """'Draw Map' button: always a fresh render (auto interval, reset view, clear annotations)."""
        if self.is_calculating:
            return
        self._start_render(fresh=True)

    def _start_render(self, fresh):
        if not self.current_view_extent: return
        params = self.get_ui_params()
        dem_path = DATA_FILES[self.active_planet]['dem']
        if self.active_planet == 'Earth' and "Tahoe" in params['selected_region']:
            dem_path = TAHOE['dem']

        if not (params['layer_contours'] or params['layer_fill']):
            self.lbl_status.setText("Turn on the Contours and/or Fill layer first.")
            return
        needs_dem = params['layer_contours'] or (params['layer_fill'] and (
            "Hachure" in params['hatch_strategy'] or "Hillshade" in params['fill_source']))
        if needs_dem and not os.path.exists(dem_path):
            QMessageBox.critical(self, "Missing File", f"Cannot find {dem_path}")
            self.lbl_status.setText("Ready.")
            return

        self.btn_draw.setEnabled(False)
        self.is_calculating = True
        self.progress_bar.setVisible(True)

        worker = RenderWorker(self.active_planet, dem_path, self.current_view_extent, params, fresh=fresh)
        worker.token = self._render_token
        worker.result_ready.connect(self.on_render_success)
        worker.error.connect(self.on_render_error)
        worker.progress.connect(self.on_render_progress)
        worker.finished.connect(self._on_worker_thread_finished)   # QThread's own finished signal
        self._workers.add(worker)
        self.worker = worker
        worker.start()

    def _on_worker_thread_finished(self):
        w = self.sender()
        if w in self._workers:
            self._workers.discard(w)
            w.deleteLater()

    def on_render_progress(self, msg):
        self.lbl_status.setText(msg)

    def _finish_render(self, status):
        self.btn_draw.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.lbl_status.setText(status)
        self.is_calculating = False
        if self.recalc_pending:
            self.recalc_pending = False
            self.recalc_timer.start(100)

    def on_render_success(self, result):
        sender = self.sender()
        if sender is not None and getattr(sender, 'token', self._render_token) != self._render_token:
            # Selection/planet changed while this was computing - discard it.
            self._finish_render("Selection changed - click Draw Map.")
            return
        self.current_dem_crop = result['dem_data']
        if result['dem_data'] is not None and result['max_e'] > result['min_e']:
            self.last_relief_ft = result['max_e'] - result['min_e']
        self.current_data_extent = result['extent']
        self.dem_segments = result['segments']
        fresh = result['fresh']

        step = result.get('auto_interval')
        if step:
            self.slider_int.blockSignals(True)
            self.txt_int.blockSignals(True)
            self.interval_ft = step
            self.slider_int.setValue(min(max(step, self.slider_int.minimum()), self.slider_int.maximum()))
            self.txt_int.setText(str(step))
            self.lbl_usgs_interval.setText(f"Interval: Auto · {step:,} ft  (relief {result['max_e'] - result['min_e']:,.0f} ft)")
            self.interval_auto_done = True
            self.slider_int.blockSignals(False)
            self.txt_int.blockSignals(False)

        self.has_drawn = True
        self._cache_stroke_metrics()
        if fresh:
            self.custom_items = []
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.update_undo_redo_btns()
            self.ax_prev.set_xlim(0.0, 1.0)
            self.ax_prev.set_ylim(0.0, 1.0)
        self.render_canvas()
        self.refresh_layer_panel()

        n = len(self.dem_segments)
        self._finish_render(f"Render complete ({n:,} strokes)." if n else
                            "Render complete - nothing to plot for these settings.")

    def on_render_error(self, err_msg):
        self._finish_render("Ready.")
        QMessageBox.critical(self, "Render Error", err_msg)

    def closeEvent(self, event):
        for w in list(self._workers):
            w.wait()
        super().closeEvent(event)

    def current_paper_color(self):
        if self.sec_fill.is_enabled() and self.combo_inks.currentText() == "Color separation" \
                and "Hachure" not in self.combo_hatch.currentText():
            return INK_SETS.get(self.combo_ink_set.currentText(), INK_SETS[DEFAULT_INK_SET])['paper']
        return '#FFFFFF'

    # --- LAYERS ---
    @staticmethod
    def layer_key(item):
        layer = item.get('layer') or ('borders' if item.get('type') == 'border' else 'contours')
        return f"fill:{item['pen_id']}" if layer == 'fill' else layer

    def is_visible(self, item):
        return self.layer_visible.get(self.layer_key(item), True)

    def annotations_visible(self):
        return self.layer_visible.get('annotations', True)

    def refresh_layer_panel(self):
        groups = {}
        for item in self.dem_segments:
            k = self.layer_key(item)
            g = groups.setdefault(k, {'pens': [], 'n': 0})
            g['n'] += 1
            if item['pen_id'] not in g['pens']:
                g['pens'].append(item['pen_id'])
        if self.custom_items:
            groups['annotations'] = {'pens': list(dict.fromkeys(i['pen_id'] for i in self.custom_items)),
                                     'n': len(self.custom_items)}
        order = ['contours', 'borders'] + sorted(k for k in groups if k.startswith('fill:')) + ['annotations']
        rows = []
        for k in order:
            if k not in groups: continue
            pens = groups[k]['pens']
            colors = [self.pens.get(p, self.pens['base'])['color'] for p in pens][:4]
            if k.startswith('fill:'):
                label = "Fill · " + self.pens.get(k[5:], {'name': k[5:]})['name']
            else:
                label = LAYER_LABELS.get(k, k)
            rows.append((k, label, colors, groups[k]['n']))
        self.layer_panel.set_layers(rows, self.layer_visible)
        self.update_time_estimate()

    def on_layer_visibility(self, key, visible):
        self.layer_visible[key] = visible
        self.render_canvas()
        self.update_time_estimate()

    # --- LIVE PLOT-TIME ESTIMATE ---
    def _cache_stroke_metrics(self):
        """Paper-mm length and end points for every stroke (done once per render)."""
        if not self.dem_segments or not self.current_view_extent:
            return
        sx, sy = self._paper_mm_per_data_unit()
        for item in self.dem_segments:
            seg = np.asarray(item['seg'], dtype=float)
            if len(seg) < 2:
                item['_len_mm'], item['_p0'], item['_p1'] = 0.0, (0.0, 0.0), (0.0, 0.0)
                continue
            d = np.diff(seg, axis=0)
            item['_len_mm'] = float(np.sum(np.hypot(d[:, 0] * sx, d[:, 1] * sy)))
            item['_p0'] = (seg[0, 0] * sx, seg[0, 1] * sy)
            item['_p1'] = (seg[-1, 0] * sx, seg[-1, 1] * sy)

    def estimate_pen_minutes(self, p_id):
        """Same model as the G-code estimator: draw at F1500, travel at F3000, a 1 mm pen lift per stroke,
        +15 % for acceleration. Travel is approximated (vpype re-sorts strokes at export)."""
        p0s, p1s, draw = [], [], 0.0
        for item in self.dem_segments:
            if item['pen_id'] != p_id or not self.is_visible(item) or '_len_mm' not in item:
                continue
            L = item['_len_mm']
            if L < MIN_SEGMENT_MM and item.get('type') != 'stipple':
                continue                                 # dropped by the export filter
            draw += L
            p0s.append(item['_p0'])
            p1s.append(item['_p1'])
        if self.annotations_visible():
            sx, sy = self._paper_mm_per_data_unit()
            for item in self.custom_items:
                if item['pen_id'] != p_id:
                    continue
                pts = np.asarray(item['points']) if item['type'] == 'line' else None
                if pts is not None and len(pts) > 1:
                    d = np.diff(pts, axis=0)
                    draw += float(np.sum(np.hypot(d[:, 0] * sx, d[:, 1] * sy)))
                    p0s.append((pts[0, 0] * sx, pts[0, 1] * sy))
                    p1s.append((pts[-1, 0] * sx, pts[-1, 1] * sy))
                elif item['type'] == 'text':
                    draw += 12.0 * len(item['text']) * item['scale'] * sx / 3.0   # rough outline length
        n = len(p0s)
        if n == 0:
            return 0.0
        p0, p1 = np.asarray(p0s), np.asarray(p1s)
        seq = float(np.sum(np.hypot(*(p0[1:] - p1[:-1]).T))) if n > 1 else 0.0
        bound = 0.6 * np.sqrt(self.pw * self.ph * n)
        travel = min(seq, bound) + np.hypot(self.pw, self.ph)           # + trips to/from home
        return (draw / DRAW_FEED + travel / TRAVEL_FEED + n * PEN_UP_DOWN_MIN) * ACCEL_FACTOR

    def update_time_estimate(self):
        if not self.has_drawn:
            self.layer_panel.set_order([], '')
            return
        rows, total = [], 0.0
        for p_id in self.plot_order_pens():
            m = self.estimate_pen_minutes(p_id)
            total += m
            t = fmt_minutes(m)
            rows.append((self.pens[p_id]["name"], t if t.startswith("<") else "\u2248 " + t))
        self.layer_panel.set_order(rows, fmt_minutes(total))

    def plot_order_pens(self):
        """Pens that have visible strokes, lightest ink first (dark inks last so they sit on top
        and wet light ink is never dragged through dark ink)."""
        used = []
        for item in self.dem_segments:
            if self.is_visible(item) and item['pen_id'] not in used:
                used.append(item['pen_id'])
        if self.annotations_visible():
            for item in self.custom_items:
                if item['pen_id'] not in used:
                    used.append(item['pen_id'])
        used = [p for p in used if p in self.pens]

        def lightness(p):
            h = self.pens[p]['color'].lstrip('#')
            r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b
        return sorted(used, key=lightness, reverse=True)

    # --- HIGH PERFORMANCE RENDERER ---
    def render_canvas(self):
        xlim, ylim = self.ax_prev.get_xlim(), self.ax_prev.get_ylim()
        is_fresh = (xlim == (0.0, 1.0) and ylim == (0.0, 1.0))

        self.ax_prev.clear()
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])
        style_axes(self.fig_prev, self.ax_prev)

        # Show the paper colour of the active ink set
        self.ax_prev.set_facecolor(self.current_paper_color())

        if not self.has_drawn or not self.current_view_extent:
            self.canvas_prev.draw_idle()
            return

        mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
        self.ax_prev.set_aspect(1 / np.cos(mid_lat))

        self._visible_idx = [i for i, item in enumerate(self.dem_segments) if self.is_visible(item)]
        self.dem_collection = None
        if self._visible_idx:
            vis = [self.dem_segments[i] for i in self._visible_idx]
            segs = [item['seg'] for item in vis]
            colors = [self.pens.get(item['pen_id'], self.pens['base'])['color'] for item in vis]
            lws = [self.pens.get(item['pen_id'], self.pens['base'])['width'] * PT_PER_MM for item in vis]
            self.dem_collection = LineCollection(segs, colors=colors, linewidths=lws, picker=True, pickradius=5,
                                                 zorder=10)
            if self.is_ink_sim_mode():
                # Hatching is shown by the subtractive ink simulation image (see _refresh_ink_sim).
                # This collection stays as an invisible layer so Paint-mode clicking still works.
                self.dem_collection.set_alpha(0.0)
            self.ax_prev.add_collection(self.dem_collection)

        for idx, item in enumerate(getattr(self, 'custom_items', []) if self.annotations_visible() else []):
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
                R = PLANET_RADII.get(self.active_planet, 6371000.0)
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

        self.ink_image = None                 # ax.clear() above removed the old one
        self.canvas_prev.draw_idle()
        self.schedule_ink_sim(0)

    # --- SUBTRACTIVE INK PREVIEW (hatch modes) ---
    def is_ink_sim_mode(self):
        """True-to-paper ink preview is used whenever a fill layer is showing (contour-only plots keep
        the classic, easier-to-read line view)."""
        return self.has_drawn and any(item.get('layer') == 'fill' and self.is_visible(item)
                                      for item in self.dem_segments)

    def schedule_ink_sim(self, delay_ms=80):
        if self.is_ink_sim_mode():
            self.ink_timer.start(delay_ms)

    def _paper_mm_per_data_unit(self):
        """(mm per data unit in x, in y) for the current page."""
        if self.chk_circle.isChecked():
            k = self._paper_mm_per_data_unit_x()
            return k, k
        e = self.current_view_extent
        return self.pw / (e[1] - e[0]), self.ph / (e[3] - e[2])

    def _paper_mm_per_data_unit_x(self):
        if self.chk_circle.isChecked():
            R = PLANET_RADII.get(self.active_planet, 6371000.0)
            return min(self.pw, self.ph) / (2.1 * R)
        return self.pw / (self.current_view_extent[1] - self.current_view_extent[0])

    def _refresh_ink_sim(self):
        """Renders each pen's strokes to its own off-screen layer at true paper line width and
        multiplies the layers together (plus the paper colour), the way translucent inks combine."""
        if self.ink_image is not None:
            try:
                self.ink_image.remove()
            except Exception:
                pass
            self.ink_image = None
        if not self.is_ink_sim_mode() or not self.dem_segments or not self.current_view_extent:
            return

        ax = self.ax_prev
        ax.apply_aspect()
        bb = ax.get_window_extent()
        w, h = int(round(bb.width)), int(round(bb.height))
        if w < 4 or h < 4:
            return
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        dpi = self.fig_prev.dpi
        px_per_mm = (w / abs(xlim[1] - xlim[0])) / self._paper_mm_per_data_unit_x()
        pt_per_mm = px_per_mm * 72.0 / dpi

        by_pen = {}
        for item in self.dem_segments:
            if self.is_visible(item):
                by_pen.setdefault(item['pen_id'], []).append(item['seg'])

        off_fig = Figure(figsize=(w / dpi, h / dpi), dpi=dpi, facecolor='white')
        off_canvas = FigureCanvasAgg(off_fig)
        off_ax = off_fig.add_axes([0, 0, 1, 1])
        off_ax.axis('off')
        off_ax.set_xlim(xlim)
        off_ax.set_ylim(ylim)
        layers = []
        for p_id, segs in by_pen.items():
            pen = self.pens.get(p_id, self.pens['base'])
            lw = float(pen.get('line_mm', pen['width'])) * pt_per_mm
            coll = off_ax.add_collection(LineCollection(segs, colors=pen['color'], linewidths=lw,
                                                        capstyle='round'))
            off_canvas.draw()
            layers.append(np.asarray(off_canvas.buffer_rgba())[..., :3].copy())
            coll.remove()

        img = composite_ink_layers(layers, self.current_paper_color())
        if img is None:
            return
        self.ink_image = ax.imshow(img, extent=(xlim[0], xlim[1], ylim[0], ylim[1]), origin='upper',
                                   aspect=ax.get_aspect(), interpolation='nearest', zorder=5)
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)
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
        self.prev_zoom_selector.set_active(checked)

    def on_prev_zoom_select(self, eclick, erelease):
        x1, x2 = sorted([eclick.xdata, erelease.xdata]);
        y1, y2 = sorted([eclick.ydata, erelease.ydata])
        self.ax_prev.set_xlim(x1, x2);
        self.ax_prev.set_ylim(y1, y2)
        self.canvas_prev.draw_idle();
        self.btn_prev_zoom.setChecked(False)
        self.schedule_ink_sim()

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
                # Use the preview axes width so this also works in circle mode (metres, not degrees).
                xl = self.ax_prev.get_xlim()
                init_scale = abs(xl[1] - xl[0]) * 0.05
                self.custom_items.append({'type': 'text', 'text': text, 'x': event.xdata, 'y': event.ydata,
                                          'rot': 0.0, 'scale': init_scale, 'pen_id': self.active_pen_id})
                self.render_canvas()
                self.refresh_layer_panel()

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
        if event.button == 2:
            self.prev_pan_active = False
            self.schedule_ink_sim()
            return
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
            self.refresh_layer_panel()

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
        self.schedule_ink_sim(150)

    def on_prev_pick(self, event):
        if event.mouseevent.button != 1 or self.canvas_mode != 'paint' or self.btn_prev_zoom.isChecked(): return
        artist = event.artist
        if self.dem_collection is not None and artist == self.dem_collection:
            idx = self._visible_idx[event.ind[0]]
            target = self.dem_segments[idx]
            target_level, target_layer = target['level'], self.layer_key(target)
            shift_held = event.mouseevent.key == 'shift'
            self.push_undo_state()
            for i, item in enumerate(self.dem_segments):
                if (shift_held and item['level'] == target_level and self.layer_key(item) == target_layer) \
                        or (not shift_held and i == idx):
                    item['pen_id'] = self.active_pen_id
            self.render_canvas()
            self.refresh_layer_panel()
            return
        if hasattr(artist, 'item_ref'):
            group, idx = artist.item_ref
            if group == 'custom':
                self.push_undo_state()
                self.custom_items[idx]['pen_id'] = self.active_pen_id
                self.render_canvas()
                self.refresh_layer_panel()

    # --- MAP LOGIC (Panning & Zooming) ---
    def reset_home_view(self):
        self.ax_map.set_xlim(-180, 180);
        self.ax_map.set_ylim(-90, 90)
        self.canvas_map.draw_idle()

    def clear_selection(self):
        self._render_token += 1
        self.last_relief_ft = None
        self.interval_auto_done = False
        self.last_drag_coords = None
        self.current_view_extent = None
        self.has_drawn = False
        self.selector.extents = (-200, -200, -100, -100)
        self.ax_prev.clear();
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])
        style_axes(self.fig_prev, self.ax_prev)
        self.canvas_prev.draw_idle()
        self.canvas_map.draw_idle()
        self.custom_items = []
        self.dem_segments = []
        self.undo_stack.clear();
        self.redo_stack.clear()
        self.update_undo_redo_btns()
        self.ink_image = None
        self.layer_panel.set_layers([], {})
        self.layer_panel.set_order([], '')

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

    def on_zoom_select(self, eclick, erelease):
        x1, x2 = sorted([eclick.xdata, erelease.xdata]);
        y1, y2 = sorted([eclick.ydata, erelease.ydata])
        self.ax_map.set_xlim(x1, x2);
        self.ax_map.set_ylim(y1, y2)
        self.canvas_map.draw_idle();
        self.btn_zoom_tool.setChecked(False)

    def on_mouse_press_map(self, event):
        if event.button == 1:
            hit = self._site_at(event)
            if hit is not None:
                if event.dblclick:
                    self.frame_site(hit[1])
                else:
                    self.toggle_site(hit[1])
                return
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
            return
        elif getattr(self, 'circle_dragging', False) and event.inaxes == self.ax_map:
            dx = event.xdata - self.circle_drag_start
            self.circle_center_lon = (self.circle_start_lon + dx + 180) % 360 - 180
            if hasattr(self, 'bg_cache'):
                self.canvas_map.restore_region(self.bg_cache)
                self.update_circle_patches()
                if self.circle_patch_1.get_visible(): self.ax_map.draw_artist(self.circle_patch_1)
                if self.circle_patch_2.get_visible(): self.ax_map.draw_artist(self.circle_patch_2)
                self.canvas_map.blit(self.ax_map.bbox)
            return
        if event.button is None:
            self._update_site_hover(event)

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

    def selected_planet(self):
        if self.radio_mars.isChecked():
            return 'Mars'
        return 'Moon' if self.radio_moon.isChecked() else 'Earth'

    def switch_planet(self):
        self.active_planet = self.selected_planet()
        self.context_ui_update()

        _, base_img = load_planet_data(self.active_planet)
        self.ax_map.clear()
        self.ax_map.imshow(base_img, extent=[-180, 180, -90, 90], origin='upper', zorder=0)
        style_axes(self.fig_map, self.ax_map)
        self.ax_map.set_xticks(range(-180, 181, 60))
        self.ax_map.set_yticks(range(-90, 91, 30))

        # Preload Geopandas Borders invisibly
        self.border_artists = []
        if HAS_GPD and self.active_planet == 'Earth':
            world_borders.boundary.plot(ax=self.ax_map, edgecolor='#F4E9CF', linewidth=0.6, alpha=0.7, zorder=2)
            state_borders.boundary.plot(ax=self.ax_map, edgecolor='#F4E9CF', linewidth=0.25, alpha=0.45, zorder=2)
            self.border_artists.extend(self.ax_map.collections)
            self.ax_map.set_xlabel('')     # geopandas adds CRS axis labels; the card title says enough
            self.ax_map.set_ylabel('')
        for artist in self.border_artists:
            artist.set_visible(self.chk_borders.isChecked())

        # Points of interest (hover = details, click = plot it, double-click = frame it)
        self.build_site_markers()

        self.circle_patch_1 = patches.Rectangle((0, 0), 0, 0, facecolor=ACCENT, alpha=0.22, edgecolor=ACCENT,
                                                linewidth=1.5, zorder=5)
        self.circle_patch_2 = patches.Rectangle((0, 0), 0, 0, facecolor=ACCENT, alpha=0.22, edgecolor=ACCENT,
                                                linewidth=1.5, zorder=5)
        self.ax_map.add_patch(self.circle_patch_1);
        self.ax_map.add_patch(self.circle_patch_2)

        self.clear_selection()
        if self.chk_circle.isChecked(): self.update_circle_patches()
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

    # --- POINTS OF INTEREST ---
    def build_site_markers(self):
        self.site_artists = []
        chosen = self.selected_sites.setdefault(self.active_planet, set())
        for site in sites_for(self.active_planet):
            name, lat, lon, cat, span, note = site
            marker, color = CATEGORY_STYLE.get(cat, ('o', '#E8B04B'))
            plotted = name in chosen
            art, = self.ax_map.plot(lon, lat, marker=marker, markersize=8 if plotted else 7,
                                    markerfacecolor=color if plotted else 'none',
                                    markeredgecolor=color if not plotted else '#2A2824',
                                    markeredgewidth=1.6, linestyle='none', zorder=12)
            art.set_visible(self.chk_sites.isChecked())
            self.site_artists.append((art, site))
        self.site_annot = self.ax_map.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords='offset points', zorder=30, fontsize=8.5,
            color='#2A2824', multialignment='left',
            bbox=dict(boxstyle='round,pad=0.5', fc='#FFFDF9', ec='#D8D1C4', lw=1),
            annotation_clip=False)
        self.site_annot.set_visible(False)

    def _site_at(self, event, radius_px=9):
        if not self.chk_sites.isChecked() or event.inaxes != self.ax_map or event.x is None:
            return None
        best, best_d = None, radius_px
        for art, site in self.site_artists:
            px, py = self.ax_map.transData.transform((site[2], site[1]))
            d = np.hypot(px - event.x, py - event.y)
            if d < best_d:
                best, best_d = (art, site), d
        return best

    def _update_site_hover(self, event):
        hit = self._site_at(event)
        if hit is None:
            if self.site_annot is not None and self.site_annot.get_visible():
                self.site_annot.set_visible(False)
                self.canvas_map.draw_idle()
            return
        art, (name, lat, lon, cat, span, note) = hit
        plotted = name in self.selected_sites.get(self.active_planet, set())
        ns = f"{abs(lat):.2f}\u00b0{'N' if lat >= 0 else 'S'}"
        ew = f"{abs(lon):.2f}\u00b0{'E' if lon >= 0 else 'W'}"
        action = "\u2713 On the plot - click to remove" if plotted else "Click to add to the plot"
        text = f"{name}\n{cat} \u00b7 {ns} {ew}\n{note}\n{action} \u00b7 double-click to frame"
        self.site_annot.xy = (lon, lat)
        # keep the tooltip inside the axes: flip it to the left / below near the right / top edge
        x0, x1 = self.ax_map.get_xlim()
        y0, y1 = self.ax_map.get_ylim()
        dx = -12 if lon > x0 + 0.6 * (x1 - x0) else 12
        dy = -12 if lat > y0 + 0.6 * (y1 - y0) else 12
        self.site_annot.set_position((dx, dy))
        self.site_annot.set_ha('right' if dx < 0 else 'left')
        self.site_annot.set_va('top' if dy < 0 else 'bottom')
        self.site_annot.set_text(text)
        self.site_annot.set_visible(True)
        self.canvas_map.draw_idle()

    def toggle_site(self, site):
        chosen = self.selected_sites.setdefault(self.active_planet, set())
        name = site[0]
        if name in chosen:
            chosen.discard(name)
        else:
            chosen.add(name)
        for art, s_ in self.site_artists:
            if s_[0] == name:
                marker, color = CATEGORY_STYLE.get(s_[3], ('o', '#E8B04B'))
                on = name in chosen
                art.set_markerfacecolor(color if on else 'none')
                art.set_markeredgecolor('#2A2824' if on else color)
                art.set_markersize(8 if on else 7)
        self.canvas_map.draw_idle()
        verb = "Added" if name in chosen else "Removed"
        self.lbl_status.setText(f"{verb} {name}." + ("" if self.has_drawn else " It will appear when you draw."))
        if self.has_drawn:
            self.trigger_recalc()

    def frame_site(self, site):
        name, lat, lon, cat, span, note = site
        if self.chk_circle.isChecked():
            self.circle_center_lon = lon
            self.update_circle_patches()
            self.canvas_map.draw_idle()
            return
        half_w = span / 2.0 / max(np.cos(np.radians(lat)), 0.2)
        half_h = span / 2.0
        x1, x2 = lon - half_w, lon + half_w
        y1, y2 = max(-90.0, lat - half_h), min(90.0, lat + half_h)
        self.last_drag_coords = (x1, x2, y1, y2)
        self.process_selection(x1, x2, y1, y2)
        self.selector.set_visible(True)
        self.selector.extents = (x1, x2, y1, y2)
        pad_x, pad_y = (x2 - x1) * 1.5, (y2 - y1) * 1.5
        self.ax_map.set_xlim(max(-180, x1 - pad_x), min(180, x2 + pad_x))
        self.ax_map.set_ylim(max(-90, y1 - pad_y), min(90, y2 + pad_y))
        self.canvas_map.draw_idle()
        self.lbl_status.setText(f"Framed {name} - click Draw Map.")

    def toggle_sites(self, checked):
        for art, _ in self.site_artists:
            art.set_visible(checked)
        if not checked and self.site_annot is not None:
            self.site_annot.set_visible(False)
        self.canvas_map.draw_idle()

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
        tiny = abs(erelease.x - eclick.x) < 4 or abs(erelease.y - eclick.y) < 4
        if tiny:
            # A click (e.g. on a point of interest), not a drag: keep the existing selection.
            if self.last_drag_coords:
                self.selector.set_visible(True)
                self.selector.extents = self.last_drag_coords
                self.canvas_map.draw_idle()
            return
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
        self._render_token += 1
        self.last_relief_ft = None
        self.interval_auto_done = False
        self.has_drawn = False
        self.ax_prev.clear();
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])
        style_axes(self.fig_prev, self.ax_prev)
        self.canvas_prev.draw_idle()
        self.ink_image = None
        self.layer_panel.set_layers([], {})
        self.layer_panel.set_order([], '')
        self.lbl_status.setText("Selection captured - click Draw Map.")

    # --- EXPORT PIPELINES ---
    def _text_mask_boxes(self):
        boxes = []
        if self.chk_mask_text.isChecked():
            for item in getattr(self, 'custom_items', []):
                if item['type'] == 'text':
                    tp = TextPath((0, 0), item['text'], size=1)
                    trans = Affine2D().scale(item['scale']).rotate_deg(item['rot']).translate(item['x'], item['y'])
                    bbox = tp.transformed(trans).get_extents()
                    pad = item['scale'] * 0.2
                    boxes.append(shp_box(bbox.x0 - pad, bbox.y0 - pad, bbox.x1 + pad, bbox.y1 + pad))
        return boxes

    @staticmethod
    def _mask_line(coords, text_boxes):
        coords = np.asarray(coords, dtype=float)
        if len(coords) < 2: return []
        if not text_boxes: return [coords]
        geoms = [LineString(coords)]
        for tbox in text_boxes:
            nxt = []
            for g in geoms:
                diff = g.difference(tbox)
                if diff.is_empty: continue
                if diff.geom_type == 'LineString':
                    nxt.append(diff)
                elif diff.geom_type == 'MultiLineString':
                    nxt.extend(diff.geoms)
            geoms = nxt
        return [np.asarray(g.coords) for g in geoms if len(g.coords) >= 2]

    def _lines_for_pen(self, p_id, text_boxes):
        """All plot lines (in data coords) for one pen, with text blanking applied."""
        lines = []
        for item in getattr(self, 'dem_segments', []):
            if item.get('pen_id') == p_id and self.is_visible(item):
                if item.get('type') == 'stipple':
                    lines.append(np.asarray(item['seg']))        # dots are never blanked or merged
                else:
                    lines.extend(self._mask_line(item['seg'], text_boxes))
        for item in (getattr(self, 'custom_items', []) if self.annotations_visible() else []):
            if item.get('pen_id') != p_id: continue
            if item['type'] == 'line':
                lines.extend(self._mask_line(item['points'], text_boxes))
            elif item['type'] == 'text':
                tp = TextPath((0, 0), item['text'], size=1)
                trans = Affine2D().scale(item['scale']).rotate_deg(item['rot']).translate(item['x'], item['y'])
                lines.extend(np.asarray(poly) for poly in tp.transformed(trans).to_polygons())
        return lines

    def _make_export_axes(self):
        export_fig = Figure(figsize=(self.pw / 25.4, self.ph / 25.4))
        export_ax = export_fig.add_axes([0, 0, 1, 1])
        export_ax.axis('off')
        if self.chk_circle.isChecked():
            R_p = PLANET_RADII.get(self.active_planet, 6371000.0)
            export_ax.set_aspect('equal')
            export_ax.set_xlim(-R_p * 1.05, R_p * 1.05)
            export_ax.set_ylim(-R_p * 1.05, R_p * 1.05)
        else:
            mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
            export_ax.set_aspect(1 / np.cos(mid_lat))
            export_ax.set_xlim(self.current_view_extent[0], self.current_view_extent[1])
            export_ax.set_ylim(self.current_view_extent[2], self.current_view_extent[3])
        return export_fig, export_ax

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

        export_fig, export_ax = self._make_export_axes()
        text_boxes = self._text_mask_boxes()
        for p_id in self.plot_order_pens():
            pen = self.pens[p_id]
            lines = self._lines_for_pen(p_id, text_boxes)
            if lines:
                export_ax.add_collection(LineCollection(lines, colors=pen['color'],
                                                        linewidths=pen['width'] * PT_PER_MM, label=pen['name']))

        export_fig.savefig(out_svg, format='svg')
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

        text_boxes = self._text_mask_boxes()

        # Lightest ink first, darkest last; the number prefix is the order to load the pens.
        for order_no, p_id in enumerate(self.plot_order_pens(), start=1):
            pen = self.pens[p_id]
            safe_pen_name = "".join([c if c.isalnum() else "_" for c in pen['name']])
            safe_pen_name = re.sub(r'_+', '_', safe_pen_name).strip('_')
            final_out_gcode = f"{dir_path}/{order_no:02d}_{safe_pen_name}_{base_name}.gcode"

            t_svg = os.path.join(temp_dir, f"temp_{p_id}.svg").replace("\\", "/")
            t_gcode = os.path.join(temp_dir, f"temp_{p_id}.gcode").replace("\\", "/")

            lines = self._lines_for_pen(p_id, text_boxes)
            has_dots = any(item.get('type') == 'stipple' and item['pen_id'] == p_id and self.is_visible(item)
                           for item in self.dem_segments)
            if has_dots:
                # vpype's length filter / merge would delete or join the tiny stipple circles, so for
                # this pen the short-stroke filter is applied here to everything except the dots.
                sx, sy = self._paper_mm_per_data_unit()
                kept = []
                for ln in lines:
                    ln = np.asarray(ln)
                    is_dot = len(ln) == 7 and np.allclose(ln[0], ln[-1])
                    length = np.sum(np.hypot(np.diff(ln[:, 0]) * sx, np.diff(ln[:, 1]) * sy))
                    if is_dot or length >= MIN_SEGMENT_MM:
                        kept.append(ln)
                lines = kept
            if not lines:
                continue

            self.lbl_status.setText(f"Optimising {pen['name']} ({len(lines):,} strokes)...")
            QApplication.processEvents()
            export_fig, export_ax = self._make_export_axes()
            export_ax.add_collection(LineCollection(lines, colors='black', linewidths=1))
            export_fig.savefig(t_svg, format='svg')

            try:
                if has_dots:
                    vpype_cmd = f'read "{t_svg}" linesort gwrite --profile gcodemm "{t_gcode}"'
                else:
                    vpype_cmd = (f'read "{t_svg}" linesimplify -t 0.1mm linemerge -t 0.3mm '
                                 f'filter --min-length {MIN_SEGMENT_MM:g}mm linesort gwrite --profile gcodemm "{t_gcode}"')
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
            msg = (f"Generated {len(exported_files)} G-Code files (Total Est. Time: {total_time_str}).\n"
                   f"Plot them in this order - lightest ink first:\n\n")
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
    app.setWindowIcon(app_icon())
    apply_theme(app)
    window = PlotterApp()
    window.showMaximized()
    sys.exit(app.exec())