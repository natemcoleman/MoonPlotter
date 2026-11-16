# Architecture: where each feature lives

This is a map of the code for people and AI assistants who need to find things quickly. Hand it to an agent
together with [`AGENTS.md`](../AGENTS.md) (working rules and history) and the root `project_context.md` /
`project_roadmap.md` (current state).

All application code is in `MoonPlotter/`. Data paths are relative to that folder: `main.py` switches to it on
start-up. Line numbers are approximate and drift as the code changes; search for the names instead.

## 1. The big picture

```
                    ┌──────────────── main.py (PlotterApp, PyQt6) ────────────────┐
 sidebar sections → │ get_ui_params() ─► RenderWorker (workers.py, QThread)        │
 (Source, Paper,    │                    ├─ contour_engine.generate_contours()     │
  Contours, Fill,   │                    ├─ hatch_engine.generate_hatches()        │
  Map furniture,    │                    └─ sites / ski / lakes overlays           │
  Layered cut-out,  │ paper modes (no worker): mission_plot / system_diagram /     │
  Solar System,     │   image_trace / svg_import / city_roads / calibration        │
  Image, City)      │          ▼                                                    │
                    │ dem_segments = list of segment dicts  ──►  preview           │
                    │ (+ decorations.build_decorations)          (render_canvas)   │
                    │          ▼                                                    │
                    │ export: per pen → temp SVG → vpype → exporter.wrap_vpype_body │
                    │         → header / M0 pen changes / footer → .gcode files    │
                    └──────────────────────────────────────────────────────────────┘
```

**The one data structure everything shares:** a stroke is a dict
`{'type', 'layer', 'seg' (n×2 array), 'level', 'pen_id'}`.

- **Map modes** (Earth, Moon, Mars): `seg` is in lon/lat.
- **Paper modes** (Solar System, Image, City, calibration sheet): `seg` is in paper mm, with the frame
  `[0, pw, -ph/2, ph/2]`.

`layer` decides the Layers-panel row (`LAYER_LABELS` in `main.py`). `pen_id` decides the pen, G-code file and
preview colour.

**Two families of modes:**

| Mode | Flag in `main.py` | Builds strokes in | Notes |
|---|---|---|---|
| Earth / Moon / Mars | `mission_mode == False` | `workers.RenderWorker` (background thread) | Selection on the source map → elevation window → contours / fill |
| Solar System | `mission_mode` | `mission_plot.build_mission_plot`, `system_diagram.build_system_diagram` | Missions download in `_MissionFetch` |
| Image | `mission_mode and image_mode` | `image_trace.trace_image`, `svg_import.fit_layers` | Tracing in `_ImageTrace` |
| City | `mission_mode and city_mode` | `city_roads.build_city_map` | Download in `_CityFetch` |
| Calibration sheet | `calibration_mode` | `calibration.build_calibration_sheet` | Tools › Pens › Calibration sheet |

## 2. Feature → file index

