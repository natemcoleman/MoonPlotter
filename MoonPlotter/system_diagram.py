"""Generalised Solar System diagram (Solar System mode > Plot: Solar System diagram).

A poster-style schematic, not to scale: the Sun's edge at the left, concentric orbit arcs centred on it,
the eight planets lined up along the middle with their names, ringed Saturn / Uranus / Neptune, banded
Jupiter, a few moons, a stippled asteroid belt between Mars and Jupiter, scattered background stars and a
title underneath. Everything is in paper millimetres (origin bottom-left, y up) and returned in the same
stroke format as mission_plot.build_mission_plot.

Layers: 'frame', 'orbits', 'markers' (Sun, rays, planets, rings, moons), 'belt', 'stars', 'labels'.
"""
import math

import numpy as np

from decorations import vector_text, min_cap_for_pen, TEXT_SCALES

DEFAULT_TITLE = "THE SOLAR SYSTEM"
DEFAULT_SUBTITLE = "OUR CELESTIAL NEIGHBORHOOD"

# Distance of each orbit from the Sun's centre, as a fraction of (frame right edge - Sun centre).
# Not to scale: inner planets get room, the belt gets a wide band, like the classic posters.
ORBIT_FRAC = {'Mercury': 0.193, 'Venus': 0.252, 'Earth': 0.310, 'Mars': 0.378,
              'Jupiter': 0.595, 'Saturn': 0.742, 'Uranus': 0.852, 'Neptune': 0.945}
BELT_FRAC = (0.405, 0.525)
SUN_FRAC_H = 0.15                 # Sun radius as a fraction of the diagram height
# Planet radius as a fraction of the diagram height (schematic sizes, Jupiter biggest)
PLANET_FRAC_H = {'Mercury': 0.016, 'Venus': 0.028, 'Earth': 0.032, 'Mars': 0.019,
                 'Jupiter': 0.090, 'Saturn': 0.062, 'Uranus': 0.047, 'Neptune': 0.044}
# Rings: (half-width / r, half-height / r, tilt deg, inner edge as a fraction of the outer ellipse)
RINGS = {'Saturn': (2.15, 0.48, -18.0, 0.80), 'Uranus': (0.28, 1.62, 6.0, None), 'Neptune': (1.75, 0.26, -8.0, None)}
# Moons: (angle deg from +x, distance / r, moon radius / r)
MOONS = {'Earth': [(52, 1.65, 0.22)],
         'Jupiter': [(55, 1.35, 0.07), (205, 1.25, 0.06), (250, 1.45, 0.05)],
         'Saturn': [(62, 1.45, 0.11), (-25, 2.45, 0.08)],
         'Uranus': [(48, 1.55, 0.09)],
         'Neptune': [(18, 1.55, 0.09)]}
# Label above (+1) or below (-1) the planet, as on the reference poster
LABEL_SIDE = {'Mercury': 1, 'Venus': 1, 'Earth': -1, 'Mars': 1, 'Jupiter': -1, 'Saturn': -1, 'Uranus': 1,
              'Neptune': -1}
ORDER = ['Mercury', 'Venus', 'Earth', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune']


def _circle(c, r, step=0.35):
    n = max(10, int(math.ceil(2 * math.pi * r / step)))
    t = np.linspace(0, 2 * np.pi, n + 1)
    return np.column_stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)])


def _ellipse(c, a, b, tilt_deg, step=0.35):
    n = max(16, int(math.ceil(2 * math.pi * max(a, b) / step)))
    t = np.linspace(0, 2 * np.pi, n + 1)
    ct, st = math.cos(math.radians(tilt_deg)), math.sin(math.radians(tilt_deg))
    x, y = a * np.cos(t), b * np.sin(t)
    return np.column_stack([c[0] + x * ct - y * st, c[1] + x * st + y * ct])


