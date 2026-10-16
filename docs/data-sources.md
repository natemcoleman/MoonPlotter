# Data sources

The app reads two kinds of raster for each body:

- an **elevation model (DEM)**, used for contours, hillshade and hachures,
- a **colour map**, used for colour fills and the source-map picture.

There are **global** files, which the app always needs, and **regional** files, which are sharper data
for small areas that you fetch or import while using the app.

## Global files

All global files go in the `MoonPlotter/` app folder. File names are set in `DATA_FILES` in `config.py`.
Global files must be plain latitude/longitude grids covering the whole body (180°W–180°E, 90°N–90°S),
with north up.

| Body | Role | File name | In the repo? | Where to get it |
|---|---|---|---|---|
| Earth | Elevation (metres, land and sea floor) | `BE_dem_v2.tif` | **No** | NOAA [ETOPO 2022](https://www.ncei.noaa.gov/products/etopo-global-relief-model), 60 arc-second **ice surface** GeoTIFF (`ETOPO_2022_v1_60s_N90W180_surface.tif`). Rename it, or point `config.py` at it. |
| Earth | Colour | `earth_color.tif` | Yes (reduced size) | Any global colour mosaic. For more detail use NASA [Blue Marble Next Generation](https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/) at 21600 × 10800. |
| Moon | Elevation (kilometres) | `ldem_16.tif` | Yes | NASA SVS [CGI Moon Kit](https://svs.gsfc.nasa.gov/4720) (`ldem_16.tif`, 5760 × 2880) |
| Moon | Colour | `lroc_color_16bit_srgb_4k.tif` | Yes | NASA SVS [CGI Moon Kit](https://svs.gsfc.nasa.gov/4720). The 8k and 16k versions also work: change the name in `config.py`. |
| Mars | Elevation (metres) | `Mars_MGS_MOLA_DEM_mosaic_global_463m.tif` | **No** (about 2 GB) | USGS Astrogeology: the URL is in `config.py` |
| Mars | Colour | `Mars_Viking_ClrMosaic_global_925m.tif` | **No** (about 760 MB) | USGS Astrogeology: the URL is in `config.py` |

Notes:

- Elevation units are set by the `'multiplier'` (to feet) for each body in `config.py`:
  - `3.28084` for metres,
  - `3280.84` for the Moon kit's kilometres.

  If you swap in a different DEM, set the multiplier to match its units.
- **Mars** stays disabled under **Source** until at least one of its files exists.
- If the Earth DEM is missing, selecting Earth (or drawing an Earth map) shows a message with the
  download link and where to put the file. Lake Tahoe and fetched regional elevation still work without
  it.
- `tahoe_lake_wgs84.tif` (in the repo) is a 10 m Lake Tahoe bathymetry model. It is used when you choose
  **Clip to region › Lake Tahoe (High-Res Bathymetry)**. `tools/make_tahoe.py` shows how it was built.
- Country and state borders come from [Natural Earth](https://www.naturalearthdata.com/). They are
  downloaded automatically when the app starts, if `geopandas` is installed and you are online.

## Regional data

The global files are coarse: about 1.8 km per pixel for the Earth DEM. For a valley, a crater or a
coastline, get sharper data for just that area:

1. Drag a selection on the source map.
2. Open **Tools › Data sources (sharper regional data)…**.
3. Under **Fetch the current selection**, pick a source, give the area a name, and click **Fetch for
   selection**. Only the pixels inside your window are downloaded.

| Body | Built-in sources |
|---|---|
| Earth | Elevation 30 m: Copernicus GLO-30 (land only) · Colour 10 m: Sentinel-2 cloudless 2024 (EOX, global) · Colour ~1 m: USGS orthoimagery / NAIP (USA only) |
| Moon | Elevation 59 m: LOLA + Kaguya merge (60°N–60°S) · Elevation 474 m: LOLA ldem_64 (global) · Colour 16k: LROC (global) |
| Mars | Elevation 200 m: HRSC + MOLA blend (global) |

Fetched files are saved in `MoonPlotter/regions/` as `<name>.tif` plus a `<name>.json` that records
the body, kind, bounds, units and source. From then on, **any selection that lies inside a regional file
uses it automatically** instead of the global file. The status line says when it does. The **Regional
data** tab lists what you have and can delete entries. Fetched areas are capped at 6000 px on the long
side. Fetch the area you'll plot, not a whole country.

Elevation and colour are separate downloads. Fetch one of each for the same area if you use colour fills.

### Importing your own GeoTIFF

**Import GeoTIFF…** copies any georeferenced GeoTIFF into the regions folder. You choose whether it is
elevation or colour, and the elevation units. Projected files, such as UTM, are re-projected to
latitude/longitude. Good sources:

- **USGS 3DEP** 1/3 arc-second (~10 m) and 1 m lidar, USA only:
  [apps.nationalmap.gov/downloader](https://apps.nationalmap.gov/downloader/)
- **GEBCO** (land and ocean depths): [download.gebco.net](https://download.gebco.net/). Draw a box and choose
  GeoTIFF.
- **OpenTopography** (free account): [portal.opentopography.org](https://portal.opentopography.org/)

The **Where to get data** tab in the dialog has the same guidance.

### Downloads behind antivirus or corporate networks

Some security software inspects HTTPS traffic, so Python rejects the certificate ("self-signed
certificate in certificate chain"). When that happens, the app retries with the `truststore` package if
it is installed, then with Windows' own `curl.exe`, then with PowerShell. These use the system
certificate store. If downloads still fail, run `pip install truststore`.

## Licences and credits

| Data | Licence / terms |
|---|---|
| NOAA ETOPO 2022, USGS 3DEP and orthoimagery, NASA SVS, USGS Astrogeology, JPL Horizons | US Government works, public domain. Credit is appreciated. |
| Copernicus GLO-30 DEM | Free to use with attribution: "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA" |
| Sentinel-2 cloudless (EOX) | **CC BY-NC-SA 4.0: non-commercial use only.** Credit: "Sentinel-2 cloudless by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2024)". Don't sell plots made with it. |
| GEBCO | Public domain, with attribution to the GEBCO Compilation Group |
| Natural Earth | Public domain |

Check each provider's current terms before using plots commercially.
