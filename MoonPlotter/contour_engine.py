import warnings

import numpy as np
import scipy.ndimage
from shapely.geometry import box as shp_box, LineString, MultiLineString
from matplotlib.figure import Figure

from config import DATA_FILES, PLANET_RADII, AUTO_CONTOUR_TARGET_LINES, NICE_INTERVALS_FT, TAHOE
from geometry_utils import chaikin_smooth, ortho_project

MAX_CONTOUR_LEVELS = 600

# Natural Earth borders are loaded ONCE here; main.py imports them from this module.
world_borders = None
state_borders = None
HAS_GPD = False
try:
    import geopandas as gpd

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        world_borders = gpd.read_file(
            "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip")
        state_borders = gpd.read_file(
            "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_1_states_provinces.zip")
    HAS_GPD = True
except ImportError:
    print("WARNING: geopandas not installed - borders and region masking disabled.")
except Exception as e:  # offline, server error, bad download...
    print(f"WARNING: could not load Natural Earth borders ({e}) - borders and region masking disabled.")
    world_borders = None
    state_borders = None


def auto_contour_interval(relief_ft, target_lines=AUTO_CONTOUR_TARGET_LINES):
    """Smallest standard interval that keeps the relief within ~target_lines contours."""
    if not np.isfinite(relief_ft) or relief_ft <= 0:
        return None
    for step in NICE_INTERVALS_FT:
        if relief_ft / step <= target_lines:
            return step
    return NICE_INTERVALS_FT[-1]


def iter_lines(geom):
    """Yields every LineString inside any (multi/collection) geometry; ignores points/polygons."""
    if geom is None or geom.is_empty:
        return
    gt = geom.geom_type
    if gt in ('LineString', 'LinearRing'):
        yield geom
    elif gt in ('MultiLineString', 'GeometryCollection'):
        for g in geom.geoms:
            yield from iter_lines(g)


