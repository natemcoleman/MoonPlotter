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
         'tone_gamma': 1.0, 'join_lines': True, 'fill_inks': 'mono', 'mono_ink_set': 'Thick Black G2 (0.7)',
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
    assert segs and len(masks) == 3
    assert all(s['layer'] == 'decor' for s in segs)


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


def test_project_roundtrip():
    st = {'planet': 'Moon', 'selection': [1.0, 2.0, 3.0, 4.0], 'custom_items': [{'points': np.zeros((2, 2))}],
          'selected_sites': {'Moon': ['Apollo 11']}}
    path = os.path.join(tempfile.gettempdir(), "tps_test.topo.json")
    project_io.save_project(path, st)
    back = project_io.load_project(path)
    assert back['selection'] == [1.0, 2.0, 3.0, 4.0]
    assert back['selected_sites']['Moon'] == ['Apollo 11']


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
