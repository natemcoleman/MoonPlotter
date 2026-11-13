"""Layered cut-outs: a topographic map built from stacked cardstock.

The elevation is split into N height bands. Layer 1 (the bottom) is the whole map rectangle; layer k is
everything at or above the k-th level, so stacked and glued they form the terrain. Lakes and other
depressions become holes, so a lake bed (Lake Tahoe...) reads as a stepped bowl.

Every piece of every layer is cut from small sheets (e.g. 150 x 150 mm) with the drag knife. Before
cutting, a pen draws on each piece the outline of the layer that goes on top of it (slightly inside it,
so the glued piece hides the line) and the piece's layer number; the top layer gets nothing drawn on
it (its number goes on the waste next to it). Pieces are rotated and packed to waste little card.

Everything here works in paper millimetres (origin bottom-left, y up) and has no Qt: it is unit-testable.
"""
import math

import numpy as np

from decorations import vector_text

SIMPLIFY_MM = 0.03          # drops redundant vertices (far below what pen or knife can show)
SHEET_MARGIN_MM = 3.0       # strip round the sheet edge that is never cut (clips, edge wear)
PIECE_GAP_MM = 4.0          # card left between two pieces: room for the knife's corner loops
ALIGN_INSET_MM = 0.6        # alignment outline is drawn this far inside the piece that covers it
LABEL_CAP_MM = 2.5          # height of the layer numbers
GRID_MM = 1.0               # packing grid
ROTATIONS = (0, 90, 180, 270, 45, 135, 225, 315)


# ---------------------------------------------------------------------------------------------
# Elevation -> layer polygons
# ---------------------------------------------------------------------------------------------
def layer_levels(zmin, zmax, n_layers):
    """Heights where layers 2..N start (layer 1 is the base): N-1 evenly spaced levels."""
    n = max(2, int(n_layers))
    return [zmin + (zmax - zmin) * k / n for k in range(1, n)]


