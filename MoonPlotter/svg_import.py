"""Multi-layer SVG import (Inkscape drawings, exported vector art) for Image mode.

Each top-level layer / group of the SVG becomes one plot layer with its own pen. Geometry is read with
vpype (already a dependency for G-code export), which flattens curves, shapes and transforms into
polylines and keeps Inkscape layer names and colours. The drawing is then scaled uniformly and centred to
fit the page.
"""
import os

import numpy as np

PX_TO_MM = 25.4 / 96.0            # vpype works in CSS pixels
QUANTIZATION_MM = 0.1             # curve flattening tolerance
PAGE_MARGIN_MM = 4.0              # kept clear around the drawing at 100 % size
FALLBACK_COLORS = [(0.0, 0.0, 0.0), (0.12, 0.25, 0.75), (0.85, 0.13, 0.23), (0.09, 0.58, 0.29),
                   (0.42, 0.17, 0.62), (1.0, 0.4, 0.0)]


def _layer_meta(lc, lid):
    name, color = None, None
    try:
        name = lc.property("vp_name")
    except Exception:
        pass
    try:
        c = lc.property("vp_color")
        if c is not None:
            color = (c.red / 255.0, c.green / 255.0, c.blue / 255.0)
    except Exception:
        pass
    return (str(name) if name else f"Layer {lid}"), color


def load_svg(path):
    """-> list of {'id', 'name', 'color' (r, g, b 0..1), 'polys': [(n, 2) mm arrays, y down]}."""
    import vpype as vp
    doc = vp.read_multilayer_svg(path, quantization=QUANTIZATION_MM / PX_TO_MM)
    layers = []
    for i, (lid, lc) in enumerate(sorted(doc.layers.items())):
        polys = [np.column_stack([ln.real, ln.imag]) * PX_TO_MM for ln in lc if len(ln) >= 2]
        if not polys:
            continue
        name, color = _layer_meta(lc, lid)
        layers.append({'id': int(lid), 'name': name, 'color': color or FALLBACK_COLORS[i % len(FALLBACK_COLORS)],
                       'polys': polys})
    if not layers:
        raise ValueError(f"No drawable paths found in {os.path.basename(path)}.")
    return layers


def bounds(layers):
    allp = np.vstack([p for l in layers for p in l['polys']])
    return allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max()


def fit_layers(layers, pw, ph, size_pct=100, margin_mm=PAGE_MARGIN_MM):
    """Image-mode layers ({'key', 'label', 'color', 'polys'} in page mm, origin bottom-left, y up): the drawing
    scaled uniformly to fit inside the page minus the margin (times size_pct) and centred."""
    x0, y0, x1, y1 = bounds(layers)
    w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    avail_w, avail_h = max(pw - 2 * margin_mm, 1.0), max(ph - 2 * margin_mm, 1.0)
    s = min(avail_w / w, avail_h / h) * size_pct / 100.0
    ox = (pw - w * s) / 2.0
    oy = (ph - h * s) / 2.0
    out = []
    for l in layers:
        polys = []
        for p in l['polys']:
            q = np.empty_like(p)
            q[:, 0] = ox + (p[:, 0] - x0) * s
            q[:, 1] = oy + (y1 - p[:, 1]) * s              # SVG y points down; the page's y points up
            polys.append(q)
        out.append({'key': f"img:svg{l['id']}", 'label': l['name'], 'color': tuple(l['color']), 'polys': polys})
    return out, s