def _runs(pts, keep):
    """Splits a polyline into the runs of points where keep is True."""
    out, cur = [], []
    for p, k in zip(pts, keep):
        if k:
            cur.append(p)
        elif cur:
            if len(cur) >= 2:
                out.append(np.array(cur))
            cur = []
    if len(cur) >= 2:
        out.append(np.array(cur))
    return out


def _in_box(pts, box):
    return (pts[:, 0] >= box[0]) & (pts[:, 0] <= box[2]) & (pts[:, 1] >= box[1]) & (pts[:, 1] <= box[3])


def _ring_coords(pts, c, a, b, tilt_deg):
    """Points -> (normalised elliptical radius, coordinate along the ring's short axis)."""
    ct, st = math.cos(math.radians(tilt_deg)), math.sin(math.radians(tilt_deg))
    dx, dy = pts[:, 0] - c[0], pts[:, 1] - c[1]
    u = dx * ct + dy * st
    v = -dx * st + dy * ct
    return np.hypot(u / a, v / b), v, u


def _dot(c, r):
    return _circle(c, max(0.08, r), 0.25)


def _asteroid(c, r, rng):
    """Small irregular rock outline."""
    n = int(rng.integers(5, 8))
    ang = np.sort(rng.uniform(0, 2 * np.pi, n))
    rad = r * rng.uniform(0.65, 1.0, n)
    pts = np.column_stack([c[0] + rad * np.cos(ang), c[1] + rad * np.sin(ang)])
    return np.vstack([pts, pts[:1]])


