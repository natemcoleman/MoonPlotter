"""Image -> pen-plotter line art.

Modes
  'Outlines'  logos / clip-art: the image is reduced to a few flat colours; the edge of every colour region is
              traced into smooth closed outlines (one layer per colour, each with its own pen). Optional
              hatch fill inside the shapes and an offset "sticker cut line" around the whole design.
  'Edges'     photos: Canny-style edge detection (blur, gradient, thin, hysteresis) and the edge pixels are
              chained into polylines.

All output is in paper millimetres (y up, origin bottom-left of the sheet), as polylines.
"""
import numpy as np
import scipy.ndimage as ndi
from PIL import Image

MODES = ["Outlines (logos, clip-art)", "Edges (photos)"]
MAX_PX = 1400                    # working resolution (longest side)


# ---------------------------------------------------------------------------------------------- loading
def load_image(path, max_px=MAX_PX):
    """RGB float image in [0,1] with transparency flattened onto white, downscaled to max_px."""
    im = Image.open(path)
    im.load()
    if im.mode in ('P', 'LA', 'L') or 'transparency' in im.info:
        im = im.convert('RGBA')
    if im.mode == 'RGBA':
        bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert('RGB')
    s = max_px / max(im.size)
    if s < 1:
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255.0


# ---------------------------------------------------------------------------------------------- colours
def quantize(rgb, n, seed=3):
    """k-means colour reduction. Returns (labels HxW int, palette n x 3)."""
    h, w, _ = rgb.shape
    px = rgb.reshape(-1, 3)
    rng = np.random.default_rng(seed)
    sample = px[rng.choice(len(px), size=min(len(px), 40000), replace=False)]
    # k-means++ init
    cent = [sample[rng.integers(len(sample))]]
    for _ in range(1, n):
        d = np.min(((sample[:, None, :] - np.array(cent)[None]) ** 2).sum(-1), axis=1)
        cent.append(sample[rng.choice(len(sample), p=d / d.sum()) if d.sum() > 0 else rng.integers(len(sample))])
    cent = np.array(cent)
    for _ in range(25):
        lab = np.argmin(((sample[:, None, :] - cent[None]) ** 2).sum(-1), axis=1)
        new = np.array([sample[lab == k].mean(0) if np.any(lab == k) else cent[k] for k in range(n)])
        if np.allclose(new, cent, atol=1e-4):
            break
        cent = new
    labels = np.empty(len(px), dtype=np.int32)
    for i in range(0, len(px), 200000):
        labels[i:i + 200000] = np.argmin(((px[i:i + 200000, None, :] - cent[None]) ** 2).sum(-1), axis=1)
    return labels.reshape(h, w), cent


def background_index(labels):
    """Colour that dominates the image border (treated as the paper, not traced)."""
    border = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    return int(np.bincount(border).argmax())


def otsu(gray):
    hist, edges = np.histogram(gray, bins=256, range=(0, 1))
    p = hist / max(hist.sum(), 1)
    w = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    with np.errstate(divide='ignore', invalid='ignore'):
        sb = (mu[-1] * w - mu) ** 2 / (w * (1 - w))
    i = int(np.nanargmax(sb))
    return float(edges[i + 1])                                # bins 0..i are the dark class


# ---------------------------------------------------------------------------------------------- tracing
def _contours(field, level):
    import contourpy
    gen = contourpy.contour_generator(z=field, line_type=contourpy.LineType.Separate)
    return [c for c in gen.lines(level) if len(c) >= 4]


