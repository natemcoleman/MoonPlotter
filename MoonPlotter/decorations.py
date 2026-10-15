"""Map furniture drawn as pen strokes: title block, scale bar, north arrow, graticule, neat-line frame.

Everything is laid out in paper millimetres, then converted to the preview's data coordinates with the
same frame maths as the hatch engine. Returns stroke dicts on layer 'decor' plus "mask" boxes (in data
coordinates) so map lines are blanked behind the title and scale bar.
"""
import numpy as np
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties

from config import PLANET_RADII
from stroke_font import stroke_text

_FONT = FontProperties(family='DejaVu Sans')
_FONT_BOLD = FontProperties(family='DejaVu Sans', weight='bold')


TEXT_SCALES = {'Small': 0.8, 'Medium': 1.0, 'Large': 1.3, 'Extra large': 1.7}
LABEL_CAP_MM = 1.9                 # Medium sizes on a 100 mm sheet (capital height)
SUB_CAP_MM = 1.9
TITLE_CAP_MM = 3.4


def min_cap_for_pen(line_mm):
    """Smallest capital height (mm) a pen of this line width can letter cleanly: the open parts of
    an 'e' or 'a' need several line widths of space or they fill in."""
    return max(1.5, 4.0 * float(line_mm or 0.5))


def vector_text(text, x, y, height, anchor='left', bold=False):
    """Plotter lettering as polylines in paper mm. 'height' is the CAPITAL height in mm; (x, y) is the
    baseline start (anchor='left'), centre ('center') or end ('right').
    Uses a single-stroke (Hershey) font when available, else outline text.
    Returns (polylines, (xmin, ymin, xmax, ymax))."""
    if not text:
        return [], None
    res = stroke_text(text, x, y, height, anchor=anchor)
    if res is not None:
        return res
    em = height / 0.73                                  # DejaVu capitals are ~0.73 em
    tp = TextPath((0, 0), text, size=1.0, prop=_FONT_BOLD if bold else _FONT)
    ext = tp.get_extents()
    height = em
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


def _wrap(text, cap, room, separators=("  \u00b7  ", " ")):
    """Splits text into lines that fit 'room' mm at this capital height (at ' · ' first, then spaces)."""
    if not text or room <= 0 or _text_w(text, cap) <= room:
        return [text] if text else []
    for sep in separators:
        parts = text.split(sep)
        if len(parts) < 2:
            continue
        lines, cur = [], parts[0]
        for part in parts[1:]:
            trial = cur + sep + part
            if _text_w(trial, cap) <= room:
                cur = trial
            else:
                lines.append(cur)
                cur = part
        lines.append(cur)
        if all(_text_w(ln, cap) <= room for ln in lines) or sep == separators[-1]:
            return [ln.strip() for ln in lines]
    return [text]


def _overlaps(a, b, gap=0.6):
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])


