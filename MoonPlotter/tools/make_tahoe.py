"""One-off: build tahoe_lake_wgs84.tif (lake-only bathymetry, feet, EPSG:4326) from tahoe.tif (NAD83 UTM 11N)."""
import sys, numpy as np, tifffile, scipy.ndimage as nd
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # run from project folder: python tools/make_tahoe.py (needs tifffile)
from utm import utm_forward

SURFACE_FT = 6225.0
src = tifffile.imread('tahoe.tif').astype(np.float32)
src[src > 1e30] = np.nan
E_ul, N_ul, px = 215179.0569, 4352156.3099, 10.0           # from tahoe.tif ModelTiepoint / PixelScale

# 1. Lake mask in UTM space: water below the surface, connected to the deepest point,
#    with narrow outlets (Truckee River, marina channels) cut by a 120 m morphological opening.
seed = np.unravel_index(np.nanargmin(src), src.shape)
water = np.nan_to_num(src, nan=1e9) <= SURFACE_FT
r = 12
core = nd.distance_transform_edt(water) > r
core = nd.distance_transform_edt(~core) <= r                 # opening (erode then dilate)
lab, _ = nd.label(core); core = lab == lab[seed]
lake = water & (nd.distance_transform_edt(~core) <= r + 2)    # restore shoreline detail
lab, _ = nd.label(lake); lake = lab == lab[seed]
print('lake area km2', lake.sum() * px * px / 1e6)
dem = np.where(lake, src, np.nan)

# 2. Output lat/lon grid around the lake (+ ~1 km margin), ~10 m cells.
rows, cols = np.nonzero(lake)
# bounding box of the lake in lat/lon via a coarse search of forward-projected grid
lat_g = np.arange(38.80, 39.40, 0.0005); lon_g = np.arange(-120.35, -119.80, 0.0005)
LO, LA = np.meshgrid(lon_g, lat_g)
E, N = utm_forward(LA, LO, -117.0)
c = np.floor((E - E_ul) / px).astype(int); rr = np.floor((N_ul - N) / px).astype(int)
ok = (c >= 0) & (c < src.shape[1]) & (rr >= 0) & (rr < src.shape[0])
hit = np.zeros_like(ok); hit[ok] = lake[rr[ok], c[ok]]
lat_min, lat_max = LA[hit].min() - 0.01, LA[hit].max() + 0.01
lon_min, lon_max = LO[hit].min() - 0.012, LO[hit].max() + 0.012
dlat = 0.00009; dlon = 0.000116
H = int(np.ceil((lat_max - lat_min) / dlat)); W = int(np.ceil((lon_max - lon_min) / dlon))
lat_max = lat_min + H * dlat; lon_max = lon_min + W * dlon
print('bounds', lon_min, lat_min, lon_max, lat_max, 'size', W, H)

lats = lat_max - (np.arange(H) + 0.5) * dlat
lons = lon_min + (np.arange(W) + 0.5) * dlon
out = np.full((H, W), np.nan, np.float32)
for i0 in range(0, H, 256):
    LO, LA = np.meshgrid(lons, lats[i0:i0 + 256])
    E, N = utm_forward(LA, LO, -117.0)
    fc = (E - E_ul) / px - 0.5; fr = (N_ul - N) / px - 0.5     # pixel-centre coordinates
    out[i0:i0 + 256] = nd.map_coordinates(dem, [fr, fc], order=1, cval=np.nan, prefilter=False)
print('valid km2', np.isfinite(out).sum() * (dlat * 111320) * (dlon * 111320 * np.cos(np.radians(39.1))) / 1e6,
      'min', np.nanmin(out), 'max', np.nanmax(out))

geokeys = (1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326)
tifffile.imwrite('tahoe_lake_wgs84.tif', out, compression='zlib',
                 extratags=[(33550, 'd', 3, (dlon, dlat, 0.0), False),
                            (33922, 'd', 6, (0.0, 0.0, 0.0, lon_min, lat_max, 0.0), False),
                            (34735, 'H', len(geokeys), geokeys, False),
                            (42113, 's', 0, 'nan', False)])
print('BOUNDS = (%.6f, %.6f, %.6f, %.6f)' % (lon_min, lat_min, lon_max, lat_max))
