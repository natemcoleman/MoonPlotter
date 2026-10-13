"""Pen calibration sheet: per pen a row of hatch patches at increasing spacing, a single-line width
test and a near-solid swatch to sample the ink colour from; then one overlap patch per pen pair.

The sheet is built in paper millimetres and returned in a 'paper' data frame where the view extent is
[0, pw, -ph/2, ph/2] (1 data unit = 1 mm, aspect 1), so it previews and exports like any map.
"""
import numpy as np

from decorations import vector_text

SPACINGS = (0.5, 0.7, 1.0, 1.4, 2.0)


def _hatch_square(x0, y0, size, spacing, angle_deg):
    """Parallel lines at angle filling the square [x0, x0+size] x [y0, y0+size]."""
    th = np.radians(angle_deg)
    d = np.array([np.cos(th), np.sin(th)])
    n = np.array([-d[1], d[0]])
    c = np.array([x0 + size / 2, y0 + size / 2])
    half = size * 0.75
    lines = []
    for off in np.arange(-half, half + 1e-9, spacing):
        p = c + n * off
        # clip the infinite line p + t d to the square (Liang-Barsky)
        t0, t1 = -1e9, 1e9
        ok = True
        for k, lo, hi in ((0, x0, x0 + size), (1, y0, y0 + size)):
            if abs(d[k]) < 1e-12:
                if p[k] < lo or p[k] > hi:
                    ok = False
                continue
            a, b = (lo - p[k]) / d[k], (hi - p[k]) / d[k]
            t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
        if ok and t1 - t0 > 0.3:
            lines.append(np.array([p + t0 * d, p + t1 * d]))
    # boustrophedon so the patch plots as zigzag-free alternating strokes
    return [ln if i % 2 == 0 else ln[::-1] for i, ln in enumerate(lines)]


def build_calibration_sheet(pens, pen_ids, pw, ph):
    """Returns (segments, view_extent)."""
    m = 5.0
    label_w = 0.0
    out = []                                            # (pen_id, polyline_mm)
    n_pens = len(pen_ids)
    pairs = [(a, b) for i, a in enumerate(pen_ids) for b in pen_ids[i + 1:]]

    cols = len(SPACINGS) + 2                           # spacing patches + width line + solid swatch
    usable_w = pw - 2 * m
    cell = min(14.0, (usable_w - (cols - 1) * 2.0) / cols)
    gap = 2.0
    row_h = cell + 6.0
    pair_rows = int(np.ceil(len(pairs) / cols)) if pairs else 0
    needed = 8.0 + n_pens * row_h + (6.0 + pair_rows * row_h if pairs else 0)
    if needed > ph - 2 * m:
        shrink = (ph - 2 * m) / needed
        cell *= shrink
        row_h *= shrink

    y = ph - m
    title, _ = vector_text("PEN CALIBRATION", m, y - 3.0, 3.0, bold=True)
    out += [('base', p) for p in title]
    # column headers centred over each patch column
    x = m + label_w
    for lab in [f"{sp:g} mm" for sp in SPACINGS] + ["line", "solid"]:
        sub, _ = vector_text(lab, x + cell / 2.0, y - 6.4, 1.6, anchor='center')
        out += [('base', p) for p in sub]
        x += cell + gap
    y -= 8.0

    for pid in pen_ids:
        pen = pens[pid]
        w = float(pen.get('line_mm', pen['width']))
        y_top = y - 2.6
        lab, _ = vector_text(f"{pen['name']}", m, y_top + 0.4, 1.8)
        out += [(pid, p) for p in lab]
        y0 = y_top - cell - 0.6
        x = m + label_w
        for sp in SPACINGS:
            out += [(pid, ln) for ln in _hatch_square(x, y0, cell, sp, 45.0)]
            x += cell + gap
        # single line (measure its width on the scan)
        out.append((pid, np.array([[x + cell * 0.5, y0], [x + cell * 0.5, y0 + cell]])))
        x += cell + gap
        # near-solid swatch: crosshatch at ~0.6x the line width
        sp = max(0.15, 0.6 * w)
        out += [(pid, ln) for ln in _hatch_square(x, y0, cell, sp, 45.0)]
        out += [(pid, ln) for ln in _hatch_square(x, y0, cell, sp, 135.0)]
        y = y0 - (row_h - cell - 2.6)

    if pairs:
        y -= 2.0
        lab, _ = vector_text("OVERLAPS (1.0 mm, crossing)", m, y - 2.0, 1.8)
        out += [('base', p) for p in lab]
        y -= 4.0
        x = m
        for i, (a, b) in enumerate(pairs):
            if i and i % cols == 0:
                x = m
                y -= row_h
            y0 = y - cell
            out += [(a, ln) for ln in _hatch_square(x, y0, cell, 1.0, 15.0)]
            out += [(b, ln) for ln in _hatch_square(x, y0, cell, 1.0, 75.0)]
            x += cell + gap

    extent = [0.0, pw, -ph / 2.0, ph / 2.0]
    segments = [{'type': 'hatch', 'layer': 'calibration', 'seg': np.column_stack([p[:, 0], p[:, 1] - ph / 2.0]),
                 'level': 0, 'pen_id': pid} for pid, p in out if len(p) >= 2]
    return segments, extent