def generate_contours(dem_crop, data_extent, view_extent, params, progress_callback=None):
    segments = []
    if dem_crop is None or np.isnan(dem_crop).all():
        return segments

    if progress_callback: progress_callback("Processing DEM topography...")

    planet = params.get('active_planet', 'Earth')
    selected_region = params.get('selected_region', 'None (Plot Full Selection)')
    is_tahoe = (planet == 'Earth' and "Tahoe" in selected_region)
    is_circle = params.get('circle_mode', False)
    mult = params.get('dem_multiplier', DATA_FILES[planet]['multiplier'])
    dem_extent = params.get('dem_extent', data_extent)

    crop_ft = dem_crop * mult
    valid_mask = ~np.isnan(crop_ft)

    # Blur sigma is specified in native DEM pixels; scale it if the worker decimated the DEM.
    blur_sigma = params.get('blur_sigma', 0.0) / max(1, params.get('dem_decimation', 1))
    if blur_sigma > 0.05:
        if progress_callback: progress_callback(f"Applying Gaussian blur (σ={blur_sigma:.2f}px)...")
        safe_crop = np.where(valid_mask, crop_ft, np.nanmax(crop_ft))
        blurred = scipy.ndimage.gaussian_filter(safe_crop, sigma=blur_sigma, mode='nearest')
        crop_ft = np.where(valid_mask, blurred, np.nan)

    min_e, max_e = np.nanmin(crop_ft), np.nanmax(crop_ft)
    interval_ft = float(params.get('interval_ft', 200))
    if interval_ft <= 0:
        return segments

    if is_tahoe:
        # Bathymetric contours every interval below the lake surface. The shoreline itself is
        # drawn from the lake mask further down (the DEM is NaN outside the lake).
        surface = TAHOE['surface_ft']
        levels = np.sort(np.arange(surface - interval_ft, min_e, -interval_ft)).tolist()
    else:
        s_lvl = np.floor(min_e / interval_ft) * interval_ft
        e_lvl = np.ceil(max_e / interval_ft) * interval_ft
        n_lvls = int(round((e_lvl - s_lvl) / interval_ft)) + 1
        if n_lvls > MAX_CONTOUR_LEVELS:
            raise ValueError(f"A {interval_ft:g} ft interval would create {n_lvls} contour levels over "
                             f"{max_e - min_e:,.0f} ft of relief. Use a larger interval.")
        levels = np.arange(s_lvl, e_lvl + interval_ft, interval_ft).tolist()
        if planet == 'Earth':
            if not params.get('show_ocean', True):
                levels = [l for l in levels if l >= 0]
            elif is_circle:
                ocean_step = interval_ft * params.get('index_interval', 5)
                levels = [l for l in levels if l >= 0 or (l < 0 and l % ocean_step == 0)]

    if len(levels) == 0: return segments
    levels = np.unique(np.sort(levels))

    R_planet = PLANET_RADII.get(planet, 6371000.0)

    # Local equirectangular frame (metres) used for simplify/smooth so tolerances are in metres.
    mid_lat_deg = (data_extent[2] + data_extent[3]) / 2.0
    mid_lon = (data_extent[0] + data_extent[1]) / 2.0
    cos_lat = np.cos(np.radians(mid_lat_deg))
    deg2rad = np.pi / 180.0
    kx = deg2rad * R_planet * cos_lat
    ky = deg2rad * R_planet

    simp_tol = params.get('simp_tol', 0.0)
    smooth_val = params.get('smooth_val', 0.0)
    chaikin_iters = 0
    if smooth_val > 10.0: chaikin_iters = 1
    if smooth_val > 40.0: chaikin_iters = 2
    if smooth_val > 150.0: chaikin_iters = 3

    region_geom = None
    mask_polygon = None
    if HAS_GPD and planet == 'Earth' and not is_tahoe and "None" not in selected_region:
        if progress_callback: progress_callback(f"Generating region mask for {selected_region}...")
        w_col = 'NAME' if 'NAME' in world_borders.columns else 'name' if 'name' in world_borders.columns else None
        s_col = 'name' if 'name' in state_borders.columns else None
        if s_col and selected_region in state_borders[s_col].values:
            region_geom = state_borders[state_borders[s_col] == selected_region].geometry.union_all()
        elif w_col and selected_region in world_borders[w_col].values:
            region_geom = world_borders[world_borders[w_col] == selected_region].geometry.union_all()
        if region_geom is not None and not region_geom.is_empty:
            view_box = shp_box(data_extent[0], data_extent[2], data_extent[1], data_extent[3])
            mask_polygon = region_geom.intersection(view_box)
            if mask_polygon.is_empty:
                mask_polygon = None

    if progress_callback: progress_callback("Calculating elevation matrix intersections...")
    fig_temp = Figure()
    ax_temp = fig_temp.add_subplot(111)
    cs = ax_temp.contour(crop_ft, levels=levels, extent=dem_extent, origin='upper')

    if progress_callback: progress_callback(f"Smoothing and projecting {len(cs.levels)} vector layers...")
    index_interval = params.get('index_interval', 5)
    contour_sets = [(level, cs.allsegs[i]) for i, level in enumerate(cs.levels)]
    if is_tahoe:
        # Shoreline = outline of the lake mask, drawn with the index pen.
        cs_shore = ax_temp.contour(valid_mask.astype(np.float32), levels=[0.5], extent=dem_extent, origin='upper')
        contour_sets.append((TAHOE['surface_ft'], cs_shore.allsegs[0]))

    # Illuminated (Tanaka) contours: each contour is split where it turns toward / away from a
    # light in the north-west. Lit stretches use the thin pen, shadowed stretches the thick pen.
    tanaka = params.get('tanaka', False)
    if tanaka:
        Hd, Wd = crop_ft.shape
        dlon = (dem_extent[1] - dem_extent[0]) / Wd
        dlat = (dem_extent[3] - dem_extent[2]) / Hd
        filled = np.where(valid_mask, crop_ft, np.nanmean(crop_ft))
        g_row, g_col = np.gradient(filled)
        lat_rows = dem_extent[3] - (np.arange(Hd) + 0.5) * dlat
        mx = (dlon * deg2rad * R_planet * np.maximum(np.cos(np.radians(lat_rows)), 1e-3))[:, None]
        grad_e = g_col / mx                                   # ft per metre, eastward
        grad_n = -g_row / (dlat * deg2rad * R_planet)         # ft per metre, northward
        az = np.radians(params.get('light_azimuth', 315.0))
        light_e, light_n = np.sin(az), np.cos(az)

    def emit_contour(c, level, pen_id, is_water):
        pieces = [(c, pen_id)]
        if tanaka and not is_water and len(c) >= 2:
            mid = 0.5 * (c[:-1] + c[1:])
            col = np.clip(((mid[:, 0] - dem_extent[0]) / dlon).astype(np.int64), 0, Wd - 1)
            row = np.clip(((dem_extent[3] - mid[:, 1]) / dlat).astype(np.int64), 0, Hd - 1)
            # A slope is lit when its downhill direction points toward the light.
            lit = (-grad_e[row, col] * light_e - grad_n[row, col] * light_n) >= 0
            cuts = np.nonzero(np.diff(lit.astype(np.int8)))[0] + 1
            bounds = np.concatenate([[0], cuts, [len(lit)]])
            pieces = [(c[a:b + 1], 'base' if lit[a] else 'pen_1') for a, b in zip(bounds[:-1], bounds[1:])]
        for pc, pid in pieces:
            if len(pc) < 2: continue
            if is_circle: pc = ortho_project(pc, R_planet, mid_lon, deg2rad)
            segments.append({'type': 'dem', 'layer': 'contours', 'seg': pc, 'level': level, 'pen_id': pid})

    for level, segs in contour_sets:
        is_index = False
        is_water = (planet == 'Earth' and level < 0 and params.get('blue_water', True))

        if is_tahoe:
            steps_down = round((TAHOE['surface_ft'] - level) / interval_ft)
            if steps_down == 0:
                is_index = True                       # shoreline
            elif params.get('auto_index', True) and steps_down % index_interval == 0:
                is_index = True
        elif params.get('auto_index', True) and round(level / interval_ft) % index_interval == 0:
            is_index = True

        pen_id = 'blue_038' if is_water else ('pen_1' if is_index else 'base')

        meter_segs = [np.column_stack(((seg[:, 0] - mid_lon) * kx, (seg[:, 1] - mid_lat_deg) * ky))
                      for seg in segs if len(seg) >= 2]
        if not meter_segs: continue

        # Batch Douglas-Peucker in GEOS (one call per level).
        if simp_tol > 0:
            simplified = MultiLineString(meter_segs).simplify(simp_tol, preserve_topology=False)
            meter_segs = [np.asarray(g.coords) for g in iter_lines(simplified)]

        for m_seg in meter_segs:
            if len(m_seg) < 2: continue
            if chaikin_iters > 0 and len(m_seg) >= 3:
                m_seg = chaikin_smooth(m_seg, chaikin_iters)

            out_seg = np.empty_like(m_seg)
            out_seg[:, 0] = mid_lon + m_seg[:, 0] / kx
            out_seg[:, 1] = mid_lat_deg + m_seg[:, 1] / ky

            if mask_polygon is not None:
                for v_line in iter_lines(LineString(out_seg).intersection(mask_polygon)):
                    if len(v_line.coords) >= 2:
                        c = np.asarray(v_line.coords)
                        emit_contour(c, level, pen_id, is_water)
            else:
                emit_contour(out_seg, level, pen_id, is_water)

    # Geopolitical reference borders. Border LINES are clipped (not polygons), so no fake
    # border segments appear along the edges of the selection.
    if params.get('show_borders', True) and planet == 'Earth' and not is_tahoe and HAS_GPD:
        if progress_callback: progress_callback("Extracting geopolitical borders...")

        def append_border(coords):
            c = np.array(coords)[:, :2]
            if len(c) < 2: return
            if data_extent[1] > 180:
                c[:, 0] = np.where(c[:, 0] < 0, c[:, 0] + 360, c[:, 0])
            elif data_extent[0] < -180:
                c[:, 0] = np.where(c[:, 0] > 0, c[:, 0] - 360, c[:, 0])
            if is_circle: c = ortho_project(c, R_planet, mid_lon, deg2rad)
            segments.append({'type': 'border', 'layer': 'borders', 'seg': c, 'level': 0, 'pen_id': 'pen_1'})

        if mask_polygon is not None:
            view_box = shp_box(data_extent[0], data_extent[2], data_extent[1], data_extent[3])
            for line in iter_lines(region_geom.boundary.intersection(view_box)):
                append_border(line.coords)

        elif "None" in selected_region:
            view_boxes = []
            if data_extent[0] < -180:
                view_boxes.extend([shp_box(data_extent[0] + 360, -90, 180, 90), shp_box(-180, -90, data_extent[1], 90)])
            elif data_extent[1] > 180:
                view_boxes.extend([shp_box(data_extent[0], -90, 180, 90), shp_box(-180, -90, data_extent[1] - 360, 90)])
            else:
                view_boxes.append(shp_box(data_extent[0], -90, data_extent[1], 90))

            for v_box in view_boxes:
                minx, miny, maxx, maxy = v_box.bounds
                for gdf in [world_borders, state_borders]:
                    if gdf is None: continue
                    subset = gdf.cx[minx:maxx, miny:maxy]   # bbox pre-filter (fast)
                    for geom in subset.geometry:
                        if geom is None or geom.is_empty: continue
                        try:
                            clipped = geom.boundary.intersection(v_box)
                        except Exception:
                            continue
                        for line in iter_lines(clipped):
                            append_border(line.coords)

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
                    segments.append({'type': 'border', 'layer': 'borders', 'seg': ortho_project(c1, R_planet, mid_lon, deg2rad), 'level': 0,
                                     'pen_id': 'red_038'})
                    segments.append({'type': 'border', 'layer': 'borders', 'seg': ortho_project(c2, R_planet, mid_lon, deg2rad), 'level': 0,
                                     'pen_id': 'red_038'})
            else:
                segments.append({'type': 'border', 'layer': 'borders', 'seg': c1, 'level': 0, 'pen_id': 'red_038'})
                segments.append({'type': 'border', 'layer': 'borders', 'seg': c2, 'level': 0, 'pen_id': 'red_038'})

    if is_circle:
        theta = np.linspace(0, 2 * np.pi, 200)
        globe_circle = np.column_stack((R_planet * np.cos(theta), R_planet * np.sin(theta)))
        segments.append({'type': 'border', 'layer': 'borders', 'seg': globe_circle, 'level': 0, 'pen_id': 'pen_1'})

    return segments
