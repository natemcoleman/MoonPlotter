# project_context.md

_Last updated: 16 Nov 2026 · repo state: `main` after the City road maps commit (not pushed)_

## Project Overview
**MoonPlotter** ("Topo Plotter Studio — Cartographer Edition", github.com/natemcoleman/MoonPlotter, MIT) is a
PyQt6 desktop app that turns topographic data and vector artwork into G-code for a **Creality Ender 3
converted into a multi-colour pen plotter and a drag-knife paper cutter**.

Goals:
- Make plotter-ready artwork from elevation data and artwork:
  - contour and hatch maps of Earth, Moon and Mars (global DEMs or fetched 30 m regional data);
  - lake bathymetry and ski-resort maps;
  - Solar System mission charts and an all-planets poster diagram;
  - **city road maps** (every road of a city from OpenStreetMap, after anvaka/city-roads);
  - traced images and multi-layer SVGs.
- Produce physical outputs:
  - multi-pen plots with `M0` pen swaps;
  - tiled multi-page plots;
  - card cut-outs;
  - **layered cardstock relief maps** (height bands cut with the knife, marked with the pen, glued in a stack).
- Generate G-code that suits the hardware: safe Z, unidirectional strokes where needed, knife corner
  loops, per-pen feed rates, accurate time estimates.

## Architecture & Tech Stack
- **Language:** Python 3.12 on Windows, in a `.venv` inside `MoonPlotter/`. Run from PyCharm with
  "Run › Current File" on `main.py`.
- **Libraries** (`requirements.txt`):
  - PyQt6 (GUI), matplotlib (preview and export figures), numpy and scipy;
  - rasterio (rasters), shapely (geometry);
  - vpype and vpype-gcode (stroke optimisation and G-code), Pillow;
  - optional: geopandas (Natural Earth borders) and truststore.
- **External data and services:**
  - NOAA ETOPO 2022, NASA SVS Moon kit, USGS Mars;
  - Copernicus GLO-30 (regional fetch), Sentinel-2 cloudless / USGS NAIP;
  - Natural Earth;
  - OpenStreetMap via Nominatim and the Overpass API (ski runs, lake shorelines, city roads, water, rail, parks);
  - NASA JPL Horizons (missions).
- **Hardware:**
  - Ender 3 running Marlin. Z moves at about 5 mm/s on stock firmware.
  - Reachable bed is about 197 × 217 mm (`BED_MM` 235² minus `GCODE_OFFSET_MM` (38, 18)).
  - Gel pens sit in a **rubber-band holder**: Z travel allowed, but the zero is fixed by the hardware.
  - **Drag knife:** 2 mm shank, 30/45/60° blades; it drops in at a fixed height.
- **Materials:** 65 lb cardstock only (about 0.23 mm thick), cut from sheets of about 150 × 150 mm.

## File Structure & Core Logic
All application code is in `MoonPlotter/`. Data paths are relative to that folder; `main.py` switches to it on start-up.

| Module | Role |
|---|---|
| `main.py` (~6,300 lines) | `PlotterApp` window: sidebar sections, source map, preview, layers panel, editing tools, projects, export orchestration, background threads (`_SkiFetch`, `_LakeBuild`, `_CityFetch`, missions, tracing) |
| `config.py` | Paper sizes and colours, built-in pens, `DATA_FILES`, `LAKES`, plotter and G-code constants; **per-machine overrides** loaded from `machine_settings.json` |
| `workers.py` | `RenderWorker` (QThread): reads the DEM / colour window (regional or lake raster if one covers the selection), runs the contour and fill engines, returns segments plus `dem_data` and `dem_mult` |
| `contour_engine.py` | Contours, index lines, Tanaka shading, below-sea-level split, borders, auto interval, lake bathymetry levels |
| `hatch_engine.py` | Fills: crosshatch, scanlines, sine squiggles, stipple, hachures; CIELAB colour separation; globe full-disc fill (`_DiscFloorRaster`) |
| `decorations.py`, `stroke_font.py` | Title block, scale bar, north arrow, grid, frame, text masks; Hershey single-stroke lettering |
| `exporter.py` | G-code header and footer, `G92` origin, pen lifts and changes, vpype command, drag-knife cut-out with corner loops, time estimate (Z-speed aware), bed checks. No Qt |
| `export_dialog.py`, `pen_dialog.py`, `data_dialog.py`, `calib_dialog.py` | Dialogs |
| `regions.py`, `net.py`, `dem_prep.py` | Regional raster fetch and import; robust HTTP; one-time `.ovr` overviews |
| `lakes.py` | Lake regions: Tahoe survey raster; Jenny and Jackson **modelled beds** (OSM shoreline in `lakes/*.json` + published depths → `lakes/*.tif`) |
| `ski.py` | Ski resorts (Overpass, mirror fallbacks, boundary filter) → run and lift segments; cached in `ski_resorts/*.json` |
| `svg_import.py`, `image_trace.py` | Multi-layer SVG import; image tracing |
| `city_roads.py` | **City road maps:** Nominatim boundary, Overpass download in ~60 km² parts (POST, retries, resumable, split on failure), `cities/*.npz` cache, road classes, clip to boundary or rectangle, two-pen split, joining OSM pieces into long strokes, crowding check, title |
| `missions.py`, `mission_plot.py`, `system_diagram.py` | JPL Horizons missions and charts; poster-style all-planets diagram |
| `layer_cut.py` | **Layered cut-outs:** height bands → shapely polygons → clean-up → pen marks (next-layer outline and layer number) → packing on sheets (FFT raster, 45° rotations, nesting in holes) → knife paths |
| `calibrate_z.py` | Pen-height and knife-depth test-ladder G-code and the result maths |
| `project_io.py`, `ui_widgets.py`, `theme.py` | Projects and presets, colourways; widgets; Qt style |
| `tests/test_engines.py` | 43 engine tests: G-code safety, contours, hatch, missions, tracing, cut-outs, calibration, city maps |
| `AGENTS.md`, `CLAUDE.md`, `docs/` | Agent handover notes; user and developer docs; **`docs/architecture.md`** maps every feature to its file |

