"""G-code assembly, estimation and checks (no Qt - unit-testable).

Pipeline per pen: plot lines -> temporary SVG (matplotlib, true paper size) -> vpype (simplify, merge,
filter, sort) -> gcodemm body -> this module wraps it with the Ender pen-plotter header, pen lifts,
feed rates, optional settle dwell and pen-change pauses.
"""
import numpy as np

import config as _cfg            # calibrated, per-machine values are read from here at run time
from config import (GCODE_OFFSET_MM, BED_MM, DEFAULT_DRAW_FEED, TRAVEL_FEED, PEN_LIFT_MM, MIN_SEGMENT_MM,
                    PEN_DOWN_ABOVE_HOME_MM, SAFE_Z_MM, CUTTER_Z_OFFSET_MM, TOOL_PEN_IDS,
                    CUTTER_BLADE_OFFSET_MM, CUTTER_CORNER_LOOP_MM, CUTOUT_OVERLAP_MM, CUTOUT_PASSES)

HOME_XYZ = (-GCODE_OFFSET_MM[0], -GCODE_OFFSET_MM[1], -PEN_DOWN_ABOVE_HOME_MM)     # defaults (see home_xyz)


def home_xyz(pen_down_above_home=None):
    """Where G92 declares the homed head to be: X/Y plot offset and Z = -(pen-down height above home)."""
    h = _cfg.PEN_DOWN_ABOVE_HOME_MM if pen_down_above_home is None else pen_down_above_home
    return -GCODE_OFFSET_MM[0], -GCODE_OFFSET_MM[1], -float(h)


def _lift(lift_mm):
    return float(_cfg.PEN_LIFT_MM if lift_mm is None else lift_mm)


def tool_down_z(pen_id):
    """Z of the tool tip when 'down': 0 (the pen plane) for pens, CUTTER_Z_OFFSET_MM for the drag knife."""
    return float(_cfg.CUTTER_Z_OFFSET_MM) if pen_id in TOOL_PEN_IDS else 0.0


def _z(v):
    """Z word value: one decimal when that is exact (keeps 'Z0.0'), else two."""
    return f"{v:.1f}" if abs(v * 10 - round(v * 10)) < 1e-9 else f"{v:.2f}"


def has_drawing_moves(gcode_lines):
    """True if a G-code body contains at least one pen-down drawing move (G1 with X/Y)."""
    return any(l.startswith(("G1 X", "G01 X", "G1 Y", "G01 Y")) for l in gcode_lines)


def vpype_command(svg_path, gcode_path, has_dots=False, no_flip=False):
    """vpype pipeline. has_dots=True ("fine" mode) only re-orders strokes: used for stipple dots and for
    lettering / map furniture, whose short strokes must not be simplified, merged or length-filtered.
    no_flip=True keeps every stroke's drawing direction (one-way sine squiggles: draw, lift, return).
    Otherwise 'reloop' moves the start / end of every closed curve to a random point, so the seams of
    nested contours don't line up into a visible streak."""
    if has_dots:
        return f'read "{svg_path}" linesort gwrite --profile gcodemm "{gcode_path}"'
    if no_flip:
        return (f'read "{svg_path}" linemerge --no-flip -t 0.3mm '
                f'filter --min-length {MIN_SEGMENT_MM:g}mm linesort --no-flip gwrite --profile gcodemm "{gcode_path}"')
    # return (f'read "{svg_path}" linesimplify -t 0.1mm linemerge -t 0.3mm '
    #         f'filter --min-length {MIN_SEGMENT_MM:g}mm linesort gwrite --profile gcodemm "{gcode_path}"')
    return (f'read "{svg_path}" linemerge -t 0.3mm reloop '
            f'filter --min-length {MIN_SEGMENT_MM:g}mm linesort gwrite --profile gcodemm "{gcode_path}"')


def header(title, lift_mm=None, pen_down_above_home=None):
    """Home, set the plot origin, then climb to a safe height BEFORE moving over the plate, go to the
    start point (0, 0) and drop to pen-up height. The first stroke lowers the pen from there."""
    lift_mm = _lift(lift_mm)
    hx, hy, hz = home_xyz(pen_down_above_home)
    return [f"; --- {title} ---", "G21", "G90", "G28",
            f"G92 X{hx:.1f} Y{hy:.1f} Z{_z(hz)}",
            f"G0 Z{SAFE_Z_MM:.1f} F1500", "M400",                 # clear the plate edge
            "G0 X0 Y0 F3000", "M400",                             # over the start point
            f"G0 Z{_z(max(lift_mm, 0.0))} F1500", "M400"]          # down to pen-up height


