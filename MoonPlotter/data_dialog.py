"""Data sources dialog: lists regional rasters, fetches the current selection from remote datasets,
imports downloaded GeoTIFFs, and explains where to get better data."""
import os

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QListWidget,
                             QListWidgetItem, QFileDialog, QMessageBox, QLineEdit, QProgressBar, QTextBrowser,
                             QTabWidget, QWidget, QInputDialog)

import regions

GUIDE_HTML = """
<h3>Where to get sharper data</h3>
<p><b>Easiest:</b> zoom the source map to a small area, drag a selection, then use <b>Fetch for selection</b>
on the first tab. Only that window is downloaded (often a few MB) and is used automatically whenever a
selection falls inside it.</p>
<h4>Earth</h4>
<ul>
<li><b>Copernicus GLO-30</b> (30 m, land, global) - built into <i>Fetch</i>. Best general upgrade for mountains,
canyons and volcanoes. (Copernicus DEM on AWS Open Data: <i>registry.opendata.aws/copernicus-dem</i>)</li>
<li><b>USGS 3DEP 1/3 arc-second</b> (~10 m, USA) and 1 m lidar - download GeoTIFF tiles from
<i>apps.nationalmap.gov/downloader</i> (Elevation Products (3DEP)), then <b>Import GeoTIFF</b>.</li>
<li><b>GEBCO 2025</b> (15 arc-second ~450 m, land + ocean depths) - for coasts and sea floor. On
<i>download.gebco.net</i> draw a box and choose <b>GeoTIFF</b>, then <b>Import GeoTIFF</b> (units: metres).</li>
<li><b>OpenTopography</b> (<i>portal.opentopography.org</i>, free account) - GUI subsetting of GLO-30, NASADEM,
SRTM and high-resolution lidar; export GeoTIFF, then <b>Import</b>.</li>
<li><b>Colour, Sentinel-2 cloudless 10 m</b> (EOX, global, no clouds) - built into <i>Fetch</i>. Free for
personal / non-commercial use (CC BY-NC-SA 4.0); credit: <i>EOxCloudless by EOX IT Services GmbH (contains
modified Copernicus Sentinel data 2024)</i>.</li>
<li><b>Colour, USGS orthoimagery ~1 m</b> (NAIP aerial photos, USA only, public domain) - built into <i>Fetch</i>.</li>
<li>Note: elevation and colour are separate downloads - fetch one of each for the same selection. Colour only
changes the plot when the fill's tone source uses the colour map.</li>
</ul>
<h4>Moon</h4>
<ul>
<li><b>LOLA + Kaguya merge, 59 m</b> (60&deg;N-60&deg;S, 22 GB file) - built into <i>Fetch</i>; only your window is read.</li>
<li><b>LOLA ldem_64</b> (~474 m, global incl. poles) - built into <i>Fetch</i>.</li>
<li><b>LROC colour 16k</b> (NASA SVS CGI Moon Kit) - built into <i>Fetch</i>. You can also replace the global map:
download <i>lroc_color_16bit_srgb_16k.tif</i> from svs.gsfc.nasa.gov/4720 and set it in config.py.</li>
</ul>
<h4>Mars</h4>
<ul>
<li><b>HRSC + MOLA blended DEM, 200 m</b> (global, 11 GB file) - built into <i>Fetch</i>.</li>
<li>Viking colour 232 m (USGS Astrogeology, "Mars Viking Colorized Global Mosaic 232m") can be imported as
colour if you download a crop with GDAL.</li>
</ul>
<p><b>Tips.</b> The app draws at most ~4000 px across, so on a 100 mm plot anything finer than ~25 px/mm is
wasted - fetch the area you will plot, not a continent. Units: Copernicus, 3DEP, GEBCO, LOLA/Kaguya and
HRSC are metres; NASA SVS <i>ldem</i> files are kilometres (handled automatically).</p>
"""


class _Job(QThread):
    done = pyqtSignal(str)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            path = self.fn(*self.args, progress=self.progress.emit)
            self.done.emit(path)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.failed.emit(f"{type(e).__name__}: {e}")