def _polygon_area(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _simplify(p, tol):
    """Ramer-Douglas-Peucker (iterative, keeps the endpoints)."""
    p = np.asarray(p, float)
    if tol <= 0 or len(p) < 4:
        return p
    keep = np.zeros(len(p), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(p) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = p[b] - p[a]
        L = np.hypot(*seg)
        q = p[a + 1:b] - p[a]
        d = np.abs(q[:, 0] * seg[1] - q[:, 1] * seg[0]) / L if L > 1e-12 else np.hypot(q[:, 0], q[:, 1])
        i = int(np.argmax(d))
        if d[i] > tol:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return p[keep]


def trace_mask(mask, px_mm, smooth_px=1.5, min_area_mm2=0.5, simplify_mm=0.05):
    """Closed outlines (in image-pixel coords x=col, y=row) around True regions of a mask."""
    f = mask.astype(np.float32)
    f = np.pad(f, 2)                                         # close shapes touching the image edge
    if smooth_px > 0:
        f = ndi.gaussian_filter(f, smooth_px)
    out = []
    for c in _contours(f, 0.5):
        c = c - 2.0                                          # undo the padding
        if _polygon_area(c) * px_mm ** 2 < min_area_mm2:
            continue
        out.append(_simplify(c, simplify_mm / px_mm))
    return out


def hatch_mask(mask, px_mm, spacing_mm, angle_deg=45.0, min_len_mm=0.8):
    """Parallel hatch lines inside the True pixels of a mask, image-pixel coords."""
    h, w = mask.shape
    sp = spacing_mm / px_mm
    a = np.radians(angle_deg)
    d = np.array([np.cos(a), np.sin(a)])
    n = np.array([-d[1], d[0]])
    c = np.array([w / 2.0, h / 2.0])
    R = 0.5 * np.hypot(w, h)
    step = 0.5                                               # sample every half pixel along each line
    ts = np.arange(-R, R, step)
    lines = []
    for k, off in enumerate(np.arange(-R, R, sp)):
        pts = c + n * off + ts[:, None] * d
        xi, yi = np.round(pts[:, 0]).astype(int), np.round(pts[:, 1]).astype(int)
        ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        on = np.zeros(len(ts), bool)
        on[ok] = mask[yi[ok], xi[ok]]
        if not on.any():
            continue
        dv = np.diff(np.concatenate([[0], on.astype(np.int8), [0]]))
        starts, ends = np.nonzero(dv == 1)[0], np.nonzero(dv == -1)[0] - 1
        for s0, e0 in zip(starts, ends):
            if (e0 - s0) * step * px_mm < min_len_mm:
                continue
            seg = np.array([pts[s0], pts[e0]])
            lines.append(seg if k % 2 == 0 else seg[::-1])  # alternate direction (less travel)
    return lines


# ---------------------------------------------------------------------------------------------- edges
def canny_edges(gray, sigma=2.0, low=0.08, high=0.2):
    g = ndi.gaussian_filter(gray, sigma)
    gx, gy = ndi.sobel(g, axis=1), ndi.sobel(g, axis=0)
    mag = np.hypot(gx, gy)
    mag /= (mag.max() or 1.0)
    ang = (np.rad2deg(np.arctan2(gy, gx)) + 180.0) % 180.0
    # non-maximum suppression along the gradient direction (4 sectors)
    nms = np.zeros_like(mag)
    pad = np.pad(mag, 1)
    H, W = mag.shape
    for lo_a, hi_a, (dy, dx) in ((0, 22.5, (0, 1)), (157.5, 180.01, (0, 1)), (22.5, 67.5, (1, 1)),
                                 (67.5, 112.5, (1, 0)), (112.5, 157.5, (1, -1))):
        sel = (ang >= lo_a) & (ang < hi_a)
        n1 = pad[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]
        n2 = pad[1 - dy:1 - dy + H, 1 - dx:1 - dx + W]
        keep = sel & (mag >= n1) & (mag >= n2)
        nms[keep] = mag[keep]
    strong = nms >= high
    weak = nms >= low
    lab, n = ndi.label(weak, structure=np.ones((3, 3)))
    keep_ids = np.unique(lab[strong])
    return np.isin(lab, keep_ids[keep_ids > 0])


_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def chain_pixels(edge, min_len_px=6):
    """Walks 1-pixel-wide edge pixels into polylines (x=col, y=row)."""
    pts = set(zip(*np.nonzero(edge)))
    deg = {}
    for (r, c) in pts:
        deg[(r, c)] = sum((r + dr, c + dc) in pts for dr, dc in _NB)
    lines = []
    visited = set()
    # start from endpoints first, then whatever is left (closed loops)
    order = [p for p in pts if deg[p] == 1] + [p for p in pts if deg[p] != 1]
    for start in order:
        if start in visited:
            continue
        path = [start]
        visited.add(start)
        cur = start
        while True:
            nxt = None
            for dr, dc in _NB:                               # prefer 4-neighbours, then diagonals
                q = (cur[0] + dr, cur[1] + dc)
                if q in pts and q not in visited:
                    if nxt is None or (abs(dr) + abs(dc) == 1):
                        nxt = q
                        if abs(dr) + abs(dc) == 1:
                            break
            if nxt is None:
                break
            path.append(nxt)
            visited.add(nxt)
            cur = nxt
        if len(path) >= min_len_px:
            a = np.array(path, dtype=float)
            lines.append(np.column_stack([a[:, 1], a[:, 0]]))
    return lines


# ---------------------------------------------------------------------------------------------- driver
def trace_image(rgb, opts, pw, ph, cache=None):
    """opts: mode, colours, invert, smooth_px, min_area_mm2, simplify_mm, size_pct, margin_mm,
    fill (bool), fill_spacing_mm, cutline (bool), cut_offset_mm, edge_sigma, edge_low, edge_high.
    Returns dict(layers=[{'key','label','color','polys'}], palette, bg_index, px_mm, box)."""
    h, w, _ = rgb.shape
    margin = float(opts.get('margin_mm', 8.0))
    scale = float(opts.get('size_pct', 100.0)) / 100.0
    px_mm = min((pw - 2 * margin) / w, (ph - 2 * margin) / h) * scale
    ox = (pw - w * px_mm) / 2.0
    oy = (ph - h * px_mm) / 2.0

    def to_mm(p):
        p = np.asarray(p, float)
        return np.column_stack([ox + p[:, 0] * px_mm, oy + (h - p[:, 1]) * px_mm])

    layers = []
    info = {'px_mm': px_mm, 'box': (ox, oy, ox + w * px_mm, oy + h * px_mm)}
    mode = opts.get('mode', MODES[0])
    smooth = float(opts.get('smooth_px', 1.5))
    min_area = float(opts.get('min_area_mm2', 0.5))
    simp = float(opts.get('simplify_mm', 0.05))

    if mode.startswith("Edges"):
        gray = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        edge = canny_edges(gray, float(opts.get('edge_sigma', 2.0)), float(opts.get('edge_low', 0.08)),
                           float(opts.get('edge_high', 0.2)))
        lines = chain_pixels(edge, min_len_px=max(3, int(1.5 / px_mm)))
        polys = [to_mm(_simplify(l, max(simp / px_mm, 0.6))) for l in lines]
        layers.append({'key': 'img:1', 'label': 'Edges', 'color': (0, 0, 0), 'polys': polys})
        info.update(palette=[(0, 0, 0)], bg_index=None, masks=[edge])
        return layers, info

    n = int(opts.get('colours', 2))
    if n <= 2:
        gray = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        t = otsu(gray)
        dark = gray < t
        if opts.get('invert'):
            dark = ~dark
        labels = np.where(dark, 1, 0)
        palette = np.array([[1, 1, 1], [0, 0, 0]], np.float32)
        if opts.get('invert'):
            palette = palette[::-1]
        bg = 0
    else:
        ck = ('q', id(rgb), n)
        if cache is not None and ck in cache:
            labels, palette = cache[ck]
        else:
            labels, palette = quantize(rgb, n)
            if cache is not None:
                cache.clear()
                cache[ck] = (labels, palette)
        bg = background_index(labels)
    masks = []
    for k in range(len(palette)):
        if k == bg:
            continue
        mask = labels == k
        if mask.sum() * px_mm ** 2 < min_area:
            continue
        polys = [to_mm(p) for p in trace_mask(mask, px_mm, smooth, min_area, simp)]
        if opts.get('fill'):
            m2 = ndi.gaussian_filter(mask.astype(np.float32), smooth) > 0.5 if smooth > 0 else mask
            polys += [to_mm(p) for p in hatch_mask(m2, px_mm, float(opts.get('fill_spacing_mm', 0.8)),
                                                   45.0 + 30.0 * len(layers))]
        if polys:
            layers.append({'key': f'img:{len(layers) + 1}', 'label': f'Colour {len(layers) + 1}',
                           'color': tuple(float(v) for v in palette[k]), 'polys': polys})
            masks.append(mask)
    if opts.get('cutline') and masks:
        sil = np.any(masks, axis=0)
        off_px = float(opts.get('cut_offset_mm', 2.0)) / px_mm
        pad = int(np.ceil(off_px)) + 4
        sil = np.pad(sil, pad)
        dist = ndi.distance_transform_edt(~sil)
        grown = ndi.binary_fill_holes(dist <= off_px)
        outer = trace_mask(grown, px_mm, smooth_px=max(1.0, 0.25 * off_px), min_area_mm2=min_area,
                           simplify_mm=simp)
        polys = [to_mm(p - pad) for p in outer]
        layers.append({'key': 'img:cut', 'label': 'Sticker cut line', 'color': (0.3, 0.3, 0.3), 'polys': polys})
    info.update(palette=[tuple(float(v) for v in c) for c in palette], bg_index=bg)
    return layers, info