def footer(message):
    return [f"G0 Z{SAFE_Z_MM:.1f} F1500", "M400", "; --- END OF PLOT ---", "G0 X0 Y0 F3000",
            "G0 Z25.0 F1500", f"M117 {message}", "M84"]


def pen_change(pen_name, lift_mm=None):
    """Climb, park, raise and pause for a pen swap (Marlin M0 shows the message and waits for a click)."""
    lift_mm = _lift(lift_mm)
    return [f"; --- PEN CHANGE: {pen_name} ---", f"G0 Z{SAFE_Z_MM:.1f} F1500", "M400",
            "G0 X0 Y0 F3000", "G0 Z25.0 F1500", "M400",
            f"M117 Load {pen_name}"[:40], f"M0 Load {pen_name}, then click"[:60],
            f"G0 Z{SAFE_Z_MM:.1f} F1500", "M400", f"G0 Z{_z(lift_mm)} F1500", "M400"]


def wrap_vpype_body(vpype_lines, draw_feed=DEFAULT_DRAW_FEED, travel_feed=TRAVEL_FEED, settle_ms=0,
                    lift_mm=None, down_z=0.0):
    """Turns vpype gcodemm output into pen-plotter moves: every travel is lift -> move -> lower.
    down_z is the tool-down height (0 for pens, tool_down_z('cutter') for the drag knife)."""
    lift_mm = _lift(lift_mm)
    out = []
    for line in vpype_lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("G0 X") or line.startswith("G00 X"):
            out.extend([f"G0 Z{_z(lift_mm)} F1500", "M400", f"{line} F{travel_feed:.0f}", "M400",
                        f"G1 Z{_z(down_z)} F1000", "M400"])
            if settle_ms > 0:
                out.append(f"G4 P{int(settle_ms)}")          # let the ink start flowing before moving
        elif line.startswith("G1 X") or line.startswith("G01 X"):
            out.append(f"{line} F{draw_feed:.0f}")
    return out


def cutout_path(pw, ph, loop_mm=CUTTER_CORNER_LOOP_MM, blade_offset_mm=CUTTER_BLADE_OFFSET_MM,
                overlap_mm=CUTOUT_OVERLAP_MM, passes=CUTOUT_PASSES, arc_steps=24):
    """Drag-knife path (plot mm, origin bottom-left) around the sheet's outer edge 0..pw x 0..ph.

    Starts mid-way along the bottom edge going right, then anticlockwise. At each corner C (arriving
    along a, leaving along b) the knife goes on to C + d*a, swings clockwise 270 deg round a circle of
    radius d centred at C + d*(a - b) - entirely outside the card - and arrives at C - d*b heading
    along b. The trailing blade has turned before it reaches the corner, so both edges are cut through
    the corner. Ends overlap_mm past the start."""
    d = max(float(loop_mm), 3.0 * float(blade_offset_mm), 0.1)
    corners = [(pw, 0.0), (pw, ph), (0.0, ph), (0.0, 0.0)]
    dirs = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)]          # edge leaving corner i-1 ...
    pts = [(pw / 2.0, 0.0)]
    for _ in range(max(1, int(passes))):
        for i, (cx, cy) in enumerate(corners):
            ax, ay = dirs[i]                       # arriving direction
            bx, by = dirs[(i + 1) % 4]             # leaving direction
            qx, qy = cx + d * (ax - bx), cy + d * (ay - by)
            th0 = np.arctan2(by, bx)               # P1 = Q + d*b
            for k in range(arc_steps + 1):         # clockwise 270 deg: P1 -> Q - d*a = C - d*b
                th = th0 - 1.5 * np.pi * k / arc_steps
                pts.append((qx + d * np.cos(th), qy + d * np.sin(th)))
        pts.append((pw / 2.0, 0.0))                # back to the start of the bottom edge
    pts.append((min(pw, pw / 2.0 + max(0.0, overlap_mm)), 0.0))
    out = [pts[0]]
    for p in pts[1:]:
        if np.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-6:
            out.append(p)
    return np.array(out)