class DataDialog(QDialog):
    def __init__(self, planet, extent, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Data sources")
        self.setMinimumSize(720, 560)
        self.planet = planet
        self.extent = extent
        self.job = None
        self.changed = False
        v = QVBoxLayout(self)
        tabs = QTabWidget()
        v.addWidget(tabs)

        # ---- tab 1: regional data
        t1 = QWidget()
        v1 = QVBoxLayout(t1)
        title = QLabel(f"REGIONAL DATA · {planet.upper()}")
        title.setObjectName("panelTitle")
        v1.addWidget(title)
        self.list = QListWidget()
        v1.addWidget(self.list, 1)
        hb = QHBoxLayout()
        b_del = QPushButton("Delete selected")
        b_del.clicked.connect(self._delete)
        hb.addWidget(b_del)
        hb.addStretch()
        b_imp = QPushButton("Import GeoTIFF…")
        b_imp.clicked.connect(self._import)
        hb.addWidget(b_imp)
        v1.addLayout(hb)

        fetch_title = QLabel("FETCH THE CURRENT SELECTION")
        fetch_title.setObjectName("panelTitle")
        v1.addWidget(fetch_title)
        self.combo_src = QComboBox()
        for src in regions.REMOTE_SOURCES.get(planet, []):
            self.combo_src.addItem(src['label'], src)
        v1.addWidget(self.combo_src)
        hf = QHBoxLayout()
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("Name for this region (e.g. grand_canyon)")
        hf.addWidget(self.txt_name, 1)
        self.btn_fetch = QPushButton("Fetch for selection")
        self.btn_fetch.setObjectName("primary")
        self.btn_fetch.clicked.connect(self._fetch)
        hf.addWidget(self.btn_fetch)
        v1.addLayout(hf)
        if extent is None:
            self.btn_fetch.setEnabled(False)
            self.txt_name.setPlaceholderText("Drag a selection on the source map first")
        else:
            l, r, b, t = extent
            note = QLabel(f"Selection: {b:.3f}° to {t:.3f}° lat, {l:.3f}° to {r:.3f}° lon")
            note.setObjectName("hint")
            v1.addWidget(note)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setVisible(False)
        v1.addWidget(self.bar)
        self.lbl = QLabel("")
        self.lbl.setObjectName("status")
        self.lbl.setWordWrap(True)
        v1.addWidget(self.lbl)
        tabs.addTab(t1, "Regional data")

        # ---- tab 2: guide
        guide = QTextBrowser()
        guide.setOpenExternalLinks(True)
        guide.setHtml(GUIDE_HTML)
        tabs.addTab(guide, "Where to get data")

        hb2 = QHBoxLayout()
        hb2.addStretch()
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.accept)
        hb2.addWidget(b_close)
        v.addLayout(hb2)
        self._refresh()

    def _refresh(self):
        self.list.clear()
        for meta in regions.list_regions(self.planet):
            l, b, r, t = meta['bounds']
            res_m = meta.get('res_deg', 0) * 111320.0
            it = QListWidgetItem(f"{meta['name']}  ·  {meta['kind']}  ·  ~{res_m:,.0f} m/px  ·  "
                                 f"{b:.2f}..{t:.2f}°N, {l:.2f}..{r:.2f}°E  ·  {meta.get('source', '')}")
            it.setData(Qt.ItemDataRole.UserRole, meta['name'])
            self.list.addItem(it)
        if self.list.count() == 0:
            self.list.addItem("No regional data yet - fetch or import below.")

    def _busy(self, on, msg=""):
        self.bar.setVisible(on)
        self.btn_fetch.setEnabled(not on and self.extent is not None)
        self.lbl.setText(msg)

    def _run(self, fn, *args):
        self.job = _Job(fn, *args)
        self.job.progress.connect(self.lbl.setText)
        self.job.done.connect(self._done)
        self.job.failed.connect(self._failed)
        self._busy(True, "Starting...")
        self.job.start()

    def _done(self, path):
        self._busy(False, f"Saved {os.path.basename(path)} - it will be used automatically for selections inside it.")
        self.changed = True
        self._refresh()

    def _failed(self, msg):
        self._busy(False, "")
        QMessageBox.warning(self, "Data", msg)

    def _fetch(self):
        src = self.combo_src.currentData()
        if not src:
            return
        name = regions.safe_name(self.txt_name.text() or f"{self.planet}_{src['kind']}_region")
        self._run(regions.fetch_region, src, self.planet, self.extent, name)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import GeoTIFF", "", "GeoTIFF (*.tif *.tiff)")
        if not path:
            return
        kind, ok = QInputDialog.getItem(self, "Import", "This file is:", ["Elevation (DEM)", "Colour image"], 0, False)
        if not ok:
            return
        kind = 'dem' if kind.startswith("Elev") else 'color'
        units = ''
        if kind == 'dem':
            units, ok = QInputDialog.getItem(self, "Import", "Elevation units:", ["m", "ft", "km"], 0, False)
            if not ok:
                return
        name = regions.safe_name(os.path.splitext(os.path.basename(path))[0])
        self._run(regions.import_geotiff, path, self.planet, kind, units, name)

    def _delete(self):
        it = self.list.currentItem()
        name = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not name:
            return
        if QMessageBox.question(self, "Delete", f"Delete regional data '{name}'?") == QMessageBox.StandardButton.Yes:
            regions.delete_region(name)
            self.changed = True
            self._refresh()

    def accept(self):
        if self.job is not None and self.job.isRunning():
            QMessageBox.information(self, "Data", "Please wait for the download to finish.")
            return
        super().accept()

    def reject(self):
        self.accept()

    def closeEvent(self, e):
        if self.job is not None and self.job.isRunning():
            self.job.wait()
        super().closeEvent(e)
