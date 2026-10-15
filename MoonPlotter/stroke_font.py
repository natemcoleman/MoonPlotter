"""Single-stroke plotter lettering (Hershey fonts, as shipped with vpype).

Outline fonts make the pen trace both edges of every letter, so small text fills in and blobs.
Hershey fonts are drawn with one centre-line stroke per letter part - what pen plotters are made for.
The glyph data is read straight from vpype's font folder (no vpype import needed); if it can't be
found, callers fall back to outline text.
"""
import importlib.util
import os
import pickle

import numpy as np

_CACHE = {}
FALLBACK_DIRS = []            # extra folders to look in (tests)

# Characters the Hershey set lacks -> close ASCII stand-ins ('°' is drawn as a small circle).
_SUBST = {'–': '-', '—': '-', '−': '-', '·': '-', '•': '-', '’': "'",
          '‘': "'", '“': '"', '”': '"', '×': 'x', '…': '...', ' ': ' '}


def _font_dirs():
    dirs = list(FALLBACK_DIRS)
    try:
        spec = importlib.util.find_spec('vpype')
        if spec and spec.submodule_search_locations:
            dirs.append(os.path.join(list(spec.submodule_search_locations)[0], 'fonts'))
    except Exception:
        pass
    dirs.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts'))
    return dirs


def load_font(name='futural'):
    """Returns the glyph list [(left, right, [polyline, ...]), ...] for ASCII 32..127, or None."""
    if name in _CACHE:
        return _CACHE[name]
    data = None
    for d in _font_dirs():
        p = os.path.join(d, name + '.pickle')
        if os.path.exists(p):
            try:
                with open(p, 'rb') as f:
                    data = pickle.load(f)
                break
            except Exception:
                data = None
    _CACHE[name] = data
    return data


def available(name='futural'):
    return load_font(name) is not None


def stroke_text(text, x, y, cap_height, anchor='left', font='futural', spacing=0.0):
    """Polylines (paper mm, y up) for 'text' with its baseline at y and capitals 'cap_height' mm tall.
    anchor: 'left' | 'center' | 'right' about x. Returns (polylines, (xmin, ymin, xmax, ymax)) or None
    if the font isn't available."""
    glyphs = load_font(font)
    if glyphs is None:
        return None
    if not text:
        return [], None
    # Hershey units: y grows downward; capital H spans -12 (top) .. 9 (baseline) in the futura sets.
    h_lines = glyphs[ord('H') - 32][2]
    ys = [p[1] for ln in h_lines for p in ln]
    top, base = min(ys), max(ys)
    k = cap_height / float(base - top)
    gap = spacing * cap_height / k                     # extra letter spacing (in font units) for pen width

    polys, cx = [], 0.0
    for ch in ''.join(_SUBST.get(c, c) for c in text):
        if ch == '°':                             # degree sign: small circle at cap height
            r = 0.16 * (base - top)
            t = np.linspace(0, 2 * np.pi, 13)
            polys.append(np.column_stack([cx + r + r * np.cos(t), top + r + r * np.sin(t)]))
            cx += 2 * r + 0.25 * (base - top) + gap
            continue
        i = ord(ch) - 32
        if i < 0 or i >= len(glyphs):
            cx += 0.5 * (base - top)
            continue
        lt, rt, lines = glyphs[i]
        for ln in lines:
            if len(ln) >= 2:
                a = np.asarray(ln, dtype=float)
                polys.append(np.column_stack([cx - lt + a[:, 0], a[:, 1]]))
        cx += (rt - lt) + gap
    width = (cx - gap) * k
    dx = {'left': 0.0, 'center': -width / 2.0, 'right': -width}[anchor]
    out = [np.column_stack([x + dx + p[:, 0] * k, y - (p[:, 1] - base) * k]) for p in polys]
    if not out:
        return [], (x + dx, y, x + dx + width, y + cap_height)
    allp = np.vstack(out)
    box = (min(allp[:, 0].min(), x + dx), min(allp[:, 1].min(), y),
           max(allp[:, 0].max(), x + dx + width), max(allp[:, 1].max(), y + cap_height))
    return out, box


def text_width(text, cap_height, font='futural'):
    r = stroke_text(text, 0.0, 0.0, cap_height, font=font)
    if r is None or r[1] is None:
        return 0.0
    return r[1][2] - r[1][0]
