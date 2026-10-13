import numpy as np
import scipy.ndimage
from shapely.geometry import box as shp_box, LineString, MultiLineString
from matplotlib.figure import Figure

from config import DATA_FILES
from geometry_utils import chaikin_smooth, ortho_project

try:
    import geopandas as gpd
    import warnings as gpd_warnings

    with gpd_warnings.catch_warnings():
        gpd_warnings.simplefilter("ignore")
        world_borders = gpd.read_file(
            "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip")
        state_borders = gpd.read_file(
            "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_1_states_provinces.zip")
    HAS_GPD = True
except ImportError:
    HAS_GPD = False
    world_borders = None
    state_borders = None


def generate_contours(dem_crop, data_extent, view_extent, params, progress_callback=None):
    segments = []
    if dem_crop is None or np.isnan(dem_crop).all():
        return segments

    if progress_callback: progress_callback("Processing DEM topography...")

    planet = params.get('active_planet', 'Earth')
    selected_region = params.get('selected_region', 'None (Plot Full Selection)')
    is_tahoe = (planet == 'Earth' and "Tahoe" in selected_region)
    is_circle = params.get('circle_mode', False)
    mult = DATA_FILES[planet]['multiplier']

    crop_ft = dem_crop.copy() * mult
    valid_mask = ~np.isnan(crop_ft)

    blur_sigma = params.get('blur_sigma', 0.0)
    if blur_sigma > 0:
        if progress_callback: progress_callback(f"Applying Gaussian blur (\u03c3={blur_sigma})...")
        safe_crop = np.where(valid_mask, crop_ft, np.nanmax(crop_ft))
        blurred = scipy.ndimage.gaussian_filter(safe_crop, sigma=blur_sigma, mode='nearest')
        crop_ft = np.where(valid_mask, blurred, np.nan)

    min_e, max_e = np.nanmin(crop_ft), np.nanmax(crop_ft)
    interval_ft = params.get('interval_ft', 200)

    if is_tahoe:
        shoreline = min_e + 1645.0
        levels = np.arange(shoreline, min_e, -interval_ft)
        levels = np.sort(levels).tolist()
    else:
        s_lvl = np.floor(min_e / interval_ft) * interval_ft
        e_lvl = np.ceil(max_e / interval_ft) * interval_ft
        levels = np.arange(s_lvl, e_lvl + interval_ft, interval_ft).tolist()
        if planet == 'Earth':
            if not params.get('show_ocean', True):
                levels = [l for l in levels if l >= 0]
            elif is_circle:
                ocean_step = interval_ft * params.get('index_interval', 5)
                levels = [l for l in levels if l >= 0 or (l < 0 and l % ocean_step == 0)]

    if len(levels) == 0: return segments
    levels = np.unique(np.sort(levels))

    if planet == 'Earth':
        R_planet = 6371000.0
    elif planet == 'Moon':
        R_planet = 1737400.0
    elif planet == 'Mars':
        R_planet = 3389500.0
    else:
        R_planet = 6371000.0

    mid_lat = np.radians((data_extent[2] + data_extent[3]) / 2)
    mid_lon = (data_extent[0] + data_extent[1]) / 2
    cos_lat = np.cos(mid_lat)
    deg2rad = np.pi / 180.0

    simp_tol = params.get('simp_tol', 0.0)
    smooth_val = params.get('smooth_val', 0.0)
    chaikin_iters = 0
    if smooth_val > 10.0: chaikin_iters = 1
    if smooth_val > 40.0: chaikin_iters = 2
    if smooth_val > 150.0: chaikin_iters = 3

    mask_polygon = None
    if HAS_GPD and planet == 'Earth' and not is_tahoe and "None" not in selected_region:
        if progress_callback: progress_callback(f"Generating region mask for {selected_region}...")
        w_col = 'NAME' if 'NAME' in world_borders.columns else 'name' if 'name' in world_borders.columns else None
        s_col = 'name' if 'name' in state_borders.columns else None
        geom = None
        if s_col and selected_region in state_borders[s_col].values:
            geom = state_borders[state_borders[s_col] == selected_region].geometry.union_all()
        elif w_col and selected_region in world_borders[w_col].values:
            geom = world_borders[world_borders[w_col] == selected_region].geometry.union_all()
        if geom:
            view_box = shp_box(data_extent[0], data_extent[2], data_extent[1], data_extent[3])
            mask_polygon = geom.intersection(view_box)

    if progress_callback: progress_callback("Calculating elevation matrix intersections...")
    fig_temp = Figure();
    ax_temp = fig_temp.add_subplot(111)
    cs = ax_temp.contour(crop_ft, levels=levels, extent=data_extent, origin='upper')

    if progress_callback: progress_callback(f"Smoothing and projecting {len(cs.levels)} vector layers...")
    for lvl_idx, level in enumerate(cs.levels):
        segs = cs.allsegs[lvl_idx]
        is_index = False
        is_water = (planet == 'Earth' and level < 0 and params.get('blue_water', True))

        if is_tahoe:
            max_level = np.max(levels)
            if level == max_level:
                is_index = True
            elif params.get('auto_index', True) and round((max_level - level) / interval_ft) % params.get(
                'index_interval', 5) == 0:
                is_index = True
        else:
            if params.get('auto_index', True) and round(level / interval_ft) % params.get('index_interval',
                                                                                          5) == 0: is_index = True

        pen_id = 'blue_038' if is_water else ('pen_1' if is_index else 'base')

        # --- MASSIVE SPEED OPTIMIZATION: BATCH GEOMETRY PROCESSING ---
        meter_segs = []
        for seg in segs:
            if len(seg) < 2: continue
            xs = (seg[:, 0] - mid_lon) * deg2rad * R_planet * cos_lat
            ys = (seg[:, 1] - mid_lat) * deg2rad * R_planet
            meter_segs.append(np.column_stack((xs, ys)))

        if not meter_segs: continue

        # Batch-run Shapely simplify in C (Bypasses the Python loops)
        if simp_tol > 0:
            mls = MultiLineString(meter_segs)
            simplified = mls.simplify(simp_tol,
                                      preserve_topology=False)  # preserve_topology=False is drastically faster
            if simplified.is_empty:
                continue
            elif simplified.geom_type == 'LineString':
                meter_segs = [np.array(simplified.coords)]
            elif simplified.geom_type == 'MultiLineString':
                meter_segs = [np.array(g.coords) for g in simplified.geoms]

        for m_seg in meter_segs:
            if len(m_seg) < 2: continue
            if chaikin_iters > 0 and len(m_seg) >= 3:
                m_seg = chaikin_smooth(m_seg, chaikin_iters)

            out_seg = np.empty_like(m_seg)
            out_seg[:, 0] = mid_lon + m_seg[:, 0] / (deg2rad * R_planet * cos_lat)
            out_seg[:, 1] = mid_lat + m_seg[:, 1] / (deg2rad * R_planet)

            if mask_polygon and not mask_polygon.is_empty:
                line_geom = LineString(out_seg)
                clipped = line_geom.intersection(mask_polygon)
                valid_lines = []
                if clipped.is_empty:
                    continue
                elif clipped.geom_type == 'LineString':
                    valid_lines.append(clipped)
                elif clipped.geom_type == 'MultiLineString':
                    valid_lines.extend(list(clipped.geoms))
                elif clipped.geom_type == 'GeometryCollection':
                    for g in clipped.geoms:
                        if g.geom_type == 'LineString':
                            valid_lines.append(g)
                        elif g.geom_type == 'MultiLineString':
                            valid_lines.extend(list(g.geoms))

                for v_line in valid_lines:
                    if len(v_line.coords) >= 2:
                        c = np.array(v_line.coords)
                        if is_circle: c = ortho_project(c, R_planet, mid_lon, deg2rad)
                        segments.append({'type': 'dem', 'seg': c, 'level': level, 'pen_id': pen_id})
            else:
                if is_circle: out_seg = ortho_project(out_seg, R_planet, mid_lon, deg2rad)
                segments.append({'type': 'dem', 'seg': out_seg, 'level': level, 'pen_id': pen_id})

    # 7. Render Geopolitical Reference Borders (Fast Extraction)
    if params.get('show_borders', True) and planet == 'Earth' and not is_tahoe:
        if progress_callback: progress_callback("Extracting geopolitical borders...")

        def append_border(coords):
            c = np.array(coords)
            if data_extent[1] > 180:
                c[:, 0] = np.where(c[:, 0] < 0, c[:, 0] + 360, c[:, 0])
            elif data_extent[0] < -180:
                c[:, 0] = np.where(c[:, 0] > 0, c[:, 0] - 360, c[:, 0])
            if is_circle: c = ortho_project(c, R_planet, mid_lon, deg2rad)
            segments.append({'type': 'border', 'seg': c, 'level': 0, 'pen_id': 'pen_1'})

        if mask_polygon and not mask_polygon.is_empty:
            bound_geom = mask_polygon.boundary
            if bound_geom.geom_type == 'LineString':
                append_border(bound_geom.coords)
            elif bound_geom.geom_type == 'MultiLineString':
                for line in bound_geom.geoms: append_border(line.coords)

        elif HAS_GPD and "None" in selected_region:
            view_boxes = []
            if data_extent[0] < -180:
                view_boxes.extend([shp_box(data_extent[0] + 360, -90, 180, 90), shp_box(-180, -90, data_extent[1], 90)])
            elif data_extent[1] > 180:
                view_boxes.extend([shp_box(data_extent[0], -90, 180, 90), shp_box(-180, -90, data_extent[1] - 360, 90)])
            else:
                view_boxes.append(shp_box(data_extent[0], -90, data_extent[1], 90))

            for v_box in view_boxes:
                for gdf in [world_borders, state_borders]:
                    if gdf is not None:
                        # Spatial bounding index completely prevents the border freeze
                        subset = gdf.cx[v_box.bounds[0]:v_box.bounds[2], v_box.bounds[1]:v_box.bounds[3]]
                        if not subset.empty:
                            try:
                                clipped = gpd.clip(subset, v_box)
                                if not clipped.empty:
                                    for geom in clipped.geometry:
                                        bound = geom.boundary
                                        if bound.geom_type == 'LineString':
                                            append_border(bound.coords)
                                        elif bound.geom_type == 'MultiLineString':
                                            for line in bound.geoms: append_border(line.coords)
                            except Exception:
                                pass

    if params.get('show_apollo', False) and planet == 'Moon':
        if progress_callback: progress_callback("Mapping Apollo site landing vectors...")
        apollo_sites = {
            "Apollo 11": (0.67416, 23.47314), "Apollo 12": (-3.0128, -23.4219),
            "Apollo 14": (-3.64589, -17.47194), "Apollo 15": (26.13239, 3.63330),
            "Apollo 16": (-8.9734, 15.5011), "Apollo 17": (20.1911, 30.7723)
        }
        rad = 0.5
        for name, (lat, lon) in apollo_sites.items():
            c1 = np.array([[lon - rad, lat - rad], [lon + rad, lat + rad]])
            c2 = np.array([[lon - rad, lat + rad], [lon + rad, lat - rad]])
            if is_circle:
                lon_diff = (lon - mid_lon + 180) % 360 - 180
                if abs(lon_diff) <= 90:
                    segments.append({'type': 'border', 'seg': ortho_project(c1, R_planet, mid_lon, deg2rad), 'level': 0,
                                     'pen_id': 'red_038'})
                    segments.append({'type': 'border', 'seg': ortho_project(c2, R_planet, mid_lon, deg2rad), 'level': 0,
                                     'pen_id': 'red_038'})
            else:
                segments.append({'type': 'border', 'seg': c1, 'level': 0, 'pen_id': 'red_038'})
                segments.append({'type': 'border', 'seg': c2, 'level': 0, 'pen_id': 'red_038'})

    if is_circle:
        theta = np.linspace(0, 2 * np.pi, 200)
        globe_circle = np.column_stack((R_planet * np.cos(theta), R_planet * np.sin(theta)))
        segments.append({'type': 'border', 'seg': globe_circle, 'level': 0, 'pen_id': 'pen_1'})

    return segments