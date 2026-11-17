"""Engine tests (no GUI, no data files). Run from the project folder:

    python -m pytest tests          (if pytest is installed)
    python tests/test_engines.py    (plain runner)
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exporter                                   # noqa: E402
import hatch_engine as he                         # noqa: E402
from calibration import build_calibration_sheet   # noqa: E402
from config import default_pens, MIN_SEGMENT_MM   # noqa: E402
from contour_engine import auto_contour_interval, generate_contours   # noqa: E402
from decorations import build_decorations, vector_text   # noqa: E402
import project_io                                 # noqa: E402
import regions                                    # noqa: E402
from sites import site_segments, SITES            # noqa: E402

PENS = default_pens()


def _params(**kw):
    p = {'active_planet': 'Moon', 'selected_region': 'None (Plot Full Selection)', 'circle_mode': False,
         'paper_w_mm': 100.0, 'paper_h_mm': 100.0, 'pens': PENS, 'hatch_spacing_mm': 1.0, 'auto_contrast': True,
         'tone_gamma': 1.0, 'join_lines': True, 'fill_inks': 'mono', 'mono_pen': 'pen_1',
         'hatch_strategy': 'Multi-Pass Crosshatch', 'interval_ft': 1000, 'simp_tol': 0.0, 'smooth_val': 0.0,
         'show_borders': False}
    p.update(kw)
    return p


def _gradient_rgb(h=200, w=200):
    g = np.tile(np.linspace(0.0, 1.0, w, dtype=np.float32), (h, 1))
    return np.repeat(g[..., None], 3, axis=2)


BOUNDS = {'lon0': 0.0, 'lon_span': 10.0, 'lat_top': 10.0, 'lat_bot': 0.0}
EXTENT = [0.0, 10.0, 0.0, 10.0]


# ---------------------------------------------------------------- contours
def test_auto_interval():
    from config import NICE_INTERVALS_FT
    for relief, n in ((30000, 20), (30000, 40), (1200, 20)):
        step = auto_contour_interval(relief, target_lines=n)
        assert step in NICE_INTERVALS_FT and relief / step <= n
        smaller = [s for s in NICE_INTERVALS_FT if s < step]
        assert not smaller or relief / max(smaller) > n      # the smallest interval that fits
    assert auto_contour_interval(0) is None


def test_contours_cone():
    y, x = np.mgrid[-1:1:200j, -1:1:200j]
    dem = (1.0 - np.hypot(x, y)).astype(np.float32) * 3.0          # km on the Moon -> ft via multiplier
    segs = generate_contours(dem, EXTENT, EXTENT, _params(interval_ft=1000))
    assert segs and all(s['layer'] == 'contours' for s in segs)
    levels = sorted({s['level'] for s in segs})
    assert all(abs(l % 1000) < 1e-6 for l in levels)


def test_contour_pens_follow_choice():
    y, x = np.mgrid[-1:1:200j, -1:1:200j]
    dem = (1.0 - np.hypot(x, y)).astype(np.float32) * 3.0
    segs = generate_contours(dem, EXTENT, EXTENT, _params(interval_ft=1000, contour_pen='white_gr05',
                                                          index_pen='white_gr10'))
    pens = {s['pen_id'] for s in segs}
    assert pens <= {'white_gr05', 'white_gr10'} and 'white_gr05' in pens
    idx = {s['level'] for s in segs if s['pen_id'] == 'white_gr10'}
    assert idx and all(abs(l % 5000) < 1e-6 for l in idx)      # every 5th level is an index line


# ---------------------------------------------------------------- hatching
def test_hatch_patterns_produce_strokes():
    for strat in he.STRATEGIES[:4]:
        segs = he.generate_hatches(_gradient_rgb(), BOUNDS, EXTENT, _params(hatch_strategy=strat))
        assert segs, strat
        assert all(s['layer'] == 'fill' for s in segs)


def test_dark_side_gets_more_ink():
    segs = he.generate_hatches(_gradient_rgb(), BOUNDS, EXTENT, _params(hatch_strategy="Variable Spacing Scanlines",
                                                                          join_lines=False))
    left = right = 0.0
    for s in segs:
        p = s['seg']
        L = np.sum(np.hypot(*np.diff(p, axis=0).T))
        if p[:, 0].mean() < 5: left += L
        else: right += L
    assert left > right * 1.5          # black on the left of the gradient


def test_join_reduces_strokes():
    a = he.generate_hatches(_gradient_rgb(), BOUNDS, EXTENT, _params(join_lines=False))
    b = he.generate_hatches(_gradient_rgb(), BOUNDS, EXTENT, _params(join_lines=True))
    assert len(b) < len(a)


def test_min_segment_length():
    segs = he.generate_hatches(_gradient_rgb(), BOUNDS, EXTENT, _params(join_lines=False))
    mm_per_unit = 100.0 / 10.0
    for s in segs:
        assert np.sum(np.hypot(*np.diff(s['seg'], axis=0).T)) * mm_per_unit >= MIN_SEGMENT_MM - 0.3


def test_tone_curve():
    d = np.linspace(0, 1, 11)
    assert np.all(he.apply_tone_curve(d, 2.0) >= d - 1e-9)
    assert np.all(he.apply_tone_curve(d, 0.5) <= d + 1e-9)


def test_blue_noise_is_permutation():
    bn = he.blue_noise()
    ranks = np.sort(((bn * bn.size) - 0.5).round().astype(int).ravel())
    assert np.array_equal(ranks, np.arange(bn.size))


def test_hachures():
    y, x = np.mgrid[0:200, 0:200]
    dem = (x * 5.0).astype(np.float32)                         # plane rising to the east
    segs = he.generate_hatches(None, None, EXTENT, _params(hatch_strategy="Hachures (Slope)"),
                               elev=(dem, BOUNDS))
    assert segs
    d = np.array([s['seg'][-1] - s['seg'][0] for s in segs])
    assert np.mean(np.abs(d[:, 0])) > 5 * np.mean(np.abs(d[:, 1]))   # strokes run along the slope (E-W)


# ---------------------------------------------------------------- exporter
def test_wrap_and_estimate():
    body = ["G00 X0.0000 Y0.0000", "G01 X10.0000 Y0.0000", "G00 X20.0000 Y0.0000", "G01 X20.0000 Y10.0000"]
    g = exporter.header("t") + exporter.wrap_vpype_body(body, 1500, 3000, settle_ms=100) + exporter.footer("done")
    assert sum(1 for l in g if l.startswith("G4 P100")) == 2
    assert sum(1 for l in g if l.startswith("G1 Z0.0")) == 2
    m = exporter.estimate_minutes(g)
    assert 0.0 < m < 1.0
    assert exporter.xy_bounds(g)[1] == 20.0


def test_colour_fill_uses_only_ticked_pens():
    ids = ['yellow_07', 'blue_07', 'red_07', 'pen_1']
    img = np.zeros((60, 60, 3), np.float32)
    img[:, :20] = (0.1, 0.6, 0.2)          # green
    img[:, 20:40] = (0.9, 0.8, 0.1)        # yellow
    img[:, 40:] = (0.1, 0.1, 0.6)          # dark blue
    segs = he.generate_hatches(img, BOUNDS, EXTENT, _params(fill_inks='color', fill_pens=ids))
    used = {s['pen_id'] for s in segs}
    assert used and used <= set(ids)
    assert {'yellow_07', 'blue_07'} <= used                  # green = yellow + blue here


def test_green_pen_used_directly():
    img = np.zeros((40, 40, 3), np.float32)
    img[:] = (0.09, 0.58, 0.29)
    segs = he.generate_hatches(img, BOUNDS, EXTENT, _params(fill_inks='color',
                                                            fill_pens=['green_07', 'yellow_07', 'blue_07']))
    n = {}
    for s in segs:
        n[s['pen_id']] = n.get(s['pen_id'], 0) + 1
    assert max(n, key=n.get) == 'green_07'


def test_duplicate_black_dropped():
    img = np.tile(np.linspace(0, 1, 50, dtype=np.float32)[None, :, None], (50, 1, 3))
    segs = he.generate_hatches(img, BOUNDS, EXTENT, _params(fill_inks='color', fill_pens=['base', 'pen_1', 'red_07']))
    assert 'base' not in {s['pen_id'] for s in segs}         # same ink as pen_1 (wider) -> skipped


def test_white_gel_on_black_paper():
    """On black paper the LIGHT parts of the image need the most white ink."""
    img = np.tile(np.linspace(0, 1, 60, dtype=np.float32)[None, :, None], (60, 1, 3))   # dark -> light
    segs = he.generate_hatches(img, BOUNDS, EXTENT, _params(fill_inks='mono', mono_pen='white_gr08',
                                                            paper_color='#1C1C1C', join_lines=False))
    assert segs and {s['pen_id'] for s in segs} == {'white_gr08'}
    left = sum(np.sum(np.hypot(*np.diff(s['seg'], axis=0).T)) for s in segs if s['seg'][:, 0].mean() < 5)
    right = sum(np.sum(np.hypot(*np.diff(s['seg'], axis=0).T)) for s in segs if s['seg'][:, 0].mean() >= 5)
    assert right > left * 1.5


def test_opaque_ink_model():
    black = he.srgb_to_linear(he.hex_to_srgb('#000000'))
    white = he.srgb_to_linear(he.hex_to_srgb('#FFFFFF'))
    assert np.allclose(he.lay_ink(black, 1.0, white, 0.0), 0.0)        # see-through ink can't lighten
    assert he.lay_ink(black, 1.0, white, 0.85).mean() > 0.8             # opaque white covers black


def test_safe_start_sequence():
    """After homing: climb to SAFE_Z before any XY move, go to (0, 0), then drop to pen-up height."""
    from config import SAFE_Z_MM, PEN_LIFT_MM, PEN_DOWN_ABOVE_HOME_MM
    h = exporter.header("t")
    i_g92 = next(i for i, l in enumerate(h) if l.startswith("G92"))
    assert f"Z{exporter._z(-PEN_DOWN_ABOVE_HOME_MM)}" in h[i_g92]
    moves = [l for l in h[i_g92 + 1:] if l.startswith("G0")]
    assert moves[0] == f"G0 Z{SAFE_Z_MM:.1f} F1500"
    assert moves[1].startswith("G0 X0 Y0")
    assert moves[2] == f"G0 Z{exporter._z(PEN_LIFT_MM)} F1500"
    for block in (exporter.footer("x"), exporter.pen_change("Pen")):
        first_xy = next(i for i, l in enumerate(block) if " X" in l)
        assert any(l.startswith(f"G0 Z{SAFE_Z_MM:.1f}") for l in block[:first_xy])


def test_custom_lift_height():
    body = ["G00 X5.0 Y5.0", "G01 X9.0 Y5.0"]
    g = exporter.wrap_vpype_body(body, lift_mm=4.5)
    assert g[0] == "G0 Z4.5 F1500" and "G1 Z0.0 F1000" in g
    assert exporter.header("t", lift_mm=4.5)[-2] == "G0 Z4.5 F1500"
    assert "G0 Z4.5 F1500" in exporter.pen_change("P", lift_mm=4.5)


def test_bed_violation():
    assert exporter.bed_violation((0, 100, 0, 100)) is None
    assert "X reaches" in exporter.bed_violation((0, 300, 0, 100))


def test_priming_split():
    lines = [np.zeros((2, 2))] * 5
    prime, rest = exporter.split_for_priming(lines, [1, 5, 2, 9, 3], 2)
    assert len(prime) == 2 and len(rest) == 3


# ---------------------------------------------------------------- decorations / calibration / sites
def test_vector_text_and_decorations():
    polys, box = vector_text("Test", 0, 0, 3.0)
    assert polys and box[2] > box[0]
    segs, masks = build_decorations({'title': True, 'title_text': 'X', 'scale_bar': True, 'north_arrow': True,
                                     'graticule': 'Edge ticks', 'frame': True, 'pen': 'base'},
                                    [-10, 30, 0, 40], 100, 100, False, 'Moon', subtitle='sub')
    assert segs and len(masks) == 4                            # scale bar, title, subtitle, north arrow
    assert all(s['layer'] == 'decor' for s in segs)


def test_single_stroke_text():
    import stroke_font
    here = os.path.dirname(os.path.abspath(__file__))
    for d in ('/tmp/fontcheck', os.path.join(here, '..', 'fonts')):
        if os.path.isdir(d):
            stroke_font.FALLBACK_DIRS.append(d)
    stroke_font._CACHE.clear()
    if not stroke_font.available():
        print("  (Hershey font not found - skipped)")
        return
    polys, box = stroke_font.stroke_text("H", 0, 0, 3.0)
    assert abs((box[3] - box[1]) - 3.0) < 1e-6                 # capital height is exactly what was asked
    assert len(polys) == 3                                      # H = two uprights + crossbar, single strokes
    p2, b2 = stroke_font.stroke_text("10\u00b0N", 5, 5, 2.0, anchor='right')
    assert abs(b2[2] - 5) < 0.3 and p2


def test_furniture_text_never_below_pen_minimum():
    from decorations import min_cap_for_pen
    assert min_cap_for_pen(0.7) > min_cap_for_pen(0.38) >= 1.5
    segs, masks = build_decorations({'title': True, 'title_text': 'A very long map title here', 'scale_bar': True,
                                     'north_arrow': True, 'graticule': 'Edge ticks', 'frame': True, 'pen': 'base',
                                     'line_mm': 0.7}, [-10, 30, 0, 40], 100, 100, False, 'Moon',
                                    subtitle="MOON  \u00b7  20.00\u00b0N 10.00\u00b0E  \u00b7  4,000 ft contours")
    # every title-block line has its own blanking box, and none of them overlaps the scale bar's
    from decorations import _overlaps
    assert len(masks) >= 4                                     # scale bar + >=2 text lines + north arrow
    assert not any(_overlaps(masks[0], m, gap=0.0) for m in masks[1:-1])


def test_calibration_sheet():
    segs, ext = build_calibration_sheet(PENS, ['base', 'pen_1', 'blue_038'], 100, 100)
    pens = {s['pen_id'] for s in segs}
    assert {'base', 'pen_1', 'blue_038'} <= pens
    allpts = np.vstack([s['seg'] for s in segs])
    assert allpts[:, 0].min() >= 0 and allpts[:, 0].max() <= 100
    assert allpts[:, 1].min() >= -50 and allpts[:, 1].max() <= 50


def test_sites():
    name = SITES['Moon'][0][0]
    segs = site_segments('Moon', {name}, [-10, 40, -10, 40], _params())
    assert segs and all(s['layer'] == 'sites' for s in segs)


# ---------------------------------------------------------------- regions / projects
def test_regions_helpers():
    u = regions.copernicus_tile_url("https://x", 39, -121)
    assert u.endswith("Copernicus_DSM_COG_10_N39_00_W121_00_DEM/Copernicus_DSM_COG_10_N39_00_W121_00_DEM.tif")
    assert regions.copernicus_tile_url("https://x", -5, 7).split('/')[-1] == "Copernicus_DSM_COG_10_S05_00_E007_00_DEM.tif"
    assert abs(regions._coverage((0, 0, 10, 10), [0, 10, 0, 10]) - 1.0) < 1e-9
    assert regions.safe_name("Grand Canyon (AZ)") == "Grand_Canyon_AZ"


def test_xyz_fetch_geometry():
    """Fake tiles encode their global Mercator pixel row; check the lat/lon re-gridding lands on it."""
    z_used = {}

    def fake_tile(url):
        z, y, x = (int(v) for v in url.split('/')[-3:])
        z_used['z'] = z
        rows = (y * 256 + np.arange(256)).astype(np.int64)
        img = np.zeros((256, 256, 3), dtype=np.uint8)
        img[..., 0] = (rows % 256)[:, None]
        img[..., 1] = ((rows // 256) % 256)[:, None]
        img[..., 2] = 7
        return img

    captured = {}
    orig_get, orig_write = regions._get_tile, regions._write
    regions._get_tile = fake_tile
    regions._write = lambda name, arr, bounds, meta, crs_epsg4326=False: captured.update(arr=arr, bounds=bounds,
                                                                                          meta=meta) or "x.tif"
    try:
        src = {'url': "https://t/{z}/{y}/{x}", 'tiles': 'xyz', 'max_zoom': 14, 'kind': 'color',
               'extent': (-180, -85, 180, 85), 'units': '', 'source': 'test'}
        ext = (-120.3, -119.9, 38.9, 39.3)
        regions.fetch_region(src, 'Earth', ext, 'tahoe_test')
    finally:
        regions._get_tile, regions._write = orig_get, orig_write
    arr, z = captured['arr'], z_used['z']
    assert arr.shape[0] == 3 and max(arr.shape[1:]) <= regions.MAX_OUT_PX
    assert np.all(arr[2] == 7)                                   # no blank (unfilled) pixels
    h = arr.shape[1]
    for i in (0, h // 2, h - 1):
        lat = ext[3] - (i + 0.5) / h * (ext[3] - ext[2])
        want = regions._merc_y(lat, z)
        got = int(arr[1, i, 0]) * 256 + int(arr[0, i, 0])
        assert abs((got % 65536) - (int(want) % 65536)) <= 1, (i, got, want)
    assert captured['bounds'] == (ext[0], ext[2], ext[1], ext[3])


def _fixture(hid):
    import missions as MS
    return MS.parse_vectors(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures',
                                              f'horizons_{hid}_coarse.txt')).read())


def test_horizons_parse_and_planet_positions():
    """Real Voyager 2 data (JPL Horizons): planets computed from orbital elements sit on the spacecraft
    path at the flyby dates."""
    import missions as MS
    jd, xyz = _fixture('-32')
    assert len(jd) == 71 and xyz.shape == (71, 3)
    for date, body in (('1979-07-09', 'Jupiter'), ('1981-08-25', 'Saturn'), ('1986-01-24', 'Uranus'),
                       ('1989-08-25', 'Neptune')):
        j = MS.jd_from_date(date)
        sc = MS.position_at(jd, xyz, j)
        pl = np.array(MS.planet_position(body, j))
        assert np.linalg.norm(sc[:2] - pl[:2]) < 0.3, body          # 90-day samples -> small interp error


def test_horizons_span_error_parsing():
    import missions as MS
    msg = 'No ephemeris for target "Pioneer 10 (spacecraft)" prior to A.D. 1972-MAR-03 02:04:42.1854 TDB'
    kind, y, mon, d = MS._SPAN_RE.search(msg).groups()
    assert (kind, y, MS._MON[mon], d) == ('prior to', '1972', 3, '03')
    assert MS.parse_vectors("no data here") == (None, None)


def test_mission_catalogue():
    import missions as MS
    hids = [m['hid'] for m in MS.MISSIONS]
    assert len(hids) == len(set(hids))
    apl_led = {m['name'] for m in MS.MISSIONS if m.get('apl') == 'led'}
    assert {'New Horizons', 'MESSENGER', 'Parker Solar Probe', 'DART', 'NEAR Shoemaker'} <= apl_led
    for m in MS.MISSIONS:
        for date, body, _ in m['events']:
            assert m['launch'] <= date, m['name']
            assert body is None or body in MS.PLANET_ELEMENTS


def test_mission_plot_layout():
    import missions as MS
    from mission_plot import build_mission_plot, _overlaps
    data = {'voyager1': _fixture('-31'), 'voyager2': _fixture('-32'), 'cassini': _fixture('-82')}
    sel = [(MS.MISSION_BY_KEY[k], p) for k, p in (('voyager1', 'red_038'), ('voyager2', 'blue_038'),
                                                   ('cassini', 'orange_07'))]
    segs, masks, info = build_mission_plot(sel, data, {'pen': 'base', 'line_mm': 0.38}, 200, 200)
    layers = {s['layer'] for s in segs}
    assert {'orbits', 'trajectory', 'markers', 'labels', 'belt'} <= layers
    pts = np.vstack([s['seg'] for s in segs])
    assert pts[:, 0].min() >= 0 and pts[:, 0].max() <= 200 and pts[:, 1].min() >= -100 and pts[:, 1].max() <= 100
    for i in range(len(masks)):                                  # labels never overlap each other
        for j in range(i + 1, len(masks)):
            assert not _overlaps(masks[i], masks[j], gap=-1.7), (masks[i], masks[j])   # 0.8 mm pad each
    assert {s['pen_id'] for s in segs if s['layer'] == 'trajectory'} == {'red_038', 'blue_038', 'orange_07'}


def test_project_roundtrip():
    st = {'planet': 'Moon', 'selection': [1.0, 2.0, 3.0, 4.0], 'custom_items': [{'points': np.zeros((2, 2))}],
          'selected_sites': {'Moon': ['Apollo 11']}}
    path = os.path.join(tempfile.gettempdir(), "tps_test.topo.json")
    project_io.save_project(path, st)
    back = project_io.load_project(path)
    assert back['selection'] == [1.0, 2.0, 3.0, 4.0]
    assert back['selected_sites']['Moon'] == ['Apollo 11']


def _logo_rgb():
    """White page with a red ring (with a hole) and a blue square."""
    h, w = 200, 300
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.ones((h, w, 3), np.float32)
    r = np.hypot(xx - 80, yy - 100)
    ring = (r < 60) & (r > 20)
    rgb[ring] = (0.9, 0.1, 0.1)
    sq = (xx > 170) & (xx < 270) & (yy > 50) & (yy < 150)
    rgb[sq] = (0.1, 0.1, 0.8)
    return rgb


def test_image_trace_outlines():
    import image_trace as it
    layers, info = it.trace_image(_logo_rgb(), {'colours': 3, 'smooth_px': 1.0}, 200, 150)
    cols = sorted(tuple(np.round(l['color'], 1)) for l in layers)
    assert len(layers) == 2 and (0.9, 0.1, 0.1) in cols and (0.1, 0.1, 0.8) in cols   # background skipped
    ring = next(l for l in layers if l['color'][0] > 0.5)
    assert len(ring['polys']) == 2                                   # outer edge + the hole
    for l in layers:
        for p in l['polys']:
            assert np.allclose(p[0], p[-1], atol=0.5)                  # closed outlines
            assert p[:, 0].min() >= 0 and p[:, 0].max() <= 200 and p[:, 1].min() >= 0 and p[:, 1].max() <= 150
    # a 2-colour trace puts both shapes into one layer
    layers2, _ = it.trace_image(_logo_rgb(), {'colours': 2}, 200, 150)
    assert len(layers2) == 1 and len(layers2[0]['polys']) == 3


def test_image_trace_fill_and_cutline():
    import image_trace as it
    layers, _ = it.trace_image(_logo_rgb(), {'colours': 3, 'fill': True, 'fill_spacing_mm': 1.0,
                                             'cutline': True, 'cut_offset_mm': 2.0}, 200, 150)
    keys = [l['key'] for l in layers]
    assert keys[-1] == 'img:cut'
    cut = layers[-1]['polys']
    assert len(cut) == 2                                              # one outline around each shape, no holes
    sq = next(l for l in layers if l['color'][2] > 0.5)
    assert len(sq['polys']) > 20                                      # outline + hatching
    # the cut line sits outside the art by about the offset
    art = np.vstack([p for l in layers[:-1] for p in l['polys']])
    c = np.vstack(cut)
    assert c[:, 0].min() < art[:, 0].min() - 1.0 and c[:, 0].max() > art[:, 0].max() + 1.0


def test_image_trace_edges():
    import image_trace as it
    layers, _ = it.trace_image(_logo_rgb(), {'mode': it.MODES[1]}, 200, 150)
    assert len(layers) == 1 and len(layers[0]['polys']) >= 3
    assert sum(len(p) for p in layers[0]['polys']) > 50


# ---------------------------------------------------------------- knife passes
def test_knife_repeat_passes():
    sq = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]])
    line = np.array([[0.0, 0.0], [5.0, 5.0]])
    out = exporter.repeat_passes([sq, line], 2)
    assert len(out) == 3                                       # square: one stroke; open line: two
    assert len(out[0]) == 9 and np.allclose(out[0][4], out[0][0]) and np.allclose(out[0][-1], sq[0])
    assert np.allclose(out[1], line) and np.allclose(out[2], line)     # same direction both times
    assert len(exporter.repeat_passes([sq, line], 1)) == 2
    # the card cut-out goes round twice: the path is about twice as long
    one = exporter.cutout_path(100.0, 80.0, passes=1)
    two = exporter.cutout_path(100.0, 80.0, passes=2)
    length = lambda p: float(np.sum(np.hypot(*np.diff(p, axis=0).T)))
    assert abs((length(two) - length(one)) - (length(one) - 5.0)) < 1e-6          # one more lap (5 mm overlap)


# ---------------------------------------------------------------- Z calibration
def test_z_calibration_math():
    import calibrate_z as cz
    assert cz.ladder_heights(3.0, 0.3, 4) == [3.0, 2.7, 2.4, 2.1]
    assert cz.pen_result(5.5, 1.2, press=0.4) == (6.3, 1.2)       # Z0 0.4 mm below the first inked line
    assert cz.knife_result(-0.6, margin=0.15) == -0.75
    # knife test made at pen-down 5.5, pen-down since raised to 6.3: same blade height -> 0.8 mm deeper
    assert cz.knife_result(-0.6, margin=0.15, test_pen_down=5.5, pen_down=6.3) == -1.55
    assert cz.config_lines({'PEN_DOWN_ABOVE_HOME_MM': 4.2, 'PEN_LIFT_MM': 1.2, 'CUTTER_Z_OFFSET_MM': -0.95}) == \
        ["PEN_DOWN_ABOVE_HOME_MM = 4.20", "PEN_LIFT_MM = 1.2", "CUTTER_Z_OFFSET_MM = -0.95"]


def test_z_calibration_read_test_file():
    import calibrate_z as cz
    with tempfile.TemporaryDirectory() as d:
        for name, (g, zs) in (("pen", cz.pen_ladder(5.5, start=3.0, step=0.3, count=13)),
                              ("knife", cz.knife_ladder(6.3, start=1.5, step=0.3, count=16))):
            p = os.path.join(d, name + ".gcode")
            with open(p, "w") as f:
                f.write("\n".join(g))
            kind, h, back = cz.read_test(p)
            assert kind == name and h == (5.5 if name == "pen" else 6.3) and back == zs
    # line 14 of a 3.0 / 0.3 ladder is Z-0.90: with 0.4 mm press the pen-down height drops by 1.3 mm
    zs = cz.ladder_heights(3.0, 0.3, 15)
    assert zs[13] == -0.9 and cz.pen_result(5.5, zs[13], press=0.4) == (4.2, 1.2)


def test_z_calibration_gcode():
    import calibrate_z as cz
    g, zs = cz.pen_ladder(6.0, start=1.5, step=0.5, count=4)
    assert any(l.startswith("G92") and "Z-6.0" in l for l in g)
    assert [l for l in g if l.startswith("G1 Z")] == ["G1 Z1.5 F300", "G1 Z1.0 F300", "G1 Z0.5 F300", "G1 Z0.0 F300"]
    assert any(l.startswith("M0") for l in g)
    k, ds = cz.knife_ladder(6.0, start=0.0, step=0.5, count=3)
    assert [l for l in k if l.startswith("G1 Z")] == ["G1 Z0.0 F300", "G1 Z-0.5 F300", "G1 Z-1.0 F300"]
    assert exporter.bed_violation(exporter.xy_bounds(k)) is None


# ---------------------------------------------------------------- layered cut-outs
def test_layer_cut_cone():
    import layer_cut as lc
    yy, xx = np.mgrid[0:120, 0:120]
    dem = 1000.0 - np.hypot(xx - 60, yy - 60) * 10.0                # a cone, peak in the middle
    ls = lc.build_layers(dem, 60.0, 60.0, 4, min_feature_mm=2.0)
    assert len(ls['layers']) == 4
    for lo, hi in zip(ls['layers'][:-1], ls['layers'][1:]):
        assert hi.difference(lo.buffer(0.05)).area < 1e-6          # every layer rests on the one below
        assert hi.area < lo.area
    plan = lc.plan_sheets(ls, 150.0, 150.0)
    assert not plan['too_big'] and plan['n_pieces'] == 4
    for sh in plan['sheets']:
        for path in sh['cuts']:
            assert path[:, 0].min() > 0 and path[:, 1].min() > 0 and path[:, 0].max() < 150 and path[:, 1].max() < 150


# ---------------------------------------------------------------------------------------------
# City road maps (city_roads.py)
# ---------------------------------------------------------------------------------------------
def _fake_city():
    import city_roads as CR
    lon0, lat0 = -77.0, 38.9
    half = 0.01                                            # ~0.87 x 1.1 km square city
    ring = [[lon0 - half, lat0 - half], [lon0 + half, lat0 - half], [lon0 + half, lat0 + half],
            [lon0 - half, lat0 + half], [lon0 - half, lat0 - half]]
    x1, y1 = CR.to_metres(lon0 + half, lat0 + half, lon0, lat0)
    roads, cls = [], []
    for i, v in enumerate(np.linspace(-float(x1) * 1.2, float(x1) * 1.2, 25)):   # grid, sticking out of the city
        c = 0 if i == 12 else (2 if i % 6 == 0 else 4)
        ys = np.linspace(-float(y1) * 1.2, float(y1) * 1.2, 25)
        for a, b in zip(ys[:-1], ys[1:]):                  # each street in pieces, like OSM ways
            roads.append(np.array([[v, a], [v, b]]))
            cls.append(c)
            roads.append(np.array([[a * x1 / y1, v * y1 / x1], [b * x1 / y1, v * y1 / x1]]))
            cls.append(c)
    meta = {'query': 'Testville', 'name': 'Testville', 'display_name': 'Testville', 'osm_relation': 1,
            'center': [lon0, lat0], 'bbox_m': [-float(x1) * 1.3, -float(y1) * 1.3, float(x1) * 1.3, float(y1) * 1.3],
            'boundary': {'type': 'Polygon', 'coordinates': [ring]}, 'max_class': 5}
    river = np.array([[-float(x1) * 1.3, 0.0], [float(x1) * 1.3, 0.0]])
    return {'meta': meta, 'roads': roads, 'road_cls': np.array(cls), 'extras': [river],
            'extra_kind': np.array([2])}


def test_city_chain_joins_pieces():
    import city_roads as CR
    lines = [np.array([[i, 0.0], [i + 1.0, 0.0]]) for i in range(10)]          # one street in 10 pieces
    lines += [np.array([[5.0, -3.0], [5.0, 0.0]]), np.array([[5.0, 0.0], [5.0, 3.0]])]   # a crossing street
    out = CR.chain_lines(lines)
    assert len(out) == 2
    total = sum(float(np.sum(np.hypot(*np.diff(l, axis=0).T))) for l in out)
    assert abs(total - 16.0) < 1e-9                       # every piece exactly once
    assert max(len(l) for l in out) == 11                 # straight through the junction


def test_city_crowding():
    import city_roads as CR
    lines = lambda gap: [np.array([[0.0, y], [30.0, y]]) for y in np.arange(0, 20, gap)]
    assert CR.crowding([(lines(0.3), 0.38)], (0, 30, 0, 20))[0] > 0.8         # 0.38 mm pen, lines 0.3 mm apart
    assert CR.crowding([(lines(0.7), 0.38)], (0, 30, 0, 20))[0] == 0.0        # gaps of 0.3 mm still show
    cross = [np.array([[0.0, 10.0], [30.0, 10.0]]), np.array([[15.0, 0.0], [15.0, 20.0]])]
    assert CR.crowding([(cross, 0.7)], (0, 30, 0, 20))[0] == 0.0              # a crossing is not crowding


def test_city_map_layers_pens_and_clip():
    import city_roads as CR
    data = _fake_city()
    pw, ph = 150.0, 200.0
    opts = {'min_class': 4, 'major_upto': 2, 'pen_major': 'pen_1', 'pen_minor': 'base', 'title': True,
            'extras': {'rivers', 'outline'}, 'pen_extras': 'blue'}
    segs, masks, info = CR.build_city_map(data, opts, pw, ph)
    layers = {s['layer'] for s in segs}
    assert {'city_major', 'city_minor', 'city_rivers', 'city_outline', 'labels'} <= layers
    assert all(s['pen_id'] == 'pen_1' for s in segs if s['layer'] == 'city_major')
    assert all(s['pen_id'] == 'base' for s in segs if s['layer'] == 'city_minor')
    pts = np.concatenate([s['seg'] for s in segs])
    assert pts[:, 0].min() >= 0 and pts[:, 0].max() <= pw and pts[:, 1].min() >= -ph / 2 and pts[:, 1].max() <= ph / 2
    # clipped to the city: no road reaches the margin band beyond the boundary's box
    bx0, by0, bx1, by1 = CR.map_box(pw, ph, True)
    road = np.concatenate([s['seg'] for s in segs if s['layer'].startswith('city_m')])
    assert road[:, 0].min() >= bx0 - 1e-6 and road[:, 0].max() <= bx1 + 1e-6
    # pieces were joined: far fewer strokes than OSM pieces
    assert info['n_strokes'] < (info['n_major'] + info['n_minor']) / 5
    # single pen: one layer, one pen
    segs1, _, _ = CR.build_city_map(data, dict(opts, single_pen=True, extras=set(), title=False), pw, ph)
    assert {s['layer'] for s in segs1} == {'city_roads'} and {s['pen_id'] for s in segs1} == {'base'}
    # smaller road classes off: fewer strokes
    segs2, _, _ = CR.build_city_map(data, dict(opts, min_class=2, extras=set(), title=False), pw, ph)
    assert len(segs2) < len([s for s in segs if s['layer'].startswith('city_m')])
    # rectangle clip: fitted rectangle keeps the map box's shape
    lon0, lat0 = data['meta']['center']
    r = CR.fit_rect((-200, -100, 200, 100), pw, ph, False)
    bx0, by0, bx1, by1 = CR.map_box(pw, ph, False)
    assert abs((r[2] - r[0]) / (r[3] - r[1]) - (bx1 - bx0) / (by1 - by0)) < 1e-9
    a, b = CR.to_lonlat(r[0], r[1], lon0, lat0)
    c, d = CR.to_lonlat(r[2], r[3], lon0, lat0)
    segs3, _, info3 = CR.build_city_map(data, dict(opts, clip='rectangle', rect=[a, b, c, d], extras=set(),
                                                   title=False), pw, ph)
    assert abs(info3['width_km'] - (r[2] - r[0]) / 1000.0) < 1e-6


def test_city_title_bottom_left():
    import city_roads as CR
    assert CR.city_name({'query': 'Washington, D.C.', 'name': 'Washington'}) == "Washington DC"
    assert CR.city_name({'query': 'Paris, France', 'name': 'Paris'}) == "Paris"
    data = _fake_city()
    pw, ph = 200.0, 150.0
    segs, _, info = CR.build_city_map(data, {'min_class': 4, 'title': True, 'extras': {'outline'}}, pw, ph)
    lab = np.concatenate([s['seg'] for s in segs if s['layer'] == 'labels'])
    m = CR.margins(pw, ph)
    assert abs(lab[:, 0].min() - m) < 1.0 and lab[:, 0].max() < pw / 2      # left-justified, bottom left
    x0, x1 = lab[:, 0].min() - 1.4, lab[:, 0].max() + 1.4
    y0, y1 = lab[:, 1].min() - 1.4, lab[:, 1].max() + 1.4
    for s in segs:                                                           # no map line under the title
        if s['layer'] != 'labels':
            p = s['seg']
            inside = (p[:, 0] > x0) & (p[:, 0] < x1) & (p[:, 1] > y0) & (p[:, 1] < y1)
            assert not inside.any()
    # the map is as big as with no title: the title takes no band of its own
    _, _, info0 = CR.build_city_map(data, {'min_class': 4, 'title': False}, pw, ph)
    assert abs(info['scale_m_per_mm'] - info0['scale_m_per_mm']) < 1e-9


def test_city_tiny_water_skipped():
    import city_roads as CR
    data = _fake_city()
    rng = np.random.default_rng(1)
    ponds = []
    for c in rng.uniform(-300, 300, (200, 2)):                  # 200 ponds ~10 m across: specks on paper
        t = np.linspace(0, 2 * np.pi, 9)
        ponds.append(np.column_stack([c[0] + 5 * np.cos(t), c[1] + 5 * np.sin(t)]))
    t = np.linspace(0, 2 * np.pi, 40)
    ponds.append(np.column_stack([250 * np.cos(t), 250 * np.sin(t)]))        # one real lake
    data['extras'], data['extra_kind'] = ponds, np.zeros(len(ponds), int)
    opts = {'min_class': 4, 'title': False, 'extras': {'water'}, 'crowding': False}
    segs, _, info = CR.build_city_map(data, opts, 150.0, 150.0)
    water = [s for s in segs if s['layer'] == 'city_water']
    assert len(water) == 1 and info['n_skipped'] >= 150                      # the lake stays, the specks go
    segs0, _, info0 = CR.build_city_map(data, dict(opts, min_shape_mm=0.0), 150.0, 150.0)
    assert len([s for s in segs0 if s['layer'] == 'city_water']) > 150 and info0['n_skipped'] == 0


def test_city_loose_road_bits():
    import city_roads as CR
    main_road = np.array([[0.0, 0.0], [10.0, 0.0]])
    stub = np.array([[5.0, 0.0], [5.0, 0.5]])           # 0.5 mm side street meeting it mid-way: keep
    speck = np.array([[20.0, 20.0], [20.4, 20.0]])      # 0.4 mm on its own: drop
    assert CR._loose_bits([main_road, stub, speck], 1.0) == [True, True, False]


def test_city_cache_roundtrip():
    import city_roads as CR
    data = _fake_city()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.npz")
        CR.save(data, p)
        back = CR.load_file(p)
    assert back['meta']['name'] == 'Testville' and len(back['roads']) == len(data['roads'])
    assert np.allclose(back['roads'][7], data['roads'][7], atol=1e-3)
    assert list(back['road_cls']) == list(data['road_cls'])


def test_city_water_area_from_cropped_outline():
    """A river's banks cropped by the bounding box (open rings) still give the water between them."""
    import city_roads as CR
    from shapely.geometry import box, LineString
    banks = [np.array([[-50.0, 40.0], [150.0, 40.0]]), np.array([[-50.0, 60.0], [150.0, 60.0]])]
    centre = LineString([(-50, 50), (150, 50)])
    w = CR._water_area(banks, centre, box(0, 0, 100, 100))
    assert w is not None and abs(w.area - 2000.0) < 1.0


if __name__ == '__main__':
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_') and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
