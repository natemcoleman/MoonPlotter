import sys
import os
import json
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
                             QProgressBar, QDoubleSpinBox, QGridLayout, QDialog, QListWidget,
                             QListWidgetItem, QToolButton, QMenu, QTableWidget, QTableWidgetItem,
                             QAbstractItemView, QHeaderView)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread, QUrl, QStandardPaths, QObject, QEvent
from PyQt6.QtGui import (QDoubleValidator, QIntValidator, QShortcut, QKeySequence, QIcon, QAction, QColor,
                         QDesktopServices, QFont)

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
from config import (DATA_FILES, PAPER_SIZES, PT_PER_MM, PLANET_RADII, PAPER_COLORS, DEFAULT_PAPER, DEFAULT_MONO_PEN,
                    TAHOE, MIN_SEGMENT_MM, DEFAULT_HATCH_SPACING_MM, load_pens, save_pens, pen_available,
                    pen_opacity, ink_lightness, TOOL_PEN_IDS, FAVORITE_PAPER_COLORS, pen_favorite,
                    FAVORITE_REGIONS, REGION_LAKES, SCALE_PRESETS)
from workers import RenderWorker
from hatch_engine import composite_ink_layers, STRATEGIES
from theme import apply_theme, style_axes, SEPIA, ACCENT, TEXT, MUTED
from ui_widgets import CollapsibleSection, LayerPanel, swatch_icon, WheelGuard, SwatchCombo
from sites import sites_for, CATEGORY_STYLE
import exporter
import regions
from decorations import build_decorations, TEXT_SCALES, LABEL_CAP_MM, min_cap_for_pen, PaperFrame
from stroke_font import stroke_text
from calibration import build_calibration_sheet
import missions as MS
from mission_plot import build_mission_plot, PATH_STYLES
import image_trace as IT
from export_dialog import ExportDialog
from pen_dialog import PenManagerDialog
from data_dialog import DataDialog
import project_io
from contour_engine import auto_contour_interval
from config import AUTO_CONTOUR_TARGET_LINES

SEL_FILL = SEPIA        # selection rectangle on the source map
SEL_EDGE = SEPIA
ZOOM_FILL = ACCENT
LAYER_LABELS = {'contours': 'Contours', 'borders': 'Borders & outlines', 'sites': 'Points of interest',
                'decor': 'Map furniture', 'calibration': 'Calibration sheet',
                'orbits': 'Planet orbits', 'trajectory': 'Trajectories', 'markers': 'Sun, planets & year marks',
                'belt': 'Asteroid belt', 'labels': 'Labels & title',
                'annotations': 'Text & drawn lines', 'img:cut': 'Sticker cut line',
                'ski_runs': 'Ski runs', 'ski_lifts': 'Ski lifts'}

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
os.chdir(APP_DIR)                  # data, pens, presets and projects are all relative to the app folder
PROJECTS_DIR = os.path.join(APP_DIR, "projects")
ETOPO_URL = "https://www.ncei.noaa.gov/products/etopo-global-relief-model"


def desktop_dir():
    """The user's Desktop (follows OneDrive redirection), falling back to the home folder."""
    try:
        d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    except Exception:
        d = ""
    return d if d and os.path.isdir(d) else os.path.expanduser("~")


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
from contour_engine import HAS_GPD, world_borders, state_borders, BORDERS_ERROR, NATURAL_EARTH_DIR, \
    NATURAL_EARTH_FILES
import dem_prep
import ski
import svg_import
from workers import clear_dem_cache

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
class _MissionFetch(QThread):
    """Downloads mission trajectories from JPL Horizons in the background."""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, mission_list):
        super().__init__()
        self.mission_list = mission_list

    def run(self):
        out = {}
        try:
            for m in self.mission_list:
                out[m['key']] = MS.fetch_mission(m, progress=self.progress.emit)
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.done.emit(out)


class _SkiFetch(QThread):
    """Loads a ski resort (OpenStreetMap) and, if needed, 30 m elevation for it, in the background."""
    done = pyqtSignal(str, object, str)
    failed = pyqtSignal(str, str)
    progress = pyqtSignal(str)

    def __init__(self, resort):
        super().__init__()
        self.resort = resort

    def run(self):
        try:
            data = ski.load(self.resort, progress=self.progress.emit)
        except Exception as e:
            self.failed.emit(self.resort, f"{type(e).__name__}: {e}")
            return
        note = ""
        ext = list(ski.dem_extent(data))       # roomy box: panning, zooming out or tiling stays covered
        try:
            if regions.best_region('Earth', 'dem', ext, min_coverage=0.99) is None:
                src = next(s for s in regions.REMOTE_SOURCES['Earth'] if s.get('tiles') == 'copernicus30')
                self.progress.emit(f"Fetching 30 m elevation for {self.resort} (one time)...")
                regions.fetch_region(src, 'Earth', ext, "ski_" + ski.slug(self.resort), progress=self.progress.emit)
                note = "30 m elevation fetched for it."
        except Exception as e:
            note = f"(30 m elevation not fetched: {e} - contours use the coarse global data.)"
        self.done.emit(self.resort, data, note)


class _DemPrep(QThread):
    """Builds elevation overviews in the background (Tools > Speed up global elevation)."""
    done = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, paths):
        super().__init__()
        self.paths = paths

    def run(self):
        msgs = []
        for p in self.paths:
            try:
                dem_prep.build_overviews(p, log=self.progress.emit)
                msgs.append(f"{os.path.basename(p)} ready")
            except Exception as e:
                msgs.append(f"{os.path.basename(p)} failed: {type(e).__name__}: {e}")
        self.done.emit("Global elevation: " + "; ".join(msgs) + ".")


class _ImageTrace(QThread):
    """Traces the loaded image in the background (k-means / outlines can take a second or two)."""
    done = pyqtSignal(object, object, int)
    failed = pyqtSignal(str)

    def __init__(self, rgb, opts, pw, ph, cache, token):
        super().__init__()
        self.args = (rgb, opts, pw, ph, cache)
        self.token = token

    def run(self):
        try:
            layers, info = IT.trace_image(*self.args)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.done.emit(layers, info, self.token)


class AspectRectangleSelector(RectangleSelector):
    """Rectangle selector that keeps every new / resized box at the paper's shape.

    ratio_fn(mid_lat) returns the box's width/height in DATA units (degrees) that prints at the paper's
    aspect ratio: paper_aspect / cos(mid_lat). Matplotlib's 'square' state is kept on permanently with
    that ratio as its aspect correction."""

    def __init__(self, ax, onselect, ratio_fn, **kw):
        self._ratio_fn = ratio_fn
        super().__init__(ax, onselect, use_data_coordinates=True, **kw)
        self.add_state('square')

    def _ratio(self, mid_lat):
        try:
            r = float(self._ratio_fn(mid_lat))
        except Exception:
            return None
        return r if np.isfinite(r) and r > 0 else None

    def _set_aspect_ratio_correction(self):
        super()._set_aspect_ratio_correction()
        if getattr(self, '_ratio_fn', None) is not None:
            try:
                y0, y1 = self.extents[2:] if getattr(self, '_selection_completed', False) else (0.0, 0.0)
            except Exception:
                y0 = y1 = 0.0
            r = self._ratio(0.5 * (y0 + y1))
            if r:
                self._aspect_ratio_correction = r

    def _onmove(self, event):
        self._state.add('square')        # always on (releasing Shift would otherwise switch it off)
        # Ratio for the latitude the box is being drawn at (the press point and the cursor).
        if self._eventpress is not None and event.ydata is not None and self._eventpress.ydata is not None:
            r = self._ratio(0.5 * (self._eventpress.ydata + event.ydata))
            if r:
                self._aspect_ratio_correction = r
        return super()._onmove(event)


class _TrackpadNav(QObject):
    """Trackpad navigation for a plot canvas, so no middle mouse button is needed.

    Two-finger scroll pans, pinch (or Ctrl + scroll) zooms. A classic notched mouse wheel (whole
    120-unit steps, vertical only) is passed through untouched, so it still zooms as before.
    Windows precision touchpads report pinch as Ctrl + wheel; macOS / Linux send a zoom gesture."""
    PX_PER_NOTCH = 100.0          # logical pixels panned per 120 units of wheel delta

    def __init__(self, app, canvas):
        super().__init__(canvas)
        self.app = app
        self.canvas = canvas
        canvas.installEventFilter(self)

    def _display_xy(self, pos):
        """Qt widget position -> matplotlib display coordinates (physical pixels, origin bottom-left)."""
        try:
            return self.canvas.mouseEventCoords(pos)
        except Exception:
            dpr = float(getattr(self.canvas, 'device_pixel_ratio', 1.0) or 1.0)
            return pos.x() * dpr, (self.canvas.height() - pos.y()) * dpr

    def eventFilter(self, obj, ev):
        if obj is not self.canvas:
            return False
        t = ev.type()
        if t == QEvent.Type.NativeGesture:
            if ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                x, y = self._display_xy(ev.position())
                self.app.nav_zoom(self.canvas, x, y, 1.0 + float(ev.value()))
                return True
            return False
        if t != QEvent.Type.Wheel:
            return False
        ad, pd = ev.angleDelta(), ev.pixelDelta()
        if ad.x() == 0 and ad.y() == 0 and pd.isNull():
            return False
        precise = (not pd.isNull()) or ad.x() != 0 or ad.y() % 120 != 0 \
            or ev.phase() != Qt.ScrollPhase.NoScrollPhase
        if not precise:
            return False                          # mouse wheel: matplotlib's scroll handlers zoom
        if self.canvas is self.app.canvas_prev and self.app.canvas_mode == 'transform':
            return False                          # scroll rotates / scales the picked text there
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = ad.y() / 120.0 if ad.y() else pd.y() / self.PX_PER_NOTCH
            if steps:
                x, y = self._display_xy(ev.position())
                self.app.nav_zoom(self.canvas, x, y, 1.2 ** steps)
            return True
        dpr = float(getattr(self.canvas, 'device_pixel_ratio', 1.0) or 1.0)
        if not pd.isNull():
            dx, dy = pd.x(), pd.y()
        else:
            dx, dy = ad.x() / 120.0 * self.PX_PER_NOTCH, ad.y() / 120.0 * self.PX_PER_NOTCH
        # Scroll up / left moves the view up / left (like scrolling a page); the OS applies "natural" reversal.
        self.app.nav_pan(self.canvas, -dx * dpr, dy * dpr)
        return True


class PlotterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Topo Plotter Studio - Cartographer Edition")
        self.setWindowIcon(app_icon())
        self.resize(1500, 500)

        self.active_planet = 'Earth'
        self.paper_name = 'Postcard'
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
        self.map_segments = []            # strokes from the worker (contours, fill, sites)
        self.decor_segments = []          # title / scale bar / north arrow / graticule / frame
        self.decor_masks = []             # data-coord boxes blanked behind the decorations
        self.calibration_mode = False
        self.mission_mode = False         # True for every paper-space mode (Solar System and Image)
        self.image_mode = False
        self.mission_data = {}
        self.image_path = None
        self.image_rgb = None
        self.svg_layers = None            # raw layers of an imported SVG (Image mode), or None
        self._image_cache = {}
        self._image_layers = []           # last trace result
        self._image_pens = {}             # traced layer key -> pen id
        self._image_job = None
        self._image_token = 0
        self._image_pending = False
        self.circle_center_lon = 0.0
        self._saved_selection = None
        self._loading = False
        self.project_path = None
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
        self.favorites = project_io.load_favorites(FAVORITE_PAPER_COLORS, FAVORITE_REGIONS)
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
        self.setup_menu()

    def setup_menu(self):
        mb = self.menuBar()

        def act(menu, text, slot, shortcut=None, tip=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setStatusTip(tip)
            a.triggered.connect(lambda _=False: slot())
            menu.addAction(a)
            return a

        m_file = mb.addMenu("&File")
        act(m_file, "New project", self.new_project, "Ctrl+N")
        act(m_file, "Open project\u2026", self.open_project, "Ctrl+O")
        self.menu_recent = m_file.addMenu("Open recent")
        self.menu_recent.aboutToShow.connect(lambda: self._fill_recent_menu(self.menu_recent))
        act(m_file, "Save project", self.save_project, "Ctrl+S")
        act(m_file, "Save project as\u2026", lambda: self.save_project(save_as=True), "Ctrl+Shift+S")
        m_file.addSeparator()
        act(m_file, "Save SVG\u2026", self.export_svg_action)
        act(m_file, "Export G-code\u2026", self.export_action, "Ctrl+E")
        m_file.addSeparator()
        act(m_file, "Quit", self.close, "Ctrl+Q")
        m_view = mb.addMenu("&View")
        self.act_show_map = QAction("Source map", self, checkable=True, checked=True)
        self.act_show_map.setShortcut(QKeySequence("Ctrl+1"))
        self.act_show_map.toggled.connect(lambda on: self.set_panel_visible('map', on))
        self.act_show_layers = QAction("Layers panel", self, checkable=True, checked=True)
        self.act_show_layers.setShortcut(QKeySequence("Ctrl+2"))
        self.act_show_layers.toggled.connect(lambda on: self.set_panel_visible('layers', on))
        act_focus = QAction("Focus on the preview", self)
        act_focus.setShortcut(QKeySequence("F11"))
        act_focus.triggered.connect(lambda: self.btn_focus.toggle())
        for a in (self.act_show_map, self.act_show_layers):
            m_view.addAction(a)
        m_view.addSeparator()
        m_view.addAction(act_focus)
        m_tools = mb.addMenu("&Tools")
        act(m_tools, "Pens\u2026", self.open_pen_manager)
        act(m_tools, "Pen calibration sheet\u2026", self.open_pen_manager)
        act(m_tools, "Data sources (sharper regional data)\u2026", self.open_data_dialog)
        act(m_tools, "Speed up global elevation (one time)\u2026", self.prepare_dem_files)

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
        if isinstance(widget, (QHBoxLayout, QGridLayout, QVBoxLayout)):
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
        proj_row = QHBoxLayout()
        proj_row.setContentsMargins(0, 8, 0, 0)
        proj_row.setSpacing(6)
        b_open = QPushButton("Open project\u2026")
        b_open.setToolTip("Open a saved .topo.json project (Ctrl+O)")
        b_open.clicked.connect(self.open_project)
        b_save = QPushButton("Save project")
        b_save.setToolTip("Save area, paper, pens, colours, map furniture, sites, annotations and painted lines (Ctrl+S)")
        b_save.clicked.connect(lambda: self.save_project())
        self.btn_recent = QToolButton()
        self.btn_recent.setText("Recent \u25be")
        self.btn_recent.setToolTip("Recently opened or saved projects")
        self.btn_recent.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_recent_btn = QMenu(self)
        self.menu_recent_btn.aboutToShow.connect(lambda: self._fill_recent_menu(self.menu_recent_btn))
        self.btn_recent.setMenu(self.menu_recent_btn)
        proj_row.addWidget(b_open)
        proj_row.addWidget(b_save)
        proj_row.addWidget(self.btn_recent)
        proj_row.addStretch()
        hv.addLayout(proj_row)
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
        self._wheel_guard = WheelGuard.install(scroll)     # wheel scrolls the panel, never the drop-downs

        # ---------- STYLE PRESETS ----------
        preset_row = QHBoxLayout()
        self.combo_preset = QComboBox()
        self.combo_preset.setToolTip("Saved looks: contour / fill / map-furniture settings (not the place).")
        self.btn_preset_apply = QPushButton("Apply")
        self.btn_preset_apply.clicked.connect(self.apply_selected_preset)
        self.btn_preset_save = QPushButton("Save\u2026")
        self.btn_preset_save.setObjectName("ghost")
        self.btn_preset_save.clicked.connect(self.save_preset_dialog)
        self.btn_preset_del = QPushButton("Delete")
        self.btn_preset_del.setObjectName("ghost")
        self.btn_preset_del.clicked.connect(self.delete_selected_preset)
        preset_row.addWidget(self.combo_preset, 1)
        preset_row.addWidget(self.btn_preset_apply)
        preset_row.addWidget(self.btn_preset_save)
        preset_row.addWidget(self.btn_preset_del)
        vbox.addWidget(self._field("Style preset", preset_row))
        self.reload_presets()

        # ---------- SOURCE ----------
        sec_src = CollapsibleSection("Source")
        self.combo_subject = QComboBox()
        for name, tip in (("Earth", ""), ("Moon", ""), ("Mars", ""),
                          ("Solar System", "Spacecraft trajectories through the solar system (Voyager, Cassini, "
                                           "New Horizons...)"),
                          ("Image", "Trace a logo, drawing or photo into pen outlines (and sticker cut lines)")):
            self.combo_subject.addItem(name)
            if tip:
                self.combo_subject.setItemData(self.combo_subject.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self.combo_subject.setCurrentText("Moon")      # works straight after cloning (data is in the repo)
        mars_files = DATA_FILES['Mars']
        if not (os.path.exists(mars_files['dem']) or os.path.exists(mars_files['color'])):
            i = self.combo_subject.findText("Mars")
            self.combo_subject.model().item(i).setEnabled(False)
            self.combo_subject.setItemData(i, "Download the USGS Mars files into the project folder:\n"
                                              f"{mars_files['dem']}\n{mars_files['color']}",
                                           Qt.ItemDataRole.ToolTipRole)
        self.combo_subject.currentIndexChanged.connect(lambda _: self.switch_planet())
        sec_src.add(self._field("Subject", self.combo_subject))

        self.combo_region = QComboBox()
        self.combo_region.setEditable(True)
        self.combo_region.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        regions = self._fill_region_combo()
        if regions:
            completer = QCompleter(regions)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self.combo_region.setCompleter(completer)
        self.combo_region.currentTextChanged.connect(self.on_region_changed)
        self.btn_fav_region = self._fav_button("region", self.toggle_fav_region)
        region_row = QHBoxLayout()
        region_row.addWidget(self.combo_region, 1)
        region_row.addWidget(self.btn_fav_region)
        self.lbl_region = sec_src.add(self._field("Clip to region", region_row))

        # Ski resort overlay (shown when a resort is picked under Clip to region)
        self.widget_ski = QWidget()
        gs = QGridLayout(self.widget_ski)
        gs.setContentsMargins(0, 0, 0, 0)
        gs.setVerticalSpacing(4)
        self.chk_ski_runs = QCheckBox("Runs")
        self.chk_ski_runs.setChecked(True)
        self.chk_ski_lifts = QCheckBox("Lifts")
        self.chk_ski_lifts.setChecked(True)
        self.chk_ski_diff = QCheckBox("Colour runs by difficulty")
        self.chk_ski_diff.setToolTip("Green = easy, blue = intermediate, thin black = advanced, thick black = expert\n"
                                     "(pens that are out of stock fall back to the Runs pen).")
        self.combo_ski_run_pen, self.combo_ski_lift_pen = QComboBox(), QComboBox()
        gs.addWidget(self.chk_ski_runs, 0, 0)
        gs.addWidget(self.combo_ski_run_pen, 0, 1)
        gs.addWidget(self.chk_ski_lifts, 1, 0)
        gs.addWidget(self.combo_ski_lift_pen, 1, 1)
        gs.addWidget(self.chk_ski_diff, 2, 0, 1, 2)
        gs.setColumnStretch(1, 1)
        for c in (self.chk_ski_runs, self.chk_ski_lifts, self.chk_ski_diff):
            c.toggled.connect(self.trigger_recalc)
        for cb in (self.combo_ski_run_pen, self.combo_ski_lift_pen):
            cb.currentIndexChanged.connect(lambda _: self.trigger_recalc())
        self.widget_ski_field = sec_src.add(self._field(
            "Ski resort overlay", self.widget_ski,
            "Lifts and runs: \u00a9 OpenStreetMap contributors (ODbL). Downloaded once, kept in ski_resorts/."))
        self.widget_ski_field.setVisible(False)

        self.combo_scale = QComboBox()
        self.combo_scale.addItem("Free (drag any area)")
        for label, miles, ft in SCALE_PRESETS:
            self.combo_scale.addItem(label, (miles, ft))
        self.combo_scale.setToolTip("Fixed map scales, so plots of different places match. With a scale picked,\n"
                                    "click (or drag) on the source map to place a frame of that width.")
        self.combo_scale.currentIndexChanged.connect(self.apply_scale_preset)
        self.widget_scale = sec_src.add(self._field("Map scale", self.combo_scale))

        self.chk_circle = QCheckBox("Circle mode (whole hemisphere)")
        self.chk_circle.toggled.connect(self.toggle_circle_mode)
        sec_src.add(self.chk_circle)
        self.chk_borders = QCheckBox("Show borders on the source map")
        self.chk_borders.setChecked(True)
        self.chk_borders.toggled.connect(self.toggle_borders)
        sec_src.add(self.chk_borders)
        self.chk_sites = QCheckBox("Show points of interest on the source map")
        self.chk_sites.setChecked(True)
        self.chk_sites.setToolTip("Landing sites, mountains, craters and other good plot subjects.")
        self.chk_sites.toggled.connect(self.toggle_sites)
        self.chk_site_labels = QCheckBox("Label plotted points of interest")
        self.chk_site_labels.setChecked(True)
        self.chk_site_labels.toggled.connect(self.trigger_recalc)
        self.lbl_sites_hint = QLabel("Hover a marker for details \u00b7 click to add it to the plot "
                                     "(filled = plotted) \u00b7 double-click to frame the area")
        self.lbl_sites_hint.setObjectName("hint")
        self.lbl_sites_hint.setWordWrap(True)
        vbox.addWidget(sec_src)
        self._build_missions_section(vbox)
        self._build_image_section(vbox)

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
        self.combo_paper_color = QComboBox()
        self._fill_paper_combo()
        self.combo_paper_color.setCurrentText(DEFAULT_PAPER)
        self.combo_paper_color.setToolTip("The sheet you will plot on. The preview shows it, and the colour\n"
                                          "fill works out the best ink mix for this paper colour.")
        self._paper_color_index = self.combo_paper_color.currentIndex()
        self.combo_paper_color.currentIndexChanged.connect(self.on_paper_color_changed)
        self.btn_fav_paper = self._fav_button("paper colour", self.toggle_fav_paper)
        paper_row = QHBoxLayout()
        paper_row.addWidget(self.combo_paper_color, 1)
        paper_row.addWidget(self.btn_fav_paper)
        sec_paper.add(self._field("Paper colour", paper_row))
        cw_row = QHBoxLayout()
        self.combo_colorway = QComboBox()
        self.combo_colorway.setToolTip("Switches the paper colour and every pen (contours, index lines, water,\n"
                                       "borders, fill, map furniture, Solar System labels) in one go.")
        self.combo_colorway.activated.connect(self.apply_selected_colorway)
        b_cw_save = QPushButton("Save\u2026")
        b_cw_save.setObjectName("ghost")
        b_cw_save.setToolTip("Save the current paper colour and pens as a colourway")
        b_cw_save.clicked.connect(self.save_colorway_dialog)
        b_cw_del = QPushButton("Delete")
        b_cw_del.setObjectName("ghost")
        b_cw_del.clicked.connect(self.delete_selected_colorway)
        cw_row.addWidget(self.combo_colorway, 1)
        cw_row.addWidget(b_cw_save)
        cw_row.addWidget(b_cw_del)
        sec_paper.add(self._field("Colourway", cw_row))
        self.reload_colorways()
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

        # Tiles: one big map split across several sheets of the chosen paper size
        tile_box = QWidget()
        gt = QGridLayout(tile_box)
        gt.setContentsMargins(0, 0, 0, 0)
        gt.setVerticalSpacing(4)
        self.spin_tile_cols, self.spin_tile_rows = QSpinBox(), QSpinBox()
        for sp in (self.spin_tile_cols, self.spin_tile_rows):
            sp.setRange(1, 8)
            sp.setValue(1)
            sp.setFixedWidth(52)
        self.spin_tile_gap = QDoubleSpinBox()
        self.spin_tile_gap.setRange(0.0, 50.0)
        self.spin_tile_gap.setSingleStep(1.0)
        self.spin_tile_gap.setDecimals(1)
        self.spin_tile_gap.setSuffix(" mm")
        self.spin_tile_gap.setToolTip("Space between the pages when they are mounted. The map skips that strip, so\n"
                                      "features line up across the gap. 0 = pages butt together.")
        self.chk_tile_cut = QCheckBox("Cut lines")
        self.chk_tile_cut.setToolTip("Show each page's edge in the preview and pre-tick 'cut out each page' (drag\n"
                                     "knife) in the export.")
        gt.addWidget(self.spin_tile_cols, 0, 0)
        gt.addWidget(QLabel("across \u00d7"), 0, 1)
        gt.addWidget(self.spin_tile_rows, 0, 2)
        gt.addWidget(QLabel("down"), 0, 3)
        gt.addWidget(QLabel("Gap"), 1, 0)
        gt.addWidget(self.spin_tile_gap, 1, 1, 1, 2)
        gt.addWidget(self.chk_tile_cut, 1, 3)
        gt.setColumnStretch(4, 1)
        for sp in (self.spin_tile_cols, self.spin_tile_rows, self.spin_tile_gap):
            sp.valueChanged.connect(lambda _: self.update_paper_dims())
        self.chk_tile_cut.toggled.connect(lambda _: self.render_canvas())
        sec_paper.add(self._field("Split into pages (tiles)", tile_box,
                                  "The paper size above is one page; the map spans all of them. Each page "
                                  "exports as its own G-code."))
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

        # Which pen draws what (pick light pens for dark paper)
        self.widget_contour_pens = QWidget()
        g = QGridLayout(self.widget_contour_pens)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)
        self.combo_contour_pen = QComboBox()
        self.combo_index_pen = QComboBox()
        self.combo_water_pen = QComboBox()
        self.combo_border_pen = QComboBox()
        self.lbl_water_pen = QLabel("Below sea level")
        self.lbl_border_pen = QLabel("Borders")
        rows = [(QLabel("Contour lines"), self.combo_contour_pen), (QLabel("Index lines"), self.combo_index_pen),
                (self.lbl_water_pen, self.combo_water_pen), (self.lbl_border_pen, self.combo_border_pen)]
        for r, (lab, cb) in enumerate(rows):
            lab.setObjectName("fieldLabel")
            g.addWidget(lab, r, 0)
            g.addWidget(cb, r, 1)
            cb.currentIndexChanged.connect(lambda _: (self._update_contrast_warning(), self.trigger_recalc()))
        g.setColumnStretch(1, 1)
        self.combo_contour_pen.setToolTip("Regular contours (and the lit side of illuminated contours).")
        self.combo_index_pen.setToolTip("Every Nth (index) contour and the shoreline (and the shadowed side of\n"
                                        "illuminated contours).")
        self.sec_contours.add(self._field("Pens", self.widget_contour_pens))
        self.widget_contrast = QWidget()
        h_warn = QHBoxLayout(self.widget_contrast)
        h_warn.setContentsMargins(0, 0, 0, 0)
        self.lbl_contrast = QLabel("")
        self.lbl_contrast.setObjectName("hint")
        self.lbl_contrast.setWordWrap(True)
        self.btn_light_pens = QPushButton("Use light pens")
        self.btn_light_pens.setObjectName("ghost")
        self.btn_light_pens.setToolTip("Switch the contour pens to the lightest pens you have in stock.")
        self.btn_light_pens.clicked.connect(self.use_light_contour_pens)
        h_warn.addWidget(self.lbl_contrast, 1)
        h_warn.addWidget(self.btn_light_pens)
        self.widget_contrast.setVisible(False)
        self.sec_contours.add(self.widget_contrast)

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
        self.chk_ocean = QCheckBox("Plot ocean / crater depths")
        self.chk_ocean.setChecked(True)
        self.chk_ocean.toggled.connect(self.trigger_recalc)
        self.sec_contours.add(self.chk_ocean)
        self.chk_blue_water = QCheckBox("Separate pen below sea level")
        self.chk_blue_water.setChecked(True)
        self.chk_blue_water.toggled.connect(lambda _: (self.context_ui_update(), self.trigger_recalc()))
        self.sec_contours.add(self.chk_blue_water)
        self.chk_plot_borders = QCheckBox("Plot country / state borders")
        self.chk_plot_borders.setChecked(True)
        self.chk_plot_borders.toggled.connect(self.trigger_recalc)
        self.sec_contours.add(self.chk_plot_borders)
        self.lbl_contour_hdr = self.sec_contours      # kept for older code paths
        vbox.addWidget(self.sec_contours)

        # ---------- FILL / HATCHING LAYER ----------
        self.sec_fill = CollapsibleSection("Fill / Hatching", expanded=False, checkable=True, checked=False)
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
        self.combo_mono_pen.currentIndexChanged.connect(lambda _: self.recalc_timer.start(150))
        self.lbl_mono_pen = self.sec_fill.add(self._field("Pen", self.combo_mono_pen))

        self.list_inks = QListWidget()
        self.list_inks.setObjectName("inkList")
        self.list_inks.setMinimumHeight(150)
        self.list_inks.setToolTip("Tick the pens the colour fill may use. It finds the best mix of the ticked\n"
                                  "inks for the paper colour - tick everything, or just a few for a simpler look.")
        self.list_inks.itemChanged.connect(lambda _: (self._update_ink_count(), self.recalc_timer.start(400)))
        ink_box = QWidget()
        ink_v = QVBoxLayout(ink_box)
        ink_v.setContentsMargins(0, 0, 0, 0)
        ink_v.setSpacing(4)
        ink_v.addWidget(self.list_inks)
        ink_btns = QHBoxLayout()
        b_all = QPushButton("All")
        b_all.setObjectName("ghost")
        b_all.setToolTip("Use every pen you have in stock")
        b_all.clicked.connect(lambda: self._set_all_inks(True))
        b_none = QPushButton("None")
        b_none.setObjectName("ghost")
        b_none.clicked.connect(lambda: self._set_all_inks(False))
        self.lbl_ink_count = QLabel("")
        self.lbl_ink_count.setObjectName("hint")
        ink_btns.addWidget(b_all)
        ink_btns.addWidget(b_none)
        ink_btns.addStretch()
        ink_btns.addWidget(self.lbl_ink_count)
        ink_v.addLayout(ink_btns)
        stock_hint = QLabel("Pen out of ink? Untick \u2018In stock\u2019 in Tools \u203a Pens\u2026 and it disappears here.")
        stock_hint.setObjectName("hint")
        stock_hint.setWordWrap(True)
        ink_v.addWidget(stock_hint)
        self.lbl_ink_set = self.sec_fill.add(self._field("Inks to use", ink_box))
        self.rebuild_ink_widgets(select_all=True)

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
        self.chk_auto_contrast.setToolTip("Stretches the fill's tones to use the full light-to-dark range.")
        self.chk_auto_contrast.toggled.connect(self.trigger_recalc)
        self.chk_join = QCheckBox("Join neighbouring fill lines (zigzag)")
        self.chk_join.setChecked(True)
        self.chk_join.setToolTip("Links the end of each stroke to the next line - far fewer pen lifts.")
        self.chk_join.toggled.connect(self.trigger_recalc)
        vbox.addWidget(self.sec_fill)

        # ---------- ADVANCED (less-used switches, grouped) ----------
        sec_adv = self.sec_advanced = CollapsibleSection("Advanced", expanded=False)

        def adv_header(text):
            lab = QLabel(text)
            lab.setObjectName("fieldLabel")
            sec_adv.add(lab)
            return lab
        self.lbl_adv_sites = adv_header("Points of interest")
        for w in (self.chk_sites, self.chk_site_labels, self.lbl_sites_hint):
            sec_adv.add(w)
        self.lbl_adv_contours = adv_header("Contours")
        sec_adv.add(self.chk_tanaka)
        self.lbl_adv_fill = adv_header("Fill / hatching")
        sec_adv.add(self.chk_auto_contrast)
        sec_adv.add(self.chk_join)
        vbox.addWidget(sec_adv)

        # ---------- MAP FURNITURE ----------
        sec_deco = self.sec_deco = CollapsibleSection("Map furniture", expanded=True)
        self.chk_deco_title = QCheckBox("Title block")
        self.chk_deco_title.toggled.connect(self.update_decorations)
        sec_deco.add(self.chk_deco_title)
        self.txt_title = QLineEdit()
        self.txt_title.setPlaceholderText("Title (e.g. Mare Imbrium)")
        self.txt_title.textChanged.connect(lambda _: self.decor_timer.start(400))
        sec_deco.add(self.txt_title)
        # What the line under the title shows (only drawn when the title block is on)
        sub_box = QWidget()
        sub_l = QGridLayout(sub_box)
        sub_l.setContentsMargins(0, 0, 0, 0)
        self.chk_sub_planet = QCheckBox("Planet")
        self.chk_sub_coords = QCheckBox("Coordinates")
        self.chk_sub_interval = QCheckBox("Contour interval")
        self.chk_sub_interval.setChecked(True)
        self.chk_sub_planet.setToolTip("Body name, e.g. MARS")
        self.chk_sub_coords.setToolTip("Centre of the map, e.g. 24.80\u00b0N 146.90\u00b0E")
        self.chk_sub_interval.setToolTip("Contour spacing, e.g. 750 ft contours (only when contours are plotted)")
        for i, c in enumerate((self.chk_sub_planet, self.chk_sub_coords, self.chk_sub_interval)):
            c.toggled.connect(self.update_decorations)
            sub_l.addWidget(c, i // 2, i % 2)
        self.widget_subtitle = sec_deco.add(self._field("Subtitle shows", sub_box))
        self.chk_deco_scale = QCheckBox("Scale bar")
        self.chk_deco_scale.toggled.connect(self.update_decorations)
        sec_deco.add(self.chk_deco_scale)
        self.chk_deco_north = QCheckBox("North arrow")
        self.chk_deco_north.toggled.connect(self.update_decorations)
        sec_deco.add(self.chk_deco_north)
        self.chk_deco_frame = QCheckBox("Neat-line frame")
        self.chk_deco_frame.toggled.connect(self.update_decorations)
        sec_deco.add(self.chk_deco_frame)
        self.combo_graticule = QComboBox()
        self.combo_graticule.addItems(["Off", "Edge ticks", "Full lines"])
        self.combo_graticule.currentTextChanged.connect(self.update_decorations)
        sec_deco.add(self._field("Coordinate grid", self.combo_graticule))
        self.combo_deco_pen = QComboBox()
        self._add_pen_items(self.combo_deco_pen, list(self.pens.items()))
        idx = self.combo_deco_pen.findData('base')
        if idx >= 0:
            self.combo_deco_pen.setCurrentIndex(idx)
        self.combo_deco_pen.currentIndexChanged.connect(self.update_decorations)
        sec_deco.add(self._field("Pen", self.combo_deco_pen, "Map lines are blanked behind the title, scale bar "
                                                             "and north arrow."))
        self.combo_text_size = QComboBox()
        self.combo_text_size.addItems(list(TEXT_SCALES.keys()))
        self.combo_text_size.setCurrentText("Medium")
        self.combo_text_size.setToolTip("Lettering size for the title, scale bar, grid and site labels.\n"
                                        "Text never goes smaller than the chosen pen can letter cleanly (about 4x its line width).")
        self.combo_text_size.currentIndexChanged.connect(
            lambda _: (self.update_decorations(), self.trigger_recalc() if self.selected_sites.get(self.active_planet)
                       else None))
        sec_deco.add(self._field("Text size", self.combo_text_size))
        self.combo_scale_units = QComboBox()
        self.combo_scale_units.addItems(["Miles", "Kilometres", "Miles + km"])
        self.combo_scale_units.setCurrentText("Miles")
        self.combo_scale_units.setToolTip("Units for the scale bar (short bars switch to feet / metres).")
        self.combo_scale_units.currentIndexChanged.connect(self.update_decorations)
        sec_deco.add(self._field("Scale bar units", self.combo_scale_units))
        vbox.addWidget(sec_deco)
        self.decor_timer = QTimer()
        self.decor_timer.setSingleShot(True)
        self.decor_timer.timeout.connect(self.update_decorations)
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
        map_card = self.map_card = QFrame()
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
        hint_map = QLabel("Drag to select · two-finger scroll or middle-drag to pan · pinch / wheel to zoom")
        hint_map.setObjectName("hint")
        hint_map.setWordWrap(True)
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
        hint_prev = QLabel("Two-finger scroll or middle-drag to pan · pinch / wheel to zoom")
        hint_prev.setObjectName("hint")
        hint_prev.setWordWrap(True)
        self.btn_focus = QPushButton("Focus")
        self.btn_focus.setCheckable(True)
        self.btn_focus.setToolTip("Hide the source map and layers panel so the preview fills the window (F11)")
        self.btn_focus.toggled.connect(self.toggle_focus_preview)
        h_prev_toolbar.addWidget(self.btn_prev_zoom)
        h_prev_toolbar.addWidget(self.btn_prev_home)
        h_prev_toolbar.addWidget(self.btn_focus)
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
        self.btn_add_pen = QPushButton("Pens\u2026")
        self.btn_add_pen.setObjectName("ghost")
        self.btn_add_pen.setToolTip("Edit pens: colours, measured line widths, feed rates, calibration")
        self.btn_add_pen.clicked.connect(self.open_pen_manager)
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
        self.layer_panel.context_requested.connect(self.on_layer_context)
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
        # New boxes keep the paper's shape while you drag (what you frame is what gets plotted).
        self.selector = AspectRectangleSelector(
            self.ax_map, self.on_select,
            lambda mid_lat: self.paper_aspect / max(np.cos(np.radians(np.clip(mid_lat, -89.0, 89.0))), 1e-3),
            useblit=True, button=[1], interactive=True,
            props=dict(facecolor=SEL_FILL, edgecolor=SEL_EDGE, alpha=0.25, fill=True, linewidth=1.5))
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
        self.canvas_prev.mpl_connect('resize_event',
                                     lambda e: (self._apply_screen_linewidths(redraw=False), self.schedule_ink_sim(150)))
        self._nav_map = _TrackpadNav(self, self.canvas_map)
        self._nav_prev = _TrackpadNav(self, self.canvas_prev)

        self.update_paper_dims()
        self.context_ui_update()
        self._update_fav_stars()

    def context_ui_update(self):
        """Shows only the options that apply to the current planet, layers and fill pattern."""
        planet = self.selected_planet()
        is_earth = (planet == 'Earth')
        strategy = self.combo_hatch.currentText()
        is_hachure = "Hachure" in strategy
        is_color = self.combo_inks.currentText() == "Color separation" and not is_hachure

        self.lbl_region.setVisible(is_earth)
        self.widget_ski_field.setVisible(is_earth and self.selected_resort() is not None)
        self.chk_borders.setVisible(is_earth)
        self.chk_plot_borders.setVisible(is_earth)
        self.chk_blue_water.setVisible(is_earth)
        self.lbl_water_pen.setVisible(is_earth and self.chk_blue_water.isChecked())
        self.combo_water_pen.setVisible(is_earth and self.chk_blue_water.isChecked())
        self.lbl_border_pen.setVisible(is_earth)
        self.combo_border_pen.setVisible(is_earth)
        self.chk_ocean.setText("Plot ocean depths" if is_earth else "Plot all depths (below datum)")

        self.lbl_source.setVisible(not is_hachure)
        self.lbl_inks.setVisible(not is_hachure)
        self.lbl_ink_set.setVisible(is_color)
        self.lbl_mono_pen.setVisible(not is_color)
        self.chk_auto_contrast.setVisible(not is_hachure)
        # Squiggles are always drawn one way (no zigzag joining), so the option doesn't apply to them.
        self.chk_join.setVisible(not ("Stipp" in strategy or is_hachure or "Squiggle" in strategy))
        self._update_contrast_warning()
        # Solar System mode: mission controls instead of the map / terrain controls
        solar = planet in ('Solar System', 'Image')
        self.sec_missions.setVisible(planet == 'Solar System')
        self.sec_image.setVisible(planet == 'Image')
        if planet == 'Image':
            self._update_image_controls()
        for w in (self.sec_contours, self.sec_fill):
            w.setVisible(not solar)
        for w in (self.chk_circle, self.chk_sites, self.chk_site_labels, self.lbl_sites_hint, self.widget_scale,
                  self.sec_advanced):
            w.setVisible(not solar)
        self.lbl_adv_fill.setVisible(not is_hachure)       # auto contrast (and join) only apply then
        if solar:
            self.lbl_region.setVisible(False)
            self.chk_borders.setVisible(False)
        for w in (self.chk_deco_scale, self.chk_deco_north, self.combo_graticule.parentWidget(),
                  self.combo_scale_units.parentWidget(), self.chk_deco_title, self.widget_subtitle):
            w.setVisible(not solar)
        if hasattr(self, 'btn_draw'):
            self.btn_draw.setText({'Solar System': "Draw Mission Plot",
                                   'Image': "Place SVG" if self.svg_layers is not None else "Trace Image"
                                   }.get(planet, "Draw Map"))

    REGION_NONE = "None (Plot Full Selection)"

    # --- FAVOURITES (paper colours and regions; pens use the 'Fav.' column in Tools > Pens) ---
    def _fav_button(self, what, slot):
        b = QToolButton()
        b.setCheckable(True)
        b.setText("\u2606")
        b.setToolTip(f"Favourite: list this {what} at the top of the drop-down")
        b.setFixedWidth(28)
        b.toggled.connect(slot)
        return b

    @staticmethod
    def _set_star(btn, on, enabled=True):
        btn.blockSignals(True)
        btn.setChecked(bool(on))
        btn.setText("\u2605" if on else "\u2606")
        btn.setEnabled(enabled)
        btn.blockSignals(False)

    def _update_fav_stars(self):
        if hasattr(self, 'btn_fav_paper'):
            name = self.combo_paper_color.currentText()
            self._set_star(self.btn_fav_paper, name in self.favorites['paper_colors'], name in PAPER_COLORS)
        if hasattr(self, 'btn_fav_region'):
            name = self.combo_region.currentText()
            self._set_star(self.btn_fav_region, name in self.favorites['regions'],
                           bool(name) and name != self.REGION_NONE and self.combo_region.findText(name) >= 0)

    def _toggle_favorite(self, kind, name, on):
        lst = self.favorites[kind]
        if on and name not in lst:
            lst.append(name)
        elif not on and name in lst:
            lst.remove(name)
        project_io.save_favorites(self.favorites)

    def toggle_fav_paper(self, on):
        name = self.combo_paper_color.currentText()
        if name not in PAPER_COLORS:
            return
        self._toggle_favorite('paper_colors', name, on)
        self._fill_paper_combo()
        self._update_fav_stars()

    def toggle_fav_region(self, on):
        name = self.combo_region.currentText()
        if not name or name == self.REGION_NONE:
            return
        self._toggle_favorite('regions', name, on)
        cb = self.combo_region
        cb.blockSignals(True)
        self._fill_region_combo()
        cb.setCurrentText(name)
        cb.blockSignals(False)
        self._update_fav_stars()

    def _fill_paper_combo(self):
        """Paper colours: favourites, a divider, the rest, then 'Custom colour...'. Keeps the selection."""
        cb = self.combo_paper_color
        cur = cb.currentData() if cb.count() else None
        cb.blockSignals(True)
        cb.clear()
        favs = [n for n in PAPER_COLORS if n in self.favorites['paper_colors']]
        others = [n for n in PAPER_COLORS if n not in self.favorites['paper_colors']]
        for name in favs:
            cb.addItem(swatch_icon([PAPER_COLORS[name]]), name, PAPER_COLORS[name])
        if favs and others:
            cb.insertSeparator(cb.count())
        for name in others:
            cb.addItem(swatch_icon([PAPER_COLORS[name]]), name, PAPER_COLORS[name])
        cb.addItem(swatch_icon(['#FFFFFF']), "Custom colour\u2026", None)
        if cur:
            i = cb.findData(cur)
            if i >= 0:
                cb.setCurrentIndex(i)
            else:
                self._set_custom_paper(cur)
            self._paper_color_index = cb.currentIndex()
        cb.blockSignals(False)

    def _fill_region_combo(self):
        """'Clip to region': None, favourites, a divider, then Lakes / States / Countries under headers.
        Returns every selectable region name (for the type-to-search completer)."""
        cb = self.combo_region
        cb.clear()
        cb.addItem(self.REGION_NONE)
        states, countries = [], []
        if HAS_GPD:
            w_col = 'NAME' if 'NAME' in world_borders.columns else 'name' if 'name' in world_borders.columns else None
            s_col = 'name' if 'name' in state_borders.columns else None
            if s_col: states = sorted(set(state_borders[s_col].dropna().tolist()))
            if w_col: countries = sorted(set(world_borders[w_col].dropna().tolist()))
        lakes = list(REGION_LAKES)
        resorts = [ski.region_label(n) for n in ski.RESORTS]
        known = set(lakes) | set(resorts) | set(states) | set(countries)
        favs = [r for r in self.favorites['regions'] if r in known]
        for r in favs:
            cb.addItem(r)
        groups = [("Lakes", lakes), ("Ski resorts", resorts), ("States", states), ("Countries", countries)]
        model = cb.model()
        for title, names in groups:
            names = [n for n in names if n not in favs]
            if not names:
                continue
            cb.insertSeparator(cb.count())
            cb.addItem(title.upper())
            hdr = model.item(cb.count() - 1)
            hdr.setEnabled(False)                       # a heading, not a choice
            f = QFont(hdr.font())
            f.setBold(True)
            hdr.setFont(f)
            for n in names:
                cb.addItem(n)
        return sorted(set(favs) | known)

    # --- MAP SCALE PRESETS ---
    def scale_preset(self):
        """(miles across, contour ft) of the chosen map scale, or None for free selection."""
        return self.combo_scale.currentData() if hasattr(self, 'combo_scale') else None

    def apply_scale_preset(self, _index=None):
        sp = self.scale_preset()
        if sp is None:
            self.interval_auto_done = False
            self.lbl_usgs_interval.setText("Interval: Auto")
            self.lbl_status.setText("Map scale: free - drag any area on the source map.")
            return
        miles, ft = sp
        self.slider_int.blockSignals(True)
        self.txt_int.blockSignals(True)
        self.interval_ft = int(ft)
        self.slider_int.setValue(min(max(int(ft), self.slider_int.minimum()), self.slider_int.maximum()))
        self.txt_int.setText(str(int(ft)))
        self.slider_int.blockSignals(False)
        self.txt_int.blockSignals(False)
        self.lbl_usgs_interval.setText(f"Interval: Map scale \u00b7 {int(ft):,} ft")
        if self._loading:
            return
        e = self.current_view_extent
        if e and not self.chk_circle.isChecked() and not self.mission_mode and not self.calibration_mode:
            self.frame_scale_at(0.5 * (e[0] + e[1]), 0.5 * (e[2] + e[3]))
        else:
            self.lbl_status.setText(f"Click the source map to place a {miles:g}-mile-wide frame.")

    def scale_box(self, cx, cy):
        """Lon/lat box centred on (cx, cy) whose printed width spans the preset's miles at paper aspect."""
        miles, _ = self.scale_preset()
        R = PLANET_RADII.get(self.active_planet, 6371000.0)
        km_per_deg = 2.0 * np.pi * R / 360.0 / 1000.0
        width_km = miles * 1.609344
        cy = float(np.clip(cy, -85.0, 85.0))
        w_deg = width_km / (km_per_deg * max(np.cos(np.radians(cy)), 1e-3))
        h_deg = width_km / self.paper_aspect / km_per_deg
        return cx - w_deg / 2, cx + w_deg / 2, cy - h_deg / 2, cy + h_deg / 2

    def frame_scale_at(self, cx, cy):
        x1, x2, y1, y2 = self.scale_box(cx, cy)
        self.last_drag_coords = (x1, x2, y1, y2)
        self.process_selection(x1, x2, y1, y2)
        self.selector.set_visible(True)
        self.selector.extents = (x1, x2, y1, y2)
        # keep the frame in view (zoom out if it is bigger than the current view)
        xl, yl = self.ax_map.get_xlim(), self.ax_map.get_ylim()
        if x1 < xl[0] or x2 > xl[1] or y1 < yl[0] or y2 > yl[1]:
            px, py = (x2 - x1) * 1.5, (y2 - y1) * 1.5
            self.ax_map.set_xlim(max(-180, x1 - px), min(180, x2 + px))
            self.ax_map.set_ylim(max(-90, y1 - py), min(90, y2 + py))
        self.canvas_map.draw_idle()
        miles, ft = self.scale_preset()
        self.lbl_status.setText(f"Framed {miles:g} miles across with {ft:,} ft contours - click Draw Map.")

    # --- COLOURWAYS ---
    def reload_colorways(self, select=None):
        self.colorways = project_io.load_colorways()
        cb = self.combo_colorway
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("Choose a colourway\u2026", None)
        for name in self.colorways:
            tag = "  (built-in)" if name in project_io.BUILTIN_COLORWAYS else ""
            cb.addItem(name + tag, name)
        cb.setCurrentIndex(max(0, cb.findData(select)) if select else 0)
        cb.blockSignals(False)

    def _colorway_widgets(self):
        return {'contour_pen': self.combo_contour_pen, 'index_pen': self.combo_index_pen,
                'water_pen': self.combo_water_pen, 'border_pen': self.combo_border_pen,
                'mono_pen': self.combo_mono_pen, 'deco_pen': self.combo_deco_pen,
                'mission_pen_labels': self.combo_mpen_labels, 'mission_pen_orbits': self.combo_mpen_orbits,
                'mission_pen_belt': self.combo_mpen_belt,
                'ski_run_pen': self.combo_ski_run_pen, 'ski_lift_pen': self.combo_ski_lift_pen}

    def current_colorway(self):
        out = {'paper_color': self.current_paper_color()}
        for k, cb in self._colorway_widgets().items():
            out[k] = cb.currentData()
        return out

    def apply_selected_colorway(self, _index=None):
        name = self.combo_colorway.currentData()
        if name and name in self.colorways:
            self.apply_colorway(name, self.colorways[name])

    def apply_colorway(self, name, cw):
        missing = []
        self._loading = True
        try:
            if cw.get('paper_color'):
                self.set_paper_color(cw['paper_color'])
            for k, cb in self._colorway_widgets().items():
                pid = cw.get(k)
                if not pid:
                    continue
                i = cb.findData(pid)
                if i >= 0:
                    cb.blockSignals(True)
                    cb.setCurrentIndex(i)
                    cb.blockSignals(False)
                elif pid in self.pens:
                    if self.pens[pid]['name'] not in missing:
                        missing.append(self.pens[pid]['name'])
        finally:
            self._loading = False
        self._image_pens = {}                       # traced image layers re-pick pens for the new paper
        self._update_contrast_warning()
        note = f" Not in stock: {', '.join(missing)}." if missing else ""
        if self.mission_mode:
            if self.image_mode:
                self._fill_image_table()
            self.update_paper_plot()
        elif self.has_drawn:
            self.render_canvas()
            self.trigger_recalc()                   # contour / fill pens are baked into the strokes
        else:
            self.render_canvas()
        self.lbl_status.setText(f"Colourway '{name}' applied.{note}")

    def save_colorway_dialog(self):
        name, ok = QInputDialog.getText(self, "Save colourway", "Colourway name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in project_io.BUILTIN_COLORWAYS:
            QMessageBox.information(self, "Colourways", "That name is a built-in colourway - choose another name.")
            return
        project_io.save_user_colorway(name, self.current_colorway())
        self.reload_colorways(select=name)
        self.lbl_status.setText(f"Saved colourway '{name}'.")

    def delete_selected_colorway(self):
        name = self.combo_colorway.currentData()
        if not name:
            return
        if name in project_io.BUILTIN_COLORWAYS:
            QMessageBox.information(self, "Colourways", "Built-in colourways can't be deleted.")
            return
        if project_io.delete_user_colorway(name):
            self.reload_colorways()

    # --- HIDEABLE PANELS ---
    def set_panel_visible(self, which, visible):
        w = {'map': self.map_card, 'layers': self.layer_panel}[which]
        w.setVisible(bool(visible))
        act = {'map': self.act_show_map, 'layers': self.act_show_layers}[which]
        if act.isChecked() != bool(visible):
            act.blockSignals(True)
            act.setChecked(bool(visible))
            act.blockSignals(False)
        self.btn_focus.blockSignals(True)
        self.btn_focus.setChecked(not (self.map_card.isVisible() or self.layer_panel.isVisible()))
        self.btn_focus.blockSignals(False)
        QTimer.singleShot(0, lambda: (self._apply_screen_linewidths(), self.schedule_ink_sim(150)))

    def toggle_focus_preview(self, on):
        """Hide (or bring back) the source map and the layers panel so the preview gets the space."""
        self.set_panel_visible('map', not on)
        self.set_panel_visible('layers', not on)

    def selected_resort(self):
        return ski.resort_from_label(self.combo_region.currentText()) if hasattr(self, 'combo_region') else None

    def on_region_changed(self, text):
        self._update_fav_stars()
        resort = ski.resort_from_label(text)
        self.widget_ski_field.setVisible(resort is not None and self.active_planet == 'Earth')
        if resort and not self._loading:
            self.start_ski_fetch(resort)
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
        shown = [(p_id, pen) for p_id, pen in self.pens.items()
                 if pen_available(p_id, pen) or p_id in TOOL_PEN_IDS or p_id == self.active_pen_id]
        for i, (p_id, pen) in enumerate(shown):
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
                "Paint Mode: click or drag across lines to give them the active pen (Shift = whole elevation level).")

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
        if self._loading or self.calibration_mode:
            return
        if self.mission_mode:
            self.update_paper_plot()
            return
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
            'site_label_cap_mm': self.site_label_cap_mm(),
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
            'fixed_interval': self.scale_preset() is not None,
            'ski_resort': self.selected_resort() if self.active_planet == 'Earth' else None,
            'ski_opts': self.ski_options(),
            'tanaka': self.chk_tanaka.isChecked(),
            'fill_source': self.combo_source.currentText(),
            'fill_inks': 'color' if self.combo_inks.currentText() == "Color separation" else 'mono',
            'tone_gamma': self.tone_gamma(),
            'join_lines': self.chk_join.isChecked(),
            'hatch_strategy': self.combo_hatch.currentText(),
            'fill_pens': self.selected_fill_pens(),
            'contour_pen': self.combo_contour_pen.currentData() or 'base',
            'index_pen': self.combo_index_pen.currentData() or 'pen_1',
            'water_pen': self.combo_water_pen.currentData() or 'blue_038',
            'border_pen': self.combo_border_pen.currentData() or 'pen_1',
            'mono_pen': self.combo_mono_pen.currentData() or DEFAULT_MONO_PEN,
            'paper_color': self.current_paper_color(),
            'hatch_spacing_mm': self.spin_spacing.value(),
            'auto_contrast': self.chk_auto_contrast.isChecked(),
            'paper_w_mm': self.pw,
            'paper_h_mm': self.ph,
            'pens': copy.deepcopy(self.pens),
            'regional_dem': self._regional('dem'),
            'regional_color': self._regional('color'),
        }

    def _regional(self, kind):
        """Sharper regional raster covering the selection (see Tools > Data sources), else None."""
        if not self.current_view_extent or self.chk_circle.isChecked():
            return None
        if kind == 'dem' and self.active_planet == 'Earth' and "Tahoe" in self.combo_region.currentText():
            return None
        try:
            meta = regions.best_region(self.active_planet, kind, self.current_view_extent)
        except Exception as e:
            print(f"Regional data lookup failed: {e}")
            return None
        if meta is None:
            return None
        return {'path': meta['path'], 'bounds': meta['bounds'], 'units': meta.get('units', 'm'),
                'name': meta['name']}

    def draw_action(self):
        """'Draw Map' button: always a fresh render (auto interval, reset view, clear annotations)."""
        if self.is_calculating:
            return
        if self.calibration_mode:
            self.exit_calibration()
        if self.image_mode:
            self.start_image_plot(fresh=True)
            return
        if self.mission_mode:
            self.start_mission_plot()
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
        if needs_dem and not os.path.exists(dem_path) and not params.get('regional_dem'):
            self.explain_missing_dem(self.active_planet, force=True)
            self.lbl_status.setText("Elevation data missing - see the message for where to get it.")
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
        self.map_segments = result['segments']
        self.dem_segments = self.map_segments + self.decor_segments
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
        self._build_decorations()
        self._cache_stroke_metrics()
        if fresh:
            self.custom_items = []
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.update_undo_redo_btns()
            self.ax_prev.set_xlim(0.0, 1.0)
            self.ax_prev.set_ylim(0.0, 1.0)
        self._apply_pending_restore()
        self.render_canvas()
        self.refresh_layer_panel()

        n = len(self.dem_segments)
        used = [f"{k} '{r['name']}'" for k, r in (('elevation', (getattr(sender, 'params', None) or {}).get('regional_dem')),
                                                  ('colour', (getattr(sender, 'params', None) or {}).get('regional_color')))
                if r]
        reg_note = (" Using regional " + " + ".join(used) + ".") if used else ""
        self._finish_render((f"Render complete ({n:,} strokes)." + reg_note) if n else
                            "Render complete - nothing to plot for these settings.")

    def on_render_error(self, err_msg):
        self._finish_render("Ready.")
        QMessageBox.critical(self, "Render Error", err_msg)

    def closeEvent(self, event):
        for w in list(self._workers):
            w.wait()
        super().closeEvent(event)

    def current_paper_color(self):
        return self.combo_paper_color.currentData() or '#FFFFFF'

    def on_paper_color_changed(self, index):
        if self.combo_paper_color.itemData(index) is None:             # "Custom colour..."
            prev = self.combo_paper_color.itemData(self._paper_color_index) or '#FFFFFF'
            c = QColorDialog.getColor(QColor(prev), self, "Paper colour")
            self.combo_paper_color.blockSignals(True)
            if c.isValid():
                self._set_custom_paper(c.name().upper())
            else:
                self.combo_paper_color.setCurrentIndex(self._paper_color_index)
            self.combo_paper_color.blockSignals(False)
        self._paper_color_index = self.combo_paper_color.currentIndex()
        self._update_contrast_warning()
        self._update_fav_stars()
        if self.has_drawn:
            self.render_canvas()
            if self.sec_fill.is_enabled():
                self.recalc_timer.start(150)

    def _set_custom_paper(self, hx):
        """Adds/updates a 'Custom (#RRGGBB)' entry (kept just above 'Custom colour...') and selects it."""
        cb = self.combo_paper_color
        last = cb.count() - 1
        if last > 0 and str(cb.itemText(last - 1)).startswith("Custom ("):
            cb.setItemText(last - 1, f"Custom ({hx})")
            cb.setItemData(last - 1, hx)
            cb.setItemIcon(last - 1, swatch_icon([hx]))
            cb.setCurrentIndex(last - 1)
        else:
            cb.insertItem(last, swatch_icon([hx]), f"Custom ({hx})", hx)
            cb.setCurrentIndex(last)

    def set_paper_color(self, hx):
        if not hx:
            return
        i = self.combo_paper_color.findData(hx)
        self.combo_paper_color.blockSignals(True)
        if i >= 0:
            self.combo_paper_color.setCurrentIndex(i)
        else:
            self._set_custom_paper(hx)
        self.combo_paper_color.blockSignals(False)
        self._paper_color_index = self.combo_paper_color.currentIndex()
        self._update_fav_stars()

    # --- FILL INKS ---
    def ink_pens(self):
        """Pens offered as fill inks: in stock, not a tool."""
        return [(pid, pen) for pid, pen in self.pens.items() if pen_available(pid, pen)]

    def rebuild_ink_widgets(self, select_all=False):
        prev_checked = set(self.selected_fill_pens()) if hasattr(self, 'list_inks') and self.list_inks.count() else None
        known = {self.list_inks.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list_inks.count())}
        prev_mono = self.combo_mono_pen.currentData()
        pens = sorted(self.ink_pens(), key=lambda kv: -ink_lightness(kv[1]['color']))
        self.list_inks.blockSignals(True)
        self.list_inks.clear()
        for pid, pen in pens:
            lw = pen.get('line_mm', pen['width'])
            it = QListWidgetItem(swatch_icon([pen['color']]), f"{pen['name']}   \u00b7 {lw:g} mm")
            it.setData(Qt.ItemDataRole.UserRole, pid)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = select_all or prev_checked is None or pid in prev_checked or pid not in known
            it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self.list_inks.addItem(it)
        self.list_inks.blockSignals(False)
        self.combo_mono_pen.blockSignals(True)
        self.combo_mono_pen.clear()
        self._add_pen_items(self.combo_mono_pen, sorted(self.ink_pens(), key=lambda kv: ink_lightness(kv[1]['color'])))
        i = self.combo_mono_pen.findData(prev_mono if prev_mono else DEFAULT_MONO_PEN)
        self.combo_mono_pen.setCurrentIndex(max(0, i))
        self.combo_mono_pen.blockSignals(False)
        for cb, default in ((self.combo_contour_pen, 'base'), (self.combo_index_pen, 'pen_1'),
                            (self.combo_water_pen, 'blue_038'), (self.combo_border_pen, 'pen_1')):
            self._fill_pen_combo(cb, default)
        if hasattr(self, 'combo_ski_run_pen'):
            self._fill_pen_combo(self.combo_ski_run_pen, 'base')
            self._fill_pen_combo(self.combo_ski_lift_pen, 'red_038')
        self._update_ink_count()
        self._update_contrast_warning()

    def _fill_pen_combo(self, cb, default):
        """Fills a pen chooser with the in-stock pens, keeping the current choice when possible."""
        cur = cb.currentData()
        cb.blockSignals(True)
        cb.clear()
        self._add_pen_items(cb, sorted(self.ink_pens(), key=lambda kv: ink_lightness(kv[1]['color'])))
        for want in (cur, default):
            i = cb.findData(want) if want else -1
            if i >= 0:
                cb.setCurrentIndex(i)
                break
        else:
            cb.setCurrentIndex(0)
        cb.blockSignals(False)

    @staticmethod
    def _split_favorites(items):
        """(pen_id, pen) pairs -> (favourites, others), each keeping the given order."""
        items = list(items)
        return [kv for kv in items if pen_favorite(kv[1])], [kv for kv in items if not pen_favorite(kv[1])]

    def _add_pen_items(self, cb, items):
        """Adds pens to a drop-down: favourites first, a divider, then the rest. The divider is not
        selectable and carries no data, so findData()/currentData() never return it."""
        favs, rest = self._split_favorites(items)
        for pid, pen in favs:
            cb.addItem(swatch_icon([pen['color']]), pen['name'], pid)
        if favs and rest:
            cb.insertSeparator(cb.count())
        for pid, pen in rest:
            cb.addItem(swatch_icon([pen['color']]), pen['name'], pid)

    def _update_contrast_warning(self):
        """Warns when a contour pen will hardly show on the chosen paper colour."""
        if not hasattr(self, 'lbl_contrast'):
            return
        paper_l = ink_lightness(self.current_paper_color())
        weak = []
        combos = [self.combo_contour_pen, self.combo_index_pen]
        if self.active_planet == 'Earth':
            combos.append(self.combo_border_pen)
            if self.chk_blue_water.isChecked():
                combos.append(self.combo_water_pen)
        for cb in combos:
            pid = cb.currentData()
            pen = self.pens.get(pid)
            if pen and abs(ink_lightness(pen['color']) - paper_l) < 0.3 and pen['name'] not in weak:
                weak.append(pen['name'])
        if weak and self.sec_contours.is_enabled():
            paper = self.combo_paper_color.currentText()
            self.lbl_contrast.setText(f"\u26a0 {', '.join(weak)} will barely show on {paper.lower()} paper.")
            self.btn_light_pens.setVisible(paper_l < 0.5)
            self.widget_contrast.setVisible(True)
        else:
            self.widget_contrast.setVisible(False)

    def use_light_contour_pens(self):
        """Dark paper: thinnest light pen for contours, a wider light pen for index lines."""
        paper_l = ink_lightness(self.current_paper_color())
        light = [(pid, pen) for pid, pen in self.ink_pens() if ink_lightness(pen['color']) - paper_l >= 0.4]
        if not light:
            QMessageBox.information(self, "Pens", "None of your in-stock pens is light enough for this paper.")
            return
        lightest = max(ink_lightness(p['color']) for _, p in light)
        best = [(pid, p) for pid, p in light if ink_lightness(p['color']) >= lightest - 0.05] or light
        best.sort(key=lambda kv: float(kv[1].get('line_mm', kv[1]['width'])))
        thin, wide = best[0][0], best[-1][0]
        for cb, pid in ((self.combo_contour_pen, thin), (self.combo_index_pen, wide),
                        (self.combo_border_pen, wide)):
            cb.blockSignals(True)
            cb.setCurrentIndex(max(0, cb.findData(pid)))
            cb.blockSignals(False)
        self._update_contrast_warning()
        self.trigger_recalc()

    def selected_fill_pens(self):
        if not hasattr(self, 'list_inks'):
            return []
        out = []
        for i in range(self.list_inks.count()):
            it = self.list_inks.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def set_fill_pens(self, ids):
        ids = set(ids or [])
        self.list_inks.blockSignals(True)
        for i in range(self.list_inks.count()):
            it = self.list_inks.item(i)
            it.setCheckState(Qt.CheckState.Checked if it.data(Qt.ItemDataRole.UserRole) in ids
                             else Qt.CheckState.Unchecked)
        self.list_inks.blockSignals(False)
        self._update_ink_count()

    def _set_all_inks(self, on):
        self.set_fill_pens([self.list_inks.item(i).data(Qt.ItemDataRole.UserRole)
                            for i in range(self.list_inks.count())] if on else [])
        self.recalc_timer.start(150)

    def _update_ink_count(self):
        n = len(self.selected_fill_pens())
        self.lbl_ink_count.setText(f"{n} of {self.list_inks.count()} pens" if n else "no pens ticked")

    # --- LAYERS ---
    @staticmethod
    def layer_key(item):
        layer = item.get('layer') or ('borders' if item.get('type') == 'border' else 'contours')
        return f"fill:{item['pen_id']}" if layer == 'fill' else layer

    def is_visible(self, item):
        return self.layer_visible.get(self.layer_key(item), True)

    def annotations_visible(self):
        return self.layer_visible.get('annotations', True)

    def on_layer_context(self, key, global_pos):
        """Right-click on a layer: pick a pen for every stroke in it."""
        menu = QMenu(self)
        title = menu.addAction("Set pen for whole layer:")
        title.setEnabled(False)
        favs, rest = self._split_favorites(sorted(self.ink_pens(), key=lambda kv: ink_lightness(kv[1]['color'])))
        for group in (favs, rest):
            if group is rest and favs and rest:
                menu.addSeparator()
            for pid, pen in group:
                a = menu.addAction(swatch_icon([pen['color']]), pen['name'])
                a.triggered.connect(lambda _=False, p=pid: self.set_layer_pen(key, p))
        menu.exec(global_pos)

    def set_layer_pen(self, key, pen_id):
        if key == 'annotations':
            targets_custom = list(range(len(self.custom_items)))
            targets = []
        else:
            targets_custom = []
            targets = [i for i, it in enumerate(self.dem_segments) if self.layer_key(it) == key]
        if not targets and not targets_custom:
            return
        self.push_undo_state()
        vis = self.layer_visible.get(key, True)
        for i in targets:
            self.dem_segments[i]['pen_id'] = pen_id
        for i in targets_custom:
            self.custom_items[i]['pen_id'] = pen_id
        if key.startswith('fill:'):                       # fill layers are keyed by pen - keep visibility
            self.layer_visible[f"fill:{pen_id}"] = vis
        if key.startswith('img:'):                        # image mode: the layer table follows
            self._image_pens[key] = pen_id
            self._fill_image_table()
        self._cache_stroke_metrics()
        self.render_canvas()
        self.refresh_layer_panel()
        self.lbl_status.setText(f"{LAYER_LABELS.get(key, key)} now uses {self.pens[pen_id]['name']}. "
                                "(Redrawing the map resets layer pens.)")

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
        order = ['calibration', 'contours', 'borders', 'ski_runs', 'ski_lifts', 'sites'] + sorted(k for k in groups if k.startswith('fill:')) + \
                ['orbits', 'belt', 'trajectory', 'markers', 'labels', 'decor', 'annotations']
        order += sorted(k for k in groups if k not in order)
        rows = []
        for k in order:
            if k not in groups: continue
            pens = groups[k]['pens']
            colors = [self.pens.get(p, self.pens['base'])['color'] for p in pens][:4]
            if k.startswith('fill:'):
                label = "Fill · " + self.pens.get(k[5:], {'name': k[5:]})['name']
            elif k.startswith('img:') and k != 'img:cut':
                label = "Image · " + next((l['label'] for l in self._image_layers if l['key'] == k), k[4:])
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

    def estimate_pen_minutes(self, p_id, feed_override=None):
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
        feed = float(feed_override or self.pens.get(p_id, {}).get('feed', DRAW_FEED))
        return (draw / feed + travel / TRAVEL_FEED + n * PEN_UP_DOWN_MIN) * ACCEL_FACTOR

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
        and wet light ink is never dragged through dark ink). Tools (the drag-knife cutter) always come
        after every ink, so nothing is drawn on paper that has already been cut."""
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
        return sorted(used, key=lambda p: (p in TOOL_PEN_IDS, -lightness(p)))

    # --- HIGH PERFORMANCE RENDERER ---
    def render_canvas(self):
        xlim, ylim = self.ax_prev.get_xlim(), self.ax_prev.get_ylim()
        is_fresh = (xlim == (0.0, 1.0) and ylim == (0.0, 1.0))

        self.ax_prev.clear()
        self.ax_prev.set_xticks([]);
        self.ax_prev.set_yticks([])
        style_axes(self.fig_prev, self.ax_prev)
        self._lw_artists = []                 # (artist, line width(s) in paper mm) - see _apply_screen_linewidths

        # Show the paper colour of the active ink set
        self.ax_prev.set_facecolor(self.current_paper_color())

        if not self.has_drawn or not self.current_view_extent:
            self.canvas_prev.draw_idle()
            return

        mid_lat = np.radians((self.current_view_extent[2] + self.current_view_extent[3]) / 2)
        self.ax_prev.set_aspect(1 / np.cos(mid_lat))

        self._visible_idx = [i for i, item in enumerate(self.dem_segments)
                             if self.is_visible(item) and item.get('layer') not in self.TOP_LAYERS]
        self.dem_collection = None
        if self._visible_idx:
            vis = [self.dem_segments[i] for i in self._visible_idx]
            segs = [item['seg'] for item in vis]
            colors = [self.pens.get(item['pen_id'], self.pens['base'])['color'] for item in vis]
            lws = [self._pen_mm(self.pens.get(item['pen_id'], self.pens['base'])) for item in vis]
            self.dem_collection = LineCollection(segs, colors=colors, linewidths=1.0, picker=True, pickradius=5,
                                                 zorder=10)
            self._lw_artists.append((self.dem_collection, lws))
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
                                          linewidth=1.0, picker=True, pickradius=5, zorder=11)
                line.item_ref = ('custom', idx)
                self._lw_artists.append((line, self._pen_mm(pen)))

            elif item['type'] == 'text':
                polys, bbox = self.text_item_polys(item)

                if self.chk_mask_text.isChecked() and bbox:
                    pad = item['scale'] * 0.2
                    rect = patches.Rectangle((bbox[0] - pad, bbox[1] - pad),
                                             bbox[2] - bbox[0] + 2 * pad, bbox[3] - bbox[1] + 2 * pad,
                                             facecolor=self.ax_prev.get_facecolor(), edgecolor='none', zorder=14)
                    self.ax_prev.add_patch(rect)

                for poly in polys:
                    line, = self.ax_prev.plot(poly[:, 0], poly[:, 1], color=pen['color'],
                                              linewidth=1.0, picker=True, pickradius=5, zorder=15)
                    line.item_ref = ('custom', idx)
                    self._lw_artists.append((line, self._pen_mm(pen)))

        for (x0, y0, x1, y1) in self.decor_masks:
            self.ax_prev.add_patch(patches.Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=self.current_paper_color(),
                                                     edgecolor='none', zorder=13))
        # Map furniture sits above its own blanking boxes (and above the ink preview image).
        top = [it for it in self.dem_segments if it.get('layer') in self.TOP_LAYERS and self.is_visible(it)]
        if top:
            tpens = [self.pens.get(it['pen_id'], self.pens['base']) for it in top]
            top_coll = self.ax_prev.add_collection(LineCollection([it['seg'] for it in top],
                                                                  colors=[p['color'] for p in tpens],
                                                                  linewidths=1.0, zorder=14))
            self._lw_artists.append((top_coll, [self._pen_mm(p) for p in tpens]))

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

        self._draw_tile_overlay()
        self._apply_screen_linewidths(redraw=False)
        self.ink_image = None                 # ax.clear() above removed the old one
        self.canvas_prev.draw_idle()
        self.schedule_ink_sim(0)

    # --- TRUE-WIDTH LINES IN THE PREVIEW ---
    MIN_PREVIEW_LW_PT = 0.1                   # keep hair-thin strokes visible when zoomed far out

    @staticmethod
    def _pen_mm(pen):
        """Physical stroke width on paper: the measured line width if calibrated, else the tip size."""
        return float(pen.get('line_mm') or pen.get('width', 0.5))

    def _screen_pt_per_mm(self):
        """Matplotlib points per paper millimetre at the preview's current zoom (same scale as the ink
        simulation), so a 0.38 mm pen is drawn 0.38 mm wide on the page as shown on screen."""
        try:
            if not self.current_view_extent:
                return PT_PER_MM
            ax = self.ax_prev
            ax.apply_aspect()
            w = ax.get_window_extent().width
            xl = ax.get_xlim()
            span = abs(xl[1] - xl[0])
            mm_per_unit = self._paper_mm_per_data_unit_x()
            if w <= 1 or span <= 0 or not mm_per_unit or mm_per_unit <= 0:
                return PT_PER_MM
            return (w / span) / mm_per_unit * 72.0 / self.fig_prev.dpi
        except Exception:
            return PT_PER_MM

    def _apply_screen_linewidths(self, redraw=True):
        """Re-scales every preview stroke to its true pen width for the current zoom / window size."""
        arts = getattr(self, '_lw_artists', None)
        if not arts:
            return
        k = self._screen_pt_per_mm()
        lo = self.MIN_PREVIEW_LW_PT
        for art, mm in arts:
            try:
                if isinstance(mm, (list, tuple)):
                    art.set_linewidths([max(lo, m * k) for m in mm])
                else:
                    art.set_linewidth(max(lo, mm * k))
            except Exception:
                pass
        if redraw:
            self.canvas_prev.draw_idle()

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
            if self.is_visible(item) and item.get('layer') not in self.TOP_LAYERS:
                by_pen.setdefault(item['pen_id'], []).append(item['seg'])

        off_fig = Figure(figsize=(w / dpi, h / dpi), dpi=dpi, facecolor='white')
        off_canvas = FigureCanvasAgg(off_fig)
        off_ax = off_fig.add_axes([0, 0, 1, 1])
        off_ax.axis('off')
        off_ax.set_xlim(xlim)
        off_ax.set_ylim(ylim)
        layers = []
        order = sorted(by_pen, key=lambda p: -ink_lightness(self.pens.get(p, self.pens['base'])['color']))
        for p_id in order:
            pen = self.pens.get(p_id, self.pens['base'])
            lw = float(pen.get('line_mm', pen['width'])) * pt_per_mm
            # draw black-on-white to get this pen's coverage, then lay its ink over the paper
            coll = off_ax.add_collection(LineCollection(by_pen[p_id], colors='black', linewidths=lw,
                                                        capstyle='round'))
            off_canvas.draw()
            cov = 1.0 - np.asarray(off_canvas.buffer_rgba())[..., 0].astype(np.float32) / 255.0
            layers.append((cov, pen['color'], pen_opacity(pen)))
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
        self._apply_screen_linewidths(redraw=False)
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

        if self.canvas_mode == 'paint':
            # Press and drag: every line the brush touches takes the active pen (Shift = whole level).
            self.paint_active = True
            self._paint_undo_pushed = False
            self._paint_levels = set()
            self._paint_done = set()
            self._paint_shift = (event.key == 'shift')
            self._paint_at(event)
            return

        if self.canvas_mode == 'draw':
            self.drawing_active = True
            self.current_freehand = [(event.xdata, event.ydata)]
            pen = self.pens[self.active_pen_id]
            self.temp_draw_line, = self.ax_prev.plot([event.xdata], [event.ydata], color=pen['color'],
                                                     lw=max(self.MIN_PREVIEW_LW_PT,
                                                            self._pen_mm(pen) * self._screen_pt_per_mm()),
                                                     zorder=20)
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

        if self.canvas_mode == 'paint' and getattr(self, 'paint_active', False):
            self._paint_at(event)
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

        if getattr(self, 'paint_active', False):
            self.paint_active = False
            if self._paint_undo_pushed:
                self.render_canvas()
                self.refresh_layer_panel()
            return

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
        x, y = event.xdata, event.ydata
        if x is None or y is None: return
        self._zoom_prev_at(x, y, scale)

    def _zoom_prev_at(self, x, y, scale):
        """Zooms the plot preview about data point (x, y); scale < 1 zooms in."""
        cx, cy = self.ax_prev.get_xlim(), self.ax_prev.get_ylim()
        nw, nh = (cx[1] - cx[0]) * scale, (cy[1] - cy[0]) * scale
        rx, ry = (cx[1] - x) / (cx[1] - cx[0]), (cy[1] - y) / (cy[1] - cy[0])
        self.ax_prev.set_xlim([x - nw * (1 - rx), x + nw * rx])
        self.ax_prev.set_ylim([y - nh * (1 - ry), y + nh * ry])
        self._apply_screen_linewidths(redraw=False)
        self.canvas_prev.draw_idle()
        self.schedule_ink_sim(150)

    # --- TRACKPAD NAVIGATION (see _TrackpadNav) ---
    def nav_pan(self, canvas, dx, dy):
        """Moves the view of a canvas by (dx, dy) display pixels (+x right, +y up)."""
        ax = self.ax_map if canvas is self.canvas_map else self.ax_prev
        inv = ax.transData.inverted()
        (x0, y0), (x1, y1) = inv.transform([(0.0, 0.0), (dx, dy)])
        xl, yl = ax.get_xlim(), ax.get_ylim()
        ax.set_xlim(xl[0] + (x1 - x0), xl[1] + (x1 - x0))
        ax.set_ylim(yl[0] + (y1 - y0), yl[1] + (y1 - y0))
        canvas.draw_idle()
        if ax is self.ax_prev:
            self.schedule_ink_sim(150)

    def nav_zoom(self, canvas, x_disp, y_disp, factor):
        """Zooms a canvas about a display-pixel point; factor > 1 zooms in."""
        if factor <= 0:
            return
        ax = self.ax_map if canvas is self.canvas_map else self.ax_prev
        x, y = ax.transData.inverted().transform((x_disp, y_disp))
        if not (np.isfinite(x) and np.isfinite(y)):
            return
        if ax is self.ax_map:
            if self.mission_mode and self.image_mode:
                return                             # same as the wheel: the image overview doesn't zoom
            self._zoom_map_at(x, y, 1.0 / factor)
        else:
            self._zoom_prev_at(x, y, 1.0 / factor)

    def on_prev_pick(self, event):
        """Painting is handled on press/drag (see _paint_at), so a click and a drag behave the same."""
        return

    PAINT_SKIP_LAYERS = ('fill', 'decor', 'calibration')
    # drawn above the label blanking boxes (and excluded from the ink simulation / paint picking)
    TOP_LAYERS = ('decor', 'labels', 'markers', 'trajectory', 'belt')

    def _paint_at(self, event):
        """Gives every line under the cursor (within the pick radius) the active pen. Hatching is left
        alone - it is regenerated from the image, so paint it via the fill's ink list instead."""
        if event.inaxes != self.ax_prev or event.xdata is None:
            return
        pen_id = self.active_pen_id
        targets = []                                      # dem_segments indices to repaint
        if self.dem_collection is not None:
            hit, info = self.dem_collection.contains(event)
            for j in (info.get('ind', []) if hit else []):
                idx = self._visible_idx[j]
                item = self.dem_segments[idx]
                if self.layer_key(item) in self.PAINT_SKIP_LAYERS:
                    continue
                if self._paint_shift:
                    key = (item['level'], self.layer_key(item))
                    if key in self._paint_levels:
                        continue
                    self._paint_levels.add(key)
                    targets += [i for i, it in enumerate(self.dem_segments)
                                if it['level'] == key[0] and self.layer_key(it) == key[1] and it['pen_id'] != pen_id]
                elif item['pen_id'] != pen_id:
                    targets.append(idx)
        custom_hits = []
        for artist in list(self.ax_prev.lines):
            ref = getattr(artist, 'item_ref', None)
            if ref and ref[0] == 'custom' and artist.contains(event)[0] \
                    and self.custom_items[ref[1]]['pen_id'] != pen_id:
                custom_hits.append((artist, ref[1]))
        if not targets and not custom_hits:
            return
        if not self._paint_undo_pushed:
            self.push_undo_state()
            self._paint_undo_pushed = True
        for i in targets:
            self.dem_segments[i]['pen_id'] = pen_id
        pen = self.pens.get(pen_id, self.pens['base'])
        for artist, ci in custom_hits:
            self.custom_items[ci]['pen_id'] = pen_id
            artist.set_color(pen['color'])
        # live feedback without a full redraw
        if targets and self.dem_collection is not None:
            from matplotlib.colors import to_rgba
            sim = self.is_ink_sim_mode()
            cols = []
            self._paint_done.update(targets)
            for idx in self._visible_idx:
                it = self.dem_segments[idx]
                c = to_rgba(self.pens.get(it['pen_id'], self.pens['base'])['color'])
                cols.append(c if (not sim or idx in self._paint_done) else (0, 0, 0, 0))
            self.dem_collection.set_alpha(None)
            self.dem_collection.set_color(cols)
        self.canvas_prev.draw_idle()

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
        self.map_segments = []
        self.decor_segments = []
        self.decor_masks = []
        self.calibration_mode = False
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
        if self.mission_mode and event.button == 1:
            return
        if event.button == 1:
            hit = self._site_at(event)
            self._site_click = hit is not None
            self._map_press_px = (event.x, event.y)
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
        if event.button is None and not self.mission_mode:
            self._update_site_hover(event)

    def on_mouse_release_map(self, event):
        press = getattr(self, '_map_press_px', None)
        if (event.button == 1 and press is not None and self.scale_preset() is not None
                and not getattr(self, '_site_click', False) and not self.mission_mode
                and not self.chk_circle.isChecked() and not self.btn_zoom_tool.isChecked()
                and event.inaxes == self.ax_map and event.xdata is not None
                and abs(event.x - press[0]) < 4 and abs(event.y - press[1]) < 4):
            self._map_press_px = None
            # a plain click with a map-scale preset: centre a frame of that width here (after the
            # selector has finished with the click)
            x, y = event.xdata, event.ydata
            QTimer.singleShot(0, lambda: self.frame_scale_at(x, y))
            return
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
        self.tile_w, self.tile_h = (w, h) if self.is_landscape else (h, w)
        cols, rows, gap = self.tile_layout()
        self.pw = cols * self.tile_w + (cols - 1) * gap
        self.ph = rows * self.tile_h + (rows - 1) * gap
        self.paper_aspect = self.pw / self.ph
        self.ax_prev.set_box_aspect(self.ph / self.pw)
        self.canvas_prev.draw_idle()
        if getattr(self, 'mission_mode', False):
            if self.has_drawn and not self.calibration_mode:
                self.update_paper_plot()
            return
        if self.last_drag_coords: self.process_selection(*self.last_drag_coords)

    # --- TILES (one map over several pages) ---
    def tile_layout(self):
        """(columns, rows, gap mm). (1, 1, 0) when the plot is a single page."""
        if not hasattr(self, 'spin_tile_cols'):
            return 1, 1, 0.0
        cols, rows = self.spin_tile_cols.value(), self.spin_tile_rows.value()
        gap = self.spin_tile_gap.value() if cols * rows > 1 else 0.0
        return cols, rows, gap

    def is_tiled(self):
        c, r, _ = self.tile_layout()
        return c * r > 1

    def tile_rects(self):
        """[(label, (x0, y0, w, h))] in paper mm over the whole mosaic (origin bottom-left); row 1 is the top."""
        cols, rows, gap = self.tile_layout()
        if cols * rows <= 1:
            return [(None, (0.0, 0.0, self.pw, self.ph))]
        out = []
        for r in range(rows):
            for c in range(cols):
                out.append((f"r{r + 1}c{c + 1}", (c * (self.tile_w + gap), (rows - 1 - r) * (self.tile_h + gap),
                                                  self.tile_w, self.tile_h)))
        return out

    def _paper_frame(self):
        return PaperFrame(self.current_view_extent, self.pw, self.ph, self.chk_circle.isChecked(), self.active_planet)

    def _paper_rect_to_data(self, x0, y0, w, h):
        """Paper-mm rectangle -> (X0, X1, Y0, Y1) in data coordinates."""
        pts = self._paper_frame().to_data([[x0, y0], [x0 + w, y0 + h]])
        return float(pts[0, 0]), float(pts[1, 0]), float(pts[0, 1]), float(pts[1, 1])

    def _draw_tile_overlay(self):
        """Preview: shades the gaps between pages and outlines each page."""
        if not self.is_tiled() or not self.current_view_extent:
            return
        cols, rows, gap = self.tile_layout()
        shade = '#B9B1A3'
        if gap > 0:
            for c in range(1, cols):
                X0, X1, Y0, Y1 = self._paper_rect_to_data(c * (self.tile_w + gap) - gap, 0, gap, self.ph)
                self.ax_prev.add_patch(patches.Rectangle((X0, Y0), X1 - X0, Y1 - Y0, facecolor=shade, alpha=0.85,
                                                         edgecolor='none', zorder=16))
            for r in range(1, rows):
                X0, X1, Y0, Y1 = self._paper_rect_to_data(0, r * (self.tile_h + gap) - gap, self.pw, gap)
                self.ax_prev.add_patch(patches.Rectangle((X0, Y0), X1 - X0, Y1 - Y0, facecolor=shade, alpha=0.85,
                                                         edgecolor='none', zorder=16))
        cut = self.chk_tile_cut.isChecked()
        for label, (x0, y0, w, h) in self.tile_rects():
            X0, X1, Y0, Y1 = self._paper_rect_to_data(x0, y0, w, h)
            self.ax_prev.add_patch(patches.Rectangle((X0, Y0), X1 - X0, Y1 - Y0, fill=False, zorder=17,
                                                     edgecolor=ACCENT if cut else '#8A8173',
                                                     linestyle='--', linewidth=1.0 if cut else 0.7))
            self.ax_prev.text(X0 + 0.02 * (X1 - X0), Y1 - 0.03 * (Y1 - Y0), label.upper(), fontsize=7,
                              color='#8A8173', va='top', ha='left', zorder=17)

    @staticmethod
    def _clip_lines(lines, box):
        """Clips polylines (data coords) to a shapely box; rings fully inside stay closed."""
        out = []
        for ln in lines:
            ln = np.asarray(ln, dtype=float)
            if len(ln) < 2:
                continue
            g = LineString(ln)
            if box.contains(g):
                out.append(ln)
                continue
            inter = g.intersection(box)
            for part in getattr(inter, 'geoms', [inter]):
                if part.geom_type == 'LineString' and len(part.coords) >= 2:
                    out.append(np.asarray(part.coords))
        return out

    def change_paper(self, name):
        self.paper_name = name;
        self.update_paper_dims()

    def toggle_orientation(self):
        self.is_landscape = not self.is_landscape;
        self.update_paper_dims()

    def selected_planet(self):
        return self.combo_subject.currentText() or 'Moon'

    def explain_missing_dem(self, planet, force=False):
        """Earth / Mars elevation files are too big for the repository: explain where to get them
        (once per session when switching to the planet, every time a draw needs the file)."""
        if planet not in DATA_FILES or os.path.exists(DATA_FILES[planet]['dem']):
            return
        shown = self.__dict__.setdefault('_dem_help_shown', set())
        if planet in shown and not force:
            return
        shown.add(planet)
        name = DATA_FILES[planet]['dem']
        if planet == 'Earth':
            url = ETOPO_URL
            body = (
                "<p>The global Earth elevation model is too large to include in the repository, so it is a "
                "one-time download.</p>"
                "<ol><li>Open the <a href='%s'>NOAA ETOPO 2022 page</a>.</li>"
                "<li>Under <b>60 Arc-Second</b>, download the <b>Ice Surface Elevation</b> GeoTIFF "
                "(<i>ETOPO_2022_v1_60s_N90W180_surface.tif</i>).</li>"
                "<li>Put it in the app folder as <b>%s</b>:<br><i>%s</i></li>"
                "<li>Select Earth again (no restart needed).</li></ol>"
                "<p>Until then you can still plot <b>Lake Tahoe</b> (Clip to region) and any area you fetch with "
                "<b>Tools &rsaquo; Data sources</b>.</p>") % (url, name, APP_DIR)
        else:
            url = "https://planetarymaps.usgs.gov/mosaic/"
            body = ("<p>The %s elevation file <b>%s</b> is not in the app folder:<br><i>%s</i></p>"
                    "<p>Download it from <a href='%s'>USGS Astrogeology</a> (the exact URLs are in "
                    "config.py) and keep the original file name.</p>") % (planet, name, APP_DIR, url)
        box = QMessageBox(self)
        box.setWindowTitle(f"{planet} elevation data needed")
        box.setIcon(QMessageBox.Icon.Information)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(body)
        b_open = box.addButton("Open download page", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is b_open:
            QDesktopServices.openUrl(QUrl(url))

    def start_ski_fetch(self, resort):
        """Loads (downloading the first time) a resort's lifts/runs, fetches 30 m elevation for it if no
        sharper elevation covers it yet, then frames the selection on the resort."""
        if self.is_calculating:
            self.lbl_status.setText("Busy - pick the resort again when the current job finishes.")
            return
        if self.chk_circle.isChecked():
            self.chk_circle.setChecked(False)
        self.is_calculating = True
        self.btn_draw.setEnabled(False)
        self.progress_bar.setVisible(True)
        self._ski_job = _SkiFetch(resort)
        self._ski_job.progress.connect(self.lbl_status.setText)
        self._ski_job.done.connect(self._on_ski_ready)
        self._ski_job.failed.connect(self._on_ski_failed)
        self._ski_job.start()

    def _ski_job_finished(self):
        self.is_calculating = False
        self.btn_draw.setEnabled(True)
        self.progress_bar.setVisible(False)

    def _on_ski_ready(self, resort, data, note):
        self._ski_job_finished()
        if self.selected_resort() != resort or self.active_planet != 'Earth':
            return
        x1, x2, y1, y2 = ski.frame_extent(data)
        self.last_drag_coords = (x1, x2, y1, y2)
        self.process_selection(x1, x2, y1, y2)
        self.selector.set_visible(True)
        px, py = (x2 - x1) * 1.5, (y2 - y1) * 1.5
        self.draw_regional_overlays(redraw=False)
        self.ax_map.set_xlim(x1 - px, x2 + px)
        self.ax_map.set_ylim(y1 - py, y2 + py)
        self.canvas_map.draw_idle()
        n_runs = sum(f['kind'] == 'run' for f in data['features'])
        n_lifts = sum(f['kind'] == 'lift' for f in data['features'])
        self.lbl_status.setText(f"{resort}: {n_runs} runs, {n_lifts} lifts framed - click Draw Map." +
                                (f" {note}" if note else ""))

    def _on_ski_failed(self, resort, msg):
        self._ski_job_finished()
        QMessageBox.warning(self, "Ski resort", f"Could not load {resort}:\n\n{msg}\n\nCheck the internet "
                                                "connection and pick the resort again.")

    def ski_options(self):
        def pen_if_ok(pid):
            pen = self.pens.get(pid)
            return pid if pen and pen_available(pid, pen) else None
        return {'runs': self.chk_ski_runs.isChecked(), 'lifts': self.chk_ski_lifts.isChecked(),
                'run_pen': self.combo_ski_run_pen.currentData() or 'base',
                'lift_pen': self.combo_ski_lift_pen.currentData() or 'pen_1',
                'by_difficulty': self.chk_ski_diff.isChecked(),
                'difficulty_pens': {'easy': pen_if_ok('green_07'), 'intermediate': pen_if_ok('blue_07'),
                                    'advanced': pen_if_ok('base'), 'expert': pen_if_ok('pen_1')}}

    def explain_missing_borders(self):
        """Once per session: why there are no country / state borders, and how to get them."""
        if HAS_GPD or self.__dict__.get('_borders_help_shown'):
            return
        self._borders_help_shown = True
        files = "".join(f"<li><a href='{u}'>{u.rsplit('/', 1)[-1]}</a></li>" for u in NATURAL_EARTH_FILES.values())
        QMessageBox.information(
            self, "Country and state borders",
            "<p>Borders and <b>Clip to region</b> need the Natural Earth outlines, which are downloaded "
            "automatically the first time the app starts. That didn't work:</p>"
            f"<p><i>{BORDERS_ERROR}</i></p>"
            "<p>Check the internet connection and restart the app, or download these two files and put them "
            f"(still zipped) in <b>{os.path.join(APP_DIR, NATURAL_EARTH_DIR)}</b>:</p><ul>{files}</ul>")

    def suggest_dem_prep(self):
        """Status-line tip when the current body's global elevation file hasn't been prepared yet."""
        p = DATA_FILES.get(self.active_planet, {}).get('dem')
        if p and dem_prep.needs_overviews(p):
            self.lbl_status.setText("Tip: Tools \u203a Speed up global elevation (one time) makes big areas "
                                    "and circle mode load several times faster.")

    def prepare_dem_files(self):
        todo = [f['dem'] for f in DATA_FILES.values() if dem_prep.needs_overviews(f['dem'])]
        if not todo:
            QMessageBox.information(self, "Global elevation", "Every global elevation file is already prepared.")
            return
        names = "\n".join(f"\u2022 {p}" for p in todo)
        if QMessageBox.question(
                self, "Speed up global elevation",
                "Build reduced-resolution copies (overviews) of:\n\n" + names +
                "\n\nThey are saved next to each file as .ovr (about a third of its size). The original files "
                "are not changed. This takes a minute or two per file.") != QMessageBox.StandardButton.Yes:
            return
        self.is_calculating = True
        self.btn_draw.setEnabled(False)
        self.progress_bar.setVisible(True)
        self._prep_job = _DemPrep(todo)
        self._prep_job.progress.connect(self.lbl_status.setText)
        self._prep_job.done.connect(self._on_dem_prep_done)
        self._prep_job.start()

    def _on_dem_prep_done(self, msg):
        clear_dem_cache()
        self.is_calculating = False
        self.btn_draw.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.lbl_status.setText(msg)

    def switch_planet(self):
        if self.selected_planet() == 'Solar System':
            self._enter_mission_mode()
            return
        if self.selected_planet() == 'Image':
            self._enter_image_mode()
            return
        if self.mission_mode:
            self._leave_mission_mode()
        self.active_planet = self.selected_planet()
        self.context_ui_update()
        if not self._loading:
            QTimer.singleShot(0, lambda p=self.active_planet: self.explain_missing_dem(p))
            if self.active_planet == 'Earth':
                QTimer.singleShot(0, self.explain_missing_borders)
            QTimer.singleShot(0, self.suggest_dem_prep)

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

        # Sharper regional data (Tools > Data sources): colour drawn on top, elevation coverage outlined
        self.draw_regional_overlays(redraw=False)

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

    def draw_regional_overlays(self, redraw=True):
        for a in getattr(self, 'region_artists', []):
            try:
                a.remove()
            except Exception:
                pass
        self.region_artists = []
        try:
            metas = regions.list_regions(self.active_planet)
        except Exception as e:
            print(f"Regional overlay skipped: {e}")
            metas = []
        for meta in metas:
            l, b, r, t = meta['bounds']
            if meta['kind'] == 'color':
                try:
                    import rasterio
                    with rasterio.open(meta['path']) as src:
                        k = max(1, int(np.ceil(max(src.width, src.height) / 2000.0)))
                        img = src.read([1, 2, 3] if src.count >= 3 else [1, 1, 1],
                                       out_shape=(3, max(1, src.height // k), max(1, src.width // k)))
                    img = np.transpose(img, (1, 2, 0))
                    if img.dtype != np.uint8:
                        img = np.clip(img / (257.0 if img.dtype == np.uint16 else 1.0), 0, 255).astype(np.uint8)
                    self.region_artists.append(self.ax_map.imshow(img, extent=[l, r, b, t], origin='upper',
                                                                  zorder=1, interpolation='bilinear'))
                except Exception as e:
                    print(f"Could not draw regional colour '{meta['name']}': {e}")
            rect = patches.Rectangle((l, b), r - l, t - b, fill=False, linewidth=0.9, zorder=3,
                                     edgecolor='#F4E9CF' if meta['kind'] == 'color' else '#7FD3FF',
                                     linestyle='-' if meta['kind'] == 'color' else '--')
            self.ax_map.add_patch(rect)
            self.region_artists.append(rect)
        # imshow can reset limits - keep the user's view
        if redraw:
            self.canvas_map.draw_idle()

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
        x, y = event.xdata, event.ydata
        if x is None or y is None: return
        self._zoom_map_at(x, y, scale)

    def _zoom_map_at(self, x, y, scale):
        """Zooms the source map about data point (x, y); scale < 1 zooms in."""
        cx, cy = self.ax_map.get_xlim(), self.ax_map.get_ylim()
        nw, nh = (cx[1] - cx[0]) * scale, (cy[1] - cy[0]) * scale
        rx, ry = (cx[1] - x) / (cx[1] - cx[0]), (cy[1] - y) / (cy[1] - cy[0])
        if self.mission_mode:
            if self.image_mode:
                return
            self.ax_map.set_xlim([x - nw * (1 - rx), x + nw * rx])
            self.ax_map.set_ylim([y - nh * (1 - ry), y + nh * ry])
        else:
            self.ax_map.set_xlim([max(-180, x - nw * (1 - rx)), min(180, x + nw * rx)])
            self.ax_map.set_ylim([max(-90, y - nh * (1 - ry)), min(90, y + nh * ry)])
        self.canvas_map.draw_idle()

    def on_select(self, eclick, erelease):
        if self.mission_mode:
            return
        x1, x2 = sorted([eclick.xdata, erelease.xdata]);
        y1, y2 = sorted([eclick.ydata, erelease.ydata])
        tiny = abs(erelease.x - eclick.x) < 4 or abs(erelease.y - eclick.y) < 4
        site_click = getattr(self, '_site_click', False)
        if self.scale_preset() is not None:
            # Fixed map scale: the centre of a drag places a frame of the preset width (plain clicks are
            # handled in on_mouse_release_map - matplotlib reports them here only sometimes).
            if not tiny and not site_click:
                self.frame_scale_at(0.5 * (x1 + x2), 0.5 * (y1 + y2))
            elif self.last_drag_coords:
                self.selector.set_visible(True)
                self.selector.extents = tuple(self.current_view_extent or self.last_drag_coords)
                self.canvas_map.draw_idle()
            return
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
        self.calibration_mode = False
        gw, gh = x2 - x1, y2 - y1
        if gw == 0 or gh == 0: return
        self.last_drag_coords = (x1, x2, y1, y2)          # remembered for re-framing and project files
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
        if self.selector.get_visible() and not self.mission_mode:
            self.selector.extents = (ex1, ex2, ey1, ey2)      # the box shows exactly what will be plotted
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

    # --- MAP FURNITURE ---
    def deco_options(self):
        return {'title': self.chk_deco_title.isChecked(), 'title_text': self.txt_title.text().strip(),
                'scale_bar': self.chk_deco_scale.isChecked(), 'north_arrow': self.chk_deco_north.isChecked(),
                'graticule': self.combo_graticule.currentText(), 'frame': self.chk_deco_frame.isChecked(),
                'pen': self.combo_deco_pen.currentData() or 'base',
                'text_size': self.combo_text_size.currentText(),
                'scale_units': self.combo_scale_units.currentText(),
                'line_mm': self._pen_line_mm(self.combo_deco_pen.currentData() or 'base')}

    def _pen_line_mm(self, pid):
        pen = self.pens.get(pid, self.pens['base'])
        return float(pen.get('line_mm', pen['width']))

    def site_label_cap_mm(self):
        k = TEXT_SCALES.get(self.combo_text_size.currentText(), 1.0) * \
            float(np.clip(min(self.pw, self.ph) / 100.0, 1.0, 2.0)) ** 0.5
        return max(min_cap_for_pen(self._pen_line_mm('red_038')), LABEL_CAP_MM * k)

    def _subtitle(self):
        e = self.current_view_extent
        if not e:
            return ""
        lat = 0.5 * (e[2] + e[3])
        lon = ((0.5 * (e[0] + e[1]) + 180) % 360) - 180
        parts = []
        if self.chk_sub_planet.isChecked():
            parts.append(self.active_planet.upper())
        if self.chk_sub_coords.isChecked():
            parts.append(f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}")
        if self.chk_sub_interval.isChecked() and self.sec_contours.is_enabled() and \
                any(i.get('layer') == 'contours' for i in self.map_segments):
            parts.append(f"{self.interval_ft:,} ft contours")
        return "  ·  ".join(parts)

    def _build_decorations(self):
        self.decor_segments, self.decor_masks = [], []
        if self.has_drawn and self.current_view_extent and not self.calibration_mode:
            try:
                self.decor_segments, self.decor_masks = build_decorations(
                    self.deco_options(), self.current_view_extent, self.pw, self.ph, self.chk_circle.isChecked(),
                    self.active_planet, subtitle=self._subtitle())
            except Exception as e:
                print(f"Decorations failed: {e}")
        self.dem_segments = self.map_segments + self.decor_segments

    def update_decorations(self, *_):
        if not self.has_drawn or self._loading:
            return
        if self.mission_mode:
            self.update_paper_plot()
            return
        self._build_decorations()
        self._cache_stroke_metrics()
        self.render_canvas()
        self.refresh_layer_panel()

    # --- STYLE PRESETS ---
    WIDGET_KEYS = None

    def _state_widgets(self):
        """key -> (widget, kind). kind: 'check' | 'value' | 'text' | 'combo' | 'data'."""
        return {
            'contours_on': (self.sec_contours.enable_box, 'check'), 'fill_on': (self.sec_fill.enable_box, 'check'),
            'density': (self.slider_density, 'value'), 'auto_index': (self.chk_auto_index, 'check'),
            'index_every': (self.spin_idx, 'value'), 'blur': (self.slider_blur, 'value'),
            'simplify': (self.slider_simp, 'value'), 'smooth': (self.slider_smooth, 'value'),
            'tanaka': (self.chk_tanaka, 'check'), 'ocean': (self.chk_ocean, 'check'),
            'blue_water': (self.chk_blue_water, 'check'), 'plot_borders': (self.chk_plot_borders, 'check'),
            'pattern': (self.combo_hatch, 'combo'), 'tone_source': (self.combo_source, 'combo'),
            'inks': (self.combo_inks, 'combo'), 'mono_pen': (self.combo_mono_pen, 'data'),
            'fill_pens': (self.list_inks, 'checklist'), 'paper_color': (self.combo_paper_color, 'paper'),
            'contour_pen': (self.combo_contour_pen, 'data'), 'index_pen': (self.combo_index_pen, 'data'),
            'water_pen': (self.combo_water_pen, 'data'), 'border_pen': (self.combo_border_pen, 'data'),
            'spacing': (self.spin_spacing, 'value'),
            'tone': (self.slider_tone, 'value'), 'auto_contrast': (self.chk_auto_contrast, 'check'),
            'join': (self.chk_join, 'check'),
            'deco_title': (self.chk_deco_title, 'check'), 'deco_title_text': (self.txt_title, 'text'),
            'deco_sub_planet': (self.chk_sub_planet, 'check'), 'deco_sub_coords': (self.chk_sub_coords, 'check'),
            'deco_sub_interval': (self.chk_sub_interval, 'check'),
            'deco_scale': (self.chk_deco_scale, 'check'), 'deco_north': (self.chk_deco_north, 'check'),
            'deco_graticule': (self.combo_graticule, 'combo'), 'deco_frame': (self.chk_deco_frame, 'check'),
            'deco_pen': (self.combo_deco_pen, 'data'), 'site_labels': (self.chk_site_labels, 'check'),
            'deco_text_size': (self.combo_text_size, 'combo'),
            'deco_scale_units': (self.combo_scale_units, 'combo'),
            'show_sites': (self.chk_sites, 'check'), 'mask_text': (self.chk_mask_text, 'check'),
            'show_map_borders': (self.chk_borders, 'check'),
            'ski_runs': (self.chk_ski_runs, 'check'), 'ski_lifts': (self.chk_ski_lifts, 'check'),
            'tile_cols': (self.spin_tile_cols, 'value'), 'tile_rows': (self.spin_tile_rows, 'value'),
            'tile_gap': (self.spin_tile_gap, 'value'), 'tile_cut': (self.chk_tile_cut, 'check'),
            'ski_by_difficulty': (self.chk_ski_diff, 'check'),
            'ski_run_pen': (self.combo_ski_run_pen, 'data'), 'ski_lift_pen': (self.combo_ski_lift_pen, 'data'),
        }

    def _get_widget_state(self):
        out = {}
        for key, (w, kind) in self._state_widgets().items():
            if kind == 'check':
                out[key] = w.isChecked()
            elif kind == 'value':
                out[key] = w.value()
            elif kind == 'text':
                out[key] = w.text()
            elif kind == 'combo':
                out[key] = w.currentText()
            elif kind == 'data':
                out[key] = w.currentData()
            elif kind == 'checklist':
                out[key] = self.selected_fill_pens()
            elif kind == 'paper':
                out[key] = self.current_paper_color()
        return out

    def _set_widget_state(self, state):
        widgets = self._state_widgets()
        for key, val in state.items():
            if key not in widgets or val is None:
                continue
            w, kind = widgets[key]
            try:
                if kind == 'check':
                    w.setChecked(bool(val))
                elif kind == 'value':
                    w.setValue(type(w.value())(val))
                elif kind == 'text':
                    w.setText(str(val))
                elif kind == 'combo':
                    if w.findText(str(val)) >= 0:
                        w.setCurrentText(str(val))
                elif kind == 'data':
                    i = w.findData(val)
                    if i >= 0:
                        w.setCurrentIndex(i)
                elif kind == 'checklist':
                    self.set_fill_pens(val)
                elif kind == 'paper':
                    self.set_paper_color(val)
            except Exception as e:
                print(f"Could not restore {key}: {e}")

    def reload_presets(self, select=None):
        self.presets = project_io.load_presets()
        self.combo_preset.blockSignals(True)
        self.combo_preset.clear()
        for name in self.presets:
            tag = "" if name not in project_io.BUILTIN_PRESETS else "  (built-in)"
            self.combo_preset.addItem(name + tag, name)
        if select:
            i = self.combo_preset.findData(select)
            if i >= 0:
                self.combo_preset.setCurrentIndex(i)
        self.combo_preset.blockSignals(False)

    def apply_selected_preset(self):
        name = self.combo_preset.currentData()
        if not name or name not in self.presets:
            return
        self._loading = True
        try:
            self._set_widget_state(self.presets[name])
        finally:
            self._loading = False
        self.context_ui_update()
        self.lbl_status.setText(f"Applied preset '{name}'.")
        if self.has_drawn:
            self.interval_auto_done = False             # let the density setting pick the interval
            self.trigger_recalc()

    def save_preset_dialog(self):
        name, ok = QInputDialog.getText(self, "Save style preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in project_io.BUILTIN_PRESETS:
            QMessageBox.information(self, "Presets", "That name is a built-in preset - choose another name.")
            return
        project_io.save_user_preset(name, self._get_widget_state())
        self.reload_presets(select=name)
        self.lbl_status.setText(f"Saved preset '{name}'.")

    def delete_selected_preset(self):
        name = self.combo_preset.currentData()
        if not name:
            return
        if name in project_io.BUILTIN_PRESETS:
            QMessageBox.information(self, "Presets", "Built-in presets can't be deleted.")
            return
        if project_io.delete_user_preset(name):
            self.reload_presets()

    # --- PROJECTS ---
    def get_project_state(self):
        st = self._get_widget_state()
        st.update({
            'planet': self.active_planet, 'region': self.combo_region.currentText(),
            'map_scale': self.combo_scale.currentText(),
            'paper_name': self.paper_name, 'custom_w': self.txt_cw.text(), 'custom_h': self.txt_ch.text(),
            'landscape': self.is_landscape, 'circle_mode': self.chk_circle.isChecked(),
            'circle_center_lon': getattr(self, 'circle_center_lon', 0.0),
            'selection': list(self.last_drag_coords) if self.last_drag_coords else None,
            'interval_ft': self.interval_ft, 'interval_auto_done': self.interval_auto_done,
            'selected_sites': {k: sorted(v) for k, v in self.selected_sites.items()},
            'layer_visible': self.layer_visible, 'custom_items': self.custom_items,
            'has_drawn': self.has_drawn and not self.calibration_mode,
            'map_view': [list(self.ax_map.get_xlim()), list(self.ax_map.get_ylim())],
            'segment_pens': self._painted_pens_state() if not self.mission_mode else None,
            'missions': {'checked': [k for k, v in self._mission_checked.items() if v], 'pens': dict(self._mission_pens),
                         'filter': self.combo_mission_filter.currentIndex(), 'path_style': self.combo_path_style.currentText(),
                         'year_marks': self.chk_year_marks.isChecked(), 'labels': self.chk_mission_labels.isChecked(),
                         'belt': self.chk_belt.isChecked(), 'title': self.txt_mission_title.text(),
                         'pen_labels': self.combo_mpen_labels.currentData(),
                         'pen_orbits': self.combo_mpen_orbits.currentData(),
                         'pen_belt': self.combo_mpen_belt.currentData()},
            'image': self._image_state(),
            'preview_view': [list(self.ax_prev.get_xlim()), list(self.ax_prev.get_ylim())] if self.has_drawn else None,
        })
        return st

    def apply_project_state(self, st):
        self._loading = True
        try:
            planet = st.get('planet', 'Earth')
            ms = st.get('missions') or {}
            if ms:
                self._mission_checked = {k: True for k in ms.get('checked', [])}
                self._mission_pens.update(ms.get('pens', {}))
                self.combo_mission_filter.setCurrentIndex(int(ms.get('filter', 0)))
                self.combo_path_style.setCurrentText(ms.get('path_style', 'Dashed'))
                self.chk_year_marks.setChecked(bool(ms.get('year_marks', True)))
                self.chk_mission_labels.setChecked(bool(ms.get('labels', True)))
                self.chk_belt.setChecked(bool(ms.get('belt', True)))
                self.txt_mission_title.setText(ms.get('title', ''))
                for cb, k in ((self.combo_mpen_labels, 'pen_labels'), (self.combo_mpen_orbits, 'pen_orbits'),
                              (self.combo_mpen_belt, 'pen_belt')):
                    i = cb.findData(ms.get(k))
                    if i >= 0:
                        cb.setCurrentIndex(i)
                self._fill_mission_table()
            if st.get('image'):
                self._apply_image_state(st['image'])
            self.combo_subject.setCurrentText(planet)
            if self.active_planet != planet:
                self.switch_planet()
            self.selected_sites = {k: set(v) for k, v in st.get('selected_sites', {}).items()}
            self.switch_planet()                       # rebuild site markers with the loaded selection
            self.txt_cw.setText(str(st.get('custom_w', '150')))
            self.txt_ch.setText(str(st.get('custom_h', '150')))
            self.is_landscape = bool(st.get('landscape', True))
            self.combo_paper.setCurrentText(st.get('paper_name', self.paper_name))
            self.paper_name = self.combo_paper.currentText()
            self.update_paper_dims()
            self._set_widget_state(st)
            self.combo_region.setCurrentText(st.get('region', "None (Plot Full Selection)"))
            i = self.combo_scale.findText(st.get('map_scale') or '')
            self.combo_scale.setCurrentIndex(max(0, i))
            if st.get('circle_mode'):
                self.circle_center_lon = float(st.get('circle_center_lon', 0.0))
                self.chk_circle.setChecked(True)
                self.update_circle_patches()
            else:
                self.chk_circle.setChecked(False)
                sel = st.get('selection')
                if sel:
                    self.last_drag_coords = tuple(sel)
                    self.process_selection(*sel)
                    self.selector.set_visible(True)
                    self.selector.extents = tuple(sel)
            mv = st.get('map_view')
            if mv:
                self.ax_map.set_xlim(*mv[0])
                self.ax_map.set_ylim(*mv[1])
            self.interval_ft = int(st.get('interval_ft', self.interval_ft))
            self.slider_int.blockSignals(True)
            self.txt_int.blockSignals(True)
            self.slider_int.setValue(min(max(self.interval_ft, self.slider_int.minimum()), self.slider_int.maximum()))
            self.txt_int.setText(str(self.interval_ft))
            self.slider_int.blockSignals(False)
            self.txt_int.blockSignals(False)
            self.layer_visible = dict(st.get('layer_visible', {}))
        finally:
            self._loading = False
        self.context_ui_update()
        self.canvas_map.draw_idle()
        if st.get('has_drawn') and self.current_view_extent and not self.mission_mode:
            # Re-render with the saved interval (no auto pick) and keep the saved annotations.
            self.interval_auto_done = True
            self.has_drawn = True
            self.custom_items = list(st.get('custom_items', []))
            self.ax_prev.set_xlim(0.0, 1.0)
            self.ax_prev.set_ylim(0.0, 1.0)
            self._pending_restore = {'pens': st.get('segment_pens'), 'view': st.get('preview_view')}
            self._start_render(fresh=False)
        if planet == 'Solar System' and st.get('has_drawn'):
            self.start_mission_plot()
        if planet == 'Image' and (self.image_rgb is not None or self.svg_layers is not None):
            self.start_image_plot(fresh=True)
        self.lbl_status.setText("Project loaded.")

    def new_project(self):
        if QMessageBox.question(self, "New project", "Clear the current map and annotations?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.project_path = None
        self.clear_selection()
        self.setWindowTitle("Topo Plotter Studio - Cartographer Edition")

    def save_project(self, save_as=False):
        path = self.project_path
        if save_as or not path:
            os.makedirs(PROJECTS_DIR, exist_ok=True)
            folder = os.path.dirname(self.project_path) if self.project_path else PROJECTS_DIR
            default = os.path.join(folder, f"{self.active_planet.lower()}_plot.topo.json")
            path, _ = QFileDialog.getSaveFileName(self, "Save project", default, "Topo projects (*.topo.json)")
            if not path:
                return
        try:
            project_io.save_project(path, self.get_project_state())
        except Exception as e:
            QMessageBox.critical(self, "Save project", f"Could not save:\n{e}")
            return
        self.project_path = path
        self._add_recent(path)
        self.setWindowTitle(f"Topo Plotter Studio - {os.path.basename(path)}")
        self.lbl_status.setText(f"Saved {os.path.basename(path)}.")

    def open_project(self, path=None):
        if not path:
            start = os.path.dirname(self.project_path) if self.project_path else PROJECTS_DIR
            path, _ = QFileDialog.getOpenFileName(self, "Open project", start,
                                                  "Topo projects (*.topo.json);;JSON (*.json)")
        if not path:
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, "Open project", f"Can't find:\n{path}")
            self._remove_recent(path)
            return
        try:
            st = project_io.load_project(path)
        except Exception as e:
            QMessageBox.critical(self, "Open project", f"Could not open:\n{e}")
            return
        self.apply_project_state(st)
        self.project_path = path
        self._add_recent(path)
        self.setWindowTitle(f"Topo Plotter Studio - {os.path.basename(path)}")

    # --- recent projects / painted lines ---
    RECENT_FILE = "recent_projects.json"

    @staticmethod
    def _rel_to_app(path):
        """Projects inside the app folder are remembered relative to it, so the list survives moving or
        cloning the folder; anything elsewhere keeps its absolute path."""
        p = os.path.abspath(path)
        try:
            if os.path.commonpath([p, APP_DIR]) == APP_DIR:
                return os.path.relpath(p, APP_DIR)
        except ValueError:                         # different drive on Windows
            pass
        return p

    def _recent(self):
        """Absolute paths of recent projects (stored relative to the app folder where possible)."""
        try:
            with open(self.RECENT_FILE) as f:
                items = json.load(f)
        except Exception:
            return []
        out = []
        for p in items:
            if isinstance(p, str):
                p = p if os.path.isabs(p) else os.path.join(APP_DIR, p)
                if os.path.abspath(p) not in out:
                    out.append(os.path.abspath(p))
        return out

    def _write_recent(self, items):
        try:
            with open(self.RECENT_FILE, 'w') as f:
                json.dump([self._rel_to_app(p) for p in items[:10]], f, indent=2)
        except Exception as e:
            print(f"Could not save recent projects: {e}")

    def _add_recent(self, path):
        path = os.path.abspath(path)
        self._write_recent([path] + [p for p in self._recent() if os.path.abspath(p) != path])

    def _remove_recent(self, path):
        self._write_recent([p for p in self._recent() if os.path.abspath(p) != os.path.abspath(path)])

    def _fill_recent_menu(self, menu):
        menu.clear()
        items = self._recent()
        if not items:
            a = menu.addAction("No recent projects")
            a.setEnabled(False)
            return
        for p in items:
            a = menu.addAction(os.path.basename(p))
            a.setToolTip(p)
            a.setStatusTip(p)
            a.triggered.connect(lambda _=False, path=p: self.open_project(path))
        menu.addSeparator()
        menu.addAction("Clear list").triggered.connect(lambda: self._write_recent([]))

    def _painted_pens_state(self):
        """Pen of every map stroke, run-length encoded, so Paint-tool edits survive save / load."""
        if not self.has_drawn or self.calibration_mode or not self.map_segments:
            return None
        runs = []
        for it in self.map_segments:
            pid = it.get('pen_id')
            if runs and runs[-1][0] == pid:
                runs[-1][1] += 1
            else:
                runs.append([pid, 1])
        return {'count': len(self.map_segments), 'runs': runs}

    def _apply_pending_restore(self):
        pend = getattr(self, '_pending_restore', None)
        self._pending_restore = None
        if not pend:
            return
        sp = pend.get('pens')
        if sp and sp.get('count') == len(self.map_segments):
            pens = [pid for pid, n in sp['runs'] for _ in range(int(n))]
            for it, pid in zip(self.map_segments, pens):
                if pid in self.pens:
                    it['pen_id'] = pid
        elif sp:
            self.lbl_status.setText("Map data changed since this project was saved - painted line colours not restored.")
        view = pend.get('view')
        if view:
            self.ax_prev.set_xlim(*view[0])
            self.ax_prev.set_ylim(*view[1])

    # --- PENS / CALIBRATION / DATA ---
    def open_pen_manager(self):
        dlg = PenManagerDialog(self.pens, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.pens = dlg.pens
        save_pens(self.pens)
        if self.active_pen_id not in self.pens:
            self.active_pen_id = 'base'
        self.rebuild_palette_ui()
        self.rebuild_ink_widgets()
        self._fill_mission_pen_combos()
        self._fill_mission_table()
        if self.has_drawn and self.sec_fill.is_enabled():
            self.recalc_timer.start(150)
        cur = self.combo_deco_pen.currentData()
        self.combo_deco_pen.blockSignals(True)
        self.combo_deco_pen.clear()
        self._add_pen_items(self.combo_deco_pen, list(self.pens.items()))
        i = self.combo_deco_pen.findData(cur) if cur else -1
        self.combo_deco_pen.setCurrentIndex(max(0, i))
        self.combo_deco_pen.blockSignals(False)
        if dlg.calibration_request:
            self.show_calibration_sheet(dlg.calibration_request)
            return
        if self.has_drawn:
            self.render_canvas()
            self.refresh_layer_panel()

    # ------------------------------------------------------------------
    # SOLAR SYSTEM / MISSIONS
    # ------------------------------------------------------------------
    MISSION_PEN_CYCLE = ['blue_038', 'red_038', 'base', 'green_07', 'orange_07', 'purple_07', 'pink_07', 'blue_07']

    def _build_missions_section(self, vbox):
        sec = self.sec_missions = CollapsibleSection("Missions")
        self.combo_mission_filter = QComboBox()
        self.combo_mission_filter.addItems(["All missions", "Johns Hopkins APL missions", "APL-built spacecraft only"])
        self.combo_mission_filter.setToolTip("APL missions: spacecraft built/operated by the Johns Hopkins Applied "
                                             "Physics Laboratory, or carrying an APL-built instrument.")
        self.combo_mission_filter.currentIndexChanged.connect(lambda _: self._fill_mission_table())
        sec.add(self._field("Show", self.combo_mission_filter))
        self.table_missions = QTableWidget(0, 3)
        self.table_missions.setHorizontalHeaderLabels(["", "Mission", "Pen"])
        self.table_missions.setWordWrap(False)
        self.table_missions.verticalHeader().setVisible(False)
        self.table_missions.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self.table_missions.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_missions.setMinimumHeight(230)
        self._mission_checked = {'voyager2': True}
        self._mission_pens = {}
        sec.add(self.table_missions)
        self.combo_path_style = QComboBox()
        self.combo_path_style.addItems(PATH_STYLES)
        self.combo_path_style.currentIndexChanged.connect(lambda _: self.update_mission_plot())
        sec.add(self._field("Trajectory line", self.combo_path_style))
        self.chk_year_marks = QCheckBox("Year markers (1 YR, 2 YR...)")
        self.chk_year_marks.setChecked(True)
        self.chk_mission_labels = QCheckBox("Labels with leader arrows")
        self.chk_mission_labels.setChecked(True)
        self.chk_belt = QCheckBox("Asteroid belt (stippled)")
        self.chk_belt.setChecked(True)
        for c in (self.chk_year_marks, self.chk_mission_labels, self.chk_belt):
            c.toggled.connect(lambda _: self.update_mission_plot())
            sec.add(c)
        # pens for everything that isn't a trajectory (light pens for dark paper)
        pen_box = QWidget()
        g = QGridLayout(pen_box)
        g.setContentsMargins(0, 0, 0, 0)
        g.setVerticalSpacing(4)
        self.combo_mpen_labels, self.combo_mpen_orbits, self.combo_mpen_belt = QComboBox(), QComboBox(), QComboBox()
        for r, (lab, cb) in enumerate((("Labels, Sun & planets", self.combo_mpen_labels),
                                        ("Orbits", self.combo_mpen_orbits), ("Asteroid belt", self.combo_mpen_belt))):
            l = QLabel(lab)
            l.setObjectName("fieldLabel")
            g.addWidget(l, r, 0)
            g.addWidget(cb, r, 1)
            cb.currentIndexChanged.connect(lambda _: self.update_mission_plot())
        g.setColumnStretch(1, 1)
        sec.add(self._field("Pens", pen_box))
        b_light = QPushButton("Use light pens (dark paper)")
        b_light.setObjectName("ghost")
        b_light.clicked.connect(self.use_light_mission_pens)
        sec.add(b_light)
        self.txt_mission_title = QLineEdit()
        self.txt_mission_title.setPlaceholderText("Title (blank = automatic)")
        self.txt_mission_title.textChanged.connect(lambda _: self.decor_timer.start(500))
        sec.add(self._field("Title", self.txt_mission_title))
        hint = QLabel("Real trajectories from NASA JPL Horizons. The first use of a mission downloads it (internet "
                      "needed, a few seconds); it is then cached in the 'missions' folder. Distances from the Sun are "
                      "compressed so inner and outer planets both fit. On dark paper, click 'Use light pens'.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        sec.add(hint)
        self.lbl_mission_status = QLabel("")
        self.lbl_mission_status.setObjectName("hint")
        self.lbl_mission_status.setWordWrap(True)
        sec.add(self.lbl_mission_status)
        sec.setVisible(False)
        vbox.addWidget(sec)
        self._fill_mission_table()
        self._fill_mission_pen_combos()

    def _fill_mission_pen_combos(self):
        for cb in (self.combo_mpen_labels, self.combo_mpen_orbits, self.combo_mpen_belt):
            self._fill_pen_combo(cb, 'base')

    def use_light_mission_pens(self):
        """Dark paper: lightest in-stock pens for labels / orbits / belt and light pens for trajectories."""
        paper_l = ink_lightness(self.current_paper_color())
        light = [(pid, p) for pid, p in self.ink_pens() if ink_lightness(p['color']) - paper_l >= 0.35]
        if not light:
            QMessageBox.information(self, "Pens", "None of your in-stock pens is light enough for this paper colour.")
            return
        light.sort(key=lambda kv: (-ink_lightness(kv[1]['color']), float(kv[1].get('line_mm', kv[1]['width']))))
        lightest = [pid for pid, p in light if ink_lightness(p['color']) >= ink_lightness(light[0][1]['color']) - 0.05]
        thin = min(lightest, key=lambda pid: float(self.pens[pid].get('line_mm', self.pens[pid]['width'])))
        for cb in (self.combo_mpen_labels, self.combo_mpen_orbits, self.combo_mpen_belt):
            cb.blockSignals(True)
            cb.setCurrentIndex(max(0, cb.findData(thin)))
            cb.blockSignals(False)
        # trajectories: keep each mission's pen if it shows on this paper, else the next light pen
        cycle = [pid for pid, _ in light]
        for i, (m, pid) in enumerate(self.mission_selection()):
            if ink_lightness(self.pens.get(pid, self.pens['base'])['color']) - paper_l < 0.35:
                self._mission_pens[m['key']] = cycle[(i + 1) % len(cycle)] if len(cycle) > 1 else cycle[0]
        self._fill_mission_table()
        self.update_mission_plot()

    def _mission_rows(self):
        f = self.combo_mission_filter.currentIndex()
        if f == 1:
            return [m for m in MS.MISSIONS if m.get('apl')]
        if f == 2:
            return [m for m in MS.MISSIONS if m.get('apl') == 'led']
        return list(MS.MISSIONS)

    def _fill_mission_table(self):
        self._sync_mission_table()                      # keep ticks / pens of rows about to be hidden
        rows = self._mission_rows()
        t = self.table_missions
        t.setRowCount(0)
        pens = [(pid, pen) for pid, pen in self.ink_pens()]
        for i, m in enumerate(rows):
            r = t.rowCount()
            t.insertRow(r)
            chk = QCheckBox()
            chk.setChecked(bool(self._mission_checked.get(m['key'])))
            chk.toggled.connect(lambda on, k=m['key']: (self._mission_checked.__setitem__(k, on),
                                                         self.update_mission_plot()))
            t.setCellWidget(r, 0, chk)
            tag = {'led': "  ★ APL", 'instrument': "  ☆ APL"}.get(m.get('apl'), "")
            years = m['launch'][:4] + ("" if not m.get('end') else f"–{m['end'][:4]}")
            it = QTableWidgetItem(f"{m['name']}  ({years}){tag}")
            cached = os.path.exists(MS.cache_path(m))
            tip = [f"{m['name']} - launched {m['launch']}",
                   "Flybys: " + (", ".join(f"{e[2]} {e[0][:4]}" for e in m['events']) or "-")]
            if m.get('apl_note'):
                tip.append("Johns Hopkins APL: " + m['apl_note'])
            if m.get('planned_after'):
                tip.append("Later parts of the path are JPL's planned trajectory.")
            tip.append("Downloaded" if cached else "Not downloaded yet (needs internet once)")
            it.setToolTip("\n".join(tip))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            t.setItem(r, 1, it)
            cb = SwatchCombo()
            cb.setFixedWidth(48)                         # swatch only when closed; names in the drop-down
            cb.view().setMinimumWidth(200)
            self._add_pen_items(cb, pens)
            want = self._mission_pens.get(m['key']) or next(
                (p for p in self.MISSION_PEN_CYCLE if cb.findData(p) >= 0), None)
            cb.setCurrentIndex(max(0, cb.findData(want) if want else -1))
            self._mission_pens[m['key']] = cb.currentData()
            cb.setToolTip(f"Pen for {m['name']}: " + self.pens.get(cb.currentData(), {}).get('name', ''))
            cb.currentIndexChanged.connect(lambda _, k=m['key'], c=cb, n=m['name']: (
                self._mission_pens.__setitem__(k, c.currentData()),
                c.setToolTip(f"Pen for {n}: " + self.pens.get(c.currentData(), {}).get('name', '')),
                self.update_mission_plot()))
            t.setCellWidget(r, 2, cb)

    def _sync_mission_table(self):
        pass                                            # state lives in _mission_checked / _mission_pens

    def mission_selection(self):
        return [(m, self._mission_pens.get(m['key']) or 'base') for m in MS.MISSIONS
                if self._mission_checked.get(m['key'])]

    def update_paper_plot(self):
        if self.image_mode:
            self.update_image_plot()
        else:
            self.update_mission_plot()

    def _enter_mission_mode(self):
        self.mission_mode = True
        self.image_mode = False
        self.active_planet = 'Solar System'
        if self.calibration_mode:
            self.calibration_mode = False
        self._render_token += 1
        self.last_drag_coords = None
        self.selector.set_visible(False)
        self.map_segments, self.decor_segments, self.decor_masks, self.dem_segments = [], [], [], []
        self.custom_items = []
        self.has_drawn = False
        self.context_ui_update()
        self._draw_mission_overview()
        self.ax_prev.clear()
        self.ax_prev.set_xticks([])
        self.ax_prev.set_yticks([])
        style_axes(self.fig_prev, self.ax_prev)
        self.canvas_prev.draw_idle()
        self.layer_panel.set_layers([], {})
        self.layer_panel.set_order([], '')
        self.lbl_status.setText("Solar System: tick missions, then Draw Mission Plot.")

    def _leave_mission_mode(self):
        self.mission_mode = False
        self.image_mode = False
        self.map_segments, self.dem_segments, self.decor_segments, self.decor_masks = [], [], [], []
        self.has_drawn = False
        self.current_view_extent = None
        self.selector.set_visible(True)

    def _draw_mission_overview(self):
        """Source-map panel in Solar System mode: true-scale orbits + downloaded trajectories."""
        ax = self.ax_map
        ax.clear()
        style_axes(self.fig_map, ax)
        ax.set_facecolor('#0B1020')
        for p in MS.PLANET_ORDER[:8]:
            o = MS.planet_orbit(p)
            ax.plot(o[:, 0], o[:, 1], color='#8A93A8', lw=0.6)
        ax.plot([0], [0], marker='*', color='#FFD34D', ms=9)
        lim = 32.0
        for m, pid in self.mission_selection():
            d = self.mission_data.get(m['key']) or MS.load_cached(m)
            if d is None:
                continue
            xyz = d[1]
            ax.plot(xyz[:, 0], xyz[:, 1], color=self.pens.get(pid, self.pens['base'])['color'], lw=1.0)
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_aspect('equal')
        ax.set_title("Solar system (true scale, AU) - scroll to zoom", fontsize=8, color='#8A93A8')
        self.canvas_map.draw_idle()

    def start_mission_plot(self):
        sel = self.mission_selection()
        if not sel:
            self.lbl_status.setText("Tick at least one mission first.")
            return
        missing = [m for m, _ in sel if m['key'] not in self.mission_data and MS.load_cached(m) is None]
        for m, _ in sel:
            if m['key'] not in self.mission_data:
                d = MS.load_cached(m)
                if d is not None:
                    self.mission_data[m['key']] = d
        if not missing:
            self.update_mission_plot(fresh=True)
            return
        self.is_calculating = True
        self.btn_draw.setEnabled(False)
        self.progress_bar.setVisible(True)
        self._mission_job = _MissionFetch(missing)
        self._mission_job.progress.connect(self.lbl_status.setText)
        self._mission_job.done.connect(self._on_missions_fetched)
        self._mission_job.failed.connect(self._on_missions_failed)
        self._mission_job.start()

    def _on_missions_fetched(self, data):
        self.mission_data.update(data)
        self.is_calculating = False
        self.btn_draw.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._fill_mission_table()
        self.update_mission_plot(fresh=True)

    def _on_missions_failed(self, msg):
        self.is_calculating = False
        self.btn_draw.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.lbl_status.setText("Download failed.")
        QMessageBox.warning(self, "Missions", f"Could not download the trajectory from NASA JPL Horizons:\n\n{msg}\n\n"
                                              "Check the internet connection and try again.")

    def update_mission_plot(self, fresh=False):
        if not self.mission_mode or self.image_mode or self._loading or self.calibration_mode:
            return
        if not fresh and not self.has_drawn:
            return
        for m, _ in self.mission_selection():
            if m['key'] not in self.mission_data:
                d = MS.load_cached(m)                   # already downloaded: no need to press Draw
                if d is not None:
                    self.mission_data[m['key']] = d
        sel = [(m, p) for m, p in self.mission_selection() if m['key'] in self.mission_data]
        waiting = [m['name'] for m, _ in self.mission_selection() if m['key'] not in self.mission_data]
        if not sel:
            if self.has_drawn:
                self.lbl_status.setText("Tick a mission and click Draw Mission Plot (new ones need a download).")
            return
        lab_pen = self.combo_mpen_labels.currentData() or 'base'
        opts = {'pen': lab_pen, 'line_mm': self._pen_line_mm(lab_pen),
                'pen_orbits': self.combo_mpen_orbits.currentData() or lab_pen,
                'pen_belt': self.combo_mpen_belt.currentData() or lab_pen,
                'belt_line_mm': self._pen_line_mm(self.combo_mpen_belt.currentData() or lab_pen),
                'text_size': self.combo_text_size.currentText(), 'title_text': self.txt_mission_title.text().strip(),
                'path_style': self.combo_path_style.currentText(), 'year_marks': self.chk_year_marks.isChecked(),
                'labels': self.chk_mission_labels.isChecked(), 'belt': self.chk_belt.isChecked()}
        try:
            segs, masks, info = build_mission_plot(sel, self.mission_data, opts, self.pw, self.ph)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.lbl_status.setText(f"Mission plot failed: {e}")
            return
        if opts and self.chk_deco_frame.isChecked():
            f = 2.0
            frame = np.array([[f, f], [self.pw - f, f], [self.pw - f, self.ph - f], [f, self.ph - f], [f, f]])
            segs.append({'type': 'border', 'layer': 'decor', 'seg': frame - [0, self.ph / 2], 'level': 0,
                         'pen_id': opts['pen_orbits']})
        self.current_view_extent = [0.0, self.pw, -self.ph / 2.0, self.ph / 2.0]
        self.map_segments = segs
        self.decor_segments, self.decor_masks = [], masks
        self.dem_segments = list(segs)
        self.has_drawn = True
        if fresh:
            self.custom_items = []
            self.ax_prev.set_xlim(0.0, 1.0)
            self.ax_prev.set_ylim(0.0, 1.0)
        self._cache_stroke_metrics()
        self.render_canvas()
        self.refresh_layer_panel()
        self._draw_mission_overview()
        names = ", ".join(m['name'] for m, _ in sel)
        clipped = [x['name'] for x in info.get('missions', []) if x.get('clipped')]
        self.lbl_status.setText(f"Mission plot: {names} ({len(segs):,} strokes)."
                                + (f" {', '.join(clipped)} continue beyond the edge." if clipped else "")
                                + (f" Click Draw Mission Plot to download {', '.join(waiting)}." if waiting else ""))

    # --- IMAGE TRACING (logos, drawings, photos -> outlines / sticker cut lines) ---
    def _build_image_section(self, vbox):
        sec = self.sec_image = CollapsibleSection("Image")
        row = QHBoxLayout()
        b_open = QPushButton("Open image / SVG…")
        b_open.clicked.connect(self.open_image)
        self.lbl_image_file = QLabel("No image loaded")
        self.lbl_image_file.setObjectName("hint")
        self.lbl_image_file.setWordWrap(True)
        row.addWidget(b_open)
        row.addWidget(self.lbl_image_file, 1)
        sec.add(self._field("Picture", row))
        self.combo_image_mode = QComboBox()
        self.combo_image_mode.addItems(IT.MODES)
        self.combo_image_mode.setToolTip("Outlines: flat-colour artwork (logos, clip-art, line drawings) - every colour "
                                         "region gets a clean closed outline.\nEdges: photos - draws the strongest "
                                         "edges as single lines.")
        sec.add(self._field("Method", self.combo_image_mode))
        self.spin_image_colours = QSpinBox()
        self.spin_image_colours.setRange(2, 8)
        self.spin_image_colours.setValue(2)
        self.spin_image_colours.setToolTip("How many colours the artwork is reduced to (the background counts as one).\n"
                                           "2 = black-and-white trace.")
        self.lbl_image_colours = sec.add(self._field("Colours (incl. background)", self.spin_image_colours))
        self.chk_image_invert = QCheckBox("Trace the light parts instead")
        self.chk_image_invert.setToolTip("For white-on-dark artwork in 2-colour mode.")
        sec.add(self.chk_image_invert)

        def slider(lo, hi, val, fmt):
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(val)
            lab = QLabel(fmt(val))
            sl.valueChanged.connect(lambda v: lab.setText(fmt(v)))
            return sl, self._slider_row(sl, lab)

        self.slider_image_size, w = slider(20, 100, 100, lambda v: f"{v}%")
        sec.add(self._field("Size on the page", w))
        self.slider_image_smooth, w = slider(0, 10, 3, lambda v: "sharp" if v == 0 else f"{v}")
        self.slider_image_smooth.setToolTip("Rounds off jagged pixel edges. Higher = smoother, less detail.")
        sec.add(self._field("Smoothing", w))
        self.slider_image_speck, w = slider(0, 20, 2, lambda v: f"{v * 0.25:g} mm²")
        self.slider_image_speck.setToolTip("Shapes smaller than this are dropped (dust, JPEG noise).")
        self.lbl_image_speck = sec.add(self._field("Ignore specks under", w))
        self.slider_image_edges, w = slider(1, 10, 5, lambda v: f"{v}")
        self.slider_image_edges.setToolTip("Higher = more (weaker) edges are drawn.")
        self.lbl_image_edges = sec.add(self._field("Edge detail", w))
        self.chk_image_fill = QCheckBox("Fill shapes with hatching")
        sec.add(self.chk_image_fill)
        self.slider_image_fill, w = slider(3, 30, 8, lambda v: f"{v / 10:g} mm")
        self.lbl_image_fill = sec.add(self._field("Hatch spacing", w))
        self.chk_image_cut = QCheckBox("Sticker cut line around the design")
        self.chk_image_cut.setToolTip("An offset outline around the whole design - draw it with a pen as a cutting "
                                      "guide, or use the Cutter tool pen.")
        sec.add(self.chk_image_cut)
        self.slider_image_cut, w = slider(1, 20, 4, lambda v: f"{v / 2:g} mm")
        self.lbl_image_cut = sec.add(self._field("Cut line offset", w))
        self.table_image = QTableWidget(0, 2)
        self.table_image.setHorizontalHeaderLabels(["Traced layer", "Pen"])
        self.table_image.verticalHeader().setVisible(False)
        self.table_image.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self.table_image.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_image.setMinimumHeight(120)
        sec.add(self.table_image)
        hint = QLabel("PNG / JPG / BMP / GIF are traced; transparent areas count as paper. SVG files (e.g. Inkscape) "
                      "are used as drawn: every layer becomes a plot layer, scaled and centred on the page. Each "
                      "layer gets its own pen - right-click a layer in the Layers tab to change it too.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        sec.add(hint)
        for wdg in (self.combo_image_mode,):
            wdg.currentIndexChanged.connect(lambda _: (self._update_image_controls(), self.schedule_image_trace()))
        self.spin_image_colours.valueChanged.connect(lambda _: (self._update_image_controls(),
                                                                self.schedule_image_trace()))
        for c in (self.chk_image_invert, self.chk_image_fill, self.chk_image_cut):
            c.toggled.connect(lambda _: (self._update_image_controls(), self.schedule_image_trace()))
        for sl in (self.slider_image_size, self.slider_image_smooth, self.slider_image_speck, self.slider_image_edges,
                   self.slider_image_fill, self.slider_image_cut):
            sl.valueChanged.connect(lambda _: self.schedule_image_trace())
        self.image_timer = QTimer()
        self.image_timer.setSingleShot(True)
        self.image_timer.timeout.connect(lambda: self.start_image_plot(fresh=False))
        sec.setVisible(False)
        vbox.addWidget(sec)
        self._update_image_controls()

    def _update_image_controls(self):
        svg = self.svg_layers is not None
        for w in (self.combo_image_mode.parentWidget(), self.slider_image_smooth.parentWidget().parentWidget(),
                  self.chk_image_invert, self.lbl_image_colours, self.chk_image_fill, self.chk_image_cut,
                  self.lbl_image_speck, self.lbl_image_edges, self.lbl_image_fill, self.lbl_image_cut):
            w.setVisible(not svg)
        self.table_image.setHorizontalHeaderLabels(["SVG layer" if svg else "Traced layer", "Pen"])
        if self.image_mode:
            self.btn_draw.setText("Place SVG" if svg else "Trace Image")
        if svg:
            return
        edges = self.combo_image_mode.currentText().startswith("Edges")
        for w in (self.lbl_image_colours, self.chk_image_fill, self.chk_image_cut, self.lbl_image_speck):
            w.setVisible(not edges)
        self.chk_image_invert.setVisible(not edges and self.spin_image_colours.value() == 2)
        self.lbl_image_edges.setVisible(edges)
        self.lbl_image_fill.setVisible(not edges and self.chk_image_fill.isChecked())
        self.lbl_image_cut.setVisible(not edges and self.chk_image_cut.isChecked())

    def image_options(self):
        e = self.slider_image_edges.value()
        return {'mode': self.combo_image_mode.currentText(), 'colours': self.spin_image_colours.value(),
                'invert': self.chk_image_invert.isChecked(), 'size_pct': self.slider_image_size.value(),
                'smooth_px': 0.5 * self.slider_image_smooth.value(),
                'min_area_mm2': 0.25 * self.slider_image_speck.value(), 'simplify_mm': 0.05, 'margin_mm': 6.0,
                'fill': self.chk_image_fill.isChecked(), 'fill_spacing_mm': self.slider_image_fill.value() / 10.0,
                'cutline': self.chk_image_cut.isChecked(), 'cut_offset_mm': self.slider_image_cut.value() / 2.0,
                'edge_sigma': 2.0, 'edge_high': 0.36 - 0.03 * e, 'edge_low': 0.4 * (0.36 - 0.03 * e)}

    def open_image(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open image or SVG", os.path.dirname(self.image_path or "") or "",
                "Images and SVG (*.svg *.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;SVG (*.svg)")
            if not path:
                return
        if path.lower().endswith('.svg'):
            try:
                layers = svg_import.load_svg(path)
            except Exception as e:
                QMessageBox.warning(self, "SVG", f"Could not read the SVG:\n{type(e).__name__}: {e}")
                return
            self.image_path, self.image_rgb, self.svg_layers = path, None, layers
            n = sum(len(l['polys']) for l in layers)
            self.lbl_image_file.setText(f"{os.path.basename(path)}  ({len(layers)} layer(s), {n:,} paths)")
        else:
            try:
                rgb = IT.load_image(path)
            except Exception as e:
                QMessageBox.warning(self, "Image", f"Could not open the image:\n{e}")
                return
            self.image_path, self.image_rgb, self.svg_layers = path, rgb, None
            self.lbl_image_file.setText(f"{os.path.basename(path)}  ({rgb.shape[1]}×{rgb.shape[0]} px)")
        self._image_cache = {}
        self._image_pens = {}
        self._image_layers = []
        self._update_image_controls()
        if self.combo_subject.currentText() != 'Image':
            self.combo_subject.setCurrentText('Image')     # -> switch_planet -> _enter_image_mode
        else:
            self._draw_image_overview()
        if not self._loading:
            self.start_image_plot(fresh=True)

    def _enter_image_mode(self):
        self._enter_mission_mode()                     # shared paper-space setup
        self.image_mode = True
        self.active_planet = 'Image'
        self.context_ui_update()
        self._draw_image_overview()
        has_src = self.image_rgb is not None or self.svg_layers is not None
        self.lbl_status.setText("Image: click 'Open image / SVG…' in the Image section." if not has_src
                                else f"Image: click {self.btn_draw.text()}.")
        if has_src and not self._loading:
            self.start_image_plot(fresh=True)

    def _draw_image_overview(self):
        ax = self.ax_map
        ax.clear()
        style_axes(self.fig_map, ax)
        if self.svg_layers is not None:
            ax.set_facecolor('#F4F1EA')                 # paper-like, so black layers stay visible
            for l in self.svg_layers:
                ax.add_collection(LineCollection(l['polys'], colors=[l['color']], linewidths=0.6))
            x0, y0, x1, y1 = svg_import.bounds(self.svg_layers)
            ax.set_xlim(x0, x1)
            ax.set_ylim(y1, y0)                         # SVG y points down
            ax.set_aspect('equal')
            ax.set_title(os.path.basename(self.image_path or ""), fontsize=8, color='#8A93A8')
        elif self.image_rgb is not None:
            ax.imshow(self.image_rgb, origin='upper', interpolation='bilinear')
            ax.set_title(os.path.basename(self.image_path or ""), fontsize=8, color='#8A93A8')
        else:
            ax.text(0.5, 0.5, "Open an image in the Image section", ha='center', va='center', color='#8A93A8',
                    transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
        self.canvas_map.draw_idle()

    def schedule_image_trace(self):
        if self.image_mode and self.has_drawn and not self._loading:
            self.image_timer.start(350)

    def start_image_plot(self, fresh=False):
        if not self.image_mode or self.calibration_mode or self._loading:
            return
        if self.svg_layers is not None:
            # vector artwork: no tracing - fit the layers to the page and plot them
            self._image_layers, scale = svg_import.fit_layers(self.svg_layers, self.pw, self.ph,
                                                              self.slider_image_size.value())
            self._image_info = {'svg_scale': scale}
            self._fill_image_table()
            self.update_image_plot(fresh=fresh or not self.has_drawn)
            return
        if self.image_rgb is None:
            self.open_image()
            return
        if self._image_job is not None and self._image_job.isRunning():
            self._image_pending = True                 # re-trace with the newest settings when this one ends
            self._image_pending_fresh = fresh or getattr(self, '_image_pending_fresh', False)
            return
        self._image_token += 1
        self.progress_bar.setVisible(True)
        self.lbl_status.setText("Tracing image…")
        job = self._image_job = _ImageTrace(self.image_rgb, self.image_options(), self.pw, self.ph,
                                            self._image_cache, self._image_token)
        job.done.connect(lambda layers, info, tok, f=fresh: self._on_image_traced(layers, info, tok, f))
        job.failed.connect(self._on_image_failed)
        self._workers.add(job)
        job.finished.connect(lambda j=job: self._workers.discard(j))
        job.start()

    def _on_image_traced(self, layers, info, token, fresh):
        self.progress_bar.setVisible(False)
        if not self.image_mode or token != self._image_token:
            return
        # a layer key now holding a different colour (colour count changed) gets a fresh default pen
        old = {l['key']: l['color'] for l in self._image_layers}
        for l in layers:
            if l['key'] in old and np.sum((np.array(old[l['key']]) - np.array(l['color'])) ** 2) > 0.03:
                self._image_pens.pop(l['key'], None)
        self._image_layers = layers
        self._image_info = info
        if self._image_pending:
            self._image_pending = False
            f = getattr(self, '_image_pending_fresh', False)
            self._image_pending_fresh = False
            self.start_image_plot(fresh=f)
            return
        self._fill_image_table()
        self.update_image_plot(fresh=fresh or not self.has_drawn)

    def _on_image_failed(self, msg):
        self.progress_bar.setVisible(False)
        self._image_pending = False
        self.lbl_status.setText(f"Image trace failed: {msg}")

    def _image_pen_choices(self, key):
        pens = sorted(self.ink_pens(), key=lambda kv: ink_lightness(kv[1]['color']))
        if key == 'img:cut':
            pens += [(pid, p) for pid, p in self.pens.items() if pid in TOOL_PEN_IDS]
        return pens

    def _default_image_pen(self, layer):
        pens = self.ink_pens()                         # (the Cutter tool is offered for the cut line, never default)
        if not pens:
            return 'base'
        def rgb(hx):
            hx = hx.lstrip('#')
            return np.array([int(hx[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])

        paper = rgb(self.current_paper_color())
        if layer['key'] == 'img:cut':                  # the pen that contrasts most with the paper
            return max(pens, key=lambda kv: np.sum((rgb(kv[1]['color']) - paper) ** 2))[0]
        want = np.array(layer['color'])
        if np.sum((want - paper) ** 2) < 0.1:          # artwork colour ~ paper colour: use the contrast pen
            want = 1.0 - paper
        return min(pens, key=lambda kv: np.sum((rgb(kv[1]['color']) - want) ** 2))[0]

    def paper_is_light(self):
        return ink_lightness(self.current_paper_color()) >= 0.5

    def _fill_image_table(self):
        t = self.table_image
        t.setRowCount(0)
        for layer in self._image_layers:
            k = layer['key']
            if k not in self._image_pens or self._image_pens[k] not in self.pens:
                self._image_pens[k] = self._default_image_pen(layer)
            r = t.rowCount()
            t.insertRow(r)
            hx = '#%02X%02X%02X' % tuple(int(round(255 * v)) for v in layer['color'])
            it = QTableWidgetItem(swatch_icon([hx]), f"{layer['label']}  ({len(layer['polys'])} strokes)")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            it.setToolTip(f"Colour in the picture: {hx}")
            t.setItem(r, 0, it)
            cb = SwatchCombo()
            cb.setFixedWidth(48)
            cb.view().setMinimumWidth(200)
            self._add_pen_items(cb, self._image_pen_choices(k))
            cb.setCurrentIndex(max(0, cb.findData(self._image_pens[k])))
            cb.setToolTip("Pen: " + self.pens.get(cb.currentData(), {}).get('name', ''))
            cb.currentIndexChanged.connect(lambda _, key=k, c=cb: (
                self._image_pens.__setitem__(key, c.currentData()),
                c.setToolTip("Pen: " + self.pens.get(c.currentData(), {}).get('name', '')),
                self.update_image_plot()))
            t.setCellWidget(r, 1, cb)

    def update_image_plot(self, fresh=False):
        if not self.image_mode or self._loading or self.calibration_mode or not self._image_layers:
            return
        y0 = self.ph / 2.0
        segs = []
        for layer in self._image_layers:
            pid = self._image_pens.get(layer['key']) or 'base'
            for p in layer['polys']:
                if len(p) >= 2:
                    segs.append({'type': 'border', 'layer': layer['key'], 'seg': np.asarray(p) - [0.0, y0],
                                 'level': 0, 'pen_id': pid})
        if self.chk_deco_frame.isChecked():
            f = 2.0
            frame = np.array([[f, f], [self.pw - f, f], [self.pw - f, self.ph - f], [f, self.ph - f], [f, f]])
            fpid = self._image_pens.get(self._image_layers[0]['key'], 'base') if self._image_layers else 'base'
            segs.append({'type': 'border', 'layer': 'decor', 'seg': frame - [0, y0], 'level': 0, 'pen_id': fpid})
        self.current_view_extent = [0.0, self.pw, -y0, y0]
        self.map_segments = segs
        self.decor_segments, self.decor_masks = [], []
        self.dem_segments = list(segs)
        self.has_drawn = True
        if fresh:
            self.custom_items = []
            self.ax_prev.set_xlim(0.0, 1.0)
            self.ax_prev.set_ylim(0.0, 1.0)
        self._cache_stroke_metrics()
        self.render_canvas()
        self.refresh_layer_panel()
        n = sum(len(l['polys']) for l in self._image_layers)
        self.lbl_status.setText(f"Image traced: {len(self._image_layers)} layer(s), {n:,} strokes. "
                                "Change pens in the Image section or right-click a layer.")

    def _image_state(self):
        return {'path': self.image_path, 'mode': self.combo_image_mode.currentText(),
                'colours': self.spin_image_colours.value(), 'invert': self.chk_image_invert.isChecked(),
                'size': self.slider_image_size.value(), 'smooth': self.slider_image_smooth.value(),
                'speck': self.slider_image_speck.value(), 'edges': self.slider_image_edges.value(),
                'fill': self.chk_image_fill.isChecked(), 'fill_spacing': self.slider_image_fill.value(),
                'cut': self.chk_image_cut.isChecked(), 'cut_offset': self.slider_image_cut.value(),
                'pens': dict(self._image_pens)}

    def _apply_image_state(self, st):
        self.combo_image_mode.setCurrentText(st.get('mode', IT.MODES[0]))
        self.spin_image_colours.setValue(int(st.get('colours', 2)))
        self.chk_image_invert.setChecked(bool(st.get('invert')))
        for sl, k in ((self.slider_image_size, 'size'), (self.slider_image_smooth, 'smooth'),
                      (self.slider_image_speck, 'speck'), (self.slider_image_edges, 'edges'),
                      (self.slider_image_fill, 'fill_spacing'), (self.slider_image_cut, 'cut_offset')):
            if k in st:
                sl.setValue(int(st[k]))
        self.chk_image_fill.setChecked(bool(st.get('fill')))
        self.chk_image_cut.setChecked(bool(st.get('cut')))
        self._update_image_controls()
        path = st.get('path')
        if path and os.path.exists(path):
            self.open_image(path)                       # _loading is set: loads without tracing
            self._image_pens = dict(st.get('pens') or {})
        elif path:
            self.lbl_image_file.setText(f"Missing: {path}")

    def show_calibration_sheet(self, pen_ids):
        if self.is_calculating:
            return
        if not self.calibration_mode:
            self._saved_selection = (self.current_view_extent, self.last_drag_coords, self.has_drawn)
        if self.chk_circle.isChecked():
            self.chk_circle.setChecked(False)
        segs, extent = build_calibration_sheet(self.pens, pen_ids, self.pw, self.ph)
        self._render_token += 1
        self.calibration_mode = True
        self.current_view_extent = extent
        self.map_segments = segs
        self.decor_segments, self.decor_masks = [], []
        self.dem_segments = list(segs)
        self.custom_items = []
        self.has_drawn = True
        self._cache_stroke_metrics()
        self.ax_prev.set_xlim(0.0, 1.0)
        self.ax_prev.set_ylim(0.0, 1.0)
        self.render_canvas()
        self.refresh_layer_panel()
        self.lbl_status.setText("Calibration sheet ready - export the G-code and plot it. Draw Map returns to "
                                "your map.")

    def exit_calibration(self):
        self.calibration_mode = False
        saved = self._saved_selection
        self.map_segments, self.dem_segments = [], []
        if saved and saved[1]:
            self.last_drag_coords = saved[1]
            self.process_selection(*saved[1])
        elif saved and saved[0]:
            self.current_view_extent = saved[0]

    def open_data_dialog(self):
        ext = self.current_view_extent if (self.current_view_extent and not self.chk_circle.isChecked()
                                           and not self.calibration_mode) else None
        dlg = DataDialog(self.active_planet, ext, self)
        dlg.exec()
        if dlg.changed:
            xl, yl = self.ax_map.get_xlim(), self.ax_map.get_ylim()
            self.draw_regional_overlays(redraw=False)
            self.ax_map.set_xlim(xl)
            self.ax_map.set_ylim(yl)
            self.canvas_map.draw_idle()
            if self.has_drawn and not self.calibration_mode:
                self.trigger_recalc()

    # --- EXPORT PIPELINES ---
    def _text_mask_boxes(self):
        boxes = [shp_box(*m) for m in self.decor_masks]
        if self.chk_mask_text.isChecked():
            for item in getattr(self, 'custom_items', []):
                if item['type'] == 'text':
                    _, bbox = self.text_item_polys(item)
                    if bbox:
                        pad = item['scale'] * 0.2
                        boxes.append(shp_box(bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad))
        return boxes

    @staticmethod
    def text_item_polys(item):
        """'Add text' annotation -> polylines in data coords (single-stroke font, outline fallback) and
        its bounding box (x0, y0, x1, y1). item['scale'] is the text size in data units (about 1 em)."""
        trans = Affine2D().scale(item['scale']).rotate_deg(item['rot']).translate(item['x'], item['y'])
        res = stroke_text(item['text'], 0.0, 0.0, 0.72)
        if res is not None:
            local = res[0]
        else:
            tp = TextPath((0, 0), item['text'], size=1)
            local = [np.asarray(p) for p in tp.to_polygons() if len(p) >= 2]
        polys = [trans.transform(np.asarray(p, dtype=float)) for p in local if len(p) >= 2]
        if not polys:
            return [], None
        allp = np.vstack(polys)
        return polys, (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())

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

    # Layers made of lettering / fine marks: exported without vpype's simplify, merge and 2 mm length filter
    # (those would delete the short strokes that make up small letters, dots and degree signs).
    FINE_LAYERS = ('decor', 'sites', 'calibration', 'labels', 'markers', 'trajectory', 'belt', 'ski_lifts')

    def _lines_for_pen(self, p_id, text_boxes, split=False):
        """All plot lines (in data coords) for one pen, with text blanking applied.
        split=True returns (map_lines, fine_lines, one_way_lines): fine_lines are lettering and map furniture,
        one_way_lines are sine squiggles, whose drawing direction must be kept."""
        lines, fine, one_way = [], [], []
        for item in getattr(self, 'dem_segments', []):
            if item.get('pen_id') == p_id and self.is_visible(item):
                if item.get('layer') in self.FINE_LAYERS or str(item.get('layer', '')).startswith('img:'):
                    fine.append(np.asarray(item['seg']))         # furniture / labels are never blanked
                elif item.get('type') == 'stipple':
                    lines.append(np.asarray(item['seg']))
                elif split and item.get('type') == 'squiggle':
                    one_way.extend(self._mask_line(item['seg'], text_boxes))
                else:
                    lines.extend(self._mask_line(item['seg'], text_boxes))
        for item in (getattr(self, 'custom_items', []) if self.annotations_visible() else []):
            if item.get('pen_id') != p_id: continue
            if item['type'] == 'line':
                lines.extend(self._mask_line(item['points'], text_boxes))
            elif item['type'] == 'text':
                fine.extend(self.text_item_polys(item)[0])
        return (lines, fine, one_way) if split else lines + fine

    @staticmethod
    def _export_figure(w_mm, h_mm):
        """Blank export figure. Its background patch is hidden: in the SVG it is a page-sized rectangle,
        which vpype would read as a stroke and draw (with every pen) along the paper's edge."""
        fig = Figure(figsize=(w_mm / 25.4, h_mm / 25.4))
        fig.patch.set_visible(False)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis('off')
        return fig, ax

    def _make_export_axes(self):
        tile = getattr(self, '_export_tile', None)
        if tile:
            # one page of a tiled plot: exactly that page's part of the map, at the same scale
            export_fig, export_ax = self._export_figure(tile['w'], tile['h'])
            export_ax.set_xlim(*tile['xlim'])
            export_ax.set_ylim(*tile['ylim'])
            return export_fig, export_ax
        export_fig, export_ax = self._export_figure(self.pw, self.ph)
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
        out_svg, _ = QFileDialog.getSaveFileName(self, "Save Editable SVG", os.path.join(desktop_dir(), default_name),
                                                 "SVG Files (*.svg)")
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
        order = self.plot_order_pens()
        if not order:
            QMessageBox.warning(self, "No Data", "Nothing visible to plot - check the layer list.")
            return
        rows = []
        for p_id in order:
            n = sum(1 for it in self.dem_segments if it['pen_id'] == p_id and self.is_visible(it))
            if self.annotations_visible():
                n += sum(1 for it in self.custom_items if it['pen_id'] == p_id)
            pen = self.pens[p_id]
            rows.append({'pen_id': p_id, 'name': pen['name'], 'color': pen['color'], 'strokes': n,
                         'feed': pen.get('feed', exporter.DEFAULT_DRAW_FEED)})
        mx, my = exporter.max_plot_area()
        note = (f"Paper {self.tile_w:g} x {self.tile_h:g} mm. With the G28 + G92 (-{exporter.GCODE_OFFSET_MM[0]:g}, "
                f"-{exporter.GCODE_OFFSET_MM[1]:g}) header the reachable plot area is about {mx:g} x {my:g} mm "
                f"(edit BED_MM in config.py if your bed differs).")
        if self.tile_w > mx + 1e-6 or self.tile_h > my + 1e-6:
            note = "⚠ " + note + " This paper size is larger than that - check the bed check after export."
        if self.is_tiled():
            cols, rws, gap = self.tile_layout()
            note = (f"Tiled: {cols} \u00d7 {rws} pages of {self.tile_w:g} \u00d7 {self.tile_h:g} mm"
                    f"{f', {gap:g} mm gaps' if gap else ''} - one set of G-code per page. " + note)
        dlg = ExportDialog(rows, lambda pid, feed: self.estimate_pen_minutes(pid, feed), note, self,
                           lift_mm=getattr(self, 'pen_lift_mm', None),
                           cutout=self.is_tiled() and self.chk_tile_cut.isChecked(),
                           tiles=len(self.tile_rects()))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        st = dlg.settings()
        self.pen_lift_mm = st['lift_mm']           # remembered for the next export this session
        changed = False
        for pid, feed in st['feeds'].items():
            if self.pens[pid].get('feed') != feed:
                self.pens[pid]['feed'] = feed
                changed = True
        if changed:
            save_pens(self.pens)
            self.update_time_estimate()
        self._export_gcode(st)

    def _vpype_body(self, lines, has_dots, tag, no_flip=False):
        """lines (data coords) -> list of vpype gcodemm lines, or None on failure."""
        if not lines:
            return []
        temp_dir = tempfile.gettempdir()
        t_svg = os.path.join(temp_dir, f"topo_{tag}.svg").replace("\\", "/")
        t_gcode = os.path.join(temp_dir, f"topo_{tag}.gcode").replace("\\", "/")
        export_fig, export_ax = self._make_export_axes()
        export_ax.add_collection(LineCollection(lines, colors='black', linewidths=1))
        with matplotlib.rc_context({'path.simplify': False}):      # keep every tiny letter stroke
            export_fig.savefig(t_svg, format='svg')
        try:
            vpype_cli.execute(exporter.vpype_command(t_svg, t_gcode, has_dots, no_flip=no_flip))
            with open(t_gcode) as f:
                body = f.read().splitlines()
        except Exception as e:
            print(f"vpype failed for {tag}: {e}")
            body = None
        for p in (t_svg, t_gcode):
            try:
                if os.path.exists(p): os.remove(p)
            except OSError:
                pass
        return body

    def _export_gcode(self, st):
        orient = "landscape" if self.is_landscape else "portrait"
        default_base_name = f"{self.active_planet.lower()}_{orient}_{int(self.pw)}x{int(self.ph)}.gcode"
        base_filepath, _ = QFileDialog.getSaveFileName(self, "Select base name for the G-code",
                                                       os.path.join(desktop_dir(), default_base_name),
                                                       "G-Code Files (*.gcode)")
        if not base_filepath:
            return
        base_filepath = base_filepath.replace("\\", "/")
        dir_path = os.path.dirname(base_filepath)
        base_name = os.path.splitext(os.path.basename(base_filepath))[0]
        text_boxes = self._text_mask_boxes()
        files, skipped, empty_tiles = [], [], []
        for label, (x0, y0, w, h) in self.tile_rects():
            if label:
                X0, X1, Y0, Y1 = self._paper_rect_to_data(x0, y0, w, h)
                self._export_tile = {'w': w, 'h': h, 'xlim': (X0, X1), 'ylim': (Y0, Y1),
                                     'box': shp_box(min(X0, X1), min(Y0, Y1), max(X0, X1), max(Y0, Y1))}
            try:
                per_pen, skip = self._build_pen_bodies(st, text_boxes, w, h, label)
            finally:
                self._export_tile = None
            skipped += [s for s in skip if s not in skipped]
            if not per_pen:
                if label:
                    empty_tiles.append(label.upper())
                continue
            files += self._gcode_files(st, per_pen, dir_path, base_name + (f"_{label}" if label else ""),
                                       label.upper() if label else "")
        if not files:
            QMessageBox.warning(self, "No Data", "No vector elements found for the selected pens.")
            self.lbl_status.setText("Ready.")
            return
        self._write_gcode_files(st, files, skipped, empty_tiles)

    def _build_pen_bodies(self, st, text_boxes, page_w, page_h, label=None):
        """Wrapped G-code body per pen for the whole plot, or for one page of a tiled plot (self._export_tile).
        Returns (per_pen [(pen_id, body)], skipped pen names)."""
        sx, sy = self._paper_mm_per_data_unit()
        tile = getattr(self, '_export_tile', None)
        per_pen = []          # (pen_id, wrapped gcode body)
        skipped = []          # pens whose strokes all vanished in the clean-up (nothing to draw)
        for p_id, feed in st['pens']:
            pen = self.pens[p_id]
            self.lbl_status.setText(f"Optimising {pen['name']}" + (f" (page {label.upper()})" if label else "") + "...")
            QApplication.processEvents()
            lines, fine, one_way = self._lines_for_pen(p_id, text_boxes, split=True)
            if tile:
                lines, fine, one_way = (self._clip_lines(x, tile['box']) for x in (lines, fine, one_way))
            has_dots = any(it.get('type') == 'stipple' and it['pen_id'] == p_id and self.is_visible(it)
                           for it in self.dem_segments)
            lengths = [float(np.sum(np.hypot(np.diff(np.asarray(ln)[:, 0]) * sx, np.diff(np.asarray(ln)[:, 1]) * sy)))
                       for ln in lines]
            if has_dots:
                keep = [i for i, ln in enumerate(lines)
                        if (len(ln) == 7 and np.allclose(ln[0], ln[-1])) or lengths[i] >= MIN_SEGMENT_MM]
                lines = [lines[i] for i in keep]
                lengths = [lengths[i] for i in keep]
            if not lines and not fine and not one_way:
                continue
            prime, rest = exporter.split_for_priming(lines, lengths, st['prime'] if not has_dots else 0)
            body = []
            ok = True
            # map lines (primed, cleaned up), then lettering last - by then the ink is flowing well
            # (one-way squiggles keep their direction: draw, lift, travel back, draw the next one)
            for part, tag, dots, no_flip in ((prime, f"{p_id}_prime", has_dots, False),
                                             (rest, p_id, has_dots, False),
                                             (one_way, f"{p_id}_oneway", False, True),
                                             (fine, f"{p_id}_text", True, False)):
                b = self._vpype_body(part, dots, tag, no_flip=no_flip)
                if b is None:
                    ok = False
                    break
                body += b
            if not ok:
                QMessageBox.warning(self, "Export", f"vpype could not optimise {pen['name']} - skipped.")
                continue
            wrapped = exporter.wrap_vpype_body(body, feed, exporter.TRAVEL_FEED, st['settle_ms'],
                                               lift_mm=st['lift_mm'], down_z=exporter.tool_down_z(p_id))
            if not exporter.has_drawing_moves(wrapped):
                # Everything for this pen was filtered out or lay off the sheet: no file, no pen-change pause.
                skipped.append(pen['name'])
                continue
            per_pen.append((p_id, wrapped))

        if st.get('cutout'):
            # Card cut-out: the drag knife traces the sheet's outer edge, after everything else (its own
            # tool-change pause in a combined file, its own file otherwise).
            cut_feed = st['feeds'].get('cutter') or self.pens.get('cutter', {}).get('feed', exporter.DEFAULT_DRAW_FEED)
            cut = exporter.cutout_body(page_w, page_h, cut_feed, exporter.TRAVEL_FEED, lift_mm=st['lift_mm'])
            for i, (p_id, body) in enumerate(per_pen):
                if p_id == 'cutter':
                    per_pen[i] = (p_id, body + cut)
                    break
            else:
                per_pen.append(('cutter', cut))
        return per_pen, skipped

    def _gcode_files(self, st, per_pen, dir_path, base_name, page=""):
        """[(path, label, gcode lines)] for one page: one combined file or one file per pen."""
        files = []
        if st['combine']:
            names = [self.pens[p]['name'] for p, _ in per_pen]
            g = exporter.header(f"COMBINED PLOT: {' -> '.join(names)}", lift_mm=st['lift_mm'])
            for i, (p_id, body) in enumerate(per_pen):
                g += exporter.pen_change(self.pens[p_id]['name'], lift_mm=st['lift_mm']) if i > 0 else \
                    [f"M117 Load {self.pens[p_id]['name']}"[:40], f"M0 Load {self.pens[p_id]['name']}, then click"[:60]]
                g += body
            g += exporter.footer("Plot complete!")
            path = f"{dir_path}/{base_name}_all_pens.gcode"
            files.append((path, (f"Page {page}: " if page else "") + "All pens (with pauses)", g))
        else:
            for order_no, (p_id, body) in enumerate(per_pen, start=1):
                pen = self.pens[p_id]
                safe = re.sub(r'_+', '_', "".join(c if c.isalnum() else "_" for c in pen['name'])).strip('_')
                g = exporter.header(f"START ROUTINE: {pen['name']}", lift_mm=st['lift_mm']) + body + \
                    exporter.footer(f"{pen['name']} Complete!")
                files.append((f"{dir_path}/{order_no:02d}_{safe}_{base_name}.gcode",
                              (f"Page {page}: " if page else "") + pen['name'], g))
        return files

    def _write_gcode_files(self, st, files, skipped, empty_tiles=()):
        report, total, problems = [], 0.0, []
        for path, label, g in files:
            try:
                with open(path, "w") as f:
                    f.write("\n".join(g))
            except Exception as e:
                QMessageBox.critical(self, "Export", f"Could not write {path}:\n{e}")
                return
            mins = exporter.estimate_minutes(g)
            total += mins
            report.append(f"• {os.path.basename(path)}  ->  {exporter.fmt_duration(mins)}")
            bad = exporter.bed_violation(exporter.xy_bounds(g))
            if bad:
                problems.append(f"{label}: {bad}")

        self.lbl_status.setText(f"Exported {len(files)} file(s). (Est: {exporter.fmt_duration(total)})")
        msg = (f"Generated {len(files)} G-code file(s), total ≈ {exporter.fmt_duration(total)}.\n"
               + ("Plot in this order - lightest ink first:\n\n" if not st['combine'] else "\n")
               + "\n".join(report))
        if skipped:
            msg += "\n\nNo file for " + ", ".join(skipped) + " - nothing left to draw after clean-up."
        if empty_tiles:
            msg += "\n\nNothing to plot on page(s) " + ", ".join(empty_tiles) + " - no file for them."
        if problems:
            msg += ("\n\n⚠ BED CHECK: these moves go outside the reachable area - the plotter would hit its "
                    "limits:\n" + "\n".join(problems) +
                    "\nUse a smaller paper size or adjust BED_MM / GCODE_OFFSET_MM in config.py.")
            QMessageBox.warning(self, "Exported - check bed limits", msg)
        else:
            QMessageBox.information(self, "Success", msg)



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