def _rings_to_geom(rings):
    """Closed rings from contourf -> polygons with holes (even-odd nesting)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    polys = []
    for r in rings:
        if len(r) >= 4:
            p = Polygon(r)
            if not p.is_valid:
                p = p.buffer(0)
            if p.area > 1e-6:
                polys.append(p)
    if not polys:
        return Polygon()
    polys.sort(key=lambda p: -p.area)
    depth = []
    for i, p in enumerate(polys):
        pt = p.representative_point()
        depth.append(sum(1 for j in range(i) if polys[j].contains(pt)))
    filled = unary_union([p for p, d in zip(polys, depth) if d % 2 == 0])
    holes = unary_union([p for p, d in zip(polys, depth) if d % 2 == 1]) if any(d % 2 for d in depth) else None
    return filled.difference(holes) if holes is not None else filled


def _clean(geom, min_feature_mm):
    """Drops parts narrower / smaller than min_feature_mm and fills narrower gaps and small holes."""
    from shapely.geometry import Polygon, MultiPolygon
    if geom.is_empty:
        return geom
    r = max(0.05, 0.5 * float(min_feature_mm))
    g = geom.buffer(-r, quad_segs=8).buffer(r, quad_segs=8)          # opening: thin necks, specks
    g = g.buffer(r, quad_segs=8).buffer(-r, quad_segs=8)             # closing: thin gaps, small holes
    min_area = 2.0 * float(min_feature_mm) ** 2
    out = []
    for p in getattr(g, 'geoms', [g]):
        if p.geom_type != 'Polygon' or p.area < min_area:
            continue
        holes = [h for h in p.interiors if Polygon(h).area >= min_area]
        out.append(Polygon(p.exterior, holes))
    g = MultiPolygon(out) if out else Polygon()
    return g.simplify(SIMPLIFY_MM, preserve_topology=True) if not g.is_empty else g


def build_layers(dem_ft, pw, ph, n_layers, min_feature_mm=2.5, smooth_mm=None):
    """dem_ft: elevation grid (row 0 = top of the map) covering the whole page pw x ph.
    NaN (outside a lake's shoreline, missing data) counts as 'above every level', so it stays on every
    layer. Returns {'levels': [...], 'layers': [geometry per layer, bottom first], 'zmin', 'zmax'}."""
    import scipy.ndimage
    from matplotlib.figure import Figure
    from shapely.geometry import box
    z = np.asarray(dem_ft, dtype=float)
    finite = np.isfinite(z)
    if not finite.any():
        raise ValueError("No elevation data in the selection.")
    zmin, zmax = float(np.nanmin(z)), float(np.nanmax(z))
    if zmax - zmin <= 0:
        raise ValueError("The selection is flat - nothing to stack.")
    big = zmax + 10.0 * (zmax - zmin) + 1.0
    z = np.where(finite, z, big)
    rows, cols = z.shape
    px_x, px_y = cols / pw, rows / ph                                # pixels per mm
    sm = (0.3 * min_feature_mm) if smooth_mm is None else smooth_mm
    if sm > 0:
        z = scipy.ndimage.gaussian_filter(z, (sm * px_y, sm * px_x), mode='nearest')
    # pad beyond the page so smoothing / clean-up never rounds the corners at the map edge
    pad = int(math.ceil(2.5 * min_feature_mm * max(px_x, px_y))) + 2
    z = np.pad(z, pad, mode='edge')
    xs = (np.arange(z.shape[1]) - pad + 0.5) / px_x
    ys = ph - (np.arange(z.shape[0]) - pad + 0.5) / px_y
    levels = layer_levels(zmin, zmax, n_layers)
    fig = Figure()
    ax = fig.add_subplot(111)
    page = box(0.0, 0.0, pw, ph)
    raw = []
    for L in levels:
        cs = ax.contourf(xs, ys, z, levels=[L, big * 2.0])
        rings = []
        for path in cs.get_paths():
            rings += [r for r in path.to_polygons(closed_only=True) if len(r) >= 4]
        raw.append(_rings_to_geom(rings))
    layers = [page]
    below = box(-pad / px_x, -pad / px_y, pw + pad / px_x, ph + pad / px_y)
    for g in raw:
        g = _clean(g.intersection(below), min_feature_mm)             # each layer rests on the one below
        layers.append(g)
        below = g if not g.is_empty else below
    layers = [layers[0]] + [_clean_clip(g, page, min_feature_mm) for g in layers[1:]]
    while len(layers) > 1 and layers[-1].is_empty:                    # nothing left that high
        layers.pop()
    return {'levels': levels[:len(layers) - 1], 'layers': layers, 'zmin': zmin, 'zmax': zmax}


def _clean_clip(g, page, min_feature_mm):
    from shapely.geometry import MultiPolygon, Polygon
    if g.is_empty:
        return g
    g = g.intersection(page)
    min_area = 2.0 * float(min_feature_mm) ** 2
    parts = [p for p in getattr(g, 'geoms', [g]) if p.geom_type == 'Polygon' and p.area >= min_area]
    return MultiPolygon(parts) if parts else Polygon()


def pieces(layer_set):
    """[(layer_index, polygon)] - every separate piece to cut, bottom layer first."""
    out = []
    for i, g in enumerate(layer_set['layers']):
        for p in getattr(g, 'geoms', [g]):
            if not p.is_empty:
                out.append((i, p))
    return out


# ---------------------------------------------------------------------------------------------
# Pen marks drawn on each piece
# ---------------------------------------------------------------------------------------------
def _boundary_lines(geom):
    lines = []
    for p in getattr(geom, 'geoms', [geom]):
        if p.is_empty or p.geom_type != 'Polygon':
            continue
        lines.append(np.asarray(p.exterior.coords))
        lines += [np.asarray(h.coords) for h in p.interiors]
    return lines


def piece_marks(layer_set, idx, poly, cap=LABEL_CAP_MM):
    """Marks for one piece (piece coordinates): (alignment lines, label lines, label_inside).
    label_inside False: the number has to go on the waste next to the piece (nothing will cover it)."""
    try:
        from shapely.ops import polylabel
    except ImportError:                       # pragma: no cover
        polylabel = None
    layers = layer_set['layers']
    text = str(idx + 1)
    if idx + 1 >= len(layers):                # top layer: nothing drawn on it
        return [], [], False
    upper = layers[idx + 1].intersection(poly)
    if upper.is_empty:
        return [], [], False
    align = _boundary_lines(upper.buffer(-ALIGN_INSET_MM, quad_segs=8).simplify(SIMPLIFY_MM))
    # the number goes where the next layer will cover it
    room = upper.buffer(-(0.9 * cap + 0.8))
    labels, inside = [], False
    if not room.is_empty:
        biggest = max(getattr(room, 'geoms', [room]), key=lambda g: g.area)
        try:
            c = polylabel(biggest, tolerance=0.2) if polylabel else biggest.representative_point()
        except Exception:
            c = biggest.representative_point()
        polys, tb = vector_text(text, c.x, c.y - cap / 2.0, cap, anchor='center')
        from shapely.geometry import box as _box
        if tb and upper.buffer(-0.4).contains(_box(*tb)):
            labels, inside = polys, True
    return align, labels, inside


# ---------------------------------------------------------------------------------------------
# Packing pieces onto sheets
# ---------------------------------------------------------------------------------------------
def _raster(geom, ox, oy, w, h, g):
    """Boolean occupancy mask (rows = y up) of geom on a grid of g mm starting at (ox, oy)."""
    from matplotlib.path import Path
    nx, ny = int(math.ceil(w / g)), int(math.ceil(h / g))
    xs = ox + (np.arange(nx) + 0.5) * g
    ys = oy + (np.arange(ny) + 0.5) * g
    X, Y = np.meshgrid(xs, ys)
    pts = np.column_stack([X.ravel(), Y.ravel()])
    m = np.zeros(nx * ny, bool)
    for p in getattr(geom, 'geoms', [geom]):
        if p.is_empty or p.geom_type != 'Polygon':
            continue
        inside = Path(np.asarray(p.exterior.coords)).contains_points(pts)
        for h in p.interiors:                    # holes are free: small pieces can be nested in them
            inside &= ~Path(np.asarray(h.coords)).contains_points(pts)
        m |= inside
    return m.reshape(ny, nx)


def pack(items, sheet_w, sheet_h, margin=SHEET_MARGIN_MM, gap=PIECE_GAP_MM, grid=GRID_MM, rotations=ROTATIONS,
         group_of=None, progress=lambda m: None):
    """items: [(key, polygon, extra)] where extra is an optional geometry (e.g. an outside label box) that
    must also be free of other pieces. Returns (placements, too_big) where placements is
    [{'key', 'sheet', 'angle', 'dx', 'dy', 'centre'}]: point p of the piece goes to
    rotate(p - centre, angle) + (dx, dy) on the sheet. group_of(key) -> pieces of different groups never
    share a sheet (e.g. one card colour per group)."""
    from scipy.signal import fftconvolve
    from shapely import affinity
    from shapely.ops import unary_union
    W, H = sheet_w - 2 * margin, sheet_h - 2 * margin
    gw, gh = int(math.floor(W / grid)), int(math.floor(H / grid))
    sheets = []          # {'occ': mask, 'group': g}
    placements, too_big = [], []
    order = sorted(items, key=lambda it: -(it[1].convex_hull.area))
    for n_done, (key, poly, extra) in enumerate(order):
        progress(f"Arranging pieces on sheets... {n_done + 1}/{len(order)}")
        grp = group_of(key) if group_of else 0
        shape = unary_union([poly] + ([extra] if extra is not None else []))
        c = poly.centroid
        cands = []
        big = poly.convex_hull.area > 0.15 * W * H
        for ang in (rotations[:4] if big else rotations):
            r = affinity.rotate(shape, ang, origin=c)
            # half the gap each side, plus the grid's worst-case rounding
            foot = r.buffer(gap / 2.0 + 0.36 * grid, quad_segs=4)
            x0, y0, x1, y1 = foot.bounds
            if x1 - x0 > W + 1e-6 or y1 - y0 > H + 1e-6:
                continue
            f = _raster(foot, x0, y0, x1 - x0, y1 - y0, grid)
            cands.append((ang, f, x0, y0))
        if not cands:
            too_big.append(key)
            continue
        best = None
        for si, sh in enumerate(sheets + [None]):
            if sh is not None and sh['group'] != grp:
                continue
            occ = sh['occ'] if sh is not None else np.zeros((gh, gw), bool)
            for ang, f, fx0, fy0 in cands:
                fh, fw = f.shape
                if fh > gh or fw > gw:
                    continue
                if occ.any():
                    hits = fftconvolve(occ.astype(np.float32), f[::-1, ::-1].astype(np.float32), mode='valid')
                    free = hits < 0.5
                else:
                    free = np.ones((gh - fh + 1, gw - fw + 1), bool)
                if not free.any():
                    continue
                iy, ix = np.nonzero(free)
                score = (iy + fh) * (gw + 1) + ix              # lowest top edge, then leftmost
                k = int(np.argmin(score))
                s = (int(score[k]), si)
                if best is None or s < best[0]:
                    best = (s, si, ang, f, fx0, fy0, int(iy[k]), int(ix[k]))
            if best is not None and best[1] == si:
                break                                         # first sheet that takes it
        if best is None:
            too_big.append(key)
            continue
        _, si, ang, f, fx0, fy0, iy, ix = best
        if si == len(sheets):
            sheets.append({'occ': np.zeros((gh, gw), bool), 'group': grp})
        fh, fw = f.shape
        sheets[si]['occ'][iy:iy + fh, ix:ix + fw] |= f
        # footprint min corner (fx0, fy0) lands on grid cell (ix, iy)
        dx = margin + ix * grid - fx0
        dy = margin + iy * grid - fy0
        placements.append({'key': key, 'sheet': si, 'angle': ang, 'dx': dx, 'dy': dy, 'centre': (c.x, c.y)})
    return placements, too_big, len(sheets)


def place_points(pts, pl):
    """Piece coordinates -> sheet coordinates for one placement."""
    pts = np.asarray(pts, float)
    a = math.radians(pl['angle'])
    ca, sa = math.cos(a), math.sin(a)
    cx, cy = pl['centre']
    x, y = pts[:, 0] - cx, pts[:, 1] - cy
    return np.column_stack([cx + x * ca - y * sa + pl['dx'], cy + x * sa + y * ca + pl['dy']])


def place_geom(geom, pl):
    from shapely import affinity
    g = affinity.rotate(geom, pl['angle'], origin=pl['centre'])
    return affinity.translate(g, pl['dx'], pl['dy'])


# ---------------------------------------------------------------------------------------------
# Knife paths
# ---------------------------------------------------------------------------------------------
def _ring_cut(coords, outer, loop_mm, overlap_mm, sharp_deg=40.0, arc_steps=18):
    """Knife path round one ring. Outer rings (anticlockwise) get a 270-ish degree loop outside every
    sharp convex corner, so the trailing blade turns before it cuts the next edge; the cut starts in the
    middle of the longest edge and runs overlap_mm past the start."""
    P = np.asarray(coords, float)
    if np.allclose(P[0], P[-1]):
        P = P[:-1]
    n = len(P)
    if n < 3:
        return P
    seg = np.roll(P, -1, axis=0) - P
    k0 = int(np.argmax(np.hypot(seg[:, 0], seg[:, 1])))
    start = P[k0] + 0.5 * seg[k0]
    P = np.vstack([P[k0 + 1:], P[:k0 + 1]])                       # walk from the vertex after the start
    out = [start]
    d = float(loop_mm)
    m = len(P)
    for i in range(m):
        C = P[i]
        prev = out[-1]
        nxt = P[(i + 1) % m] if i + 1 < m else start
        a = C - prev
        b = nxt - C
        la, lb = np.hypot(*a), np.hypot(*b)
        if outer and d > 0 and la > 1e-9 and lb > 1e-9:
            a, b = a / la, b / lb
            turn = math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]))
            if turn > sharp_deg:                                     # convex (left) turn on a CCW ring
                q = C + d * (a - b)
                th0 = math.atan2((C + d * a - q)[1], (C + d * a - q)[0])
                th1 = math.atan2((C - d * b - q)[1], (C - d * b - q)[0])
                sweep = (th0 - th1) % (2 * math.pi)                  # clockwise, the long way round
                out.append(C + d * a)
                for s in range(1, arc_steps + 1):
                    th = th0 - sweep * s / arc_steps
                    out.append(q + d * np.array([math.cos(th), math.sin(th)]))
                out.append(C.copy())
                continue
        out.append(C)
    out.append(start)
    # overlap: carry on along the ring past the start
    rest, left = [], float(overlap_mm)
    cur = start
    for p in list(P):
        step = np.hypot(*(p - cur))
        if step >= left:
            rest.append(cur + (p - cur) * (left / step) if step > 0 else p)
            break
        rest.append(p)
        left -= step
        cur = p
    return np.array(out + rest)


def cut_paths(poly, loop_mm, overlap_mm):
    """(hole paths, outer path) for one placed piece: holes are cut first so the piece stays put."""
    from shapely.geometry.polygon import orient
    p = orient(poly.simplify(0.05, preserve_topology=True), sign=1.0)                                    # exterior anticlockwise, holes clockwise
    holes = [_ring_cut(h.coords, False, 0.0, min(overlap_mm, 0.25 * h.length)) for h in p.interiors]
    outer = _ring_cut(p.exterior.coords, True, loop_mm, min(overlap_mm, 0.25 * p.exterior.length))
    return holes, outer


def order_nearest(paths, start=(0.0, 0.0)):
    """Greedy nearest-neighbour order (paths may be reversed only if closed=False; here kept as is)."""
    left = list(paths)
    out = []
    cur = np.asarray(start, float)
    while left:
        k = min(range(len(left)), key=lambda i: np.hypot(*(np.asarray(left[i][0]) - cur)))
        p = left.pop(k)
        out.append(p)
        cur = np.asarray(p[-1])
    return out


def order_pen_lines(lines, start=(0.0, 0.0)):
    """Nearest-neighbour order for pen strokes; open strokes may be drawn either way round."""
    left = [np.asarray(l, float) for l in lines if len(l) >= 2]
    out = []
    cur = np.asarray(start, float)
    while left:
        best, k, rev = None, 0, False
        for i, l in enumerate(left):
            for r, end in ((False, l[0]), (True, l[-1])):
                dd = np.hypot(*(end - cur))
                if best is None or dd < best:
                    best, k, rev = dd, i, r
        l = left.pop(k)
        l = l[::-1] if rev else l
        out.append(l)
        cur = l[-1]
    return out


def as_moves(lines):
    """Polylines -> vpype-style 'G0 X.. Y..' / 'G1 X.. Y..' lines (exporter.wrap_vpype_body input)."""
    out = []
    for l in lines:
        out.append(f"G0 X{l[0][0]:.3f} Y{l[0][1]:.3f}")
        out += [f"G1 X{x:.3f} Y{y:.3f}" for x, y in l[1:]]
    return out


# ---------------------------------------------------------------------------------------------
# Whole job
# ---------------------------------------------------------------------------------------------
def plan_sheets(layer_set, sheet_w, sheet_h, base_marks=(), colour_groups=0, progress=lambda m: None):
    """Packs every piece onto sheets and works out what is drawn and cut on each.

    base_marks: extra pen lines (piece coordinates) for the bottom layer - the map furniture.
    colour_groups: 0 = one card colour for all; k > 0 = a new card colour every k layers (pieces of
    different colours never share a sheet).
    Returns {'sheets': [{'marks': [...], 'cuts': [knife paths in cutting order],
    'group': g, 'pieces': [(layer, placed polygon)]}], 'too_big': [layer numbers], 'n_pieces'}."""
    from shapely.geometry import box
    items, info = [], {}
    for key, (li, poly) in enumerate(pieces(layer_set)):
        align, labels, inside = piece_marks(layer_set, li, poly)
        marks = list(align) + list(labels)
        if li == 0:
            marks += list(base_marks)
        extra = None
        ext_label = None
        if not inside:
            # number on the waste, just below the piece (packed with it, so there is room for it)
            x0, y0, x1, y1 = poly.bounds
            polys, tb = vector_text(str(li + 1), (x0 + x1) / 2.0, y0 - LABEL_CAP_MM - 1.5, LABEL_CAP_MM,
                                    anchor='center')
            if tb:
                extra = box(tb[0] - 0.3, tb[1] - 0.3, tb[2] + 0.3, tb[3] + 0.3)
                ext_label = polys
        info[key] = (li, poly, marks, ext_label)
        items.append((key, poly, extra))
    grp = (lambda k: info[k][0] // colour_groups) if colour_groups and colour_groups > 0 else None
    placements, too_big, n_sheets = pack(items, sheet_w, sheet_h, group_of=grp, progress=progress)
    sheets = [{'marks': [], 'cuts': [], 'pieces': [], 'group': 0} for _ in range(n_sheets)]
    from config import CUTTER_CORNER_LOOP_MM, CUTTER_BLADE_OFFSET_MM, CUTOUT_OVERLAP_MM
    loop = max(float(CUTTER_CORNER_LOOP_MM), 3.0 * float(CUTTER_BLADE_OFFSET_MM))
    for pl in placements:
        li, poly, marks, ext_label = info[pl['key']]
        sh = sheets[pl['sheet']]
        sh['group'] = (li // colour_groups) if colour_groups else 0
        placed = place_geom(poly, pl)
        sh['pieces'].append((li, placed))
        sh['marks'] += [place_points(m, pl) for m in marks if len(m) >= 2]
        if ext_label:
            sh['marks'] += [place_points(m, pl) for m in ext_label if len(m) >= 2]
        holes, outer = cut_paths(placed, loop, CUTOUT_OVERLAP_MM)
        sh['cuts'].append((placed.area, order_nearest(holes, outer[0]) + [outer]))
    for sh in sheets:
        # smallest piece first, each piece's holes before its outline: a piece nested in another's hole
        # is always cut before that hole drops out, and the sheet stays whole as long as possible
        sh['cuts'] = [path for _, paths in sorted(sh['cuts'], key=lambda t: t[0]) for path in paths]
        sh['marks'] = order_pen_lines(sh['marks'])
    return {'sheets': sheets, 'too_big': sorted({info[k][0] + 1 for k in too_big}),
            'n_pieces': len(items)}
