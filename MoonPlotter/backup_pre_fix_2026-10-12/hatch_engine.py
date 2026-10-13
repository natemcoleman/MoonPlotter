import numpy as np
from geometry_utils import ortho_project


def generate_hatches(color_crop, data_extent, params, progress_callback=None):
    segments = []
    if color_crop is None: return segments

    mode = params.get('plot_mode', '')
    strategy = params.get('hatch_strategy', 'Multi-Pass Thresholded')
    is_circle = params.get('circle_mode', False)
    planet = params.get('active_planet', 'Earth')

    if planet == 'Earth':
        R_planet = 6371000.0
    elif planet == 'Moon':
        R_planet = 1737400.0
    else:
        R_planet = 3389500.0

    lon_min, lon_max, lat_min, lat_max = data_extent
    # FIXED: Keep mid_lat in degrees for the geographical bounding box!
    mid_lat = (lat_min + lat_max) / 2.0
    mid_lon = (lon_min + lon_max) / 2.0
    deg2rad = np.pi / 180.0

    view_width = lon_max - lon_min
    if view_width == 0: return segments
    base_spacing = view_width / 150.0

    if progress_callback: progress_callback(f"Analyzing luminance using {strategy}...")

    img_norm = color_crop.astype(np.float32) / 255.0

    # Extract Channels based on Mode
    if "Monochrome" in mode:
        intensity_maps = {
            'pen_1': np.clip(1.0 - (0.299 * img_norm[:, :, 0] + 0.587 * img_norm[:, :, 1] + 0.114 * img_norm[:, :, 2]),
                             0, 1)}
        if "Multi-Pass" in strategy:
            passes = [{'pen': 'pen_1', 'angle': 45, 'thresh': 0.3}, {'pen': 'pen_1', 'angle': 135, 'thresh': 0.6},
                      {'pen': 'pen_1', 'angle': 0, 'thresh': 0.85}]
        else:
            passes = [{'pen': 'pen_1', 'angle': 0, 'thresh': 0.1}]
    else:
        intensity_maps = {'cyan_07': np.clip(1.0 - img_norm[:, :, 0], 0, 1),
                          'pink_07': np.clip(1.0 - img_norm[:, :, 1], 0, 1)}
        if "Multi-Pass" in strategy:
            passes = [{'pen': 'cyan_07', 'angle': 15, 'thresh': 0.3}, {'pen': 'cyan_07', 'angle': 105, 'thresh': 0.65},
                      {'pen': 'pink_07', 'angle': 75, 'thresh': 0.3}, {'pen': 'pink_07', 'angle': 165, 'thresh': 0.65}]
        else:
            passes = [{'pen': 'cyan_07', 'angle': 15, 'thresh': 0.1}, {'pen': 'pink_07', 'angle': 75, 'thresh': 0.1}]

    H, W = color_crop.shape[:2]
    if H == 0 or W == 0: return segments

    for p in passes:
        pen_id = p['pen'];
        angle_deg = p['angle'];
        threshold = p['thresh']
        if progress_callback: progress_callback(f"Generating {angle_deg}\u00b0 {pen_id} hatches...")

        angle_rad = np.radians(angle_deg)
        cos_a, sin_a = np.cos(angle_rad), np.sin(angle_rad)
        diag = np.sqrt(view_width ** 2 + (lat_max - lat_min) ** 2)

        offsets = np.arange(-diag, diag, base_spacing)
        t_vals = np.arange(-diag, diag, base_spacing * 0.5)

        for offset in offsets:
            x_line = mid_lon + offset * np.cos(angle_rad + np.pi / 2) + t_vals * cos_a
            y_line = mid_lat + offset * np.sin(angle_rad + np.pi / 2) + t_vals * sin_a

            # Bound points to the user's selection window
            valid = (x_line >= lon_min) & (x_line <= lon_max) & (y_line >= lat_min) & (y_line <= lat_max)
            x_val, y_val = x_line[valid], y_line[valid]
            if len(x_val) < 2: continue

            ix = np.clip(((x_val - lon_min) / view_width * (W - 1)).astype(int), 0, W - 1)
            iy = np.clip(((lat_max - y_val) / (lat_max - lat_min) * (H - 1)).astype(int), 0, H - 1)

            local_intensity = intensity_maps[pen_id][iy, ix]

            if "Squiggles" in strategy:
                # STRATEGY 3: Modulate line amplitude vertically based on shadow intensity
                amp = local_intensity * (base_spacing * 1.5)
                freq = 25.0
                x_sq = x_val + np.sin(t_vals[valid] * freq) * amp * np.cos(angle_rad + np.pi / 2)
                y_sq = y_val + np.sin(t_vals[valid] * freq) * amp * np.sin(angle_rad + np.pi / 2)

                if np.mean(local_intensity) > 0.05:  # Only draw if there's *some* shadow
                    seg = np.column_stack((x_sq, y_sq))
                    if is_circle: seg = ortho_project(seg, R_planet, mid_lon, deg2rad)
                    segments.append({'type': 'hatch', 'seg': seg, 'level': 0, 'pen_id': pen_id})

            elif "Variable Spacing" in strategy:
                # STRATEGY 2: Use random 1D noise thresholding to break lines organically
                noise = np.random.rand(len(x_val))
                ink_mask = local_intensity > noise
                splits = np.where(np.diff(ink_mask.astype(int)) != 0)[0] + 1
                for cx, cy, cmask in zip(np.split(x_val, splits), np.split(y_val, splits), np.split(ink_mask, splits)):
                    if cmask.all() and len(cx) >= 2:
                        seg = np.column_stack((cx, cy))
                        if is_circle: seg = ortho_project(seg, R_planet, mid_lon, deg2rad)
                        segments.append({'type': 'hatch', 'seg': seg, 'level': 0, 'pen_id': pen_id})

            else:
                # STRATEGY 1: Multi-Pass Thresholded (Standard plot)
                ink_mask = local_intensity > threshold
                splits = np.where(np.diff(ink_mask.astype(int)) != 0)[0] + 1
                for cx, cy, cmask in zip(np.split(x_val, splits), np.split(y_val, splits), np.split(ink_mask, splits)):
                    if cmask.all() and len(cx) >= 2:
                        seg = np.column_stack((cx, cy))
                        if is_circle: seg = ortho_project(seg, R_planet, mid_lon, deg2rad)
                        segments.append({'type': 'hatch', 'seg': seg, 'level': 0, 'pen_id': pen_id})

    if progress_callback: progress_callback("Hatching complete!")
    return segments