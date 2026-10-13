import os
import traceback

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window
from PyQt6.QtCore import QThread, pyqtSignal

# Local imports
from config import DATA_FILES, PLANET_RADII, MAX_DEM_PX, TAHOE
from contour_engine import generate_contours, auto_contour_interval
from hatch_engine import generate_hatches

COLOR_PX_PER_MM = 4.0      # colour map resolution requested for hatching (0.25 mm / px)
MAX_COLOR_PX = 4000


# ==========================================
# RASTER HELPERS
# ==========================================
def read_dem_window(path, ds_bounds, lon_min, lon_max, lat_min, lat_max, wrap, max_px=MAX_DEM_PX):
    """Reads the DEM pixels covering a lon/lat box, decimated by GDAL (block average) so the
    longest side is <= max_px.

    ds_bounds: (left, bottom, right, top) of the whole raster in degrees, or None to use the
    file's own georeferencing. wrap=True for global rasters (longitudes wrap at +-180).
    Returns (float32 array, exact [lon0, lon1, lat0, lat1] pixel-edge extent, decimation k),
    or (None, None, 1) if the box does not touch the raster.
    """
    with rasterio.open(path) as src:
        W, H = src.width, src.height
        if ds_bounds is None:
            b = src.bounds
            ds_bounds = (b.left, b.bottom, b.right, b.top)
        L, B, R, T = ds_bounds
        pxw, pxh = (R - L) / W, (T - B) / H

        r0 = max(0, int(np.floor((T - lat_max) / pxh)))
        r1 = min(H, int(np.ceil((T - lat_min) / pxh)))
        c0 = int(np.floor((lon_min - L) / pxw))
        c1 = int(np.ceil((lon_max - L) / pxw))
        if wrap:
            if c1 - c0 >= W:
                c0, c1 = 0, W
            pieces, c = [], c0
            while c < c1:
                base = int(np.floor(c / W))
                s0 = c - base * W
                e0 = min(W, c1 - base * W)
                pieces.append((s0, e0))
                c = base * W + e0
        else:
            c0, c1 = max(0, c0), min(W, c1)
            pieces = [(c0, c1)]
        if r1 <= r0 or c1 <= c0:
            return None, None, 1

        rows, cols = r1 - r0, c1 - c0
        k = max(1, int(np.ceil(max(rows, cols) / float(max_px))))
        oh = max(1, int(round(rows / k)))
        nodata = src.nodata
        parts = []
        for s0, e0 in pieces:
            ow = max(1, int(round((e0 - s0) / k)))
            a = src.read(1, window=Window(s0, r0, e0 - s0, rows), out_shape=(oh, ow),
                         resampling=Resampling.average if k > 1 else Resampling.nearest)
            a = a.astype(np.float32)
            if nodata is not None and not np.isnan(nodata):
                a[a == np.float32(nodata)] = np.nan
            parts.append(a)
    dem = np.hstack(parts) if len(parts) > 1 else parts[0]
    extent = [L + c0 * pxw, L + c1 * pxw, T - r1 * pxh, T - r0 * pxh]
    return dem, extent, k


def read_color_window(path, lon_min, lon_max, lat_min, lat_max, deg_per_px_lon, deg_per_px_lat):
    """Reads a global equirectangular colour map window (lon may run past +-180).

    Returns (rgb float32 (h, w, 3) in 0..1, bounds dict) or (None, None) if empty.
    The bounds are the exact pixel-edge bounds of the returned array.
    """
    lat_top, lat_bot = min(lat_max, 90.0), max(lat_min, -90.0)
    if lat_top <= lat_bot or lon_max <= lon_min:
        return None, None

    with rasterio.open(path) as src:
        W, H = src.width, src.height
        r0 = int(np.clip(np.floor((90.0 - lat_top) / 180.0 * H), 0, H - 1))
        r1 = int(np.clip(np.ceil((90.0 - lat_bot) / 180.0 * H), r0 + 1, H))

        if lon_max - lon_min >= 360.0:
            pieces = [(0, W)]
        else:
            a = (lon_min + 180.0) % 360.0 - 180.0
            b = a + (lon_max - lon_min)
            c0 = int(np.clip(np.floor((a + 180.0) / 360.0 * W), 0, W - 1))
            if b <= 180.0:
                pieces = [(c0, int(np.clip(np.ceil((b + 180.0) / 360.0 * W), c0 + 1, W)))]
            else:
                c1 = int(np.clip(np.ceil((b - 180.0) / 360.0 * W), 1, W))
                pieces = [(c0, W), (0, c1)]
        total_cols = sum(e - s for s, e in pieces)
        lon0 = -180.0 + pieces[0][0] * 360.0 / W
        lon_span = total_cols * 360.0 / W
        lat_span = (r1 - r0) * 180.0 / H

        out_w = int(np.clip(np.ceil(lon_span / max(deg_per_px_lon, 1e-9)), 2, min(total_cols, MAX_COLOR_PX)))
        out_h = int(np.clip(np.ceil(lat_span / max(deg_per_px_lat, 1e-9)), 2, min(r1 - r0, MAX_COLOR_PX)))
        bands = [1, 2, 3] if src.count >= 3 else [1]

        parts = []
        for s, e in pieces:
            pw = max(1, int(round(out_w * (e - s) / total_cols)))
            arr = src.read(bands, window=Window(s, r0, e - s, r1 - r0), out_shape=(len(bands), out_h, pw),
                           resampling=Resampling.average)
            parts.append(arr)
        arr = np.concatenate(parts, axis=2)

    if np.issubdtype(arr.dtype, np.integer):
        rgb = arr.astype(np.float32) / float(np.iinfo(arr.dtype).max)
    else:
        rgb = arr.astype(np.float32)
        if np.nanmax(rgb) > 1.5:
            rgb /= 255.0
    rgb = np.moveaxis(rgb, 0, -1)
    if rgb.shape[2] == 1:
        rgb = np.repeat(rgb, 3, axis=2)
    bounds = {'lon0': lon0, 'lon_span': lon_span, 'lat_top': 90.0 - r0 * 180.0 / H,
              'lat_bot': 90.0 - r1 * 180.0 / H}
    return np.clip(rgb, 0.0, 1.0), bounds


