"""Pen manager: edit pen name, colour, nominal tip, measured line width and feed rate; make a
calibration sheet for the checked pens."""
import copy

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
                             QPushButton, QDoubleSpinBox, QSpinBox, QCheckBox, QColorDialog, QDialogButtonBox,
                             QHeaderView, QAbstractItemView, QMessageBox)

from config import DEFAULT_DRAW_FEED, default_pens

HELP = ("How to calibrate: plot the calibration sheet, scan it at 600 dpi (or photograph it flat in "
        "daylight). Measure one single line's width in mm and enter it as Line width. Sample the colour "
        "from the middle of the solid swatch and paste it as the pen colour. The colour separation, the "
        "ink preview and the time estimates then match your real pens.")


class PenManagerDialog(QDialog):
    def __init__(self, pens, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pens")
        self.setMinimumWidth(760)
        self.pens = copy.deepcopy(pens)
        self.calibration_request = None
        v = QVBoxLayout(self)

        title = QLabel("PEN LIBRARY")
        title.setObjectName("panelTitle")
        v.addWidget(title)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Cal.", "Name", "Colour", "Tip (mm)", "Line width (mm)",
                                              "Feed (mm/min)", "ID"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in (0, 2, 3, 4, 5, 6):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.table, 1)
        self._fill()

        h = QHBoxLayout()
        b_add = QPushButton("Add pen")
        b_add.clicked.connect(self._add)
        b_del = QPushButton("Remove selected")
        b_del.clicked.connect(self._remove)
        b_reset = QPushButton("Restore built-in pens")
        b_reset.clicked.connect(self._restore)
        h.addWidget(b_add)
        h.addWidget(b_del)
        h.addWidget(b_reset)
        h.addStretch()
        b_cal = QPushButton("Make calibration sheet for checked pens")
        b_cal.clicked.connect(self._calibrate)
        h.addWidget(b_cal)
        v.addLayout(h)

        hint = QLabel(HELP)
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(hint)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    # ---- table
    def _fill(self):
        self.table.setRowCount(0)
        for pid, pen in self.pens.items():
            self._append_row(pid, pen)

    def _append_row(self, pid, pen):
        r = self.table.rowCount()
        self.table.insertRow(r)
        chk = QCheckBox()
        self.table.setCellWidget(r, 0, chk)
        self.table.setItem(r, 1, QTableWidgetItem(pen.get('name', pid)))
        btn = QPushButton(pen.get('color', '#000000'))
        btn.setProperty('color', pen.get('color', '#000000'))
        self._style_color_button(btn)
        btn.clicked.connect(lambda _=False, b=btn: self._pick_color(b))
        self.table.setCellWidget(r, 2, btn)
        tip = QDoubleSpinBox()
        tip.setRange(0.05, 5.0)
        tip.setDecimals(2)
        tip.setSingleStep(0.05)
        tip.setValue(float(pen.get('width', 0.5)))
        self.table.setCellWidget(r, 3, tip)
        lw = QDoubleSpinBox()
        lw.setRange(0.0, 5.0)
        lw.setDecimals(2)
        lw.setSingleStep(0.05)
        lw.setSpecialValueText("= tip")
        lw.setValue(float(pen.get('line_mm', 0.0) or 0.0))
        lw.setToolTip("Measured width of a single stroke on paper. Leave at '= tip' to use the tip size.")
        self.table.setCellWidget(r, 4, lw)
        fd = QSpinBox()
        fd.setRange(200, 6000)
        fd.setSingleStep(100)
        fd.setValue(int(pen.get('feed', DEFAULT_DRAW_FEED)))
        self.table.setCellWidget(r, 5, fd)
        it = QTableWidgetItem(pid)
        it.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(r, 6, it)

    @staticmethod
    def _style_color_button(btn):
        c = QColor(btn.property('color'))
        fg = '#FFFFFF' if c.lightnessF() < 0.55 else '#2A2824'
        btn.setStyleSheet(f"background: {c.name()}; color: {fg}; border: 1px solid #A79F90;")
        btn.setText(c.name().upper())

    def _pick_color(self, btn):
        c = QColorDialog.getColor(QColor(btn.property('color')), self, "Pen colour (sample it from your scan)")
        if c.isValid():
            btn.setProperty('color', c.name())
            self._style_color_button(btn)

    def _read_table(self):
        pens = {}
        for r in range(self.table.rowCount()):
            pid = self.table.item(r, 6).text()
            lw = self.table.cellWidget(r, 4).value()
            pen = {'id': pid, 'name': self.table.item(r, 1).text().strip() or pid,
                   'color': self.table.cellWidget(r, 2).property('color'),
                   'width': round(self.table.cellWidget(r, 3).value(), 3),
                   'feed': int(self.table.cellWidget(r, 5).value())}
            if lw > 0:
                pen['line_mm'] = round(lw, 3)
            pens[pid] = pen
        return pens

    def _checked_ids(self):
        return [self.table.item(r, 6).text() for r in range(self.table.rowCount())
                if self.table.cellWidget(r, 0).isChecked()]

    def _add(self):
        n = 1
        while f"custom_{n}" in self.pens or any(self.table.item(r, 6).text() == f"custom_{n}"
                                                for r in range(self.table.rowCount())):
            n += 1
        pid = f"custom_{n}"
        self._append_row(pid, {'name': f"New pen {n}", 'color': '#2F4B7C', 'width': 0.5})

    def _remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        protected = set(default_pens())
        for r in rows:
            if self.table.item(r, 6).text() in protected:
                QMessageBox.information(self, "Built-in pen", "Built-in pens are used by the ink sets and can't be "
                                        "removed - edit them instead.")
                continue
            self.table.removeRow(r)

    def _restore(self):
        current = self._read_table()
        for pid, pen in default_pens().items():
            current[pid] = pen
        self.pens = current
        self._fill()

    def _calibrate(self):
        ids = self._checked_ids()
        if not ids:
            QMessageBox.information(self, "Calibration", "Tick the pens to calibrate in the 'Cal.' column first.")
            return
        self.pens = self._read_table()
        self.calibration_request = ids
        self.accept()

    def _save(self):
        self.pens = self._read_table()
        self.accept()
