"""Solar-system mission diagrams in the style of the classic NASA trajectory charts.

Everything is laid out in paper millimetres. Distances from the Sun are compressed (log scale) so the
inner planets stay readable next to Neptune; directions are true. Returns stroke dicts (same format as
the map layers, in the 'paper' data frame x in [0, pw], y in [-ph/2, ph/2]) plus mask boxes for labels.

Layers: 'orbits' (planet orbits), 'trajectory', 'markers' (Sun, planets, year dots), 'labels', 'belt'.
"""
import math

import numpy as np

import missions as M
from decorations import vector_text, min_cap_for_pen, TEXT_SCALES, _wrap

R0_AU = 0.5                       # compression knee: f(r) = ln(1 + r / R0)
PATH_STYLES = ["Dashed", "Dotted", "Solid"]


def f_scale(r):
    return np.log1p(np.asarray(r, float) / R0_AU)


class _Layout:
    def __init__(self, cx, cy, k):
        self.cx, self.cy, self.k = cx, cy, k

    def to_mm(self, xy):
        xy = np.atleast_2d(np.asarray(xy, float))[:, :2]
        r = np.hypot(xy[:, 0], xy[:, 1])
        rr = self.k * f_scale(r)
        with np.errstate(invalid='ignore', divide='ignore'):
            ux = np.where(r > 0, xy[:, 0] / r, 0.0)
            uy = np.where(r > 0, xy[:, 1] / r, 0.0)
        return np.column_stack([self.cx + rr * ux, self.cy + rr * uy])

    def radius_mm(self, r_au):
        return float(self.k * f_scale(r_au))


def _circle(c, r, n=16):
    t = np.linspace(0, 2 * np.pi, n + 1)
    return np.column_stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)])


def _filled_dot(c, r, line_mm):
    """Concentric rings that fill a dot of radius r with a pen of this line width."""
    out, rr = [], r
    step = max(0.15, 0.8 * line_mm)
    while rr > step * 0.4:
        out.append(_circle(c, rr, max(8, int(2 * np.pi * rr / 0.4))))
        rr -= step
    out.append(np.array([c, c + [0.05, 0.0]]))
    return out


def _star(c, r):
    out = []
    for a in np.arange(0, np.pi, np.pi / 4):
        d = np.array([np.cos(a), np.sin(a)]) * r
        out.append(np.array([c - d, c + d]))
    out.append(_circle(c, r * 0.35, 10))
    return out


def _arrowhead(tip, direction, size):
    d = np.asarray(direction, float)
    n = np.linalg.norm(d)
    if n < 1e-9:
        return []
    d = d / n
    out = []
    for s in (1, -1):
        a = math.radians(24) * s
        rot = np.array([d[0] * math.cos(a) - d[1] * math.sin(a), d[0] * math.sin(a) + d[1] * math.cos(a)])
        out.append(np.array([tip - rot * size, tip]))
    return out


def _resample(path, step):
    """Points every 'step' mm along a polyline, plus the cumulative distances."""
    seg = np.hypot(*np.diff(path, axis=0).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    if s[-1] <= 0:
        return path[:1], np.array([0.0])
    t = np.arange(0, s[-1], step)
    return np.column_stack([np.interp(t, s, path[:, 0]), np.interp(t, s, path[:, 1])]), t


def _styled_path(path, style, line_mm):
    if len(path) < 2:
        return []
    if style == "Solid":
        return [path]
    if style == "Dotted":
        pts, _ = _resample(path, max(0.9, 2.6 * line_mm))
        return [_circle(p, max(0.1, 0.25 * line_mm), 6) for p in pts]
    dash, gap = max(1.2, 3.0 * line_mm), max(0.9, 2.2 * line_mm)
    seg = np.hypot(*np.diff(path, axis=0).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    out = []
    t = 0.0
    while t < s[-1]:
        t1 = min(t + dash, s[-1])
        idx = (s > t) & (s < t1)
        pts = [np.interp(t, s, path[:, 0]), np.interp(t, s, path[:, 1])]
        piece = [pts] + path[idx].tolist() + [[np.interp(t1, s, path[:, 0]), np.interp(t1, s, path[:, 1])]]
        out.append(np.array(piece))
        t = t1 + gap
    return out


def _overlaps(a, b, gap=0.5):
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])


