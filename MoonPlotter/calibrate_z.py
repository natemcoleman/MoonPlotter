"""Test plots for calibrating the pen and drag-knife heights (Tools > Calibrate pen & knife heights).

The pens and the knife sit at a fixed height in their holders, so the only adjustments are in software:
  PEN_DOWN_ABOVE_HOME_MM  where 'pen down' (Z0) is, measured up from the homed Z position
  PEN_LIFT_MM             how far the pen lifts above Z0 between strokes
  CUTTER_Z_OFFSET_MM      how far below Z0 the knife goes (negative = deeper)

Pen test: a column of short lines, each drawn lower than the one above it. The first line that shows ink
is where the tip meets the paper. Knife test: a grid of small squares, each cut deeper than the last; the
first one that drops out is the depth needed. No Qt here - unit-testable.
"""
import numpy as np

import exporter
from exporter import _z, SAFE_Z_MM, TRAVEL_FEED

LONG_EVERY = 5        # every 5th test line is longer, to make counting easy


def ladder_heights(start, step, count):
    """Heights relative to the current Z0, highest first."""
    return [round(start - i * step, 4) for i in range(int(count))]


def _start(title, pen_down_above_home, travel_z, prompt):
    g = exporter.header(title, lift_mm=travel_z, pen_down_above_home=pen_down_above_home)
    return g + [f"M117 {prompt}"[:40], f"M0 {prompt}, then click"[:60]]


def pen_ladder(pen_down_above_home, start=3.0, step=0.3, count=13, feed=800.0, x0=10.0, y0=10.0,
               length=30.0, row=6.0):
    """G-code for the pen test. Line 1 (top) is the highest. Returns (gcode lines, heights)."""
    zs = ladder_heights(start, step, count)
    travel = max(zs[0] + 1.5, 1.0)
    g = _start(f"PEN HEIGHT TEST: pen-down height {pen_down_above_home:.2f} mm, lines from Z{zs[0]:+.2f} "
               f"in {step:g} mm steps", pen_down_above_home, travel, "Load a pen")
    n = len(zs)
    for i, z in enumerate(zs):
        y = y0 + (n - 1 - i) * row
        ln = length * (1.5 if (i + 1) % LONG_EVERY == 0 else 1.0)
        g += [f"; line {i + 1}: Z{z:+.2f}",
              f"G0 Z{_z(travel)} F1500", "M400",
              f"G0 X{x0:.2f} Y{y:.2f} F{TRAVEL_FEED:.0f}", "M400",
              f"M117 Line {i + 1}: Z{z:+.2f}"[:40],
              f"G1 Z{_z(z)} F300", "M400",
              f"G1 X{x0 + ln:.2f} Y{y:.2f} F{feed:.0f}",
              f"G0 Z{_z(travel)} F1500", "M400"]
    g += exporter.footer("Pen test done")
    return g, zs


def knife_ladder(pen_down_above_home, start=1.5, step=0.3, count=16, feed=600.0, x0=10.0, y0=10.0,
                 size=10.0, gap=8.0, per_row=4):
    """G-code for the knife test: squares cut left to right, top row first, each deeper than the last.
    Depths are relative to Z0 (the pen-down plane). Returns (gcode lines, depths)."""
    zs = ladder_heights(start, step, count)
    travel = max(zs[0] + 1.5, SAFE_Z_MM)
    rows = int(np.ceil(len(zs) / per_row))
    g = _start(f"KNIFE DEPTH TEST: pen-down height {pen_down_above_home:.2f} mm, squares from Z{zs[0]:+.2f} "
               f"in {step:g} mm steps", pen_down_above_home, travel, "Fit the knife, scrap card")
    sq = exporter.cutout_path(size, size, passes=1)
    for i, z in enumerate(zs):
        r, c = divmod(i, per_row)
        ox = x0 + c * (size + gap)
        oy = y0 + (rows - 1 - r) * (size + gap)
        p = sq + np.array([ox, oy])
        g += [f"; square {i + 1}: Z{z:+.2f}",
              f"G0 Z{_z(travel)} F1500", "M400",
              f"G0 X{p[0, 0]:.3f} Y{p[0, 1]:.3f} F{TRAVEL_FEED:.0f}", "M400",
              f"M117 Square {i + 1}: Z{z:+.2f}"[:40],
              f"G1 Z{_z(z)} F300", "M400"]
        g += [f"G1 X{x:.3f} Y{y:.3f} F{feed:.0f}" for x, y in p[1:]]
        g += [f"G0 Z{_z(travel)} F1500", "M400"]
    g += exporter.footer("Knife test done")
    return g, zs


def pen_result(pen_down_above_home, first_inked_z, press=0.4, air=0.8):
    """New (pen-down height, pen lift): Z0 moves to `press` mm below the first line that showed ink (the
    holder's spring / rubber band takes it up); the pen lifts `air` mm clear of the paper."""
    new_h = float(pen_down_above_home) + float(first_inked_z) - float(press)
    return round(new_h, 2), round(float(press) + float(air), 2)


def knife_result(first_through_z, margin=0.15, test_pen_down=None, pen_down=None):
    """Knife depth (CUTTER_Z_OFFSET_MM, relative to Z0): the first square that dropped out, plus a little
    margin. The test's depths were relative to the pen-down height it was made with (test_pen_down); if
    the pen-down height has changed since (pen_down), the depth is moved so the blade still reaches the
    same physical height."""
    shift = 0.0
    if test_pen_down is not None and pen_down is not None:
        shift = float(test_pen_down) - float(pen_down)
    return round(float(first_through_z) + shift - float(margin), 2)


def read_test(path):
    """Reads a pen or knife test G-code file written by pen_ladder / knife_ladder.
    Returns ('pen' | 'knife', pen-down height it was made with, [Z of line / square 1, 2, ...])."""
    import re
    kind, h, zs = None, None, []
    with open(path, 'r', errors='replace') as f:
        for line in f:
            m = re.search(r"(PEN HEIGHT|KNIFE DEPTH) TEST: pen-down height (-?[\d.]+) mm", line)
            if m and kind is None:
                kind, h = ('pen' if m.group(1) == 'PEN HEIGHT' else 'knife'), float(m.group(2))
                continue
            m = re.match(r";\s*(?:line|square) (\d+): Z([+-]?[\d.]+)", line)
            if m:
                zs.append((int(m.group(1)), float(m.group(2))))
    if kind is None or not zs:
        raise ValueError("This isn't a pen or knife test file made by MoonPlotter.")
    return kind, h, [z for _, z in sorted(zs)]


def config_lines(values):
    """The config.py lines for these values (as in config.py: PEN_LIFT_MM with one decimal)."""
    fmt = {'PEN_DOWN_ABOVE_HOME_MM': "{:.2f}", 'PEN_LIFT_MM': "{:.1f}", 'CUTTER_Z_OFFSET_MM': "{:.2f}",
           'Z_MAX_FEED': "{:.1f}"}
    return [f"{k} = {fmt[k].format(float(v))}" for k, v in values.items() if k in fmt]