| Feature (as the user sees it) | Main code | Supporting code |
|---|---|---|
| Choosing Earth / Moon / Mars / City / Solar System / Image | `main.py` `switch_planet`, `context_ui_update` | `config.DATA_FILES` |
| Source map: selecting an area, zoom, pan, circle (globe) mode | `main.py` sections "MAP LOGIC", `on_select`, `process_selection`, `AspectRectangleSelector`, `toggle_circle_mode` | |
| Map-scale presets ("1 mile wide" frames) | `main.py` "MAP SCALE PRESETS" (`apply_scale_preset`, `frame_scale_at`) | `config.SCALE_PRESETS` |
| Clip to region (states, countries, lakes, ski resorts) | `main.py` `on_region_changed`, `_fill_region_combo` | `contour_engine` (Natural Earth borders), `lakes.py`, `ski.py` |
| Reading elevation and colour rasters | `workers.py` `read_dem_window`, `read_color_window`, `RenderWorker._read_dem` | `dem_prep.py` (one-time `.ovr` overviews) |
| Sharper regional data (30 m Copernicus, NAIP, imports) | `regions.py` | `data_dialog.py` (Tools › Data sources), `net.py` |
| Contours, index lines, Tanaka shading, auto interval | `contour_engine.py` `generate_contours`, `auto_contour_interval` | `main.py` "MAP & DATA CALCULATION" (`update_interval`, `use_auto_interval`) |
| Country / state borders | `contour_engine.py` (`world_borders`, `state_borders`, `ensure_natural_earth`) | `natural_earth/` (downloaded) |
| Fill / hatching (crosshatch, scanlines, squiggles, stipple, hachures) | `hatch_engine.py` `generate_hatches` | `main.py` "FILL INKS" |
| Colour separation (several inks mixing on paper) | `hatch_engine.py` `_separate`, `predict_colors`, `composite_ink_layers` | `main.py` "SUBTRACTIVE INK PREVIEW" |
| Globe full-disc fill (no white gaps) | `hatch_engine.py` `_DiscFloorRaster`, `_extend_runs`, `_earth_land_ocean_separate` | |
| Points of interest (landing sites, landmarks) | `sites.py` | `main.py` "POINTS OF INTEREST" |
| Ski resorts (runs and lifts over contours) | `ski.py` | `main.py` `_SkiFetch`, `start_ski_fetch`, `ski_options` |
| Lake bathymetry (Tahoe survey; Jenny and Jackson modelled) | `lakes.py` | `config.LAKES`, `tools/make_tahoe.py`, `main.py` `_LakeBuild` |
| Map furniture: title, scale bar, north arrow, graticule, frame | `decorations.py` `build_decorations` | `stroke_font.py` (Hershey lettering), `main.py` "MAP FURNITURE" |
| Text masks (contours blanked behind labels) | `decorations.py` (masks), `main.py` `_text_mask_boxes`, `_mask_line` | |
| Preview drawing, true pen widths, ink simulation | `main.py` `render_canvas`, "TRUE-WIDTH LINES", `_refresh_ink_sim` | `hatch_engine.lay_ink` |
| Layers panel (show / hide, change a layer's pen) | `ui_widgets.LayerPanel` | `main.py` "LAYERS" (`refresh_layer_panel`, `set_layer_pen`) |
| Hand-drawn lines, text, painting strokes with a pen | `main.py` "INTERACTIVE MOUSE CONTROLS (Preview Canvas)" (`_paint_at`) | |
| Live plot-time estimate | `main.py` "LIVE PLOT-TIME ESTIMATE" | `exporter.estimate_minutes` |
| Paper size, orientation, paper colour | `main.py` `update_paper_dims`, `on_paper_color_changed` | `config.PAPER_SIZES`, `PAPER_COLORS` |
| Tiling (one plot over several pages, hide pages) | `main.py` "TILES" (`tile_layout`, `tile_rects`, `_rebuild_tile_pages`) | export loop in `_export_gcode` |
| Pens: list, colours, widths, feed rates, favourites | `config.load_pens` / `save_pens` (`plotter_pens.json`) | `pen_dialog.py` (Tools › Pens), `main.py` "PEN PALETTE" |
| Pen calibration sheet (line widths, hatch densities) | `calibration.py` | `pen_dialog.py` |
| Pen-height and knife-depth calibration (Z ladders) | `calibrate_z.py` | `calib_dialog.py`, `config` machine settings (`machine_settings.json`) |
| Colourways and style presets | `project_io.py` | `main.py` "COLOURWAYS", "STYLE PRESETS" |
| Projects (.topo.json), recent projects | `project_io.py` | `main.py` "PROJECTS" (`get_project_state`, `apply_project_state`) |
| Export: G-code per pen, pen changes, priming, card cut-out | `main.py` "EXPORT PIPELINES" (`export_action`, `_build_pen_bodies`, `_vpype_body`) | `exporter.py`, `export_dialog.py` |
| G-code safety (Z model, bed limits, header / footer) | `exporter.py` (`header`, `wrap_vpype_body`, `bed_violation`) | `config` plotter constants |
| Drag-knife card cut-out (corner loops) | `exporter.cutout_path`, `cutout_body` | |
| Layered cardstock cut-outs (height bands, packing, knife paths) | `layer_cut.py` | `main.py` "LAYERED CUT-OUT" (`_rebuild_cut_layers`, `export_cut_sheets`) |
| Solar System missions (JPL Horizons) | `missions.py`, `mission_plot.py` | `main.py` `_build_missions_section`, `update_mission_plot`, `missions/` cache |
| Solar System diagram (all planets poster) | `system_diagram.py` | |
| Image tracing (logos, photos) and sticker cut lines | `image_trace.py` | `main.py` "IMAGE TRACING", `_ImageTrace` |
| SVG import (multi-layer Inkscape files) | `svg_import.py` | Image mode |
| **City road maps** (OpenStreetMap, after city-roads) | `city_roads.py` (download, cache, clip, road classes, joining, crowding, title) | `main.py` "CITY ROAD MAPS" (`_build_city_section`, `load_city`, `update_city_plot`, `_draw_city_overview`, `_draw_city_overlay`), `_CityFetch`, `cities/` cache |
| Trackpad scroll / pinch | `main.py` `_TrackpadNav`, "TRACKPAD NAVIGATION" | |
| Hide / show the source map and layers panes | `main.py` "HIDEABLE PANELS" | `ui_widgets.LayerPanel.hide_requested` |
| Look and feel | `theme.py`, `ui_widgets.py` | |
| Robust downloads (antivirus / proxy HTTPS inspection) | `net.py` `fetch_bytes` | used by `regions`, `ski`, `lakes`, `missions`, `city_roads` |

## 3. File → features

| File | What it contains |
|---|---|
| `main.py` (~6,300 lines) | The whole window (`PlotterApp`): sidebar construction (`init_ui`, one `CollapsibleSection` per sidebar section), source map, preview, layers, editing tools, projects, export. It also holds the background threads `_MissionFetch`, `_LakeBuild`, `_SkiFetch`, `_CityFetch`, `_DemPrep` and `_ImageTrace`, and the source-map helpers `AspectRectangleSelector` and `_TrackpadNav`. Methods are grouped under `# --- SECTION ---` comments; see the list below. |
| `config.py` | Constants: paper sizes and colours, `DATA_FILES`, `LAKES`, plotter / G-code numbers (feeds, Z heights, bed size), built-in pens and pen file handling, per-machine Z overrides (`machine_settings.json`) |
| `workers.py` | `RenderWorker`: reads the elevation window (in-memory cache), runs contours and fills, adds sites / ski overlays, and returns segments plus `dem_data`. Raster readers for global and regional files. |
| `contour_engine.py` | Contour tracing, index contours, Tanaka, ocean split, Natural Earth borders (geopandas, optional), auto interval |
| `hatch_engine.py` | All fill patterns, tone curves, blue noise, CIELAB colour separation, ink compositing for the preview, globe disc fill |
| `decorations.py` | Map furniture as pen strokes, `vector_text` (stroke lettering), `PaperFrame` (lon/lat ↔ paper mm), `min_cap_for_pen` |
| `stroke_font.py` | Hershey single-stroke font loader and `stroke_text` |
| `exporter.py` | G-code pieces and checks (no Qt): header / footer, `G92` origin, pen lifts and changes, tool heights, vpype command line, card cut-out, time estimate, bed limits, priming |
| `export_dialog.py` | Export dialog (pen order, feeds, options) |
| `pen_dialog.py` | Tools › Pens (edit pens, make a calibration sheet) |
| `calibration.py` | Pen calibration sheet strokes |
| `calibrate_z.py`, `calib_dialog.py` | Tools › Calibrate pen & knife heights: test-ladder G-code and the dialog that saves the results |
| `data_dialog.py` | Tools › Data sources (regional rasters) |
| `regions.py` | Regional raster registry, remote fetch (windowed GeoTIFF, Copernicus GLO-30 tiles, XYZ imagery), GeoTIFF import |
| `dem_prep.py` | One-time overview (`.ovr`) building for the big global rasters |
| `net.py` | `fetch_bytes`: urllib → truststore → curl.exe → PowerShell fallbacks |
| `sites.py` | Points of interest per body, and their marker / label strokes |
| `ski.py` | Ski resorts from OpenStreetMap: download, cache (`ski_resorts/`), boundary filter, run / lift strokes |
| `lakes.py` | Lake bathymetry regions: shoreline download and the modelled lake beds (`lakes/`) |
| `layer_cut.py` | Layered cut-outs: height bands → polygons → clean-up → pen marks → sheet packing → knife paths |
| `missions.py` | Mission catalogue, JPL Horizons download and cache (`missions/`), Keplerian planet orbits |
| `mission_plot.py` | Mission chart layout in paper mm (compressed scale, labels) |
| `system_diagram.py` | All-planets poster diagram |
| `image_trace.py` | Image → line art (colour reduction, outlines, hatch fill, edges, cut line) |
| `svg_import.py` | Multi-layer SVG import for Image mode |
| `city_roads.py` | City road maps: Nominatim boundary lookup, tiled Overpass download, `cities/*.npz` cache, local-metre projection, road classes, clipping (boundary or rectangle), joining pieces into long strokes, crowding measure, title. No Qt. |
| `project_io.py` | Project files, presets, colourways, favourites (JSON) |
| `ui_widgets.py` | `CollapsibleSection`, `LayerPanel`, `SwatchCombo`, `WheelGuard`, `swatch_icon` |
| `theme.py` | Qt style sheet and matplotlib axes styling |
| `geometry_utils.py` | Small helpers (Chaikin smoothing, orthographic projection) used by `contour_engine` and `sites` |
| `svg_plotter.py` | An older stand-alone SVG-to-G-code window. Nothing in the main app imports it. |
| `tests/test_engines.py` | Engine tests (no GUI): G-code safety, contours, hatching, missions, tracing, cut-outs, calibration, city maps. Run the file directly. |
| `tools/` | One-off data scripts (`make_tahoe.py`, `utm.py`) |

### `main.py` sections (search for these headings)

| Heading | What's there |
|---|---|
| `UI CONSTRUCTION` (`init_ui`) | Every sidebar section, the source map, the preview, the layers panel and the bottom bar. Sections: Source, Paper, Contours, Fill / Hatching, Layered cut-out, Advanced, Map furniture. Solar System, Image and City are built by `_build_missions_section`, `_build_image_section` and `_build_city_section`. |
| `FAVOURITES`, `MAP SCALE PRESETS`, `COLOURWAYS`, `HIDEABLE PANELS` | Small UI features |
| `LAYERED CUT-OUT` | The cut-out mode: preview, sheet export |
| `PEN PALETTE & STATES` | Pen buttons, undo / redo |
| `MAP & DATA CALCULATION` | Contour interval, `get_ui_params`, `draw_action`, `_start_render`, `on_render_success` |
| `FILL INKS` | Fill-pen choice, contrast warnings |
| `LAYERS`, `LIVE PLOT-TIME ESTIMATE` | Layers panel, time estimate |
| `HIGH PERFORMANCE RENDERER`, `TRUE-WIDTH LINES IN THE PREVIEW`, `SUBTRACTIVE INK PREVIEW` | Preview drawing (`render_canvas`) |
| `PREVIEW ZOOM & PAN`, `INTERACTIVE MOUSE CONTROLS`, `TRACKPAD NAVIGATION` | Preview interaction, drawing tools |
| `MAP LOGIC (Panning & Zooming)` | Source map, paper size, `switch_planet`, regional overlays, circle mode |
| `TILES` | Multi-page plots |
| `POINTS OF INTEREST`, `MAP FURNITURE` | Sites, decorations |
| `STYLE PRESETS`, `PROJECTS`, `recent projects` | Saving and loading |
| `PENS / CALIBRATION / DATA` | Dialog launchers |
| (missions block) | Solar System mode |
| `CITY ROAD MAPS` | City mode |
| `IMAGE TRACING` | Image mode |
| `EXPORT PIPELINES` | SVG / G-code export |

## 4. Data on disk (inside `MoonPlotter/`)

| Folder / file | What | In git? |
|---|---|---|
| `ldem_16.tif`, `lroc_color_16bit_srgb_4k.tif` | Moon elevation and colour | yes |
| `BE_dem_v2.tif`, `earth_color.tif`, Mars files | Earth and Mars global data | no (see `docs/data-sources.md`) |
| `regions/` | Regional rasters fetched or imported in the app | no |
| `natural_earth/` | Borders (downloaded in the app) | no |
| `ski_resorts/`, `missions/`, `lakes/*.json` | Downloaded OSM / JPL data (re-downloaded if missing) | yes |
| `cities/` | Downloaded city roads (`<name>.npz`) | no |
| `plotter_pens.json`, `machine_settings.json`, `favorites.json`, `recent_projects.json` | Per-machine settings | no |
| `presets.json`, `projects/` | Style presets; saved projects | presets: yes; projects: no |
