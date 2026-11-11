"""One-time preparation of the big global elevation files.

GDAL "overviews" are reduced-resolution copies (1/2, 1/4, 1/8, 1/16) stored next to the original as
<file>.ovr. Large selections (a whole hemisphere in circle mode, a country) are read at reduced resolution
anyway, and with overviews GDAL reads the small copy instead of decompressing the whole full-size file.
The original .tif is not modified. Run from the app (Tools > Speed up global elevation...) or directly:

    python dem_prep.py            (prepares every global DEM in config.DATA_FILES that exists)
"""
import os
import sys
import time

import rasterio
from rasterio.enums import Resampling

OVR_FACTORS = [2, 4, 8, 16]
MIN_SIZE_PX = 8000          # smaller rasters read fast enough without overviews


def ovr_path(path):
    return path + ".ovr"


def has_overviews(path):
    try:
        with rasterio.open(path) as src:
            return bool(src.overviews(1))
    except Exception:
        return False


def needs_overviews(path):
    """True for a large raster that has no overviews yet."""
    if not os.path.exists(path):
        return False
    try:
        with rasterio.open(path) as src:
            return max(src.width, src.height) >= MIN_SIZE_PX and not src.overviews(1)
    except Exception:
        return False


def build_overviews(path, log=print):
    """Writes <path>.ovr. Returns seconds taken. The source file's size and modification time are
    checked before and after, so a change to the original is reported (it should never happen)."""
    t0 = time.perf_counter()
    before = (os.path.getsize(path), os.path.getmtime(path))
    env = dict(TIFF_USE_OVR=True,                 # always a separate .ovr file, never inside the .tif
               COMPRESS_OVERVIEW='DEFLATE', PREDICTOR_OVERVIEW=3 if _is_float(path) else 2,
               BIGTIFF_OVERVIEW='IF_SAFER', GDAL_NUM_THREADS='ALL_CPUS', GDAL_CACHEMAX=512)
    log(f"Building overviews {OVR_FACTORS} for {os.path.basename(path)} (one time, a minute or two)...")
    with rasterio.Env(**env):
        # Read-only: GDAL then has to put the overviews in the external .ovr file.
        with rasterio.open(path) as src:
            if hasattr(src, 'build_overviews'):
                src.build_overviews(OVR_FACTORS, Resampling.average)
            else:
                src = None
        if src is None:
            with rasterio.open(path, 'r+') as dst:
                dst.build_overviews(OVR_FACTORS, Resampling.average)
    after = (os.path.getsize(path), os.path.getmtime(path))
    if after != before:
        log(f"WARNING: {path} changed while building overviews (size/time {before} -> {after}).")
    dt = time.perf_counter() - t0
    log(f"Done in {dt:.0f} s: {ovr_path(path)} ({os.path.getsize(ovr_path(path)) / 1e6:.0f} MB)")
    return dt


def _is_float(path):
    with rasterio.open(path) as src:
        return src.dtypes[0].startswith('float')


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.getcwd())
    from config import DATA_FILES
    todo = [f['dem'] for f in DATA_FILES.values() if needs_overviews(f['dem'])]
    if not todo:
        print("Nothing to do: every global elevation file is already prepared (or missing).")
    for p in todo:
        build_overviews(p)