def build_decorations(opts, view_extent, pw, ph, circle, planet, subtitle=""):
    """opts: dict(title, title_text, scale_bar, north_arrow, graticule ('Off'|'Edge ticks'|'Full lines'),
    frame, pen, text_size, line_mm). Returns (segments, mask_boxes_in_data_coords)."""
    fr = PaperFrame(view_extent, pw, ph, circle, planet)
    pen = opts.get('pen', 'base')
    lines_mm, masks_mm = [], []
    m = 4.0                                            # margin from the paper edge, mm
    pad = 1.2
    # Text sizes (capital height, mm): grow a little with the sheet, scale with the user's choice, and
    # never go below what this pen can letter legibly.
    k = TEXT_SCALES.get(opts.get('text_size', 'Medium'), 1.0) * float(np.clip(min(pw, ph) / 100.0, 1.0, 2.0)) ** 0.5
    floor = min_cap_for_pen(opts.get('line_mm', 0.5))
    cap_label = max(floor, LABEL_CAP_MM * k)           # graticule / scale-bar labels
    cap_sub = max(floor, SUB_CAP_MM * k)
    cap_title = max(floor * 1.4, TITLE_CAP_MM * k)
    line_gap = 0.55                                    # line spacing as a fraction of cap height

    def add(polys):
        lines_mm.extend(polys)

    def bbox_of(boxes):
        boxes = [b for b in boxes if b]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

    # ---- neat-line frame
    if opts.get('frame'):
        f = 2.0
        lines_mm.append(np.array([[f, f], [pw - f, f], [pw - f, ph - f], [f, ph - f], [f, f]]))

    # ---- scale bar (bottom right) - laid out first so the title knows how much room is left
    scale_box = None
    scale_span = (0, 0)                                # range of lines_mm holding the scale bar
    scale_mask = None
    if opts.get('scale_bar'):
        first = len(lines_mm)
        kmpm = fr.km_per_mm()
        if np.isfinite(kmpm) and kmpm > 0:
            units = opts.get('scale_units', 'Miles')
            bars = {'Miles': ['mi'], 'Kilometres': ['km'], 'Miles + km': ['mi', 'km']}.get(units, ['mi'])
            x1 = pw - m
            h = max(1.2, 0.5 * cap_label)
            y = m + cap_label + 1.6
            boxes = []
            for unit in reversed(bars):                # build upward: the last bar sits at the bottom
                length, label = _scale_bar_for(unit, kmpm, pw * 0.22)
                x0 = x1 - length
                lines_mm.append(np.array([[x0, y], [x1, y]]))
                for xx in (x0, (x0 + x1) / 2, x1):
                    lines_mm.append(np.array([[xx, y], [xx, y + (h if xx != (x0 + x1) / 2 else h * 0.6)]]))
                polys, box = vector_text(label, (x0 + x1) / 2, y - cap_label - 1.4, cap_label, anchor='center')
                if box and box[0] < x0 - 0.5:          # wider than the bar: right-align under it
                    polys, box = vector_text(label, x1, y - cap_label - 1.4, cap_label, anchor='right')
                add(polys)
                boxes += [box, (x0, y, x1, y + h)]
                y += h + cap_label + 3.0
            if circle:
                p2, b2 = vector_text("(at centre)", x1, y - cap_label - 1.4, floor, anchor='right')
                add(p2)
                boxes.append(b2)
            scale_box = bbox_of(boxes)
            scale_span = (first, len(lines_mm))
            scale_mask = len(masks_mm)
            masks_mm.append((scale_box[0] - pad, scale_box[1] - pad, scale_box[2] + pad, scale_box[3] + pad))

    # ---- title block (bottom left): wraps instead of running into the scale bar
    if opts.get('title') and (opts.get('title_text') or subtitle):
        room = (scale_box[0] - 3.0 if scale_box else pw - m) - m
        title_text = opts.get('title_text', '')
        th = cap_title
        tw = _text_w(title_text, th)
        if tw > room > 0:
            th = max(floor * 1.2, th * room / tw)
        sub_lines = _wrap(subtitle, cap_sub, room)
        title_lines = _wrap(title_text, th, room, separators=(" ",))
        boxes = []
        y = m
        for ln in reversed(sub_lines):                 # build upward from the bottom margin
            polys, box = vector_text(ln, m, y, cap_sub)
            add(polys)
            boxes.append(box)
            y = (box[3] if box else y + cap_sub) + line_gap * cap_sub
        if sub_lines:
            y += 0.4 * th
        for ln in reversed(title_lines):
            polys, box = vector_text(ln, m, y, th, bold=True)
            add(polys)
            boxes.append(box)
            y = (box[3] if box else y + th) + line_gap * th
        tb = bbox_of(boxes)
        if tb:
            # one blanking box per text line, so a short title doesn't hide the map beside it
            for b in boxes:
                if b:
                    masks_mm.append((b[0] - pad, b[1] - pad * 0.6, b[2] + pad, b[3] + pad * 0.6))
            if scale_box and _overlaps(tb, scale_box, gap=1.0):
                # an unbreakable long word still reaches the scale bar: lift the bar above the title block
                dy = tb[3] + 3.0 * pad - scale_box[1]
                for i in range(*scale_span):
                    lines_mm[i] = lines_mm[i] + np.array([0.0, dy])
                x0, y0, x1, y1 = masks_mm[scale_mask]
                masks_mm[scale_mask] = (x0, y0 + dy, x1, y1 + dy)

    # ---- north arrow (top right; not meaningful for the globe view)
    if opts.get('north_arrow') and not circle:
        n_cap = max(floor, 2.4 * k)
        s = max(6.0, min(12.0, pw * 0.06 * k), n_cap * 2.2)
        cx, top = pw - m - s * 0.35, ph - m
        base = top - s
        lines_mm.append(np.array([[cx - s * 0.3, base], [cx, top], [cx + s * 0.3, base], [cx, base + s * 0.28],
                                  [cx - s * 0.3, base]]))
        lines_mm.append(np.array([[cx, top], [cx, base + s * 0.28]]))
        polys, box = vector_text("N", cx, base - n_cap - 1.0, n_cap, anchor='center', bold=True)
        add(polys)
        masks_mm.append((cx - s * 0.3 - pad, (box[1] if box else base - 3) - pad, cx + s * 0.3 + pad, top + pad))

    # ---- graticule (labels skip anything they would collide with)
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
            taken = list(masks_mm)
            edge = 2.5 if opts.get('frame') else 0.5

            def try_label(text, lx, ly, anchor='left'):
                polys, box = vector_text(text, lx, ly, cap_label, anchor=anchor)
                if not box or box[0] < edge or box[2] > pw - edge or box[1] < edge or box[3] > ph - edge:
                    return
                if any(_overlaps(box, t) for t in taken):
                    return
                add(polys)
                taken.append(box)

            for lon in lon_ticks:
                x = (lon - view_extent[0]) / (view_extent[1] - view_extent[0]) * pw
                if grat == 'Full lines':
                    lines_mm.append(np.array([[x, 0.0], [x, ph]]))
                else:
                    lines_mm.append(np.array([[x, 0.0], [x, tick]]))
                    lines_mm.append(np.array([[x, ph - tick], [x, ph]]))
                w = ((lon + 180) % 360) - 180
                try_label(f"{fmt(abs(w))}\u00b0{'E' if w >= 0 else 'W'}", x + 0.8, ph - tick - cap_label - 0.8)
            for lat in lat_ticks:
                y = (lat - view_extent[2]) / (view_extent[3] - view_extent[2]) * ph
                if grat == 'Full lines':
                    lines_mm.append(np.array([[0.0, y], [pw, y]]))
                else:
                    lines_mm.append(np.array([[0.0, y], [tick, y]]))
                    lines_mm.append(np.array([[pw - tick, y], [pw, y]]))
                try_label(f"{fmt(abs(lat))}\u00b0{'N' if lat >= 0 else 'S'}", tick + 0.8, y + 0.8)

    segments = [{'type': 'border', 'layer': 'decor', 'seg': fr.to_data(ln), 'level': 0, 'pen_id': pen}
                for ln in lines_mm if len(ln) >= 2]
    masks = []
    for (x0, y0, x1, y1) in masks_mm:
        d = fr.to_data(np.array([[x0, y0], [x1, y1]]))
        masks.append((min(d[0, 0], d[1, 0]), min(d[0, 1], d[1, 1]), max(d[0, 0], d[1, 0]), max(d[0, 1], d[1, 1])))
    return segments, masks


