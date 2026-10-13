import numpy as np
import rasterio
from rasterio.transform import from_bounds as transform_from_bounds
from PyQt6.QtCore import QThread, pyqtSignal

# Local imports
from config import DATA_FILES
from contour_engine import generate_contours
from hatch_engine import generate_hatches


class RenderWorker(QThread):
    finished = pyqtSignal(list, object, list, float, float)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, planet, dem_path, geo_bounds, params, base_img=None):
        super().__init__()
        self.planet = planet
        self.dem_path = dem_path
        self.geo_bounds = geo_bounds
        self.params = params
        self.base_img = base_img

    def run(self):
        try:
            mode = self.params.get('plot_mode', 'Topography (DEM)')
            lon_min, lon_max, lat_min, lat_max = self.geo_bounds
            extent = [lon_min, lon_max, lat_min, lat_max]

            if "Topography" in mode:
                self.progress.emit("Reading DEM topography data from disk...")

                with rasterio.open(self.dem_path) as src:
                    global_dems = [DATA_FILES['Earth']['dem'], DATA_FILES['Moon']['dem'], DATA_FILES['Mars']['dem']]
                    is_global = self.dem_path in global_dems
                    active_transform = transform_from_bounds(-180, -90, 180, 90, src.width,
                                                             src.height) if is_global else src.transform

                    def fetch_window(l_min, l_max):
                        w = rasterio.windows.from_bounds(l_min, lat_min, l_max, lat_max, transform=active_transform)
                        return src.read(1, window=w, boundless=True, fill_value=np.nan).astype(np.float32)

                    if is_global and lon_min < -180:
                        dem_data = np.hstack((fetch_window(lon_min + 360, 180), fetch_window(-180, lon_max)))
                    elif is_global and lon_max > 180:
                        dem_data = np.hstack((fetch_window(lon_min, 180), fetch_window(-180, lon_max - 360)))
                    else:
                        dem_data = fetch_window(lon_min, lon_max)

                dem_data[dem_data < -100000] = np.nan
                mult = DATA_FILES[self.planet]['multiplier']

                if self.planet == 'Earth' and "Tahoe" in self.params.get('selected_region', ''):
                    dem_data[dem_data * mult > 6226.0] = np.nan

                crop_ft = dem_data * mult
                min_e = 0.0 if np.isnan(crop_ft).all() else float(np.nanmin(crop_ft))
                max_e = 0.0 if np.isnan(crop_ft).all() else float(np.nanmax(crop_ft))

                segments = generate_contours(dem_data, extent, extent, self.params,
                                             progress_callback=self.progress.emit)
                self.finished.emit(segments, dem_data, extent, min_e, max_e)

            else:
                self.progress.emit("Cropping Color Map arrays...")
                # Hatching Modes: We use the already-loaded base_img passed from main.py
                if self.base_img is None:
                    raise ValueError("Color image not loaded in memory.")

                h, w = self.base_img.shape[:2]

                # Convert geographical degrees to pixel indices
                x1 = int((lon_min + 180) / 360.0 * w)
                x2 = int((lon_max + 180) / 360.0 * w)
                y1 = int((90 - lat_max) / 180.0 * h)
                y2 = int((90 - lat_min) / 180.0 * h)

                # Handle dateline wrapping for the color map slice
                if x1 < 0:
                    left_crop = self.base_img[y1:y2, x1 + w:w]
                    right_crop = self.base_img[y1:y2, 0:x2]
                    color_crop = np.hstack((left_crop, right_crop))
                elif x2 > w:
                    left_crop = self.base_img[y1:y2, x1:w]
                    right_crop = self.base_img[y1:y2, 0:x2 - w]
                    color_crop = np.hstack((left_crop, right_crop))
                else:
                    color_crop = self.base_img[y1:y2, x1:x2]

                segments = generate_hatches(color_crop, extent, self.params, progress_callback=self.progress.emit)

                # Return dummy values for DEM elements since we didn't calculate them
                self.finished.emit(segments, None, extent, 0.0, 0.0)

        except Exception as e:
            self.error.emit(str(e))