class _Labeler:
    """Places labels near targets with a leader arrow, avoiding each other and the paper edge."""

    def __init__(self, pw, ph, cap, line_mm, margin):
        self.pw, self.ph, self.cap, self.line_mm, self.m = pw, ph, cap, line_mm, margin
        self.taken = []            # boxes (x0, y0, x1, y1)
        self.lines, self.masks = [], []

    def reserve(self, box):
        self.taken.append(box)

    def place(self, text, target, prefer_dir, dist=3.5, arrow=True, cap=None, lines_out=None):
        cap = cap or self.cap
        target = np.asarray(target, float)
        pd = np.asarray(prefer_dir, float)
        pd = pd / (np.linalg.norm(pd) or 1.0)
        base_ang = math.atan2(pd[1], pd[0])
        _, box0 = vector_text(text, 0.0, 0.0, cap)
        if not box0:
            return None
        w, h = box0[2] - box0[0], box0[3] - box0[1]
        for extra in (0.0, 2.5, 5.0, 8.0):
            for da in (0, 25, -25, 50, -50, 80, -80, 115, -115, 150, -150, 180):
                a = base_ang + math.radians(da)
                u = np.array([math.cos(a), math.sin(a)])
                # distance from target to the box centre so the box clears the target by 'dist'
                reach = dist + extra + 0.5 * (abs(u[0]) * w + abs(u[1]) * h)
                c = target + u * reach
                box = (c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2)
                if box[0] < self.m or box[2] > self.pw - self.m or box[1] < self.m or box[3] > self.ph - self.m:
                    continue
                if any(_overlaps(box, t) for t in self.taken):
                    continue
                polys, tb = vector_text(text, box[0] - box0[0], box[1] - box0[1], cap)
                out = lines_out if lines_out is not None else self.lines
                out.extend(polys)
                self.taken.append(box)
                self.masks.append((box[0] - 0.8, box[1] - 0.8, box[2] + 0.8, box[3] + 0.8))
                if arrow:
                    # leader from the box edge (toward the target) to just short of the target
                    start = np.array(c) - u * (0.5 * (abs(u[0]) * w + abs(u[1]) * h) + 0.6)
                    vec = target - start
                    L = np.linalg.norm(vec)
                    if L > 1.6:
                        end = target - vec / L * 1.3
                        out.append(np.array([start, end]))
                        out.extend(_arrowhead(end, vec, max(0.8, 0.45 * cap)))
                return box
        return None


def _auto_planets(mission_list):
    visited = {'Earth'}
    for m in mission_list:
        for _, body, _ in m['events']:
            if body:
                visited.add(body)
    order = M.PLANET_ORDER
    outer = max(order.index(b) for b in visited)
    shown = [p for i, p in enumerate(order) if i <= outer]
    if outer >= order.index('Jupiter'):
        shown = [p for p in shown if p not in ('Mercury', 'Venus') or p in visited]
    if 'Pluto' in shown and 'Pluto' not in visited:
        shown.remove('Pluto')
    return shown, visited


def _years_label_step(n_years):
    for s in (1, 2, 5, 10, 20):
        if n_years / s <= 6:
            return s
    return 50


