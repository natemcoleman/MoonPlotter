"""Raster -> vector hatching for pen plotters.

All hatch geometry is generated in *paper millimetres*, so line spacing, pen
width and minimum stroke length mean the same thing on every map scale and in
both flat and circle (orthographic) modes. Points are converted to the preview's
data coordinates (lon/lat degrees, or orthographic metres) only at the end.

Tone pipeline:
  1. sRGB -> linear light, optional auto-contrast stretch.
  2. Colour separation: every ink is modelled as a transparent filter whose
     transmittance is the pen's preview colour. With independent hatch layers
     (Demichel overlap) the printed colour is
        paper * prod_i(1 - c_i * (1 - ink_i))
     where c_i is the area coverage of ink i. A lookup table of that model is
     searched (nearest in CIELAB) for every pixel colour. Monochrome is the
     same model with one black ink (solved analytically).
  3. Each ink's density map (0..1 of that pattern's max coverage) drives one of
     three hatch patterns.
"""
import numpy as np
import scipy.ndimage
from scipy.spatial import cKDTree

from config import PLANET_RADII, MIN_SEGMENT_MM, INK_SETS, DEFAULT_INK_SET, MONO_INK_SET, \
    DEFAULT_HATCH_SPACING_MM

DEG2RAD = np.pi / 180.0
SAMPLE_STEP_MM = 0.2          # sampling step along straight hatch lines
GAP_CLOSE_MM = 0.6            # bridge pen-up gaps shorter than this
MAX_COVERAGE = 0.95           # never try to lay down more ink than this per pen
REG_WEIGHT = 2.0              # Lab units per full ink layer (prefers less ink on ties)


# ------------------------------------------------------------------
# Colour helpers
# ------------------------------------------------------------------
_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                     [0.2126729, 0.7151522, 0.0721750],
                     [0.0193339, 0.1191920, 0.9503041]])
_WHITE_XYZ = _RGB2XYZ.sum(axis=1)


def hex_to_srgb(h):
    h = h.lstrip('#')
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64) / 255.0


def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_lab(rgb):
    xyz = (rgb @ _RGB2XYZ.T) / _WHITE_XYZ
    eps, kappa = 216.0 / 24389.0, 24389.0 / 27.0
    f = np.where(xyz > eps, np.cbrt(np.maximum(xyz, 0.0)), (kappa * xyz + 16.0) / 116.0)
    return np.stack([116.0 * f[..., 1] - 16.0,
                     500.0 * (f[..., 0] - f[..., 1]),
                     200.0 * (f[..., 1] - f[..., 2])], axis=-1)


def luminance(lin):
    return lin @ _RGB2XYZ[1]


# ------------------------------------------------------------------
# Pattern coverage models
# ------------------------------------------------------------------
def _squiggle_params(spacing):
    line_spacing = 2.0 * spacing          # squiggle centre lines are further apart...
    amp_max = 0.45 * line_spacing         # ...so the wiggles never touch neighbours
    wavelength = 1.5 * spacing
    return line_spacing, amp_max, wavelength


def _squiggle_table(line_w, spacing):
    """Coverage as a function of amplitude for a sine squiggle (arc-length model)."""
    line_spacing, amp_max, wavelength = _squiggle_params(spacing)
    amps = np.linspace(0.0, amp_max, 256)
    phi = np.linspace(0.0, 2.0 * np.pi, 256, endpoint=False)
    slope = 2.0 * np.pi * amps[:, None] / wavelength * np.cos(phi)[None, :]
    arc = np.sqrt(1.0 + slope ** 2).mean(axis=1)
    cov = np.minimum(1.0, line_w * arc / line_spacing)
    cov = np.maximum.accumulate(cov)
    keep = cov <= MAX_COVERAGE
    keep[0] = True
    return amps[keep], cov[keep]


def pattern_max_coverage(strategy, line_w, spacing, n_passes):
    k = min(1.0, line_w / spacing)
    if "Squiggle" in strategy:
        return float(_squiggle_table(line_w, spacing)[1][-1])
    if "Variable" in strategy:
        return min(MAX_COVERAGE, k)
    return min(MAX_COVERAGE, 1.0 - (1.0 - k) ** n_passes)