def cutout_body(pw, ph, feed, travel_feed=TRAVEL_FEED, lift_mm=None, down_z=None, **kw):
    """G-code body (no header) that cuts the card outline with the drag knife."""
    lift_mm = _lift(lift_mm)
    if down_z is None:
        down_z = tool_down_z('cutter')
    p = cutout_path(pw, ph, **kw)
    out = ["; --- CARD CUT-OUT (drag knife) ---", f"G0 Z{_z(lift_mm)} F1500", "M400",
           f"G0 X{p[0, 0]:.4f} Y{p[0, 1]:.4f} F{travel_feed:.0f}", "M400", f"G1 Z{_z(down_z)} F1000", "M400"]
    out += [f"G1 X{x:.4f} Y{y:.4f} F{feed:.0f}" for x, y in p[1:]]
    out += [f"G0 Z{_z(lift_mm)} F1500", "M400"]
    return out


def estimate_minutes(gcode_lines, accel_factor=1.15):
    """Distance / feed for every G0/G1 plus G4 dwells, times an acceleration allowance. Z moves are
    capped at the firmware's Z speed (config.Z_MAX_FEED) - on an Ender 3 the pen lifts dominate."""
    total = 0.0
    cx, cy, cz = home_xyz()
    zmax = float(getattr(_cfg, 'Z_MAX_FEED', 300.0))
    cf = 1500.0
    for line in gcode_lines:
        line = line.split(';')[0].strip()
        if not line:
            continue
        parts = line.split()
        cmd = parts[0]
        if cmd in ('G0', 'G00', 'G1', 'G01'):
            nx, ny, nz = cx, cy, cz
            for p in parts[1:]:
                try:
                    if p[0] == 'X': nx = float(p[1:])
                    elif p[0] == 'Y': ny = float(p[1:])
                    elif p[0] == 'Z': nz = float(p[1:])
                    elif p[0] == 'F': cf = float(p[1:])
                except ValueError:
                    pass
            dxy = np.hypot(nx - cx, ny - cy)
            dz = abs(nz - cz)
            if cf > 0:
                t = np.hypot(dxy, dz) / cf
                if dz > 0 and zmax > 0:
                    t = max(t, dz / min(cf, zmax))         # the Z axis can't go faster than its limit
                total += t
            cx, cy, cz = nx, ny, nz
        elif cmd == 'G4':
            for p in parts[1:]:
                if p[0] == 'P':
                    total += float(p[1:]) / 60000.0
                elif p[0] == 'S':
                    total += float(p[1:]) / 60.0
    return total * accel_factor


def xy_bounds(gcode_lines):
    """(xmin, xmax, ymin, ymax) of all X/Y coordinates written, or None."""
    xs, ys = [], []
    for line in gcode_lines:
        line = line.split(';')[0]
        for p in line.split():
            try:
                if p[0] == 'X': xs.append(float(p[1:]))
                elif p[0] == 'Y': ys.append(float(p[1:]))
            except (ValueError, IndexError):
                pass
    if not xs or not ys:
        return None
    return min(xs), max(xs), min(ys), max(ys)


def bed_violation(bounds, bed=BED_MM, offset=GCODE_OFFSET_MM):
    """Human-readable problem string if the plot leaves the reachable bed area, else None."""
    if bounds is None:
        return None
    xmin, xmax, ymin, ymax = bounds
    max_x, max_y = bed[0] - offset[0], bed[1] - offset[1]
    problems = []
    if xmax > max_x + 1e-6: problems.append(f"X reaches {xmax:.1f} mm (limit {max_x:.0f})")
    if ymax > max_y + 1e-6: problems.append(f"Y reaches {ymax:.1f} mm (limit {max_y:.0f})")
    if xmin < -offset[0] - 1e-6: problems.append(f"X goes to {xmin:.1f} mm (limit {-offset[0]:.0f})")
    if ymin < -offset[1] - 1e-6: problems.append(f"Y goes to {ymin:.1f} mm (limit {-offset[1]:.0f})")
    return "; ".join(problems) if problems else None


def max_plot_area():
    return BED_MM[0] - GCODE_OFFSET_MM[0], BED_MM[1] - GCODE_OFFSET_MM[1]


def split_for_priming(lines, lengths_mm, count):
    """Returns (prime, rest): the `count` longest strokes are drawn first so the pen is flowing
    before fine detail starts."""
    if count <= 0 or len(lines) <= count:
        return [], list(lines)
    order = np.argsort(np.asarray(lengths_mm))[::-1]
    prime_idx = set(order[:count].tolist())
    prime = [lines[i] for i in sorted(prime_idx)]
    rest = [ln for i, ln in enumerate(lines) if i not in prime_idx]
    return prime, rest


def fmt_duration(minutes):
    h = int(minutes // 60)
    m = int(minutes % 60)
    s = int((minutes * 60) % 60)
    return f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s"
