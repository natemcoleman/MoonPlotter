"""Tools > Calibrate pen & knife heights: test plots + the three per-machine Z values."""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel, QPushButton,
                             QDoubleSpinBox, QSpinBox, QDialogButtonBox, QFileDialog, QMessageBox, QScrollArea,
                             QWidget, QFrame)

import config as cfg
import calibrate_z as CZ


def _dspin(lo, hi, step, val, suffix=" mm", dec=2):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setSuffix(suffix)
    s.setValue(val)
    return s


def _hint(text):
    lab = QLabel(text)
    lab.setObjectName("hint")
    lab.setWordWrap(True)
    return lab


def _result_label():
    """Selectable, monospaced box for the config.py lines."""
    lab = QLabel("")
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    f = QFont("Consolas")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSizeF(max(f.pointSizeF(), 10.0))
    lab.setFont(f)
    lab.setStyleSheet("QLabel { background: #F4F1EA; border: 1px solid #C9C2B3; padding: 6px 8px 8px 8px; }")
    return lab


def _fit_lines(lab, n):
    """Room for n lines of the label's font (underscores sit low and get clipped otherwise)."""
    lab.setMinimumHeight(int(n * lab.fontMetrics().lineSpacing() + 20))


class CalibrationDialog(QDialog):
    def __init__(self, parent=None, pen_feed=800.0, knife_feed=600.0, save_dir=None):
        super().__init__(parent)
        self.setWindowTitle("Calibrate pen & knife heights")
        self.setMinimumWidth(600)
        self.pen_feed, self.knife_feed = float(pen_feed), float(knife_feed)
        self.save_dir = save_dir or os.path.expanduser("~")
        cur = cfg.machine_settings()
        outer = QVBoxLayout(self)
        scroll = QScrollArea()                            # scrolls on small screens
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 6, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        v.addWidget(_hint(
            "Your pens and knife sit at a fixed height in their holders, so these numbers are how you adjust "
            "them. Do step 1, then step 2 (the knife depth is measured from the pen height). Each step shows "
            "the exact lines to put in config.py. Redo this after re-levelling the bed or on another machine."))

        # ---- current values
        box = QGroupBox("This machine")
        gl = QGridLayout(box)
        self.spin_h = _dspin(-20.0, 40.0, 0.05, cur['PEN_DOWN_ABOVE_HOME_MM'])
        self.spin_h.setToolTip("Pen down (Z0) is this far above the homed Z position. Higher = lighter pen.")
        self.spin_lift = _dspin(0.3, 15.0, 0.1, cur['PEN_LIFT_MM'], dec=1)
        self.spin_lift.setToolTip("How far the pen lifts above Z0 between strokes. Less = faster plots.")
        self.spin_knife = _dspin(-10.0, 5.0, 0.05, cur['CUTTER_Z_OFFSET_MM'])
        self.spin_knife.setToolTip("Knife-down height relative to Z0 (negative = deeper).")
        self.spin_zspeed = _dspin(0.5, 50.0, 0.5, cur['Z_MAX_FEED'] / 60.0, suffix=" mm/s", dec=1)
        self.spin_zspeed.setToolTip("Your firmware's maximum Z speed (M503 > M203 Z). Only used for time "
                                    "estimates; stock Ender 3 is 5 mm/s.")
        for r, (lab, w) in enumerate((("Pen-down height above home  (PEN_DOWN_ABOVE_HOME_MM)", self.spin_h),
                                      ("Pen lift  (PEN_LIFT_MM)", self.spin_lift),
                                      ("Knife depth from pen-down  (CUTTER_Z_OFFSET_MM)", self.spin_knife),
                                      ("Max Z speed  (Z_MAX_FEED ÷ 60)", self.spin_zspeed))):
            gl.addWidget(QLabel(lab), r, 0)
            gl.addWidget(w, r, 1)
        v.addWidget(box)

        # ---- step 1: pen
        b1 = QGroupBox("1 · Pen height")
        g1 = QGridLayout(b1)
        self.spin_p_start = _dspin(-10.0, 15.0, 0.1, 3.0)
        self.spin_p_step = _dspin(0.05, 2.0, 0.05, 0.3)
        self.spin_p_count = QSpinBox()
        self.spin_p_count.setRange(3, 30)
        self.spin_p_count.setValue(13)
        g1.addWidget(QLabel("Top line at Z0 +"), 0, 0)
        g1.addWidget(self.spin_p_start, 0, 1)
        g1.addWidget(QLabel("step down"), 0, 2)
        g1.addWidget(self.spin_p_step, 0, 3)
        g1.addWidget(QLabel("lines"), 0, 4)
        g1.addWidget(self.spin_p_count, 0, 5)
        row = QHBoxLayout()
        b_pen = QPushButton("Save pen test G-code…")
        b_pen.clicked.connect(self.save_pen_test)
        b_read = QPushButton("Read a test file…")
        b_read.setToolTip("Already plotted a test? Open its G-code file: the line heights and the pen-down "
                          "height it was made with are read from it.")
        b_read.clicked.connect(lambda: self.read_test_file('pen'))
        row.addWidget(b_pen, 1)
        row.addWidget(b_read)
        g1.addLayout(row, 1, 0, 1, 6)
        g1.addWidget(_hint("Plot it on plain paper. Lines are drawn top to bottom, each lower than the last "
                           "(every 5th is longer, for counting; the printer screen shows each line's height). "
                           "No ink anywhere? Lower 'Top line'. Ink on line 1 already? Raise it. The settings "
                           "above must match the test you plotted - 'Read a test file…' fills them in."),
                     2, 0, 1, 6)
        g1.addWidget(QLabel("Test made with pen-down height"), 3, 0, 1, 2)
        self.spin_p_testh = _dspin(-20.0, 40.0, 0.05, cur['PEN_DOWN_ABOVE_HOME_MM'])
        self.spin_p_testh.setToolTip("The pen-down height when the test G-code was made (it is in the file's "
                                     "first line). Filled in automatically when you save or read a test.")
        g1.addWidget(self.spin_p_testh, 3, 2)
        g1.addWidget(QLabel("First solid line:"), 4, 0, 1, 2)
        self.spin_p_first = QSpinBox()
        self.spin_p_first.setRange(1, self.spin_p_count.value())
        self.spin_p_first.setValue(7)
        g1.addWidget(self.spin_p_first, 4, 2)
        g1.addWidget(QLabel("press"), 4, 3)
        self.spin_press = _dspin(0.0, 3.0, 0.1, 0.4)
        self.spin_press.setToolTip("Z0 goes this far below the first solid line, so the holder's spring keeps "
                                   "steady contact. 0.3-0.5 mm suits a rubber-band holder.")
        g1.addWidget(self.spin_press, 4, 4)
        g1.addWidget(QLabel("Put in config.py:"), 5, 0, 1, 6)
        self.lbl_pen_result = _result_label()
        _fit_lines(self.lbl_pen_result, 2)
        g1.addWidget(self.lbl_pen_result, 6, 0, 1, 6)
        row = QHBoxLayout()
        self.lbl_pen = _hint("")
        b_copy = QPushButton("Copy")
        b_copy.setToolTip("Copy these config.py lines")
        b_copy.clicked.connect(lambda: self._copy(self.lbl_pen_result))
        b_use = QPushButton("Use")
        b_use.setToolTip("Put these values in 'This machine' above. Do this before step 2: the knife test and "
                         "its result use that pen-down height. Click Save to keep them.")
        b_use.clicked.connect(self.use_pen_result)
        row.addWidget(self.lbl_pen, 1)
        row.addWidget(b_copy)
        row.addWidget(b_use)
        g1.addLayout(row, 7, 0, 1, 6)
        v.addWidget(b1)

        # ---- step 2: knife
        b2 = QGroupBox("2 · Knife depth")
        g2 = QGridLayout(b2)
        self.spin_k_start = _dspin(-10.0, 10.0, 0.1, 1.5)
        self.spin_k_step = _dspin(0.05, 2.0, 0.05, 0.3)
        self.spin_k_count = QSpinBox()
        self.spin_k_count.setRange(3, 24)
        self.spin_k_count.setValue(16)
        g2.addWidget(QLabel("First square at Z0 +"), 0, 0)
        g2.addWidget(self.spin_k_start, 0, 1)
        g2.addWidget(QLabel("step down"), 0, 2)
        g2.addWidget(self.spin_k_step, 0, 3)
        g2.addWidget(QLabel("squares"), 0, 4)
        g2.addWidget(self.spin_k_count, 0, 5)
        row = QHBoxLayout()
        b_knife = QPushButton("Save knife test G-code…")
        b_knife.clicked.connect(self.save_knife_test)
        b_read2 = QPushButton("Read a test file…")
        b_read2.setToolTip("Already cut a test? Open its G-code file: the square depths and the pen-down "
                           "height it was made with are read from it.")
        b_read2.clicked.connect(lambda: self.read_test_file('knife'))
        row.addWidget(b_knife, 1)
        row.addWidget(b_read2)
        g2.addLayout(row, 1, 0, 1, 6)
        g2.addWidget(_hint("Uses the pen-down height above, so do step 1 first. Lay scrap card of the kind you "
                           "will cut on a mat. 10 mm squares are cut left to right, top row first, each deeper "
                           "than the last."), 2, 0, 1, 6)
        g2.addWidget(QLabel("Test made with pen-down height"), 3, 0, 1, 2)
        self.spin_k_testh = _dspin(-20.0, 40.0, 0.05, cur['PEN_DOWN_ABOVE_HOME_MM'])
        self.spin_k_testh.setToolTip("The pen-down height when the knife test was made. If the pen-down "
                                     "height has changed since, the knife depth is corrected for it.")
        g2.addWidget(self.spin_k_testh, 3, 2)
        g2.addWidget(QLabel("First square that drops out:"), 4, 0, 1, 2)
        self.spin_k_first = QSpinBox()
        self.spin_k_first.setRange(1, self.spin_k_count.value())
        self.spin_k_first.setValue(10)
        g2.addWidget(self.spin_k_first, 4, 2)
        g2.addWidget(QLabel("+ margin"), 4, 3)
        self.spin_margin = _dspin(0.0, 1.0, 0.05, 0.15)
        g2.addWidget(self.spin_margin, 4, 4)
        g2.addWidget(QLabel("Put in config.py:"), 5, 0, 1, 6)
        self.lbl_knife_result = _result_label()
        _fit_lines(self.lbl_knife_result, 1)
        g2.addWidget(self.lbl_knife_result, 6, 0, 1, 6)
        row = QHBoxLayout()
        self.lbl_knife = _hint("")
        b_copy2 = QPushButton("Copy")
        b_copy2.setToolTip("Copy this config.py line")
        b_copy2.clicked.connect(lambda: self._copy(self.lbl_knife_result))
        b_use2 = QPushButton("Use")
        b_use2.setToolTip("Put this value in 'This machine' above (click Save to keep it).")
        b_use2.clicked.connect(self.use_knife_result)
        row.addWidget(self.lbl_knife, 1)
        row.addWidget(b_copy2)
        row.addWidget(b_use2)
        g2.addLayout(row, 7, 0, 1, 6)
        v.addWidget(b2)

        self.lbl_where = _hint("")
        outer.addWidget(self.lbl_where)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
                              | QDialogButtonBox.StandardButton.RestoreDefaults)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.restore_defaults)
        outer.addWidget(bb)

        # the results follow every change
        self.spin_p_count.valueChanged.connect(lambda n: self.spin_p_first.setMaximum(n))
        self.spin_k_count.valueChanged.connect(lambda n: self.spin_k_first.setMaximum(n))
        for w in (self.spin_p_start, self.spin_p_step, self.spin_p_count, self.spin_p_testh, self.spin_p_first,
                  self.spin_press, self.spin_k_start, self.spin_k_step, self.spin_k_count, self.spin_k_testh,
                  self.spin_k_first, self.spin_margin, self.spin_h):
            w.valueChanged.connect(lambda *_: self.update_results())
        self.update_results()
        # as tall as the content, but never taller than the screen
        scr = (parent.screen() if parent is not None else None) or QGuiApplication.primaryScreen()
        want = body.sizeHint().height() + 140
        if scr is not None:
            want = min(want, int(scr.availableGeometry().height() * 0.92))
        self.resize(max(self.minimumWidth(), body.sizeHint().width() + 40), want)

    # ---- results
    def _pen_numbers(self):
        """(line index, its Z, new pen-down height, new lift) from the pen test settings."""
        zs = CZ.ladder_heights(self.spin_p_start.value(), self.spin_p_step.value(), self.spin_p_count.value())
        i = min(self.spin_p_first.value(), len(zs)) - 1
        new_h, lift = CZ.pen_result(self.spin_p_testh.value(), zs[i], self.spin_press.value())
        return i, zs[i], new_h, lift

    def _knife_numbers(self):
        """(square index, its Z, knife depth for the pen-down height in 'This machine')."""
        zs = CZ.ladder_heights(self.spin_k_start.value(), self.spin_k_step.value(), self.spin_k_count.value())
        i = min(self.spin_k_first.value(), len(zs)) - 1
        d = CZ.knife_result(zs[i], self.spin_margin.value(), test_pen_down=self.spin_k_testh.value(),
                            pen_down=self.spin_h.value())
        return i, zs[i], d

    def update_results(self):
        i, z, new_h, lift = self._pen_numbers()
        self.lbl_pen_result.setText("\n".join(CZ.config_lines({'PEN_DOWN_ABOVE_HOME_MM': new_h,
                                                               'PEN_LIFT_MM': lift})))
        self.lbl_pen.setText(f"Line {i + 1} was drawn at Z{z:+.2f}, with the pen-down height at "
                             f"{self.spin_p_testh.value():.2f} mm.")
        k, kz, d = self._knife_numbers()
        self.lbl_knife_result.setText(CZ.config_lines({'CUTTER_Z_OFFSET_MM': d})[0])
        moved = abs(self.spin_k_testh.value() - self.spin_h.value()) > 1e-9
        self.lbl_knife.setText(f"Square {k + 1} was cut at Z{kz:+.2f}"
                               + (f". Corrected for the pen-down height going from {self.spin_k_testh.value():.2f}"
                                  f" to {self.spin_h.value():.2f} mm ('This machine')." if moved else
                                  f", with the pen-down height at {self.spin_h.value():.2f} mm."))
        if os.path.exists(cfg.MACHINE_SETTINGS_FILE):
            self.lbl_where.setText(
                f"Note: this computer has {cfg.MACHINE_SETTINGS_FILE}, which overrides config.py. If you edit "
                "config.py by hand, also click Use and Save here (or delete that file), or your edit won't be "
                "used.")
        else:
            self.lbl_where.setText(
                "Either copy the lines into config.py (then Cancel), or click Use and Save: Save writes "
                f"{cfg.MACHINE_SETTINGS_FILE}, which overrides config.py on this computer from then on.")

    def _copy(self, label):
        QGuiApplication.clipboard().setText(label.text())

    # ---- helpers
    def _save_gcode(self, name, lines):
        path, _ = QFileDialog.getSaveFileName(self, "Save test G-code", os.path.join(self.save_dir, name),
                                              "G-Code Files (*.gcode)")
        if not path:
            return False
        try:
            with open(path, "w") as f:
                f.write("\n".join(lines))
        except Exception as e:
            QMessageBox.critical(self, "Calibration", f"Could not write {path}:\n{e}")
            return False
        return True

    def save_pen_test(self):
        g, zs = CZ.pen_ladder(self.spin_h.value(), self.spin_p_start.value(), self.spin_p_step.value(),
                              self.spin_p_count.value(), feed=self.pen_feed)
        if self._save_gcode("pen_height_test.gcode", g):
            self.spin_p_testh.setValue(self.spin_h.value())
            QMessageBox.information(self, "Calibration", f"Saved. Line 1 = Z{zs[0]:+.2f} ... line {len(zs)} = "
                                    f"Z{zs[-1]:+.2f}. Plot it, then set 'First solid line': the lines to put in "
                                    "config.py are shown under it.")

    def save_knife_test(self):
        g, zs = CZ.knife_ladder(self.spin_h.value(), self.spin_k_start.value(), self.spin_k_step.value(),
                                self.spin_k_count.value(), feed=self.knife_feed)
        if self._save_gcode("knife_depth_test.gcode", g):
            self.spin_k_testh.setValue(self.spin_h.value())
            QMessageBox.information(self, "Calibration", f"Saved. Square 1 = Z{zs[0]:+.2f} ... square {len(zs)} "
                                    f"= Z{zs[-1]:+.2f}. Cut it, then set 'First square that drops out'.")

    def read_test_file(self, want):
        path, _ = QFileDialog.getOpenFileName(self, "Open a test G-code file", self.save_dir,
                                              "G-Code Files (*.gcode);;All files (*)")
        if not path:
            return
        try:
            kind, h, zs = CZ.read_test(path)
        except Exception as e:
            QMessageBox.warning(self, "Calibration", f"Could not read {os.path.basename(path)}:\n{e}")
            return
        if kind != want or len(zs) < 2:
            QMessageBox.warning(self, "Calibration", f"{os.path.basename(path)} is a {kind} test, not a "
                                f"{want} test.")
            return
        start, step, n = zs[0], round(zs[0] - zs[1], 4), len(zs)
        if want == 'pen':
            spins = (self.spin_p_start, self.spin_p_step, self.spin_p_count, self.spin_p_testh)
        else:
            spins = (self.spin_k_start, self.spin_k_step, self.spin_k_count, self.spin_k_testh)
        for s, val in zip(spins, (start, step, n, h)):
            s.setValue(val)
        self.update_results()

    def use_pen_result(self):
        _, _, new_h, lift = self._pen_numbers()
        self.spin_h.setValue(new_h)
        self.spin_lift.setValue(lift)

    def use_knife_result(self):
        self.spin_knife.setValue(self._knife_numbers()[2])

    def restore_defaults(self):
        d = cfg.MACHINE_DEFAULTS
        self.spin_h.setValue(d['PEN_DOWN_ABOVE_HOME_MM'])
        self.spin_lift.setValue(d['PEN_LIFT_MM'])
        self.spin_knife.setValue(d['CUTTER_Z_OFFSET_MM'])
        self.spin_zspeed.setValue(d['Z_MAX_FEED'] / 60.0)

    def values(self):
        return {'PEN_DOWN_ABOVE_HOME_MM': self.spin_h.value(), 'PEN_LIFT_MM': self.spin_lift.value(),
                'CUTTER_Z_OFFSET_MM': self.spin_knife.value(), 'Z_MAX_FEED': self.spin_zspeed.value() * 60.0}

    def save(self):
        try:
            cfg.save_machine_settings(self.values())
        except Exception as e:
            QMessageBox.critical(self, "Calibration", f"Could not save {cfg.MACHINE_SETTINGS_FILE}:\n{e}")
            return
        self.accept()