# ------------------------------------------------------------------
# Paper frame: paper mm <-> data coords <-> lon/lat
# ------------------------------------------------------------------
class _Frame:
    def __init__(self, view_extent, params):
        self.pw = float(params.get('paper_w_mm', 100.0))
        self.ph = float(params.get('paper_h_mm', 100.0))
        self.circle = bool(params.get('circle_mode', False))
        self.lon_min, self.lon_max, self.lat_min, self.lat_max = view_extent
        self.mid_lon = 0.5 * (self.lon_min + self.lon_max)
        if self.circle:
            self.R = PLANET_RADII.get(params.get('active_planet', 'Earth'), 6371000.0)
            # Export axes: xlim/ylim = +-1.05R with equal aspect -> square box of side min(pw, ph), centred.
            self.scale = min(self.pw, self.ph) / (2.1 * self.R)      # mm per metre
            self.cx, self.cy = self.pw / 2.0, self.ph / 2.0
            self.r_mm = self.R * self.scale

    def inside(self, U, V):
        if self.circle:
            return (U - self.cx) ** 2 + (V - self.cy) ** 2 <= self.r_mm ** 2
        return (U >= 0.0) & (U <= self.pw) & (V >= 0.0) & (V <= self.ph)

    def to_data(self, U, V):
        if self.circle:
            return (U - self.cx) / self.scale, (V - self.cy) / self.scale
        X = self.lon_min + U / self.pw * (self.lon_max - self.lon_min)
        Y = self.lat_min + V / self.ph * (self.lat_max - self.lat_min)
        return X, Y

    def to_lonlat(self, U, V):
        X, Y = self.to_data(U, V)
        if not self.circle:
            return X, Y
        r2 = (X * X + Y * Y) / (self.R * self.R)
        z = np.sqrt(np.clip(1.0 - r2, 0.0, 1.0))
        lat = np.degrees(np.arcsin(np.clip(Y / self.R, -1.0, 1.0)))
        lon = self.mid_lon + np.degrees(np.arctan2(X / self.R, z))
        return lon, lat

    def clamp(self, U, V):
        if self.circle:
            return U, V
        return np.clip(U, 0.0, self.pw), np.clip(V, 0.0, self.ph)

    def mm_per_deg(self):
        if self.circle:
            return self.R * DEG2RAD * self.scale
        return self.pw / (self.lon_max - self.lon_min)


# ------------------------------------------------------------------
# Raster sampling
# ------------------------------------------------------------------
class _Raster:
    """Nearest-pixel lookup into a lon/lat raster; lon wraps modulo 360."""

    def __init__(self, arr, bounds):
        self.arr = arr
        self.h, self.w = arr.shape[:2]
        self.lon0 = bounds['lon0']
        self.span = bounds['lon_span']
        self.lat_top = bounds['lat_top']
        self.lat_bot = bounds['lat_bot']

    def sample(self, lon, lat):
        col = np.floor(np.mod(lon - self.lon0, 360.0) / self.span * self.w).astype(np.int64)
        row = np.floor((self.lat_top - lat) / (self.lat_top - self.lat_bot) * self.h).astype(np.int64)
        ok = (col >= 0) & (col < self.w) & (row >= 0) & (row < self.h)
        out = np.zeros(lon.shape, dtype=np.float32)
        out[ok] = self.arr[row[ok], col[ok]]
        return out


# ------------------------------------------------------------------
# Colour separation
# ------------------------------------------------------------------
def _separate(rgb, valid, inks, paper_hex, max_cov, auto_contrast):
    """Returns a list of density maps (0..1, one per ink) for an sRGB image."""
    lo, hi = 0.0, 1.0
    if auto_contrast and valid.any():
        p_lo, p_hi = np.percentile(luminance(srgb_to_linear(rgb[valid])), [0.5, 99.5])
        if p_hi - p_lo > 1e-4:
            lo, hi = p_lo, p_hi

    paper = srgb_to_linear(hex_to_srgb(paper_hex))
    ink_lin = [srgb_to_linear(hex_to_srgb(c)) for c in inks]
    Yp = max(float(luminance(paper)), 1e-6)

    # Black-point compensation: image black -> darkest colour these inks can reach, so dark
    # tones keep their separation instead of all clipping to the same maximum-ink value.
    if len(inks) == 1:
        y_floor = 1.0 - max_cov[0] * (1.0 - float(luminance(ink_lin[0])) / Yp)
    else:
        k = len(inks)
        n = {2: 33, 3: 17, 4: 11}.get(k, 7)
        grid = np.stack(np.meshgrid(*[np.linspace(0.0, 1.0, n)] * k, indexing='ij'), axis=-1).reshape(-1, k)
        pred = np.ones((grid.shape[0], 3)) * paper
        for i in range(k):
            c = grid[:, i:i + 1] * max_cov[i]
            pred *= 1.0 - c * (1.0 - ink_lin[i])
        y_floor = float(luminance(pred).min()) / Yp
    y_floor = float(np.clip(y_floor, 0.0, 0.99))

    def stretch(lin):
        lin = np.clip((lin - lo) / (hi - lo), 0.0, 1.0)
        return y_floor + lin * (1.0 - y_floor)

    if len(inks) == 1:
        # Analytic single-ink solution on luminance (media-relative: image white -> bare paper).
        Yi = luminance(ink_lin[0])
        Yt = luminance(stretch(srgb_to_linear(rgb)) * paper)
        cov = (1.0 - Yt / max(Yp, 1e-6)) / max(1.0 - Yi / max(Yp, 1e-6), 1e-6)
        d = np.clip(cov / max_cov[0], 0.0, 1.0)
        d[~valid] = 0.0
        return [d.astype(np.float32)]

    feat = np.column_stack([linear_to_lab(pred), REG_WEIGHT * (grid * np.asarray(max_cov)).sum(axis=1)])
    tree = cKDTree(feat)

    # Solve once per distinct (6-bit quantised) colour.
    q = np.clip(np.round(rgb * 63.0), 0, 63).astype(np.int32)
    key = (q[..., 0] << 12) | (q[..., 1] << 6) | q[..., 2]
    uniq, inv = np.unique(key.ravel(), return_inverse=True)
    uq = np.stack([(uniq >> 12) & 63, (uniq >> 6) & 63, uniq & 63], axis=-1) / 63.0
    u_lin = stretch(srgb_to_linear(uq))
    u_feat = np.column_stack([linear_to_lab(u_lin * paper), np.zeros(len(uniq))])
    _, idx = tree.query(u_feat)
    dens = grid[idx][inv.ravel()].reshape(rgb.shape[0], rgb.shape[1], k)
    dens[~valid] = 0.0
    return [dens[..., i].astype(np.float32) for i in range(k)]


