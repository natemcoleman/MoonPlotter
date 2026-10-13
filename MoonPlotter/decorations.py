"""Map furniture drawn as pen strokes: title block, scale bar, north arrow, graticule, neat-line frame.

Everything is laid out in paper millimetres, then converted to the preview's data coordinates with the
same frame maths as the hatch engine. Returns stroke dicts on layer 'decor' plus "mask" boxes (in data
coordinates) so map lines are blanked behind the title and scale bar.
"""
import numpy as np
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties

from config import PLANET_RADII

_FONT = FontProperties(family='DejaVu Sans')
_FONT_BOLD = FontProperties(family='DejaVu Sans', weight='bold')


def vector_text(text, x, y, height, anchor='left', bold=False):
    """Text outlines as polylines in paper mm. (x, y) is the baseline start (anchor='left'),
    centre ('center') or end ('right'). Returns (polylines, (xmin, ymin, xmax, ymax))."""
    if not text:
        return [], None
    tp = TextPath((0, 0), text, size=1.0, prop=_FONT_BOLD if bold else _FONT)
    ext = tp.get_extents()
    w = ext.x1 * height
    dx = {'left': 0.0, 'center': -w / 2.0, 'right': -w}[anchor]
    polys = [np.column_stack([x + dx + p[:, 0] * height, y + p[:, 1] * height])
             for p in tp.to_polygons() if len(p) >= 2]
    box = (x + dx + ext.x0 * height, y + ext.y0 * height, x + dx + ext.x1 * height, y + ext.y1 * height)
    return polys, box


def _nice(value, steps=(1, 2, 2.5, 5)):
    if value <= 0:
        return 1.0
    e = np.floor(np.log10(value))
    best = None
    for k in (e - 1, e, e + 1):
        for s in steps:
            c = s * 10 ** k
            if c <= value and (best is None or c > best):
                best = c
    return best or 10 ** e


class PaperFrame:
    """Paper mm <-> preview data coordinates (mirrors hatch_engine._Frame)."""

    def __init__(self, view_extent, pw, ph, circle, planet):
        self.pw, self.ph, self.circle = pw, ph, circle
        self.lon_min, self.lon_max, self.lat_min, self.lat_max = view_extent
        self.R = PLANET_RADII.get(planet, 6371000.0)
        self.mid_lon = 0.5 * (self.lon_min + self.lon_max)
        if circle:
            self.scale = min(pw, ph) / (2.1 * self.R)

    def to_data(self, pts):
        pts = np.asarray(pts, dtype=float)
        if self.circle:
            return np.column_stack([(pts[:, 0] - self.pw / 2) / self.scale, (pts[:, 1] - self.ph / 2) / self.scale])
        x = self.lon_min + pts[:, 0] / self.pw * (self.lon_max - self.lon_min)
        y = self.lat_min + pts[:, 1] / self.ph * (self.lat_max - self.lat_min)
        return np.column_stack([x, y])

    def lonlat_to_paper(self, lon, lat):
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        if self.circle:
            lam = np.radians(lon - self.mid_lon)
            phi = np.radians(lat)
            x = self.R * np.cos(phi) * np.sin(lam) * self.scale + self.pw / 2
            y = self.R * np.sin(phi) * self.scale + self.ph / 2
            visible = np.cos(phi) * np.cos(lam) >= 0
            return x, y, visible
        x = (lon - self.lon_min) / (self.lon_max - self.lon_min) * self.pw
        y = (lat - self.lat_min) / (self.lat_max - self.lat_min) * self.ph
        return x, y, np.ones_like(x, dtype=bool)

    def km_per_mm(self):
        if self.circle:
            return 1.0 / self.scale / 1000.0
        mid_lat = np.radians(0.5 * (self.lat_min + self.lat_max))
        deg_per_mm = (self.lon_max - self.lon_min) / self.pw
        return deg_per_mm * np.pi / 180.0 * self.R * np.cos(mid_lat) / 1000.0


