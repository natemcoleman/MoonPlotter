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
from hatch_engine import generate_hatches, srgb_to_linear, linear_to_srgb

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

    # ------------------------------------------------------------------
    def _read_dem(self, lon_min, lon_max, lat_min, lat_max, is_tahoe, max_px=MAX_DEM_PX):
        if is_tahoe:
            mult = TAHOE['multiplier']
            dem, ext, k = read_dem_window(self.dem_path, TAHOE['bounds'], lon_min, lon_max, lat_min, lat_max,
                                          wrap=False, max_px=max_px)
        else:
            mult = DATA_FILES[self.planet]['multiplier']
            is_global = self.dem_path in [DATA_FILES[p]['dem'] for p in DATA_FILES]
            dem, ext, k = read_dem_window(self.dem_path, (-180.0, -90.0, 180.0, 90.0) if is_global else None,
                                          lon_min, lon_max, lat_min, lat_max, wrap=is_global, max_px=max_px)
        if dem is None:
            raise ValueError("The selection does not overlap the DEM.")
        dem[dem < -100000] = np.nan
        dem[dem > 1e30] = np.nan          # float nodata sentinels (e.g. 3.4e38)
        return dem, ext, k, mult

    def run(self):
        try:
            lon_min, lon_max, lat_min, lat_max = self.geo_bounds
            extent = [lon_min, lon_max, lat_min, lat_max]
            do_contours = self.params.get('layer_contours', True)
            do_fill = self.params.get('layer_fill', False)
            result = {'segments': [], 'dem_data': None, 'extent': extent, 'min_e': 0.0, 'max_e': 0.0,
                      'auto_interval': None, 'fresh': self.fresh}
            is_tahoe = self.planet == 'Earth' and "Tahoe" in self.params.get('selected_region', '')
            segments = []
            dem_cache = None

            # ---------------- Contour layer ----------------
            if do_contours:
                self.progress.emit("Reading DEM topography data from disk...")
                dem_data, dem_extent, k, mult = self._read_dem(lon_min, lon_max, lat_min, lat_max, is_tahoe)
                dem_cache = (dem_data, dem_extent, mult)
                if k > 1:
                    self.progress.emit(f"DEM read at 1/{k} resolution ({dem_data.shape[1]}x{dem_data.shape[0]} px)...")
                self.params['dem_decimation'] = k
                self.params['dem_extent'] = dem_extent
                self.params['dem_multiplier'] = mult

                crop_ft = dem_data * mult
                all_nan = np.isnan(crop_ft).all()
                min_e = 0.0 if all_nan else float(np.nanmin(crop_ft))
                max_e = 0.0 if all_nan else float(np.nanmax(crop_ft))

                # Auto interval for fresh draws, computed BEFORE contouring (single pass).
                if (self.fresh or self.params.get('force_auto_interval')) and not is_tahoe and not all_nan:
                    lo = max(min_e, 0.0) if (self.planet == 'Earth' and not self.params.get('show_ocean', True)) else min_e
                    step = auto_contour_interval(max_e - lo)
                    if step:
                        self.params['interval_ft'] = step
                        result['auto_interval'] = step

                segments += generate_contours(dem_data, extent, extent, self.params,
                                              progress_callback=self.progress.emit)
                result.update({'dem_data': dem_data, 'min_e': min_e, 'max_e': max_e})

            # ---------------- Fill / hatch layer ----------------
            if do_fill:
                segments += self._fill_layer(extent, is_tahoe, dem_cache)

            result['segments'] = segments
            self.result_ready.emit(result)

        except Exception as e:
            traceback.print_exc()
            self.error.emit(f"{type(e).__name__}: {e}")

    def _fill_layer(self, extent, is_tahoe, dem_cache):
        lon_min, lon_max, lat_min, lat_max = extent
        strategy = self.params.get('hatch_strategy', '')
        source = self.params.get('fill_source', 'Color map')
        need_color = "Hachure" not in strategy and "Color" in source
        need_dem = "Hachure" in strategy or "Hillshade" in source or is_tahoe

        pw = float(self.params.get('paper_w_mm', 100.0))
        ph = float(self.params.get('paper_h_mm', 100.0))
        R = PLANET_RADII.get(self.planet, 6371000.0)
        if self.params.get('circle_mode', False):
            mm_per_deg = R * np.pi / 180.0 * min(pw, ph) / (2.1 * R)
            dpp_lon = dpp_lat = 1.0 / (mm_per_deg * COLOR_PX_PER_MM)
        else:
            dpp_lon = (lon_max - lon_min) / (pw * COLOR_PX_PER_MM)
            dpp_lat = (lat_max - lat_min) / (ph * COLOR_PX_PER_MM)

        dem_m = dem_bounds = None
        if need_dem:
            if dem_cache is not None:
                dem, dext, mult = dem_cache
            else:
                self.progress.emit("Reading DEM for the fill layer...")
                want = int(np.ceil(max((lon_max - lon_min) / dpp_lon, (min(lat_max, 90) - max(lat_min, -90)) / dpp_lat)))
                dem, dext, _, mult = self._read_dem(lon_min, lon_max, lat_min, lat_max, is_tahoe,
                                                    max_px=int(np.clip(want, 64, MAX_COLOR_PX)))
            dem_m = (dem * mult * 0.3048).astype(np.float32)
            dem_bounds = {'lon0': dext[0], 'lon_span': dext[1] - dext[0], 'lat_top': dext[3], 'lat_bot': dext[2]}

        if "Hachure" in strategy:
            return generate_hatches(None, None, extent, self.params, progress_callback=self.progress.emit,
                                    elev=(dem_m, dem_bounds))

        rgb = bounds = None
        if need_color:
            color_path = DATA_FILES[self.planet]['color']
            if not os.path.exists(color_path):
                raise ValueError(f"Cannot find colour map {color_path}")
            self.progress.emit("Reading colour map window...")
            rgb, bounds = read_color_window(color_path, lon_min, lon_max, lat_min, lat_max, dpp_lon, dpp_lat)
            if rgb is None:
                return []

        if "Hillshade" in source or is_tahoe:
            self.progress.emit("Computing hillshade...")
            dark = hillshade_darkness(dem_m, dem_bounds, R)              # NaN outside data
            if rgb is None:
                # Pure hillshade: grey image whose linear lightness is 1 - darkness.
                lin = 1.0 - np.nan_to_num(dark, nan=0.0)
                g = linear_to_srgb(lin).astype(np.float32)
                rgb = np.repeat(g[..., None], 3, axis=2)
                rgb[~np.isfinite(dark)] = np.nan
                bounds = dem_bounds
            else:
                # Resample darkness onto the colour grid; multiply (shaded relief) and/or mask (Tahoe).
                h, w = rgb.shape[:2]
                lon_c = bounds['lon0'] + (np.arange(w) + 0.5) / w * bounds['lon_span']
                lat_c = bounds['lat_top'] - (np.arange(h) + 0.5) / h * (bounds['lat_top'] - bounds['lat_bot'])
                LO, LA = np.meshgrid(lon_c, lat_c)
                dk = _sample_nearest(dark, dem_bounds, LO, LA)
                if "Hillshade" in source:
                    lin = srgb_to_linear(rgb) * (1.0 - 0.75 * np.nan_to_num(dk, nan=0.0))[..., None]
                    rgb = linear_to_srgb(lin).astype(np.float32)
                if is_tahoe:
                    rgb[~np.isfinite(dk)] = np.nan              # nothing outside the lake

        return generate_hatches(rgb, bounds, extent, self.params, progress_callback=self.progress.emit)


