import os
import re
import sys
import tempfile
import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QComboBox, QSplitter,
    QFileDialog, QMessageBox, QFrame, QDoubleSpinBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.collections import LineCollection
import matplotlib.patches as patches

import vpype
import vpype_cli

# Reuse your existing MoonPlotter modules & pen library directly!
from config import (
    PAPER_SIZES, PAPER_COLORS, DEFAULT_PAPER, PT_PER_MM, MIN_SEGMENT_MM,
    PEN_LIFT_MM, DEFAULT_DRAW_FEED, load_pens, save_pens,
    pen_available, ink_lightness
)
import exporter


class SVGPlotterStudio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SVG Plotter Studio — Ender 3 G-Code Generator")
        self.resize(1320, 760)

        self.pens = load_pens()
        self.svg_path = None
        self.paper_name = 'Ender3_Safe (200x200)'
        self.is_landscape = True
        self.pw, self.ph = 200.0, 200.0

        # Parsed & laid-out vpype document in mm coordinates
        self.doc = None
        # Layer metadata list: [{'lid': int, 'label': str, 'enabled': bool, 'pen_id': str, 'fine': bool, 'strokes': int}]
        self.layer_configs = []

        self._init_ui()
        self._update_paper_dims()

    # ------------------------------------------------------------------
    # UI SETUP
    # ------------------------------------------------------------------
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(12)

        # ================= LEFT SIDEBAR =================
        sidebar = QFrame()
        sidebar.setFixedWidth(430)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(6, 6, 6, 6)
        side_layout.setSpacing(10)

        # 1. SVG Input & Grouping
        sec_svg = QFrame()
        v_svg = QVBoxLayout(sec_svg)
        v_svg.setContentsMargins(0, 0, 0, 0)
        lbl_svg_hdr = QLabel("<b>1. Import SVG & Layer Splitting</b>")
        v_svg.addWidget(lbl_svg_hdr)

        row_file = QHBoxLayout()
        self.btn_open = QPushButton("Open SVG…")
        self.btn_open.clicked.connect(self.open_svg)
        self.lbl_file = QLabel("No SVG loaded")
        self.lbl_file.setWordWrap(True)
        row_file.addWidget(self.btn_open)
        row_file.addWidget(self.lbl_file, 1)
        v_svg.addLayout(row_file)

        row_split = QHBoxLayout()
        row_split.addWidget(QLabel("Group pens by:"))
        self.combo_split = QComboBox()
        self.combo_split.addItems([
            "Stroke Color (stroke='#...')",
            "SVG Layer (<g inkscape:label / id>)",
            "Single Pen (Flatten All)"
        ])
        self.combo_split.currentIndexChanged.connect(self.reload_svg_document)
        row_split.addWidget(self.combo_split, 1)
        v_svg.addLayout(row_split)
        side_layout.addWidget(sec_svg)

        # 2. Paper & Layout
        sec_paper = QFrame()
        v_paper = QVBoxLayout(sec_paper)
        v_paper.setContentsMargins(0, 0, 0, 0)
        v_paper.addWidget(QLabel("<b>2. Paper & Layout</b>"))

        row_paper = QHBoxLayout()
        self.combo_paper = QComboBox()
        self.combo_paper.addItems(list(PAPER_SIZES.keys()))
        self.combo_paper.setCurrentText(self.paper_name)
        self.combo_paper.currentTextChanged.connect(self.on_paper_changed)
        self.btn_rotate = QPushButton("Rotate")
        self.btn_rotate.clicked.connect(self.toggle_orientation)
        row_paper.addWidget(self.combo_paper, 1)
        row_paper.addWidget(self.btn_rotate)
        v_paper.addLayout(row_paper)

        # Custom paper size row
        self.widget_custom_paper = QWidget()
        row_cust = QHBoxLayout(self.widget_custom_paper)
        row_cust.setContentsMargins(0, 0, 0, 0)
        self.spin_cw = QDoubleSpinBox()
        self.spin_cw.setRange(20.0, 400.0)
        self.spin_cw.setValue(150.0)
        self.spin_cw.setSuffix(" mm (W)")
        self.spin_ch = QDoubleSpinBox()
        self.spin_ch.setRange(20.0, 400.0)
        self.spin_ch.setValue(150.0)
        self.spin_ch.setSuffix(" mm (H)")
        self.spin_cw.valueChanged.connect(self._update_paper_dims)
        self.spin_ch.valueChanged.connect(self._update_paper_dims)
        row_cust.addWidget(self.spin_cw)
        row_cust.addWidget(self.spin_ch)
        self.widget_custom_paper.setVisible(False)
        v_paper.addWidget(self.widget_custom_paper)

        row_margin = QHBoxLayout()
        row_margin.addWidget(QLabel("Margin:"))
        self.spin_margin = QDoubleSpinBox()
        self.spin_margin.setRange(0.0, 60.0)
        self.spin_margin.setValue(10.0)
        self.spin_margin.setSingleStep(1.0)
        self.spin_margin.setSuffix(" mm")
        self.spin_margin.valueChanged.connect(self.apply_layout_and_refresh)
        row_margin.addWidget(self.spin_margin)

        row_margin.addWidget(QLabel("Paper color:"))
        self.combo_paper_color = QComboBox()
        for name, hx in PAPER_COLORS.items():
            self.combo_paper_color.addItem(name, hx)
        self.combo_paper_color.setCurrentText(DEFAULT_PAPER)
        self.combo_paper_color.currentIndexChanged.connect(self.render_preview)
        row_margin.addWidget(self.combo_paper_color, 1)
        v_paper.addLayout(row_margin)

        self.chk_fit_paper = QCheckBox("Scale SVG to fit within paper margins")
        self.chk_fit_paper.setChecked(True)
        self.chk_fit_paper.toggled.connect(self.apply_layout_and_refresh)
        v_paper.addWidget(self.chk_fit_paper)

        self.lbl_bed_note = QLabel("")
        self.lbl_bed_note.setWordWrap(True)
        self.lbl_bed_note.setStyleSheet("color: #666; font-size: 11px;")
        v_paper.addWidget(self.lbl_bed_note)
        side_layout.addWidget(sec_paper)

        # 3. Layers & Pen Assignment Table
        sec_pens = QFrame()
        v_pens = QVBoxLayout(sec_pens)
        v_pens.setContentsMargins(0, 0, 0, 0)
        v_pens.addWidget(QLabel("<b>3. Layers & Pen Assignment</b>"))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Plot", "SVG Layer", "Pen", "Fine/Text", "Strokes"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        v_pens.addWidget(self.table, 1)

        row_order = QHBoxLayout()
        btn_up = QPushButton("▲ Up")
        btn_down = QPushButton("▼ Down")
        btn_auto_sort = QPushButton("Sort Light → Dark")
        btn_auto_sort.setToolTip("Plot lightest inks first so wet light pens never drag through dark ink")
        btn_up.clicked.connect(lambda: self.move_layer_row(-1))
        btn_down.clicked.connect(lambda: self.move_layer_row(1))
        btn_auto_sort.clicked.connect(self.sort_layers_light_to_dark)
        row_order.addWidget(btn_up)
        row_order.addWidget(btn_down)
        row_order.addStretch()
        row_order.addWidget(btn_auto_sort)
        v_pens.addLayout(row_order)
        side_layout.addWidget(sec_pens, 1)

        # 4. G-Code Export Settings (matching exporter.py)
        sec_exp = QFrame()
        v_exp = QVBoxLayout(sec_exp)
        v_exp.setContentsMargins(0, 0, 0, 0)
        v_exp.addWidget(QLabel("<b>4. Ender 3 G-Code Settings</b>"))

        row_hw = QHBoxLayout()
        row_hw.addWidget(QLabel("Pen Lift Z:"))
        self.spin_lift = QDoubleSpinBox()
        self.spin_lift.setRange(0.5, 15.0)
        self.spin_lift.setSingleStep(0.5)
        self.spin_lift.setValue(PEN_LIFT_MM)
        self.spin_lift.setSuffix(" mm")
        row_hw.addWidget(self.spin_lift)

        row_hw.addWidget(QLabel("Settle dwell:"))
        self.spin_settle = QSpinBox()
        self.spin_settle.setRange(0, 1000)
        self.spin_settle.setSingleStep(20)
        self.spin_settle.setValue(0)
        self.spin_settle.setSuffix(" ms")
        row_hw.addWidget(self.spin_settle)
        v_exp.addLayout(row_hw)

        self.chk_combine = QCheckBox("Combine all pens into ONE file (with M0 LCD pauses)")
        self.chk_combine.setChecked(True)
        v_exp.addWidget(self.chk_combine)

        self.lbl_status = QLabel("Load an SVG file to begin.")
        self.lbl_status.setWordWrap(True)
        v_exp.addWidget(self.lbl_status)

        self.btn_export = QPushButton("Export G-Code…")
        self.btn_export.setStyleSheet("font-weight: bold; padding: 8px;")
        self.btn_export.clicked.connect(self.export_gcode_action)
        v_exp.addWidget(self.btn_export)
        side_layout.addWidget(sec_exp)

        main_layout.addWidget(sidebar)

        # ================= RIGHT PREVIEW CANVAS =================
        prev_card = QFrame()
        v_prev = QVBoxLayout(prev_card)
        v_prev.setContentsMargins(6, 6, 6, 6)
        v_prev.addWidget(QLabel("<b>PLOT PREVIEW (True Paper & Pen Width Scale)</b>"))

        self.fig = Figure(figsize=(6, 6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        v_prev.addWidget(self.canvas, 1)

        main_layout.addWidget(prev_card, 1)

    # ------------------------------------------------------------------
    # PAPER & LAYOUT MANAGEMENT
    # ------------------------------------------------------------------
    def on_paper_changed(self, name):
        self.paper_name = name
        self._update_paper_dims()

    def toggle_orientation(self):
        self.is_landscape = not self.is_landscape
        self._update_paper_dims()

    def _update_paper_dims(self):
        if self.paper_name == 'Custom (mm)':
            self.widget_custom_paper.setVisible(True)
            w, h = self.spin_cw.value(), self.spin_ch.value()
        else:
            self.widget_custom_paper.setVisible(False)
            w, h = PAPER_SIZES[self.paper_name]

        self.pw, self.ph = (w, h) if self.is_landscape else (h, w)
        mx, my = exporter.max_plot_area()
        warn = "⚠ EXCEEDS BED LIMIT! " if (self.pw > mx + 1e-6 or self.ph > my + 1e-6) else ""
        self.lbl_bed_note.setText(
            f"{warn}Paper: {self.pw:g} × {self.ph:g} mm  |  Max reachable area: {mx:g} × {my:g} mm"
        )
        self.apply_layout_and_refresh()

    # ------------------------------------------------------------------
    # SVG LOADING & VPYPE LAYOUT
    # ------------------------------------------------------------------
    def open_svg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open SVG File", "", "SVG Files (*.svg)"
        )
        if not path:
            return
        self.svg_path = path.replace("\\", "/")
        self.lbl_file.setText(os.path.basename(path))
        self.reload_svg_document()

    def _build_layout_pipeline(self):
        """Builds the vpype read + layout command so all layers stay in exact relative registration."""
        mode_idx = self.combo_split.currentIndex()
        if mode_idx == 0:
            read_part = f'read --attr stroke "{self.svg_path}"'
        elif mode_idx == 1:
            read_part = f'read "{self.svg_path}"'
        else:
            read_part = f'read -m "{self.svg_path}"'

        margin = self.spin_margin.value()
        if self.chk_fit_paper.isChecked():
            layout_part = f'layout --fit-to-margins {margin:g}mm --align center --valign center {self.pw:g}x{self.ph:g}mm'
        else:
            layout_part = f'pagesize {self.pw:g}x{self.ph:g}mm center'

        return f"{read_part} {layout_part}"

    def reload_svg_document(self):
        if not self.svg_path or not os.path.exists(self.svg_path):
            return
        try:
            self.doc = vpype_cli.execute(self._build_layout_pipeline())
        except Exception as e:
            QMessageBox.critical(self, "SVG Load Error", f"vpype could not read SVG:\n{e}")
            return

        # Build default pen assignments for each detected layer
        avail_pens = [pid for pid, p in self.pens.items() if pen_available(pid, p)]
        if not avail_pens:
            avail_pens = ['base']

        self.layer_configs = []
        for idx, (lid, lc) in enumerate(sorted(self.doc.layers.items())):
            prop_color = lc.property("svg_stroke") or lc.property("vp_color")
            prop_name = lc.property("vp_name") or f"Layer {lid}"
            label = f"{prop_name} ({prop_color})" if prop_color else str(prop_name)

            # Match closest pen by hex color if split by stroke color, else cycle pens
            chosen_pen = avail_pens[idx % len(avail_pens)]
            if prop_color:
                chosen_pen = self._closest_pen_for_hex(str(prop_color), avail_pens)

            self.layer_configs.append({
                'lid': lid,
                'label': label,
                'enabled': True,
                'pen_id': chosen_pen,
                'fine': False,
                'strokes': len(lc)
            })

        self._populate_table()
        self.render_preview()
        total_strokes = sum(c['strokes'] for c in self.layer_configs)
        self.lbl_status.setText(f"Loaded {len(self.layer_configs)} layer(s), {total_strokes:,} raw paths.")

    def apply_layout_and_refresh(self):
        """Re-runs layout when paper size or margin changes, preserving the user's pen choices."""
        if not self.svg_path or not os.path.exists(self.svg_path):
            self.render_preview()
            return
        try:
            self.doc = vpype_cli.execute(self._build_layout_pipeline())
        except Exception:
            return
        self.render_preview()

    def _closest_pen_for_hex(self, hex_str, avail_pens):
        h = hex_str.lstrip('#')
        if len(h) != 6:
            return avail_pens[0]
        try:
            target = np.array([int(h[i:i+2], 16) for i in (0, 2, 4)], dtype=float)
        except ValueError:
            return avail_pens[0]

        best_pid, best_dist = avail_pens[0], float('inf')
        for pid in avail_pens:
            ph = self.pens[pid]['color'].lstrip('#')
            rgb = np.array([int(ph[i:i+2], 16) for i in (0, 2, 4)], dtype=float)
            d = float(np.sum((rgb - target) ** 2))
            if d < best_dist:
                best_pid, best_dist = pid, d
        return best_pid

    # ------------------------------------------------------------------
    # PEN TABLE & PLOT ORDER
    # ------------------------------------------------------------------
    def _populate_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.layer_configs))
        avail_pens = [(pid, p) for pid, p in self.pens.items() if pen_available(pid, p)]

        for row, cfg in enumerate(self.layer_configs):
            # 0: Enable checkbox
            chk_en = QCheckBox()
            chk_en.setChecked(cfg['enabled'])
            chk_en.toggled.connect(lambda val, r=row: self._on_row_changed(r))
            self.table.setCellWidget(row, 0, chk_en)

            # 1: Layer label
            it_lbl = QTableWidgetItem(cfg['label'])
            it_lbl.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 1, it_lbl)

            # 2: Pen selector
            cb_pen = QComboBox()
            for pid, pen in avail_pens:
                cb_pen.addItem(f"{pen['name']} ({pen['width']}mm)", pid)
            i = cb_pen.findData(cfg['pen_id'])
            cb_pen.setCurrentIndex(max(0, i))
            cb_pen.currentIndexChanged.connect(lambda _, r=row: self._on_row_changed(r))
            self.table.setCellWidget(row, 2, cb_pen)

            # 3: Fine / Text mode (has_dots=True in exporter.vpype_command)
            chk_fine = QCheckBox()
            chk_fine.setChecked(cfg['fine'])
            chk_fine.setToolTip("Skip simplify/merge/2mm filter (preserve tiny text & stipple dots)")
            chk_fine.toggled.connect(lambda val, r=row: self._on_row_changed(r))
            self.table.setCellWidget(row, 3, chk_fine)

            # 4: Stroke count
            it_cnt = QTableWidgetItem(f"{cfg['strokes']:,}")
            it_cnt.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 4, it_cnt)

        self.table.blockSignals(False)

    def _sync_from_table(self):
        for row, cfg in enumerate(self.layer_configs):
            chk_en = self.table.cellWidget(row, 0)
            cb_pen = self.table.cellWidget(row, 2)
            chk_fine = self.table.cellWidget(row, 3)
            if chk_en:
                cfg['enabled'] = chk_en.isChecked()
            if cb_pen:
                cfg['pen_id'] = cb_pen.currentData()
            if chk_fine:
                cfg['fine'] = chk_fine.isChecked()

    def _on_row_changed(self, _row):
        self._sync_from_table()
        self.render_preview()

    def move_layer_row(self, delta):
        self._sync_from_table()
        row = self.table.currentRow()
        new_row = row + delta
        if 0 <= row < len(self.layer_configs) and 0 <= new_row < len(self.layer_configs):
            self.layer_configs[row], self.layer_configs[new_row] = (
                self.layer_configs[new_row], self.layer_configs[row]
            )
            self._populate_table()
            self.table.selectRow(new_row)
            self.render_preview()

    def sort_layers_light_to_dark(self):
        self._sync_from_table()
        self.layer_configs.sort(
            key=lambda c: ink_lightness(self.pens.get(c['pen_id'], self.pens['base'])['color']),
            reverse=True
        )
        self._populate_table()
        self.render_preview()

    # ------------------------------------------------------------------
    # LIVE PREVIEW RENDERER
    # ------------------------------------------------------------------
    def render_preview(self):
        self.ax.clear()
        paper_hex = self.combo_paper_color.currentData() or '#FFFFFF'
        self.ax.set_facecolor(paper_hex)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.ax.set_xlim(0.0, self.pw)
        # SVG / vpype uses Y-down (0 at top, ph at bottom)
        self.ax.set_ylim(self.ph, 0.0)
        self.ax.set_aspect('equal')

        # Draw margin guide box
        m = self.spin_margin.value()
        if 0 < m * 2 < min(self.pw, self.ph):
            rect = patches.Rectangle(
                (m, m), self.pw - 2 * m, self.ph - 2 * m,
                fill=False, edgecolor='#888888', linestyle='--', linewidth=0.8, alpha=0.6
            )
            self.ax.add_patch(rect)

        if self.doc is not None:
            # Convert vpype internal pixel units (96 DPI) to mm
            px_to_mm = 25.4 / 96.0
            for cfg in self.layer_configs:
                if not cfg['enabled'] or cfg['lid'] not in self.doc.layers:
                    continue
                pen = self.pens.get(cfg['pen_id'], self.pens['base'])
                lc = self.doc.layers[cfg['lid']]
                segs = []
                for line in lc:
                    pts = np.column_stack([line.real, line.imag]) * px_to_mm
                    if len(pts) >= 2:
                        segs.append(pts)
                if segs:
                    coll = LineCollection(
                        segs,
                        colors=pen['color'],
                        linewidths=pen['width'] * PT_PER_MM,
                        capstyle='round',
                        joinstyle='round'
                    )
                    self.ax.add_collection(coll)

        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    # VPYPE BODY & EXPORTER.PY G-CODE GENERATION
    # ------------------------------------------------------------------
    def _vpype_body_for_layer(self, lid: int, has_dots: bool, tag: str):
        """
        Matches main.py:_vpype_body & exporter.vpype_command:
        1. Reads & lays out the full SVG on the paper so all layers keep registration.
        2. Isolates layer `lid` with `ldelete --keep`.
        3. Runs linesimplify, linemerge, filter --min-length 2mm, linesort, and gwrite --profile gcodemm.
        """
        temp_dir = tempfile.gettempdir()
        t_gcode = os.path.join(temp_dir, f"svgplot_{tag}.gcode").replace("\\", "/")

        base_pipeline = self._build_layout_pipeline()
        keep_cmd = f"ldelete --keep {lid}"

        if has_dots:
            opt_cmd = f'linesort gwrite --profile gcodemm "{t_gcode}"'
        else:
            # opt_cmd = (
            #     f'linesimplify -t 0.1mm linemerge -t 0.3mm '
            #     f'filter --min-length {MIN_SEGMENT_MM:g}mm '
            #     f'linesort gwrite --profile gcodemm "{t_gcode}"'
            # )
            opt_cmd = (
                f'linemerge -t 0.3mm '
                f'filter --min-length {MIN_SEGMENT_MM:g}mm '
                f'linesort gwrite --profile gcodemm "{t_gcode}"'
            )

        full_cmd = f"{base_pipeline} {keep_cmd} {opt_cmd}"
        try:
            vpype_cli.execute(full_cmd)
            with open(t_gcode, "r", encoding="utf-8") as f:
                body = f.read().splitlines()
        except Exception as e:
            print(f"vpype failed for {tag}: {e}")
            body = None
        finally:
            if os.path.exists(t_gcode):
                try:
                    os.remove(t_gcode)
                except OSError:
                    pass
        return body

    def export_gcode_action(self):
        if self.doc is None or not self.svg_path:
            QMessageBox.warning(self, "No SVG", "Open an SVG file first.")
            return

        self._sync_from_table()
        active_layers = [c for c in self.layer_configs if c['enabled']]
        if not active_layers:
            QMessageBox.warning(self, "No Layers", "Tick at least one layer to plot.")
            return

        orient = "landscape" if self.is_landscape else "portrait"
        svg_stem = os.path.splitext(os.path.basename(self.svg_path))[0]
        default_name = f"{svg_stem}_{orient}_{int(self.pw)}x{int(self.ph)}.gcode"
        base_filepath, _ = QFileDialog.getSaveFileName(
            self, "Select base name for G-code", default_name, "G-Code Files (*.gcode)"
        )
        if not base_filepath:
            return

        base_filepath = base_filepath.replace("\\", "/")
        dir_path = os.path.dirname(base_filepath)
        base_name = os.path.splitext(os.path.basename(base_filepath))[0]

        lift_mm = self.spin_lift.value()
        settle_ms = self.spin_settle.value()

        # Group consecutive or identical pens in table order
        per_pen = []
        for idx, cfg in enumerate(active_layers):
            p_id = cfg['pen_id']
            pen = self.pens.get(p_id, self.pens['base'])
            feed = float(pen.get('feed', DEFAULT_DRAW_FEED))
            self.lbl_status.setText(f"Optimising {cfg['label']} ({pen['name']})…")
            QApplication.processEvents()

            raw_body = self._vpype_body_for_layer(cfg['lid'], cfg['fine'], f"L{cfg['lid']}_{idx}")
            if raw_body is None:
                QMessageBox.warning(self, "Export Warning", f"vpype failed on {cfg['label']} — skipped.")
                continue

            wrapped = exporter.wrap_vpype_body(
                raw_body,
                draw_feed=feed,
                travel_feed=exporter.TRAVEL_FEED,
                settle_ms=settle_ms,
                lift_mm=lift_mm
            )
            if wrapped:
                # If the previous entry uses the exact same pen, append to it so we don't pause twice
                if per_pen and per_pen[-1][0] == p_id:
                    per_pen[-1][1].extend(wrapped)
                else:
                    per_pen.append((p_id, wrapped))

        if not per_pen:
            QMessageBox.warning(self, "Empty Plot", "No plottable strokes survived filtering.")
            return

        # Assemble final files using exporter.header, exporter.pen_change, exporter.footer
        files = []
        if self.chk_combine.isChecked():
            names = [self.pens[p]['name'] for p, _ in per_pen]
            g = exporter.header(f"COMBINED PLOT: {' -> '.join(names)}", lift_mm=lift_mm)
            for i, (p_id, body) in enumerate(per_pen):
                pen_name = self.pens[p_id]['name']
                if i > 0:
                    g += exporter.pen_change(pen_name, lift_mm=lift_mm)
                else:
                    g += [f"M117 Load {pen_name}"[:40], f"M0 Load {pen_name}, then click"[:60]]
                g += body
            g += exporter.footer("Plot complete!")
            out_path = f"{dir_path}/{base_name}_all_pens.gcode"
            files.append((out_path, "All pens (with pauses)", g))
        else:
            for order_no, (p_id, body) in enumerate(per_pen, start=1):
                pen = self.pens[p_id]
                safe = re.sub(r'_+', '_', "".join(c if c.isalnum() else "_" for c in pen['name'])).strip('_')
                g = (
                    exporter.header(f"START ROUTINE: {pen['name']}", lift_mm=lift_mm)
                    + body
                    + exporter.footer(f"{pen['name']} Complete!")
                )
                out_path = f"{dir_path}/{order_no:02d}_{safe}_{base_name}.gcode"
                files.append((out_path, pen['name'], g))

        # Write files, estimate duration, and run exporter.bed_violation()
        report, total, problems = [], 0.0, []
        for path, label, g in files:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(g))
            mins = exporter.estimate_minutes(g)
            total += mins
            report.append(f"• {os.path.basename(path)}  →  {exporter.fmt_duration(mins)}")
            bad = exporter.bed_violation(exporter.xy_bounds(g))
            if bad:
                problems.append(f"{label}: {bad}")

        self.lbl_status.setText(f"Exported {len(files)} file(s) — Est: {exporter.fmt_duration(total)}")
        msg = f"Generated {len(files)} G-code file(s), total ≈ {exporter.fmt_duration(total)}:\n\n" + "\n".join(report)
        if problems:
            msg += "\n\n⚠ BED CHECK VIOLATION:\n" + "\n".join(problems)
            QMessageBox.warning(self, "Exported — Check Bed Limits", msg)
        else:
            QMessageBox.information(self, "Success", msg)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = SVGPlotterStudio()
    win.show()
    sys.exit(app.exec())