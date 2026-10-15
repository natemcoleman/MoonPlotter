"""G-code assembly, estimation and checks (no Qt - unit-testable).

Pipeline per pen: plot lines -> temporary SVG (matplotlib, true paper size) -> vpype (simplify, merge,
filter, sort) -> gcodemm body -> this module wraps it with the Ender pen-plotter header, pen lifts,
feed rates, optional settle dwell and pen-change pauses.
"""
import numpy as np

from config import (GCODE_OFFSET_MM, BED_MM, DEFAULT_DRAW_FEED, TRAVEL_FEED, PEN_LIFT_MM, MIN_SEGMENT_MM,
                    PEN_DOWN_ABOVE_HOME_MM, SAFE_Z_MM)

HOME_XYZ = (-GCODE_OFFSET_MM[0], -GCODE_OFFSET_MM[1], -PEN_DOWN_ABOVE_HOME_MM)


def vpype_command(svg_path, gcode_path, has_dots=False):
    """vpype pipeline. has_dots=True ("fine" mode) only re-orders strokes: used for stipple dots and for
    lettering / map furniture, whose short strokes must not be simplified, merged or length-filtered."""
    if has_dots:
        return f'read "{svg_path}" linesort gwrite --profile gcodemm "{gcode_path}"'
    return (f'read "{svg_path}" linesimplify -t 0.1mm linemerge -t 0.3mm '
            f'filter --min-length {MIN_SEGMENT_MM:g}mm linesort gwrite --profile gcodemm "{gcode_path}"')


def header(title, lift_mm=PEN_LIFT_MM):
    """Home, set the plot origin, then climb to a safe height BEFORE moving over the plate, go to the
    start point (0, 0) and drop to pen-up height. The first stroke lowers the pen from there."""
    return [f"; --- {title} ---", "G21", "G90", "G28",
            f"G92 X{HOME_XYZ[0]:.1f} Y{HOME_XYZ[1]:.1f} Z{HOME_XYZ[2]:.1f}",
            f"G0 Z{SAFE_Z_MM:.1f} F1500", "M400",                 # clear the plate edge
            "G0 X0 Y0 F3000", "M400",                             # over the start point
            f"G0 Z{max(lift_mm, 0.0):.1f} F1500", "M400"]          # down to pen-up height


def footer(message):
    return [f"G0 Z{SAFE_Z_MM:.1f} F1500", "M400", "; --- END OF PLOT ---", "G0 X0 Y0 F3000",
            "G0 Z25.0 F1500", f"M117 {message}", "M84"]


def pen_change(pen_name, lift_mm=PEN_LIFT_MM):
    """Climb, park, raise and pause for a pen swap (Marlin M0 shows the message and waits for a click)."""
    return [f"; --- PEN CHANGE: {pen_name} ---", f"G0 Z{SAFE_Z_MM:.1f} F1500", "M400",
            "G0 X0 Y0 F3000", "G0 Z25.0 F1500", "M400",
            f"M117 Load {pen_name}"[:40], f"M0 Load {pen_name}, then click"[:60],
            f"G0 Z{SAFE_Z_MM:.1f} F1500", "M400", f"G0 Z{lift_mm:.1f} F1500", "M400"]


def wrap_vpype_body(vpype_lines, draw_feed=DEFAULT_DRAW_FEED, travel_feed=TRAVEL_FEED, settle_ms=0,
                    lift_mm=PEN_LIFT_MM):
    """Turns vpype gcodemm output into pen-plotter moves: every travel is lift -> move -> lower."""
    out = []
    for line in vpype_lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("G0 X") or line.startswith("G00 X"):
            out.extend([f"G0 Z{lift_mm:.1f} F1500", "M400", f"{line} F{travel_feed:.0f}", "M400",
                        "G1 Z0.0 F1000", "M400"])
            if settle_ms > 0:
                out.append(f"G4 P{int(settle_ms)}")          # let the ink start flowing before moving
        elif line.startswith("G1 X") or line.startswith("G01 X"):
            out.append(f"{line} F{draw_feed:.0f}")
    return out


def estimate_minutes(gcode_lines, accel_factor=1.15):
    """Distance / feed for every G0/G1 plus G4 dwells, times an acceleration allowance."""
    total = 0.0
    cx, cy, cz = HOME_XYZ
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
            d = np.sqrt((nx - cx) ** 2 + (ny - cy) ** 2 + (nz - cz) ** 2)
            if cf > 0:
                total += d / cf
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