MI_KM = 1.609344


def _scale_bar_for(unit, km_per_mm, target_mm):
    """(bar length mm, label) for a round distance close to target_mm long, in 'mi' or 'km'.
    Short miles bars switch to feet, short km bars to metres."""
    if unit == 'mi':
        mi_per_mm = km_per_mm / MI_KM
        mi = _nice(target_mm * mi_per_mm, steps=(1, 2, 2.5, 5))
        if mi >= 0.25:
            return mi / mi_per_mm, (f"{mi:g} mile" if mi == 1 else f"{mi:g} miles")
        ft = _nice(target_mm * mi_per_mm * 5280.0)
        return ft / 5280.0 / mi_per_mm, f"{ft:,.0f} ft"
    km = _nice(target_mm * km_per_mm)
    if km >= 1:
        return km / km_per_mm, f"{km:g} km"
    return km / km_per_mm, f"{km * 1000:g} m"


def _text_w(text, cap):
    _, box = vector_text(text, 0.0, 0.0, cap)
    return (box[2] - box[0]) if box else 0.0


def _runs_to_lines(x, y, vis, out):
    idx = np.nonzero(vis)[0]
    if len(idx) < 2:
        return
    breaks = np.nonzero(np.diff(idx) > 1)[0] + 1
    for run in np.split(idx, breaks):
        if len(run) >= 2:
            out.append(np.column_stack([x[run], y[run]]))