**Data model:** everything drawable is a list of segment dicts `{'type','layer','seg' (n×2),'level','pen_id'}`.
- Map modes use lon/lat coordinates.
- Paper modes (Solar System, Image, City, calibration sheet) use mm, with the frame `[0, pw, -ph/2, ph/2]`.

**Render flow:** `_start_render()` → `RenderWorker` → `on_render_success()` → decorations → `render_canvas()`
(or the ink simulation; or `_draw_cut_preview()` in layered cut-out mode).

**Export flow:** `export_action()` → `ExportDialog` → for each tile and pen:
1. a temporary SVG at true paper size;
2. vpype;
3. `wrap_vpype_body`;
4. header and footer, or `M0` pen changes.

In layered cut-out mode, `export_cut_sheets()` writes one file per sheet instead: pen marks, `M0` for the knife, then the cuts.

## Key Decisions & Constraints
- **Owner's rules:**
  - keep the repo light (no heavy dependencies);
  - **outline a plan and ask before** refactoring the pipeline or changing G-code that moves the machine;
  - commit to `main` once it works; **the owner pushes**;
  - test in the real app via PyCharm;
  - feed rates live in `plotter_pens.json`;
  - any data not in the repo must come with instructions or an in-app download prompt;
  - be accurate and label anything modelled or untested;
  - keep this file and `project_roadmap.md` updated at milestones and on "Update state".
- **Z model:**
  - `G28`, then `G92 Z-(PEN_DOWN_ABOVE_HOME_MM)`, so Z0 = pen down;
  - pen up is at `PEN_LIFT_MM`;
  - knife down is at `CUTTER_Z_OFFSET_MM`, measured from Z0;
  - these and `Z_MAX_FEED` are per machine: defaults are in `config.py`, overrides in `machine_settings.json`
    (git-ignored), set via **Tools › Calibrate pen & knife heights**;
  - read them at run time (`config.X`), never bound at import.
- **Export figure background patch must stay hidden.** Otherwise vpype draws the page edge with every
  pen; this was a real bug, fixed in Phase 4.
- **Vpype handling:** `FINE_LAYERS` and `img:*` layers skip vpype's simplify, merge and 2 mm filter.
  Contours use `reloop` (random seams); squiggles use `--no-flip` (one-way strokes).
- **Layered cut-outs:**
  - equal height steps; layer 1 is the full map;
  - lakes and hollows become holes; each layer is constrained to rest on the one below;
  - no uncut tabs;
  - the alignment outline is drawn 0.6 mm inside the next layer; nothing is drawn on the top layer;
  - cut order: small pieces first, then holes before outlines;
  - no circle mode or region clipping;
  - the plot must be about 10 mm smaller than the cut sheet.
- **City maps:**
  - City mode is a paper mode (`mission_mode and city_mode`), so the elevation pipeline (`RenderWorker`)
    is untouched.
  - Everything down to the chosen road type is drawn everywhere (owner's choice, for a consistent look);
    there is no adaptive thinning.
  - A thick pen for major roads and a thin pen for the rest, or one pen for every road.
  - Roads are clipped to the city boundary or to a rectangle dragged on the source map (fitted to the page shape).
  - City layers skip vpype's merge and 2 mm filter (`FINE_LAYERS`). The app joins the pieces itself, and
    the filter would leave gaps between junctions.
  - Overpass needs POST for long queries (GET → HTTP 502); the backup servers were down in Nov 2026.
- **Jenny and Jackson Lake beds are modelled, not surveyed.** The outline and depth range are real; the
  bed shape is idealised. Depths come from Larsen et al. 2016 and USGS 2005.
- **Code conventions:**
  - `.py` files use **CRLF** line endings; docs use LF;
  - PyCharm auto-stages new files, so unstage temporary files before committing;
  - in Qt menu text, `&&` means a literal `&`.
- **Environment limits for agents:** the owner's Linux bridge has no network and no PyQt6, vpype or
  rasterio. App code runs only on Windows through PyCharm, which agents can click but not type into.
  Network fetches work only from the owner's PC.

## Current State
- The original 4-phase roadmap (UI polish → pathing and G-code → workflow presets → new features) is
  **complete**. Since then the project has added:
  - Jenny and Jackson lakes;
  - whole-disc globe fills;
  - page show/hide;
  - the typed contour interval kept on redraw;
  - the Solar System diagram;
  - pane hide/show buttons;
  - **layered cut-outs**;
  - **pen and knife height calibration**;
  - **city road maps** (tested on screen with Washington, D.C. and Paris, including G-code export and
    project save/load);
  - `docs/architecture.md`, a feature → file map.
- All 43 tests pass. Not pushed: `5a6fb6c`, `a2e67cd` and the City commit.
- **Not yet verified on the machine:**
  - a city road map plot;
  - calibration (no `machine_settings.json` saved yet);
  - the right-edge line fix;
  - card and layered cut-outs;
  - tiled plots;
  - lake and diagram exports;
  - SVG import of real files;
  - trackpad gestures.
