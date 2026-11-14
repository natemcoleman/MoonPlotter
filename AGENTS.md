# AGENTS.md: handover notes for AI assistants

This file is for an AI agent picking up work on this repository. It covers what the project is, how the
owner likes to work, how the code fits together, what has been done, and what is still open. Read it
before changing anything. The user-facing docs are in `README.md` and `docs/`. The code map and data flow
are in [`docs/development.md`](docs/development.md).

Last updated: 13 November 2026, at commit `021070e` on `main`.

---

## 1. What the project is

**MoonPlotter** (window title "Topo Plotter Studio, Cartographer Edition") is a PyQt6 desktop app. It
turns topographic data and vector artwork into G-code for the owner's **Creality Ender 3**. The printer
has been converted into:

- a **multi-colour pen plotter**: gel pens, swapped by hand at `M0` pauses;
- a **drag-knife cutter** for paper and cardstock: a 2 mm-shank blade, 30, 45 or 60°.

It can make:

- **Contour and hatch maps** of the Earth, Moon and Mars, from global DEMs or fetched 30 m regional data.
- **Lake bathymetry** (Tahoe from a survey; Jenny and Jackson modelled) and **ski resorts** (OpenStreetMap
  runs and lifts over contours).
- **Solar System** mission trajectory charts (JPL Horizons) and a poster-style **all-planets diagram**.
- **Image tracing** and **multi-layer SVG import**.
- **Multi-page (tiled) plots**, **card cut-outs** and **layered cardstock relief maps**. Layered relief
  maps are stacked height bands, cut with the knife and marked with the pen.

Repo: `github.com/natemcoleman/MoonPlotter`. Licence: MIT. Python 3.12 on Windows, in a `.venv` inside
`MoonPlotter/`.

---

## 2. How the owner works (follow these)

- **Keep the repo light.** Avoid heavy dependencies unless absolutely necessary. The current stack is in
  `requirements.txt`; geopandas is optional.
- **Ask before refactoring.** If a feature means restructuring the data pipeline, outline the plan and
  wait before writing code. Anything that changes the G-code that moves the machine (heights, safety
  moves, pauses) also needs the owner's go-ahead first.
- **Commit to `main`** once a change works. Use one descriptive commit per feature batch, authored as
  `natemcoleman <112529346+natemcoleman@users.noreply.github.com>`. **Do not push**; the owner pushes.
- **Test in the real app.** Run it from PyCharm on the owner's Windows machine and check the UI by eye,
  not just the unit tests.
- **Feed rates live in `plotter_pens.json`** (the `feed` per pen), not in code.
- **Data rule:** any data the app needs that isn't in the repo must come with instructions or an in-app
  prompt, so a fresh clone can get it. See the setup checklist in `docs/data-sources.md`.
- **Be accurate.** "Make no mistakes" was an explicit instruction. Check numbers and sources, and say
  clearly when something is modelled or untested.
- End each status report with the owner's **pending physical test list** (section 7).
- **Living state files:** keep `project_context.md` (overview, architecture, files, decisions, current
  state) and `project_roadmap.md` (next steps, bugs, backlog, recently completed) in the repo root up to
  date. Update both at each milestone or change of major task, and output both in full when the owner
  types **"Update state"**. Read them first when picking up the project.

### The owner's hardware and materials

- Pens sit in a **rubber-band holder**. It allows upward Z travel, but its zero is fixed by the hardware.
  The **knife drops in and sits at a fixed height**. So tool heights can only be tuned in software (see
  section 5, "Z model").
- Cuts **65 lb cardstock only** (about 0.23 mm thick, no foam board), from sheets of about **150 × 150 mm**.
  **No uncut tabs.**
- Wants a different card colour per height band for layered cut-outs eventually. There aren't enough
  colours yet; the checkbox exists but goes unused.
- Reachable bed is about 197 × 217 mm (`BED_MM` (235, 235) minus `GCODE_OFFSET_MM` (38, 18)).

---

## 3. Environment and workflow notes

- **Running:** open `MoonPlotter/main.py` in PyCharm and use **Run › Current File**. The window takes
  **30 to 45 s** to appear: importing `workers` → `contour_engine` loads geopandas and the Natural Earth
  borders.
- **Tests:** run `python tests/test_engines.py` from `MoonPlotter/` (38 tests, all passing at
  `021070e`). They need the full stack (PyQt6, vpype, rasterio, shapely). Running from `tests/` creates
  a stray `tests/natural_earth/` folder (see the known issues in section 8); delete it, don't commit it.
- **Line endings:** `.py` files use **CRLF**. Keep them CRLF when editing. Markdown docs are LF.
- **PyCharm auto-adds new files to git.** Temporary scripts and test outputs you create (for example
  `_something.py`) get staged automatically. Run `git rm --cached` and delete them before committing.
- **Never commit:** `machine_settings.json`, `plotter_pens.json`, `favorites.json` or
  `recent_projects.json` (personal, already in `.gitignore`), or `*.tif`, `*.ovr`, `regions/`,
  `natural_earth/` (data).
