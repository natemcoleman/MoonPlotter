import numpy as np


# ==========================================
# VECTORIZED MATH ALGORITHMS (No JIT Required)
# ==========================================

def chaikin_smooth(coords, iterations=2):
    """Vectorized Chaikin corner-cutting for sweeping cartographic curves."""
    if len(coords) < 3 or iterations == 0:
        return coords

    for _ in range(iterations):
        p0, p1 = coords[:-1], coords[1:]
        q = 0.75 * p0 + 0.25 * p1
        r = 0.25 * p0 + 0.75 * p1
        new_coords = np.empty((len(coords) * 2 - 2, 2))
        new_coords[0::2] = q
        new_coords[1::2] = r
        coords = np.vstack((coords[0], new_coords, coords[-1]))

    return coords


def ortho_project(coords, R_planet, mid_lon, deg2rad):
    """Vectorized 3D Orthographic spherical projection."""
    c = np.array(coords)
    lam = c[:, 0] * deg2rad
    phi = c[:, 1] * deg2rad
    lam0 = mid_lon * deg2rad
    x = R_planet * np.cos(phi) * np.sin(lam - lam0)
    y = R_planet * np.sin(phi)

    return np.column_stack((x, y))