def _sample_nearest(arr, bounds, lon, lat):
    h, w = arr.shape
    col = np.floor(np.mod(lon - bounds['lon0'], 360.0) / bounds['lon_span'] * w).astype(np.int64)
    row = np.floor((bounds['lat_top'] - lat) / (bounds['lat_top'] - bounds['lat_bot']) * h).astype(np.int64)
    ok = (col >= 0) & (col < w) & (row >= 0) & (row < h)
    out = np.full(lon.shape, np.nan, dtype=np.float32)
    out[ok] = arr[row[ok], col[ok]]
    return out


def hillshade_darkness(dem_m, bounds, R, azimuth=315.0, altitude=45.0):
    """Shadow-only hillshade: 0 on flat or sunlit ground, up to 1 in the deepest shade.

    Vertical exaggeration is chosen automatically so the 80th-percentile slope is ~35 degrees,
    which keeps relief readable from a Tahoe close-up to a whole planet.
    """
    h, w = dem_m.shape
    valid = np.isfinite(dem_m)
    if not valid.any():
        return np.full(dem_m.shape, np.nan, np.float32)
    filled = np.where(valid, dem_m, np.nanmean(dem_m))
    dlon = bounds['lon_span'] / w
    dlat = (bounds['lat_top'] - bounds['lat_bot']) / h
    lat_rows = bounds['lat_top'] - (np.arange(h) + 0.5) * dlat
    dx = (np.radians(dlon) * R * np.maximum(np.cos(np.radians(lat_rows)), 1e-3))[:, None]
    dy = np.radians(dlat) * R
    g_row, g_col = np.gradient(filled)
    dzdx = g_col / dx
    dzdy = -g_row / dy
    slope = np.hypot(dzdx, dzdy)
    p80 = np.percentile(slope[valid], 80)
    zf = float(np.clip(np.tan(np.radians(35.0)) / p80, 0.2, 5000.0)) if p80 > 0 else 1.0
    nx, ny = -zf * dzdx, -zf * dzdy
    norm = np.sqrt(nx * nx + ny * ny + 1.0)
    az, alt = np.radians(azimuth), np.radians(altitude)
    lx, ly, lz = np.cos(alt) * np.sin(az), np.cos(alt) * np.cos(az), np.sin(alt)
    shade = np.clip((nx * lx + ny * ly + lz) / norm, 0.0, 1.0)
    dark = np.clip((lz - shade) / lz, 0.0, 1.0).astype(np.float32)
    dark[~valid] = np.nan
    return dark