- **Agent tooling seen so far:** the owner's files are reachable through a device bridge. Its Linux shell
  has **no network and no PyQt6/vpype/rasterio**, so app code can only be exercised on Windows, through
  PyCharm. The Linux shell is fine for git, grep and small numpy-free checks. PyCharm can only be
  clicked, not typed into. To run a scratch script, create it, use **File › Reload All from Disk**,
  double-click it in the project tree, then **Run Current File**. The app window itself accepts typing.
- **Network fetches** (Overpass, Copernicus, JPL Horizons, Natural Earth) work from the owner's machine
  only.

---

## 4. Architecture in brief

Read `docs/development.md` for the module table, the segment format and the render and export data flow.
Key points:

- **Everything drawable is a list of segment dicts**: `{'type', 'layer', 'seg' (n×2 array), 'level',
  'pen_id'}`.
  - **Map modes** (Earth, Moon, Mars) use lon/lat data coordinates.
  - **Paper modes** (Solar System, Image, calibration sheet) use millimetres, with frame
    `[0, pw, -ph/2, ph/2]`.
  - `decorations.PaperFrame` and `PlotterApp._paper_frame()` convert between paper mm and data
    coordinates.
- **Render:** `PlotterApp._start_render()` → `workers.RenderWorker` (QThread) → `on_render_success()`.
  The worker returns `dem_data` (raw units) and `dem_mult` (to feet). It assumes the DEM covers exactly
  the view extent, the same as the contour engine.
- **Export:** `export_action()` → `ExportDialog` → for each pen, a temporary SVG at paper size →
  `vpype` → `exporter.wrap_vpype_body()` → `header` / `pen_change` / `footer`.
  - `_export_gcode` loops over **tiles** (`tile_rects()` / `visible_tile_rects()`).
  - The export figure's **background patch must stay hidden** (`_export_figure()`). Otherwise vpype reads
    it as a page-edge stroke drawn by every pen; this was a real bug, fixed in Phase 4.
- **Layers that skip vpype simplification** (lettering and small marks): `FINE_LAYERS` and any `img:*`
  layer. `TOP_LAYERS` draw above the text-blanking boxes.
- **Project persistence:** most widgets register in `PlotterApp._state_widgets()`. Extra state, such as
  hidden tiles or the manual-interval flag, is saved in the project dict and restored in
  `apply_project_state()`. Guard handlers with `self._loading`.
- **Pen combo boxes** are filled by `_fill_pen_combo()`. Light and dark colourways live in
  `project_io.py`.

### Modules added after Phase 3 (not all in the development.md table yet)

| Module | Purpose |
|---|---|
| `ski.py` | Ski resorts (`RESORTS`): Overpass query with mirror fallbacks, boundary filtering, run and lift segments. Data cached in `ski_resorts/*.json` (in the repo). |
| `lakes.py` | Lake bathymetry regions (`config.LAKES`). Tahoe is a survey raster; Jenny and Jackson are **modelled beds** built from the OSM shoreline (`lakes/*.json`, in the repo) and published depths, written once to `lakes/*.tif`. |
| `svg_import.py` | Multi-layer SVG import through `vpype.read_multilayer_svg`, scaled and centred. |
| `system_diagram.py` | Poster-style all-planets Solar System diagram, in paper mm. |
| `layer_cut.py` | Layered cardstock cut-outs: height bands → shapely polygons → cleaning → pen marks → packing on sheets (FFT raster packing, 45° rotations, nesting in holes) → knife paths with corner loops. No Qt. |
| `calibrate_z.py` | Pen-height and knife-depth test ladders and the result maths. No Qt. |
| `calib_dialog.py` | **Tools › Calibrate pen & knife heights** dialog. |
| `dem_prep.py` | One-time `.ovr` overviews for the large global DEMs (**Tools › Speed up global elevation**). |

---

## 5. Things that are easy to get wrong

- **Z model.** `exporter.header()` homes, then sets `G92 Z-(PEN_DOWN_ABOVE_HOME_MM)`, so **Z0 = pen
  down**.
  - Pen up is `PEN_LIFT_MM` above Z0.
  - The knife goes down to `CUTTER_Z_OFFSET_MM`, measured from Z0 (`exporter.tool_down_z()`).
  - These three values plus `Z_MAX_FEED` are **per machine**: defaults are in `config.py`, overridden by
    `machine_settings.json`, which the calibration dialog writes.
  - Read them at run time as `config.<NAME>` (`exporter` uses `_cfg.`, `main` uses `cfg.`). **Don't**
    bind them at import time, or a newly saved calibration won't take effect.
- **Z speed.** Stock Ender 3 firmware moves Z at only about 5 mm/s (`Z_MAX_FEED = 300` mm/min). Pen
  lifts dominate plot time, and both time estimators take this into account.
- **Load pauses happen at different heights.** The first pen in a combined file loads at the lift
  height; later pens and the knife load at Z25. With fixed-height holders this doesn't matter.