# ------------------------------------------------------------------
# Scan-line machinery
# ------------------------------------------------------------------
def _bitrev(j, bits):
    r = np.zeros_like(j)
    for b in range(bits):
        r |= ((j >> b) & 1) << (bits - 1 - b)
    return r


def _scan_grid(frame, angle_deg, spacing, dt, phase):
    D = 0.5 * np.hypot(frame.pw, frame.ph) + spacing + 2.0 * dt
    th = np.radians(angle_deg)
    ex, ey = np.cos(th), np.sin(th)
    nx, ny = -ey, ex
    offs = np.arange(-D + (phase % spacing), D, spacing)
    ts = np.arange(-D, D + dt, dt)
    U = frame.pw / 2.0 + offs[:, None] * nx + ts[None, :] * ex
    V = frame.ph / 2.0 + offs[:, None] * ny + ts[None, :] * ey
    return offs, ts, U, V, (nx, ny)


def _clean_runs(on, inside, dt):
    g = max(1, int(round(GAP_CLOSE_MM / dt)))
    m = max(2, int(np.ceil(MIN_SEGMENT_MM / dt)) + 1)
    if g > 1:
        on = scipy.ndimage.binary_closing(on, structure=np.ones((1, g), dtype=bool))
    on &= inside
    on = scipy.ndimage.binary_opening(on, structure=np.ones((1, m), dtype=bool))
    return on


def _runs(on):
    J, K = on.shape
    pad = np.zeros((J, K + 2), dtype=np.int8)
    pad[:, 1:-1] = on
    d = np.diff(pad, axis=1)
    rows, starts = np.nonzero(d == 1)
    _, ends = np.nonzero(d == -1)      # row-major order -> pairs line up with starts
    return rows, starts, ends