# ==========================================
# BACKGROUND RENDER THREAD
# ==========================================
class RenderWorker(QThread):
    # NOTE: deliberately NOT named 'finished' - that would shadow QThread.finished,
    # which main.py uses to know when the thread has really exited.
    result_ready = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, planet, dem_path, geo_bounds, params, fresh=False):
        super().__init__()
        self.planet = planet
        self.dem_path = dem_path
        self.geo_bounds = list(geo_bounds)
        self.params = dict(params)
        self.fresh = fresh

    def run(self):
        try:
            mode = self.params.get('plot_mode', 'Topography (DEM)')
            lon_min, lon_max, lat_min, lat_max = self.geo_bounds
            extent = [lon_min, lon_max, lat_min, lat_max]
            result = {'segments': [], 'dem_data': None, 'extent': extent, 'min_e': 0.0, 'max_e': 0.0,
                      'auto_interval': None, 'fresh': self.fresh, 'mode': mode}

            if "Topography" in mode:
                self.progress.emit("Reading DEM topography data from disk...")
                is_tahoe = self.planet == 'Earth' and "Tahoe" in self.params.get('selected_region', '')
                if is_tahoe:
                    mult = TAHOE['multiplier']
                    dem_data, dem_extent, k = read_dem_window(self.dem_path, TAHOE['bounds'], lon_min, lon_max,
                                                              lat_min, lat_max, wrap=False)
                else:
                    mult = DATA_FILES[self.planet]['multiplier']
                    global_dems = [DATA_FILES[p]['dem'] for p in DATA_FILES]
                    is_global = self.dem_path in global_dems
                    dem_data, dem_extent, k = read_dem_window(self.dem_path, (-180.0, -90.0, 180.0, 90.0) if is_global
                                                              else None, lon_min, lon_max, lat_min, lat_max,
                                                              wrap=is_global)
                if dem_data is None:
                    raise ValueError("The selection does not overlap the DEM.")
                if k > 1:
                    self.progress.emit(f"DEM read at 1/{k} resolution ({dem_data.shape[1]}x{dem_data.shape[0]} px)...")

                dem_data[dem_data < -100000] = np.nan
                dem_data[dem_data > 1e30] = np.nan          # float nodata sentinels (e.g. 3.4e38)
                self.params['dem_decimation'] = k
                self.params['dem_extent'] = dem_extent
                self.params['dem_multiplier'] = mult

                crop_ft = dem_data * mult
                all_nan = np.isnan(crop_ft).all()
                min_e = 0.0 if all_nan else float(np.nanmin(crop_ft))
                max_e = 0.0 if all_nan else float(np.nanmax(crop_ft))

                # Auto (USGS-style) interval for fresh draws, computed BEFORE contouring so we
                # never need a second pass.
                if self.fresh and not is_tahoe and not all_nan:
                    lo = max(min_e, 0.0) if (self.planet == 'Earth' and not self.params.get('show_ocean', True)) else min_e
                    step = auto_contour_interval(max_e - lo)
                    if step:
                        self.params['interval_ft'] = step
                        result['auto_interval'] = step

                segments = generate_contours(dem_data, extent, extent, self.params,
                                             progress_callback=self.progress.emit)
                result.update({'segments': segments, 'dem_data': dem_data, 'min_e': min_e, 'max_e': max_e})

            else:
                color_path = DATA_FILES[self.planet]['color']
                if not os.path.exists(color_path):
                    raise ValueError(f"Cannot find colour map {color_path}")
                self.progress.emit("Reading colour map window...")

                pw = float(self.params.get('paper_w_mm', 100.0))
                ph = float(self.params.get('paper_h_mm', 100.0))
                if self.params.get('circle_mode', False):
                    R = PLANET_RADII.get(self.planet, 6371000.0)
                    mm_per_deg = R * np.pi / 180.0 * min(pw, ph) / (2.1 * R)
                    dpp_lon = dpp_lat = 1.0 / (mm_per_deg * COLOR_PX_PER_MM)
                else:
                    dpp_lon = (lon_max - lon_min) / (pw * COLOR_PX_PER_MM)
                    dpp_lat = (lat_max - lat_min) / (ph * COLOR_PX_PER_MM)

                rgb, bounds = read_color_window(color_path, lon_min, lon_max, lat_min, lat_max, dpp_lon, dpp_lat)
                segments = generate_hatches(rgb, bounds, extent, self.params,
                                            progress_callback=self.progress.emit) if rgb is not None else []
                result['segments'] = segments

            self.result_ready.emit(result)

        except Exception as e:
            traceback.print_exc()
            self.error.emit(f"{type(e).__name__}: {e}")