- **Drag knife.** Corner loops sit outside sharp convex corners (`exporter.cutout_path()`,
  `layer_cut._ring_cut()`). Layered cut-outs cut small pieces first, and each piece's holes before its
  outline.
- **Coordinates for decorations and paper.** In map modes, convert data coordinates to mm with
  `_paper_mm_per_data_unit()` and the view extent's lower-left corner (see `_cut_base_marks()`).
- **Qt menus.** In Qt menu text, `&` is a mnemonic; write `&&` for a literal ampersand.
- **Overpass** sometimes returns 502 or times out. `ski.py` and `lakes.py` try `overpass-api.de` twice,
  then two mirrors.

---

## 6. History (all on `main`)

| Commit | Summary |
|---|---|
| Phase 1 `2651199` | UI polish, cutter depth, preview line widths, border layer fix |
| Phase 2 `d2961db` | Random contour seams (vpype `reloop`), one-way sine squiggles, card cut-out with corner loops, filled Earth globe |
| Phase 3 `96ce28d`, `fd5f62a` | Colourways, map-scale presets, aspect-locked selection, focus view, favourites, Natural Earth borders cached on disk, DEM in-memory cache, `.ovr` overviews |
| Phase 4 `7216849` | Ski resorts, multi-page tiling, SVG import; fixed the page-edge stroke drawn by every pen; fixed a regional-DEM NameError |
| `96b8297` | Jenny and Jackson lakes (modelled), whole-disc globe fills for every fill style, show/hide individual pages, typed contour interval kept on redraw (**Auto** button), Solar System all-planets diagram |
| `4490743` | Hide/show buttons for the source map and layers panes |
| `f46e4d7` | **Layered cut-outs** (stacked cardstock relief) |
| `021070e` | **Pen and knife height calibration** dialog, `machine_settings.json`, real Z speed in time estimates, new tests |

The original 4-phase roadmap (UI polish → pathing and G-code → workflow presets → new features) is
complete.

---

## 7. Owner's pending physical tests (remind them at the end of each report)

1. Run **Tools › Calibrate pen & knife heights**: the pen test, then the knife test on scrap 65 lb card.
   Save the results. This replaces the old "test plot at 5.5 mm pen height" item.
2. Try **two-finger scroll and pinch** zoom in the previews.
3. On the next plot, check that the **line along the right edge** is gone. It was most likely the
   page-edge stroke fixed in Phase 4. This was never confirmed on paper.
4. Do a **card cut-out on scrap paper**. This also tests the layered cut-outs.
5. **Push the commits.** Six were not pushed as of `021070e`.

Also untested on the machine: tiled multi-page plots (alignment across the gap), lake and Solar System
diagram exports, ski overlay combined with tiling, and SVG import of real Inkscape or Illustrator files.

---

## 8. Known issues and ideas (none started)

Known issues:

- **Slow start-up** (30 to 45 s), because borders and geopandas load at import. Fix: load them lazily,
  the first time Earth is selected.
- The Natural Earth cache path is **relative to the working directory**. Running tests from `tests/`
  downloads into `tests/natural_earth/`. Make it relative to the app folder.
- Layered cut-outs of **lake bowls** (Tahoe) need one sheet per layer, because every layer is nearly the
  whole rectangle. The plot must be about 10 mm smaller than the cut sheet.
- **Region clipping** (country or state) is ignored in layered cut-out mode, and circle mode is
  unsupported there.
- The Solar System diagram is laid out for **landscape** paper only.
- Pane visibility (source map and layers) **isn't remembered** between sessions.

Ideas the owner has seen (ask before starting any):

- Faster start-up (above).
- Registration or corner marks for tiled pages.
- Hatch fills for SVG import (fills are currently ignored).
- An in-app "add a resort or lake" dialog.
- One-click poster presets.
- An "equal area per layer" option for layered cut-outs.
- Per-height card colours (the checkbox exists).
- A portrait layout for the Solar System diagram.
- Screenshots for the new docs features.
- The **real Jenny and Jackson Lake survey rasters**: the NPS raster cited in Larsen et al. 2016. The
  owner could request them. Plug them in by setting `kind: 'survey'` in `config.LAKES`.

---

## 9. Quick reference: where to change things

| Want to… | Edit |
|---|---|
| Add a pen | `default_pens()` in `config.py`; the user's own pens are in `plotter_pens.json` (feeds too) |
| Add a lake | `LAKES` in `config.py` (`kind` 'survey' or 'model'), see `lakes.py` |
| Add a ski resort | `RESORTS` in `ski.py`; delete its `ski_resorts/*.json` to re-download |
| Add a regional data source | `REMOTE_SOURCES` in `regions.py` |
| Add a mission | `MISSIONS` in `missions.py` |
| Change G-code structure or safety | `exporter.py` (ask the owner first), plus `tests/test_engines.py` |
| Change machine heights | **Tools › Calibrate…** (writes `machine_settings.json`); defaults in `config.py` |
| Change the layered cut-out packing or cutting | `layer_cut.py` (constants at the top) |