def build_decorations(opts, view_extent, pw, ph, circle, planet, subtitle=""):
    """opts: dict(title, title_text, scale_bar, north_arrow, graticule ('Off'|'Edge ticks'|'Full lines'),
    frame, pen). Returns (segments, mask_boxes_in_data_coords)."""
    fr = PaperFrame(view_extent, pw, ph, circle, planet)
    pen = opts.get('pen', 'base')
    lines_mm, masks_mm = [], []
    m = 4.0                                            # margin from the paper edge, mm
    pad = 1.2

    def add(polys):
        lines_mm.extend(polys)

    # ---- neat-line frame
    if opts.get('frame'):
        f = 2.0
        lines_mm.append(np.array([[f, f], [pw - f, f], [pw - f, ph - f], [f, ph - f], [f, f]]))

    # ---- graticule
    grat = opts.get('graticule', 'Off')
    if grat != 'Off':
        if circle:
            step = 15.0
            for lon in np.arange(-180.0, 180.0, step):
                lat = np.linspace(-90, 90, 181)
                x, y, vis = fr.lonlat_to_paper(np.full_like(lat, lon), lat)
                _runs_to_lines(x, y, vis, lines_mm)
            for lat0 in np.arange(-75.0, 76.0, step):
                lon = np.linspace(fr.mid_lon - 180, fr.mid_lon + 180, 361)
                x, y, vis = fr.lonlat_to_paper(lon, np.full_like(lon, lat0))
                _runs_to_lines(x, y, vis, lines_mm)
        else:
            span = max(view_extent[1] - view_extent[0], view_extent[3] - view_extent[2])
            step = _nice(span / 4.0, steps=(1, 2, 2.5, 5))
            lon_ticks = np.arange(np.ceil(view_extent[0] / step) * step, view_extent[1] + 1e-9, step)
            lat_ticks = np.arange(np.ceil(max(view_extent[2], -90) / step) * step,
                                  min(view_extent[3], 90) + 1e-9, step)
            fmt = (lambda v: f"{v:g}") if step >= 1 else (lambda v: f"{v:.2f}".rstrip('0').rstrip('.'))
            tick = 2.5
            for lon in lon_ticks:
                x = (lon - view_extent[0]) / (view_extent[1] - view_extent[0]) * pw
                if grat == 'Full lines':
                    lines_mm.append(np.array([[x, 0.0], [x, ph]]))
                else:
                    lines_mm.append(np.array([[x, 0.0], [x, tick]]))
                    lines_mm.append(np.array([[x, ph - tick], [x, ph]]))
                lab = f"{fmt(abs(((lon + 180) % 360) - 180))}°{'E' if ((lon + 180) % 360) - 180 >= 0 else 'W'}"
                polys, box = vector_text(lab, x + 0.6, ph - tick - 2.0, 1.6)
                if box and box[2] < pw - m:
                    add(polys)
            for lat in lat_ticks:
                y = (lat - view_extent[2]) / (view_extent[3] - view_extent[2]) * ph
                if grat == 'Full lines':
                    lines_mm.append(np.array([[0.0, y], [pw, y]]))
                else:
                    lines_mm.append(np.array([[0.0, y], [tick, y]]))
                    lines_mm.append(np.array([[pw - tick, y], [pw, y]]))
                lab = f"{fmt(abs(lat))}°{'N' if lat >= 0 else 'S'}"
                polys, box = vector_text(lab, tick + 0.6, y + 0.6, 1.6)
                if box and box[3] < ph - m:
                    add(polys)

    # ---- title block (bottom left)
    if opts.get('title') and (opts.get('title_text') or subtitle):
        th = max(2.5, min(5.0, pw * 0.035))
        y0 = m
        polys2, box2 = vector_text(subtitle, m, y0, th * 0.5)
        y1 = (box2[3] + th * 0.45) if box2 else y0
        polys1, box1 = vector_text(opts.get('title_text', ''), m, y1, th, bold=True)
        add(polys1)
        add(polys2)
        boxes = [b for b in (box1, box2) if b]
        if boxes:
            masks_mm.append((min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
                             max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad))

    # ---- scale bar (bottom right)
    if opts.get('scale_bar'):
        kmpm = fr.km_per_mm()
        if np.isfinite(kmpm) and kmpm > 0:
            target_mm = pw * 0.25
            km = _nice(target_mm * kmpm)
            length = km / kmpm
            x1 = pw - m
            x0 = x1 - length
            y = m + 2.2
            h = 1.2
            lines_mm.append(np.array([[x0, y], [x1, y]]))
            for xx in (x0, (x0 + x1) / 2, x1):
                lines_mm.append(np.array([[xx, y], [xx, y + (h if xx != (x0 + x1) / 2 else h * 0.6)]]))
            label = f"{km:g} km" if km >= 1 else f"{km * 1000:g} m"
            polys, box = vector_text(label, (x0 + x1) / 2, y - 2.2, 1.7, anchor='center')
            add(polys)
            note = " (at centre)" if circle else ""
            if note:
                p2, b2 = vector_text(note.strip(), x1, y + h + 0.8, 1.3, anchor='right')
                add(p2)
                box = (min(box[0], b2[0]), box[1], max(box[2], b2[2]), b2[3]) if box else b2
            masks_mm.append((x0 - pad, (box[1] if box else y - 2.5) - pad, x1 + pad, y + h + (3.0 if note else 0.4) + pad))

    # ---- north arrow (top right; not meaningful for the globe view)
    if opts.get('north_arrow') and not circle:
        s = max(5.0, min(9.0, pw * 0.06))
        cx, top = pw - m - s * 0.35, ph - m
        base = top - s
        lines_mm.append(np.array([[cx - s * 0.3, base], [cx, top], [cx + s * 0.3, base], [cx, base + s * 0.28],
                                  [cx - s * 0.3, base]]))
        lines_mm.append(np.array([[cx, top], [cx, base + s * 0.28]]))
        polys, box = vector_text("N", cx, base - s * 0.42, s * 0.32, anchor='center', bold=True)
        add(polys)
        masks_mm.append((cx - s * 0.3 - pad, (box[1] if box else base - 3) - pad, cx + s * 0.3 + pad, top + pad))

    segments = [{'type': 'border', 'layer': 'decor', 'seg': fr.to_data(ln), 'level': 0, 'pen_id': pen}
                for ln in lines_mm if len(ln) >= 2]
    masks = []
    for (x0, y0, x1, y1) in masks_mm:
        d = fr.to_data(np.array([[x0, y0], [x1, y1]]))
        masks.append((min(d[0, 0], d[1, 0]), min(d[0, 1], d[1, 1]), max(d[0, 0], d[1, 0]), max(d[0, 1], d[1, 1])))
    return segments, masks


def _runs_to_lines(x, y, vis, out):
    idx = np.nonzero(vis)[0]
    if len(idx) < 2:
        return
    breaks = np.nonzero(np.diff(idx) > 1)[0] + 1
    for run in np.split(idx, breaks):
        if len(run) >= 2:
            out.append(np.column_stack([x[run], y[run]]))