def _straight_segments(frame, on, U, V, pen_id, level):
    rows, s, e = _runs(on)
    if len(rows) == 0:
        return []
    U0, V0 = frame.to_data(U[rows, s], V[rows, s])
    U1, V1 = frame.to_data(U[rows, e - 1], V[rows, e - 1])
    pts = np.stack([np.column_stack([U0, V0]), np.column_stack([U1, V1])], axis=1)
    return [{'type': 'hatch', 'seg': p, 'level': level, 'pen_id': pen_id} for p in pts]


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------
def generate_hatches(rgb, rgb_bounds, view_extent, params, progress_callback=None):
    """rgb: float (h, w, 3) sRGB 0..1 covering rgb_bounds (see workers.read_color_window)."""
    segments = []
    if rgb is None or rgb.size == 0:
        return segments

    mode = params.get('plot_mode', '')
    strategy = params.get('hatch_strategy', 'Multi-Pass Thresholded')
    pens = params.get('pens', {})
    spacing = max(0.2, float(params.get('hatch_spacing_mm', DEFAULT_HATCH_SPACING_MM)))
    auto_contrast = params.get('auto_contrast', True)

    if "Monochrome" in mode:
        ink_set, n_passes = MONO_INK_SET, 3
    else:
        ink_set, n_passes = INK_SETS.get(params.get('ink_set', DEFAULT_INK_SET), INK_SETS[DEFAULT_INK_SET]), 2

    inks = [(pid, ang) for pid, ang in ink_set['inks'] if pid in pens]
    if not inks:
        raise ValueError("None of the pens in the selected ink set exist in the pen list.")

    frame = _Frame(view_extent, params)
    if frame.pw <= 0 or frame.ph <= 0 or view_extent[1] <= view_extent[0]:
        return segments

    line_w = [float(pens[pid].get('line_mm', pens[pid]['width'])) for pid, _ in inks]
    max_cov = [pattern_max_coverage(strategy, w, spacing, n_passes) for w in line_w]

    # --- Prefilter to roughly the hatch resolution, then separate ---
    if progress_callback: progress_callback(f"Separating {len(inks)} ink layer(s)...")
    valid = np.isfinite(rgb).all(axis=-1)
    rgb = np.where(valid[..., None], rgb, 1.0).astype(np.float64)
    mm_per_px = rgb_bounds['lon_span'] / rgb.shape[1] * frame.mm_per_deg()
    sigma = 0.35 * spacing / max(mm_per_px, 1e-9)
    if sigma > 0.3:
        rgb = scipy.ndimage.gaussian_filter(rgb, sigma=(sigma, sigma, 0), mode='nearest')
    dens_maps = _separate(rgb, valid, [pens[pid]['color'] for pid, _ in inks], ink_set['paper'],
                          max_cov, auto_contrast)

    golden = 0.6180339887
    for ink_idx, ((pen_id, base_angle), dmap, lw, cmax) in enumerate(zip(inks, dens_maps, line_w, max_cov)):
        raster = _Raster(dmap, rgb_bounds)
        if progress_callback: progress_callback(f"Hatching {pens[pen_id]['name']} ({strategy})...")

        if "Squiggle" in strategy:
            line_spacing, _, wavelength = _squiggle_params(spacing)
            amps, covs = _squiggle_table(lw, spacing)
            dt = wavelength / 16.0
            offs, ts, U, V, (nx, ny) = _scan_grid(frame, base_angle, line_spacing, dt, golden * ink_idx * line_spacing)
            inside = frame.inside(U, V)
            d = raster.sample(*frame.to_lonlat(U, V)) * inside
            c = d * cmax
            c0 = covs[0]                                   # coverage of a straight line
            on = (c >= 0.5 * c0) & inside
            on = _clean_runs(on, inside, dt)
            A = np.interp(c, covs, amps) if len(covs) > 1 else np.zeros_like(c)
            wave = np.sin(2.0 * np.pi * ts / wavelength)[None, :]
            Ud, Vd = frame.clamp(U + A * wave * nx, V + A * wave * ny)
            rows, s, e = _runs(on)
            Xd, Yd = frame.to_data(Ud, Vd)
            level = 1000 * (ink_idx + 1)
            for r, a, b in zip(rows, s, e):
                segments.append({'type': 'hatch', 'seg': np.column_stack([Xd[r, a:b], Yd[r, a:b]]),
                                 'level': level, 'pen_id': pen_id})

        elif "Variable" in strategy:
            dt = SAMPLE_STEP_MM
            offs, ts, U, V, _ = _scan_grid(frame, base_angle, spacing, dt, golden * ink_idx * spacing)
            inside = frame.inside(U, V)
            d = raster.sample(*frame.to_lonlat(U, V)) * inside
            # Ordered dither across line index: dark -> every line, light -> every 2nd/4th/.../16th.
            j = np.arange(len(offs))
            thr = (_bitrev(j % 16, 4) + 0.5) / 16.0
            on = (d >= thr[:, None]) & inside
            on = _clean_runs(on, inside, dt)
            segments.extend(_straight_segments(frame, on, U, V, pen_id, 1000 * (ink_idx + 1)))

        else:
            # Multi-pass thresholded crosshatch. Pass n turns on where the target is closer to
            # the n-pass coverage than to the (n-1)-pass coverage.
            k = min(1.0, lw / spacing)
            levels = np.array([1.0 - (1.0 - k) ** n for n in range(n_passes + 1)])
            levels = levels / levels[-1]
            angles = [base_angle, base_angle + 90.0, base_angle + 45.0, base_angle + 135.0][:n_passes]
            dt = SAMPLE_STEP_MM
            for p, ang in enumerate(angles):
                thr = 0.5 * (levels[p] + levels[p + 1])
                offs, ts, U, V, _ = _scan_grid(frame, ang, spacing, dt, golden * (ink_idx * n_passes + p) * spacing)
                inside = frame.inside(U, V)
                d = raster.sample(*frame.to_lonlat(U, V)) * inside
                on = (d >= thr) & inside
                on = _clean_runs(on, inside, dt)
                segments.extend(_straight_segments(frame, on, U, V, pen_id, 1000 * (ink_idx + 1) + p))

    if progress_callback: progress_callback(f"Hatching complete ({len(segments)} strokes).")
    return segments
