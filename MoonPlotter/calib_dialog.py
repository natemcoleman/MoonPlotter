"""Tools > Calibrate pen & knife heights: test plots + the three per-machine Z values."""
import os

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel, QPushButton,
                             QDoubleSpinBox, QSpinBox, QDialogButtonBox, QFileDialog, QMessageBox)

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


class CalibrationDialog(QDialog):
    def __init__(self, parent=None, pen_feed=800.0, knife_feed=600.0, save_dir=None):
        super().__init__(parent)
        self.setWindowTitle("Calibrate pen & knife heights")
        self.setMinimumWidth(560)
        self.pen_feed, self.knife_feed = float(pen_feed), float(knife_feed)
        self.save_dir = save_dir or os.path.expanduser("~")
        self._pen_zs, self._knife_zs = None, None
        cur = cfg.machine_settings()
        v = QVBoxLayout(self)
        v.addWidget(_hint(
            "Your pens and knife sit at a fixed height in their holders, so these numbers are how you adjust "
            "them. Do step 1, then step 2 (the knife depth is measured from the pen height). Redo this after "
            "re-levelling the bed or on another machine. Values are saved for this computer only "
            f"({cfg.MACHINE_SETTINGS_FILE}); the defaults stay in config.py."))

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
        for r, (lab, w) in enumerate((("Pen-down height above home", self.spin_h), ("Pen lift", self.spin_lift),
                                      ("Knife depth (from pen-down)", self.spin_knife),
                                      ("Max Z speed", self.spin_zspeed))):
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
        b_pen = QPushButton("Save pen test G-code…")
        b_pen.clicked.connect(self.save_pen_test)
        g1.addWidget(b_pen, 1, 0, 1, 6)
        g1.addWidget(_hint("Plot it on plain paper. Lines are drawn top to bottom, each lower than the last "
                           "(every 5th is longer, for counting; the printer screen shows each line's height). "
                           "No ink anywhere? Lower 'Top line'. Ink on line 1 already? Raise it."), 2, 0, 1, 6)
        g1.addWidget(QLabel("First line with ink:"), 3, 0, 1, 2)
        self.spin_p_first = QSpinBox()
        self.spin_p_first.setRange(1, 30)
        self.spin_p_first.setValue(7)
        g1.addWidget(self.spin_p_first, 3, 2)
        g1.addWidget(QLabel("press"), 3, 3)
        self.spin_press = _dspin(0.0, 3.0, 0.1, 0.4)
        self.spin_press.setToolTip("Z0 goes this far below the first inked line, so the holder's spring keeps "
                                   "steady contact. 0.3-0.5 mm suits a rubber-band holder.")
        g1.addWidget(self.spin_press, 3, 4)
        b_use = QPushButton("Use")
        b_use.clicked.connect(self.use_pen_result)
        g1.addWidget(b_use, 3, 5)
        self.lbl_pen = _hint("")
        g1.addWidget(self.lbl_pen, 4, 0, 1, 6)
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
        b_knife = QPushButton("Save knife test G-code…")
        b_knife.clicked.connect(self.save_knife_test)
        g2.addWidget(b_knife, 1, 0, 1, 6)
        g2.addWidget(_hint("Uses the pen-down height above, so do step 1 first. Lay scrap card of the kind you "
                           "will cut on a mat. 10 mm squares are cut left to right, top row first, each deeper "
                           "than the last."), 2, 0, 1, 6)
        g2.addWidget(QLabel("First square that drops out:"), 3, 0, 1, 2)
        self.spin_k_first = QSpinBox()
        self.spin_k_first.setRange(1, 24)
        self.spin_k_first.setValue(10)
        g2.addWidget(self.spin_k_first, 3, 2)
        g2.addWidget(QLabel("+ margin"), 3, 3)
        self.spin_margin = _dspin(0.0, 1.0, 0.05, 0.15)
        g2.addWidget(self.spin_margin, 3, 4)
        b_use2 = QPushButton("Use")
        b_use2.clicked.connect(self.use_knife_result)
        g2.addWidget(b_use2, 3, 5)
        self.lbl_knife = _hint("")
        g2.addWidget(self.lbl_knife, 4, 0, 1, 6)
        v.addWidget(b2)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
                              | QDialogButtonBox.StandardButton.RestoreDefaults)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.restore_defaults)
        v.addWidget(bb)

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
            self._pen_zs = zs
            self.spin_p_first.setMaximum(len(zs))
            self.lbl_pen.setText(f"Saved. Line 1 = Z{zs[0]:+.2f} ... line {len(zs)} = Z{zs[-1]:+.2f} "
                                 f"(pen-down height {self.spin_h.value():.2f} mm). Plot it, then enter the first "
                                 "line with ink and click Use.")

    def save_knife_test(self):
        g, zs = CZ.knife_ladder(self.spin_h.value(), self.spin_k_start.value(), self.spin_k_step.value(),
                                self.spin_k_count.value(), feed=self.knife_feed)
        if self._save_gcode("knife_depth_test.gcode", g):
            self._knife_zs = zs
            self.spin_k_first.setMaximum(len(zs))
            self.lbl_knife.setText(f"Saved. Square 1 = Z{zs[0]:+.2f} ... square {len(zs)} = Z{zs[-1]:+.2f}. "
                                   "Cut it, then enter the first square that drops out and click Use.")

    def use_pen_result(self):
        zs = self._pen_zs
        if zs is None:
            QMessageBox.information(self, "Calibration", "Save the pen test G-code first (again after each "
                                    "'Use': the test is relative to the pen-down height it was made with).")
            return
        i = self.spin_p_first.value() - 1
        if i >= len(zs):
            return
        old = self.spin_h.value()
        new_h, lift = CZ.pen_result(old, zs[i], self.spin_press.value())
        self.spin_h.setValue(new_h)
        self.spin_lift.setValue(lift)
        self._pen_zs = None                       # the ladder was relative to the old height
        self._knife_zs = None                     # ... and so was any knife test
        self.lbl_pen.setText(f"Line {i + 1} (Z{zs[i]:+.2f}) touched first: pen-down height {old:.2f} -> "
                             f"{new_h:.2f} mm, lift {lift:.1f} mm. Save, or run the pen test again to check "
                             "(the first line with ink should now be near Z+" f"{self.spin_press.value():.2f}).")

    def use_knife_result(self):
        zs = self._knife_zs
        if zs is None:
            QMessageBox.information(self, "Calibration", "Save the knife test G-code first.")
            return
        i = self.spin_k_first.value() - 1
        if i >= len(zs):
            return
        d = CZ.knife_result(zs[i], self.spin_margin.value())
        self.spin_knife.setValue(d)
        self.lbl_knife.setText(f"Square {i + 1} (Z{zs[i]:+.2f}) dropped out: knife depth {d:+.2f} mm from "
                               "pen-down. Save to use it.")

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