def build_system_diagram(opts, pw, ph):
    """opts: pen, pen_orbits, pen_belt, line_mm, belt_line_mm, text_size, title_text, labels (bool),
    belt (bool), moons (bool), stars (bool), rays (bool), frame (bool). Returns (segments, masks, info)."""
    pen = opts.get('pen', 'base')
    pen_orbits = opts.get('pen_orbits') or pen
    pen_belt = opts.get('pen_belt') or pen
    line_mm = float(opts.get('line_mm', 0.38))
    belt_line = float(opts.get('belt_line_mm', line_mm))
    rng = np.random.default_rng(20261111)
    k_txt = TEXT_SCALES.get(opts.get('text_size', 'Medium'), 1.0) * float(np.clip(min(pw, ph) / 100.0, 1.0, 2.2)) ** 0.5
    floor = min_cap_for_pen(line_mm)
    cap = max(floor, 1.7 * k_txt)
    cap_title = max(floor * 1.3, 3.0 * k_txt)
    cap_sub = max(floor, 1.35 * k_txt)
    m = 4.0
    segs = []                       # (layer, pen, polyline)
    masks = []                      # label boxes: map lines are blanked behind them
    label_boxes = []

    def add(layer, p, polys):
        for q in polys:
            if q is not None and len(q) >= 2:
                segs.append((layer, p, np.asarray(q, float)))

    # ---- title (under the frame)
    raw = (opts.get('title_text') or "").strip()
    if raw:
        title, _, sub = (s.strip() for s in raw.partition("|"))
    else:
        title, sub = DEFAULT_TITLE, DEFAULT_SUBTITLE
    y_sub = m
    if sub:
        polys, box = vector_text(sub.upper(), pw / 2, y_sub, cap_sub, anchor='center')
        add('labels', pen, polys)
        label_boxes.append(box)
        y_title = box[3] + 0.9 * cap_sub
    else:
        y_title = m
    polys, box = vector_text(title.upper(), pw / 2, y_title, cap_title, anchor='center')
    add('labels', pen, polys)
    label_boxes.append(box)
    frame_bottom = box[3] + 0.9 * cap_title

    # ---- frame and diagram geometry
    fx0, fx1, fy0, fy1 = m, pw - m, frame_bottom, ph - m
    dh = fy1 - fy0
    cy = (fy0 + fy1) / 2.0
    sun_c = np.array([fx0, cy])
    frame_box = (fx0, fy0, fx1, fy1)
    L = fx1 - sun_c[0]
    orbit_r = {p: ORBIT_FRAC[p] * L for p in ORDER}
    sun_r = min(SUN_FRAC_H * dh, 0.62 * orbit_r['Mercury'] / 1.0 - 1.0)
    sun_r = max(sun_r, 2.0)

    # planet sizes: from the height, shrunk if neighbours would touch (narrow or tall paper)
    def extent(p, r):                          # horizontal half-extent incl. ring
        return r * (RINGS[p][0] if p in RINGS and RINGS[p][0] > 1 else 1.0)
    s = 1.0
    for a, b in zip(ORDER[:-1], ORDER[1:]):
        gap = orbit_r[b] - orbit_r[a] - 1.5
        need = extent(a, PLANET_FRAC_H[a] * dh) + extent(b, PLANET_FRAC_H[b] * dh)
        if need > gap > 0:
            s = min(s, gap / need)
    planet_r = {p: max(0.6, PLANET_FRAC_H[p] * dh * s) for p in ORDER}
    centres = {p: np.array([sun_c[0] + orbit_r[p], cy]) for p in ORDER}

    # occluders: things the orbit arcs, stars and belt must stop at
    def planet_clear(pts, pad=0.6):
        ok = np.ones(len(pts), bool)
        for p in ORDER:
            c, r = centres[p], planet_r[p]
            if p in RINGS:
                a_, b_, tilt, _ = RINGS[p]
                rr, _, _ = _ring_coords(pts, c, a_ * r + pad, b_ * r + pad, tilt)
                ok &= rr > 1.0
            ok &= np.hypot(pts[:, 0] - c[0], pts[:, 1] - c[1]) > r + pad
        return ok

    # ---- frame (left edge runs through the Sun's centre, like the poster)
    if opts.get('frame', True):
        add('frame', pen_orbits, [np.array([[fx0, fy0], [fx1, fy0], [fx1, fy1], [fx0, fy1], [fx0, fy0]])])

    # ---- label helpers
    def place_label(text, x, y, anchor='center'):
        polys, box = vector_text(text, x, y, cap, anchor=anchor)
        if not box:
            return None
        for b in label_boxes:
            if not (box[2] + 0.6 < b[0] or b[2] + 0.6 < box[0] or box[3] + 0.6 < b[1] or b[3] + 0.6 < box[1]):
                return None
        add('labels', pen, polys)
        label_boxes.append(box)
        masks.append((box[0] - 0.7, box[1] - 0.7, box[2] + 0.7, box[3] + 0.7))
        return box

    def clear_of_labels(pts, pad=0.6):
        ok = np.ones(len(pts), bool)
        for b in label_boxes:
            ok &= ~_in_box(pts, (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad))
        return ok

    if opts.get('labels', True):                   # first, so the Sun's rays keep clear of it
        place_label("SUN", sun_c[0] + sun_r * 1.25, cy - sun_r * 0.12 - cap, anchor='left')

    # ---- Sun: disc edge + rays
    sun_pts = _circle(sun_c, sun_r)
    add('markers', pen, _runs(sun_pts, _in_box(sun_pts, frame_box)))
    if opts.get('rays', True):
        for i, a in enumerate(np.arange(-82.5, 83.0, 7.5)):
            u = np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])
            r1 = sun_r * 1.18
            r2 = sun_r * (1.55 + 0.45 * rng.uniform()) if i % 3 else sun_r * 2.05
            style = ('solid', 'dash', 'dot')[int(rng.integers(0, 3))]
            if abs(a) < 1e-6:
                style = 'solid'
            if style == 'solid':
                pieces = [(r1, r2)]
            elif style == 'dash':
                d = max(1.0, 0.09 * sun_r)
                pieces = [(t, min(t + d, r2)) for t in np.arange(r1, r2, 1.8 * d)]
            else:
                d = max(0.5, 0.035 * sun_r)
                pieces = [(t, t + 0.01) for t in np.arange(r1, r2, 2.6 * d)]
            for t0, t1 in pieces:
                q = np.array([sun_c + u * t0, sun_c + u * t1])
                dense = sun_c + u * np.linspace(t0, t1, max(2, int((t1 - t0) / 0.3) + 2))[:, None]
                if _in_box(q, frame_box).all() and clear_of_labels(dense, 0.5).all():
                    add('markers', pen, [q])

    # ---- orbits (arcs clipped to the frame, broken at the planets)
    for p in ORDER:
        pts = _circle(sun_c, orbit_r[p], 0.3)
        keep = _in_box(pts, (fx0 + 0.01, fy0 + 0.01, fx1 - 0.01, fy1 - 0.01)) & planet_clear(pts, pad=0.5)
        add('orbits', pen_orbits, _runs(pts, keep))

    # ---- planets
    for p in ORDER:
        c, r = centres[p], planet_r[p]
        body = _circle(c, r)
        if p in RINGS:
            a_, b_, tilt, inner = RINGS[p]
            A, B = a_ * r, b_ * r
            # planet outline hidden where the front half of the ring crosses it
            rr, v, u = _ring_coords(body, c, A, B, tilt)
            front = (v < 0) if A >= B else (u < 0)
            hide = front & (rr <= 1.0) & (rr >= (inner or 0.82) - 0.06)
            add('markers', pen, _runs(body, ~hide))
            for scale in ([1.0, inner] if inner else [1.0]):
                ring = _ellipse(c, A * scale, B * scale, tilt)
                _, v2, u2 = _ring_coords(ring, c, A, B, tilt)
                behind = (v2 > 0) if A >= B else (u2 > 0)
                inside = np.hypot(ring[:, 0] - c[0], ring[:, 1] - c[1]) < r
                add('markers', pen, _runs(ring, ~(behind & inside)))
        else:
            add('markers', pen, [body])
        if p == 'Jupiter':
            for f in (-0.62, -0.30, -0.12, 0.22, 0.48, 0.74):       # cloud bands
                y = c[1] + f * r
                half = math.sqrt(max(r * r - (f * r) ** 2, 0.0)) - 0.25
                if half > 0.5:
                    add('markers', pen, [np.array([[c[0] - half, y], [c[0] + half, y]])])
            add('markers', pen, [_ellipse(c + np.array([0.25 * r, -0.45 * r]), 0.2 * r, 0.11 * r, 0.0)])
        if opts.get('moons', True):
            for ang, dist, mr in MOONS.get(p, []):
                mc = c + dist * r * np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
                add('markers', pen, [_circle(mc, max(0.35, mr * r))])

    if opts.get('labels', True):
        for p in ORDER:
            c, r = centres[p], planet_r[p]
            half_h = r * (RINGS[p][1] if p in RINGS and RINGS[p][1] > 1 else 1.0)
            if p == 'Saturn':
                half_h = r * 1.25
            for side in (LABEL_SIDE[p], -LABEL_SIDE[p]):
                y = c[1] + side * (half_h + 1.4) + (0 if side > 0 else -cap)
                if place_label(p.upper(), c[0], y) is not None:
                    break
        if opts.get('belt', True):
            bx = sun_c[0] + 0.5 * (BELT_FRAC[0] + BELT_FRAC[1]) * L
            place_label("ASTEROID BELT", bx, cy + 0.6 * cap)

    # ---- asteroid belt: stipple band (denser in the middle) + a few rock outlines
    belt_pts = np.zeros((0, 2))
    if opts.get('belt', True):
        r_in, r_out = BELT_FRAC[0] * L, BELT_FRAC[1] * L
        spacing = max(0.75, 2.4 * belt_line)
        a_lo, a_hi = -math.pi / 2, math.pi / 2
        area = 0.5 * math.pi * (r_out ** 2 - r_in ** 2)
        n_try = int(area / spacing ** 2 * 1.4)
        r = np.sqrt(rng.uniform(r_in ** 2, r_out ** 2, n_try))
        a = rng.uniform(a_lo, a_hi, n_try)
        t = (r - r_in) / (r_out - r_in)
        wob = 0.12 * np.sin(5.0 * a)                               # meandering band, as hand-drawn
        dens = 0.15 + 0.85 * np.clip(np.sin(np.pi * np.clip(t + wob, 0, 1)), 0, 1) ** 1.5
        keepm = rng.uniform(size=n_try) < dens
        pts = np.column_stack([sun_c[0] + r * np.cos(a), sun_c[1] + r * np.sin(a)])[keepm]
        pts = pts[_in_box(pts, (fx0 + 1, fy0 + 1, fx1 - 1, fy1 - 1)) & clear_of_labels(pts)]
        chosen = []                                                # minimum spacing (blue-noise-ish)
        cell = spacing / math.sqrt(2)
        grid = {}
        for q in pts:
            gx, gy = int(q[0] // cell), int(q[1] // cell)
            if any((grid.get((gx + dx, gy + dy)) is not None and
                    math.hypot(*(grid[(gx + dx, gy + dy)] - q)) < spacing)
                   for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)):
                continue
            grid[(gx, gy)] = q
            chosen.append(q)
        chosen = np.array(chosen) if chosen else np.zeros((0, 2))
        rocks = rng.choice(len(chosen), size=min(len(chosen), max(4, int(0.012 * len(chosen)) + 6)),
                           replace=False) if len(chosen) else []
        rock_set = set(int(i) for i in rocks)
        for i, q in enumerate(chosen):
            if i in rock_set:
                add('belt', pen_belt, [_asteroid(q, rng.uniform(0.6, 1.3) * spacing * 0.8, rng)])
            elif rng.uniform() < 0.12:
                add('belt', pen_belt, [_dot(q, 0.32 * spacing)])
            else:
                add('belt', pen_belt, [_dot(q, max(0.1, 0.22 * belt_line))])
        belt_pts = chosen

    # ---- background stars (sparse dots, kept off the planets, orbits, belt and labels)
    if opts.get('stars', True):
        n = int((fx1 - fx0) * (fy1 - fy0) * 0.010)
        pts = np.column_stack([rng.uniform(fx0 + 1.5, fx1 - 1.5, n), rng.uniform(fy0 + 1.5, fy1 - 1.5, n)])
        d_sun = np.hypot(pts[:, 0] - sun_c[0], pts[:, 1] - sun_c[1])
        ok = (d_sun > sun_r * 2.2) & planet_clear(pts, pad=2.0) & clear_of_labels(pts, 1.0)
        for p in ORDER:
            ok &= np.abs(d_sun - orbit_r[p]) > 1.2
        if opts.get('belt', True):
            ok &= (d_sun < BELT_FRAC[0] * L - 1.5) | (d_sun > BELT_FRAC[1] * L + 1.5)
        for p in ORDER:
            for ang, dist, mr in MOONS.get(p, []):
                mc = centres[p] + dist * planet_r[p] * np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
                ok &= np.hypot(pts[:, 0] - mc[0], pts[:, 1] - mc[1]) > 2.0
        for q in pts[ok]:
            add('stars', pen_belt, [_dot(q, max(0.1, 0.22 * belt_line) * (2.2 if rng.uniform() < 0.1 else 1.0))])

    shift = np.array([0.0, -ph / 2.0])
    out = [{'type': 'border', 'layer': layer, 'seg': poly + shift, 'level': 0, 'pen_id': pid}
           for layer, pid, poly in segs]
    masks = [(b[0], b[1] - ph / 2, b[2], b[3] - ph / 2) for b in masks]
    info = {'planets': ORDER, 'scale': s, 'belt_dots': int(len(belt_pts))}
    return out, masks, info
