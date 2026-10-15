"""Export dialog: per-pen plot list (order, strokes, estimate, feed rate) and plotting options."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
                             QCheckBox, QSpinBox, QDialogButtonBox, QHeaderView, QAbstractItemView)

from ui_widgets import swatch_icon


class ExportDialog(QDialog):
    """rows: list of dicts {pen_id, name, color, strokes, minutes, feed}. Read the result with settings()."""

    def __init__(self, rows, estimate_fn, plot_area_note, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export G-code")
        self.setMinimumWidth(620)
        self.rows = rows
        self.estimate_fn = estimate_fn            # (pen_id, feed) -> minutes
        v = QVBoxLayout(self)
        v.setSpacing(10)

        title = QLabel("PENS · PLOT ORDER (LIGHTEST INK FIRST)")
        title.setObjectName("panelTitle")
        v.addWidget(title)

        self.table = QTableWidget(len(rows), 5)
        self.table.setHorizontalHeaderLabels(["Plot", "Pen", "Strokes", "Draw feed (mm/min)", "Est. time"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in (0, 2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.checks, self.feeds = [], []
        for r, row in enumerate(rows):
            chk = QCheckBox()
            chk.setChecked(True)
            chk.toggled.connect(self._update_total)
            self.table.setCellWidget(r, 0, chk)
            self.checks.append(chk)
            it = QTableWidgetItem(swatch_icon([row['color']]), f"{r + 1}. {row['name']}")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(r, 1, it)
            it = QTableWidgetItem(f"{row['strokes']:,}")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, 2, it)
            sp = QSpinBox()
            sp.setRange(200, 6000)
            sp.setSingleStep(100)
            sp.setValue(int(row['feed']))
            sp.setToolTip("Pen-down speed. Thick gel pens skip if this is too fast.")
            sp.valueChanged.connect(lambda _v, rr=r: self._update_row(rr))
            self.table.setCellWidget(r, 3, sp)
            self.feeds.append(sp)
            self.table.setItem(r, 4, QTableWidgetItem(""))
        self.table.setMinimumHeight(min(60 + 34 * len(rows), 320))
        v.addWidget(self.table)

        opt_title = QLabel("OPTIONS")
        opt_title.setObjectName("panelTitle")
        v.addWidget(opt_title)
        self.chk_combine = QCheckBox("One combined file with a pause (M0) to swap pens between colours")
        self.chk_combine.setToolTip("Keeps the same home position for every pen - no re-homing between pens.")
        v.addWidget(self.chk_combine)
        h = QHBoxLayout()
        h.addWidget(QLabel("Settle after each pen-down:"))
        self.spin_settle = QSpinBox()
        self.spin_settle.setRange(0, 1000)
        self.spin_settle.setSingleStep(25)
        self.spin_settle.setSuffix(" ms")
        self.spin_settle.setToolTip("A short dwell (G4) so gel ink starts flowing before the pen moves - fewer skipped starts.")
        self.spin_settle.valueChanged.connect(self._update_all)
        h.addWidget(self.spin_settle)
        h.addSpacing(16)
        h.addWidget(QLabel("Prime with the"))
        self.spin_prime = QSpinBox()
        self.spin_prime.setRange(0, 200)
        self.spin_prime.setValue(10)
        self.spin_prime.setToolTip("These longest strokes are drawn first, so the ink is flowing before fine detail.")
        h.addWidget(self.spin_prime)
        h.addWidget(QLabel("longest strokes first"))
        h.addStretch()
        v.addLayout(h)

        self.lbl_area = QLabel(plot_area_note)
        self.lbl_area.setObjectName("hint")
        self.lbl_area.setWordWrap(True)
        v.addWidget(self.lbl_area)
        self.lbl_total = QLabel("")
        self.lbl_total.setObjectName("intervalBadge")
        v.addWidget(self.lbl_total)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.btn_ok = bb.addButton("Export…", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_ok.setObjectName("primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._update_all()

    def _row_minutes(self, r):
        m = self.estimate_fn(self.rows[r]['pen_id'], self.feeds[r].value())
        # dwell per stroke
        return m + self.rows[r]['strokes'] * self.spin_settle.value() / 60000.0 * 1.0

    def _update_row(self, r):
        from exporter import fmt_duration
        self.table.item(r, 4).setText("≈ " + fmt_duration(self._row_minutes(r)))
        self._update_total()

    def _update_all(self, *_):
        for r in range(len(self.rows)):
            self._update_row(r)

    def _update_total(self, *_):
        from exporter import fmt_duration
        total = sum(self._row_minutes(r) for r in range(len(self.rows)) if self.checks[r].isChecked())
        n = sum(c.isChecked() for c in self.checks)
        self.lbl_total.setText(f"{n} pen(s) · total ≈ {fmt_duration(total)}")
        self.btn_ok.setEnabled(n > 0)

    def settings(self):
        return {
            'pens': [(row['pen_id'], self.feeds[r].value()) for r, row in enumerate(self.rows)
                     if self.checks[r].isChecked()],
            'feeds': {row['pen_id']: self.feeds[r].value() for r, row in enumerate(self.rows)},
            'combine': self.chk_combine.isChecked(),
            'settle_ms': self.spin_settle.value(),
            'prime': self.spin_prime.value(),
        }