def build_mission_plot(selection, data, opts, pw, ph):
    """selection: [(mission dict, pen_id)]; data: {key: (jd, xyz)}.
    opts: title_text, text_size, line_mm (labels pen width), pen (labels/orbits pen), path_style,
    year_marks, labels, belt, today_jd. Returns (segments, masks, info)."""
    pen = opts.get('pen', 'base')                       # labels, title, Sun and planet markers
    pen_orbits = opts.get('pen_orbits') or pen
    pen_belt = opts.get('pen_belt') or pen
    line_mm = float(opts.get('line_mm', 0.38))
    k_txt = TEXT_SCALES.get(opts.get('text_size', 'Medium'), 1.0) * float(np.clip(min(pw, ph) / 100.0, 1.0, 2.0)) ** 0.5
    floor = min_cap_for_pen(line_mm)
    cap = max(floor, 1.9 * k_txt)
    cap_title = max(floor * 1.4, 3.4 * k_txt)
    m = 4.0
    segs = []                                       # (layer, pen, polyline)

    def add(layer, p, polys):
        for q in polys:
            if len(q) >= 2:
                segs.append((layer, p, np.asarray(q, float)))

    sel = [(mi, p) for mi, p in selection if mi['key'] in data]
    mlist = [mi for mi, _ in sel]
    if not sel:
        return [], [], {'error': "No mission data loaded."}

    # ---- title block (top)
    names = [x['name'].upper() for x in mlist]
    title = opts.get('title_text') or (
        f"{names[0]} MISSION TRAJECTORY" if len(names) == 1 else
        ", ".join(names[:-1]) + " & " + names[-1] + " TRAJECTORIES")
    title_lines = _wrap(title, cap_title, pw - 2 * m, separators=(" ",))
    y = ph - m - cap_title
    lab = _Labeler(pw, ph, cap, line_mm, m)
    for ln in title_lines:
        polys, box = vector_text(ln, pw / 2, y, cap_title, anchor='center')
        add('labels', pen, polys)
        lab.reserve(box)
        lab.masks.append((box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1))
        y -= cap_title * 1.6
    sub = "Data: NASA / JPL Horizons"
    if len(mlist) == 1:
        d0 = M.date_from_jd(M.jd_from_date(mlist[0]['launch']))
        sub = f"Launched {d0.day} {d0.strftime('%b %Y')}  ·  " + sub
    sub_cap = max(floor, cap * 0.9)
    polys, box = vector_text(sub, pw / 2, y + cap_title * 0.5, sub_cap, anchor='center')
    add('labels', pen, polys)
    lab.reserve(box)
    top_used = box[1] - 2.0

    # ---- legend (bottom) for several missions
    bottom_used = m
    if len(sel) > 1:
        x = m
        yl = m + 0.5
        for mi, p in sel:
            seg_len = 8.0
            add('trajectory', p, _styled_path(np.array([[x, yl + cap * 0.4], [x + seg_len, yl + cap * 0.4]]),
                                              opts.get('path_style', 'Dashed'), line_mm))
            polys, box = vector_text(mi['name'], x + seg_len + 1.5, yl, cap)
            add('labels', p, polys)
            lab.reserve((x, yl - 0.5, box[2], box[3] + 0.5))
            x = box[2] + 5.0
            if x > pw - 30 and mi is not sel[-1][0]:
                x, yl = m, yl + cap * 2.0
        bottom_used = yl + cap + 3.0

    # ---- diagram geometry
    shown, visited = _auto_planets(mlist)
    orbits_au = {p: M.planet_orbit(p) for p in shown}
    r_frame = max(np.hypot(*o.T).max() for o in orbits_au.values())
    escapes = any(mi.get('escapes') for mi in mlist)
    r_clip_scaled = f_scale(r_frame) * (1.13 if escapes else 1.02)
    avail_h = top_used - bottom_used
    avail_w = pw - 2 * m
    radius_mm = 0.5 * min(avail_w, avail_h) - max(4.0, 2.2 * cap)
    k = radius_mm / r_clip_scaled
    cx, cy = pw / 2, bottom_used + avail_h / 2
    L = _Layout(cx, cy, k)
    centre = np.array([cx, cy])

    # ---- asteroid belt (stipple), drawn first so labels can avoid nothing; masks applied later
    if opts.get('belt', True) and 'Jupiter' in shown:
        r_in, r_out = L.radius_mm(M.ASTEROID_BELT_AU[0]), L.radius_mm(M.ASTEROID_BELT_AU[1])
        add('belt', pen_belt, _belt_dots(centre, r_in, r_out, opts.get('belt_line_mm', line_mm)))

    # ---- orbits
    for p in shown:
        add('orbits', pen_orbits, [L.to_mm(orbits_au[p])])

    # ---- Sun
    star_r = max(1.6, 1.1 * cap)
    add('markers', pen, _star(centre, star_r))
    lab.reserve((cx - star_r, cy - star_r, cx + star_r, cy + star_r))
    if opts.get('labels', True):
        lab.place("SUN", centre, (0, 1), dist=star_r + 1.0, arrow=False)

    # ---- trajectories
    dot_r = max(1.0, 0.75 * cap)
    info = {'missions': []}
    labelled_orbits = set()
    extra = {mi['key']: {'marks': [], 'years': [], 'escape': None} for mi in mlist}   # per-call scratch
    for mi, p in sel:
        jd, xyz = data[mi['key']]
        end_jd = min(jd[-1], M.jd_from_date(mi['end'])) if mi.get('end') else jd[-1]
        keep = jd <= end_jd + 1e-6
        jd_m, xyz_m = jd[keep], xyz[keep]
        rs = f_scale(np.hypot(xyz_m[:, 0], xyz_m[:, 1]))
        clipped = False
        if np.any(rs > r_clip_scaled):
            first_out = int(np.argmax(rs > r_clip_scaled))
            jd_m, xyz_m = jd_m[:first_out + 1], xyz_m[:first_out + 1]
            clipped = True
        path = L.to_mm(xyz_m)
        if clipped:                                  # trim the last point exactly onto the clip circle
            v = path[-1] - centre
            path[-1] = centre + v / np.linalg.norm(v) * (k * r_clip_scaled)
        add('trajectory', p, _styled_path(path, opts.get('path_style', 'Dashed'), line_mm))
        # labels must not sit on the trajectory line itself
        samples, _ = _resample(path, 2.0)
        for q in samples:
            lab.reserve((q[0] - 0.7, q[1] - 0.7, q[0] + 0.7, q[1] + 0.7))
        if clipped and mi.get('escapes'):
            tail = path[-1] - path[max(0, len(path) - 4)]
            add('trajectory', p, _arrowhead(path[-1] + tail / (np.linalg.norm(tail) or 1) * 0.1, tail, 2.2))
            e = path[-1]
            lab.reserve((e[0] - 2.5, e[1] - 2.5, e[0] + 2.5, e[1] + 2.5))
        info['missions'].append({'name': mi['name'], 'points': len(path), 'clipped': clipped})

        # launch (Earth)
        start = path[0]
        add('markers', pen, _filled_dot(start, dot_r * 0.9, line_mm))
        lab.reserve((start[0] - dot_r, start[1] - dot_r, start[0] + dot_r, start[1] + dot_r))

        # flybys - marker on the spacecraft's own position at the event date
        for date, body, text in mi['events']:
            j = M.jd_from_date(date)
            pos = M.position_at(jd_m, xyz_m, j)
            if pos is None:
                continue
            pm = L.to_mm(pos)[0]
            r_dot = dot_r if body and body not in ('Earth',) else dot_r * 0.8
            add('markers', p if body is None else pen, _filled_dot(pm, r_dot if body else 0.6 * dot_r, line_mm))
            lab.reserve((pm[0] - r_dot, pm[1] - r_dot, pm[0] + r_dot, pm[1] + r_dot))
            extra[mi['key']]['marks'].append((pm, body, text, date))

        # year marks
        if opts.get('year_marks', True):
            n_years = (jd_m[-1] - jd_m[0]) / 365.25
            step = _years_label_step(n_years)
            launch_jd = M.jd_from_date(mi['launch'])
            for yr in range(step, int(n_years) + 1, step):
                pos = M.position_at(jd_m, xyz_m, launch_jd + yr * 365.25)
                if pos is None:
                    continue
                pm = L.to_mm(pos)[0]
                yr_r = max(0.45, 0.35 * cap)
                add('markers', p, _filled_dot(pm, yr_r, line_mm))
                lab.reserve((pm[0] - yr_r - 0.4, pm[1] - yr_r - 0.4, pm[0] + yr_r + 0.4, pm[1] + yr_r + 0.4))
                extra[mi['key']]['years'].append((pm, yr))

        # escape label
        if clipped and mi.get('escapes') and opts.get('labels', True):
            extra[mi['key']]['escape'] = path[-1]

    # ---- labels: flybys first (most important), then years, escape, launch, orbits
    if opts.get('labels', True):
        multi = len(sel) > 1
        for mi, p in sel:
            for pm, body, text, date in extra[mi['key']]['marks']:
                lab.place(f"{text.upper()} {date[:4]}", pm, pm - centre, dist=dot_r + 1.0)
        for mi, p in sel:
            jd, xyz = data[mi['key']]
            start = L.to_mm(xyz[0])[0]
            lab.place("EARTH" + (f" {mi['launch'][:4]}" if multi else ""), start, start - centre,
                      dist=dot_r + 1.0)
        for mi, p in sel:
            for pm, yr in extra[mi['key']]['years']:
                lab.place(f"{yr} YR", pm, pm - centre, dist=2.5)
            esc = extra[mi['key']]['escape']
            if esc is not None:
                lab.place("ESCAPES SOLAR SYSTEM" if not multi else f"{mi['name'].upper()} ESCAPES",
                          esc, esc - centre, dist=3.0)
        # orbits of planets nobody visited (e.g. MARS ORBIT) - label the ring itself
        for pname in shown:
            if pname in visited or pname in labelled_orbits:
                continue
            ring = L.to_mm(orbits_au[pname])
            # point on the ring furthest from all trajectories, preferring the lower half like the classic charts
            cand = ring[::12]
            tr = np.vstack([s for (lay, _, s) in segs if lay == 'trajectory']) if any(
                lay == 'trajectory' for lay, _, _ in segs) else np.zeros((1, 2))
            dmin = np.array([np.min(np.hypot(*(tr - c).T)) for c in cand])
            score = dmin - 0.15 * (cand[:, 1] - cy)
            target = cand[int(np.argmax(score))]
            if lab.place(f"{pname.upper()} ORBIT", target, target - centre, dist=2.5):
                labelled_orbits.add(pname)
        if opts.get('belt', True) and 'Jupiter' in shown:
            target = centre + np.array([0.0, -1.0]) * L.radius_mm(M.ASTEROID_BELT_AU[1])
            lab.place("ASTEROIDS", target, (0, -1), dist=2.0)
        add('labels', pen, lab.lines)

    shift = np.array([0.0, -ph / 2.0])
    out = [{'type': 'border', 'layer': layer, 'seg': poly + shift, 'level': 0, 'pen_id': pid}
           for layer, pid, poly in segs]
    masks = [(b[0], b[1] - ph / 2, b[2], b[3] - ph / 2) for b in lab.masks]
    return out, masks, info


def _belt_dots(centre, r_in, r_out, line_mm):
    """Stippled ring: dart-throwing with a minimum spacing, denser in the middle of the belt."""
    rng = np.random.default_rng(7)
    spacing = max(0.75, 2.2 * line_mm)
    area = np.pi * (r_out ** 2 - r_in ** 2)
    n_try = int(area / spacing ** 2 * 3.0)
    cell = spacing / np.sqrt(2)
    grid = {}
    out = []
    width = r_out - r_in
    for _ in range(n_try):
        r = np.sqrt(rng.uniform(r_in ** 2, r_out ** 2))
        a = rng.uniform(0, 2 * np.pi)
        # density profile: keep fewer dots near the edges
        t = (r - r_in) / width
        if rng.uniform() > 0.35 + 0.65 * np.sin(np.pi * t):
            continue
        p = centre + r * np.array([np.cos(a), np.sin(a)])
        gx, gy = int(p[0] // cell), int(p[1] // cell)
        ok = True
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                q = grid.get((gx + dx, gy + dy))
                if q is not None and np.hypot(*(q - p)) < spacing:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            grid[(gx, gy)] = p
            out.append(_circle(p, max(0.08, 0.2 * line_mm), 5))
    return out
