# project_roadmap.md

_Last updated: 16 Nov 2026 · `main` after the City road maps commit_

## Immediate Next Steps
1. **Calibrate the machine (owner, at the plotter).**
   - Open **Tools › Calibrate pen & knife heights**.
   - Pen test: save it (or **Read a test file…** for one already plotted), enter the first solid line; the
     `config.py` lines are shown. Click **Use**.
   - Knife test: save it, cut it on scrap 65 lb card on a mat, enter the first square that drops out.
   - Copy the lines into `config.py`, or click **Save** (writes `machine_settings.json`, which overrides it).
   - Expect pen-down to rise by about 2 mm from 5.5, the lift to drop to about 1.2 mm, and the knife depth
     to be well under the current -2.0 mm.
   - Report the numbers so they can be sanity-checked.
2. **Physical verification pass (owner).**
   - Plot a city map, e.g. a downtown rectangle of Washington, D.C. or Paris down to residential streets on
     a postcard, thick pen for major roads. Check that the strokes join cleanly and the time estimate.
   - Run a short contour plot to confirm the right-edge line is gone and the new pen height works.
   - Do a card cut-out on scrap.
   - Do a small layered cut-out: Grand Targhee, 100 × 100 mm map, 150 mm sheets, 4–6 layers.
   - Note any knife issues: pieces not dropping out, corner tearing, alignment-outline offset.
3. **Faster start-up (agent).**
   - Move the geopandas and Natural Earth loading out of import time, so they load on first Earth use.
   - Make the Natural Earth cache path absolute (relative to the app folder).
   - Goal: the window appears in under 10 s, down from 30–45 s.
   - Before starting, outline the plan; it touches `contour_engine` imports.

## Pending Bugs/Issues
- **City downloads are slow when Overpass is busy.** D.C. took about 5 minutes, Paris about 10, with many
  HTTP 502 retries. They're resumable and only needed once per city. The backup Overpass servers
  (kumi.systems, private.coffee) weren't answering in Nov 2026.
- City maps: where a road runs exactly along the city limit, clipping leaves it as short dashes (visible
  on D.C.'s north-east edge). A possible fix is to keep roads within a few metres outside the boundary.
- Slow start-up (30–45 s), caused by the borders and geopandas import.
- The Natural Earth cache uses a path relative to the working directory, so running tests from `tests/`
  creates `tests/natural_earth/`.
- Tool-load pauses happen at different heights (first pen at the lift height, later tools at Z25). This
  is harmless with fixed-height holders but inconsistent.
- Layered cut-outs:
  - lake bowls need one sheet per layer;
  - region clipping is ignored;
  - circle mode is unsupported;
  - the map must fit inside the sheet with about 10 mm to spare.
- The Solar System diagram is laid out for landscape only.
- Pane visibility isn't remembered between sessions.
- Time estimate: the per-stroke Z cost assumes lift and drop happen at `Z_MAX_FEED`. Recheck it against a
  real plot after calibration.

## Future Features (Backlog)
**High**
- Registration or corner marks for tiled pages, plus an alignment guide.
- An "equal area per layer" option for layered cut-outs, as an alternative to even height steps.
- An optional `CUTOUT_PASSES` / multi-pass setting in the calibration dialog, if 65 lb card needs it.

**Medium**
- City maps: building outlines or water hatching; preset road-type/pen combinations; a circle crop.
- Hatch fills for imported SVG shapes (fills are currently ignored).
- An in-app "Add ski resort / lake" dialog: OSM name lookup and depth entry.
- Per-height card colours for layered cut-outs (the checkbox exists; this needs more card colours).
- Real Jenny and Jackson Lake survey rasters (request the NPS data cited in Larsen et al. 2016). Plug
  them in via `kind: 'survey'` in `config.LAKES`.

**Low**
- A portrait layout for the Solar System diagram.
- One-click poster presets, for example navy paper + diagram + white pens.
- Remember pane visibility.
- Screenshots of the new features in `docs/`.

## Recently Completed
- **Calibration dialog shows the `config.py` lines live.** Enter the first solid line or the first square that
  drops out, and the exact `PEN_DOWN_ABOVE_HOME_MM` / `PEN_LIFT_MM` / `CUTTER_Z_OFFSET_MM` lines appear, with
  Copy and Use buttons. **Read a test file…** takes the heights from an earlier test's G-code. The knife depth
  is corrected if the pen-down height changed after the knife test. 44/44 tests pass.
- **City road maps** (Subject › City). Covers boundary or rectangle clip, smallest road type, thick/thin pens
  or a single pen, and water, coastline, rivers, rail, parks and outline extras. Also a crowding check with
  a red overlay, a title, project save/load, resumable tiled downloads and `net.fetch_bytes` POST support.
  Tested with Washington, D.C. and Paris; 43/43 tests pass.
- `docs/architecture.md`: a feature → file map for people and agents.
- Added `project_context.md` and `project_roadmap.md` (these files) as living state documents.
- `5a6fb6c` — `AGENTS.md` handover notes and `CLAUDE.md` pointer; `docs/development.md` module table updated.
- `021070e` — Pen and knife height calibration dialog, `machine_settings.json` per-machine overrides,
  Z-speed-aware time estimates, new tests (38/38 pass).
- `f46e4d7` — Layered cardstock cut-outs: height bands, pen marks, sheet packing, knife paths, sheet preview, per-sheet G-code and SVG.
- `4490743` — Hide/show buttons for the source map and layers panes.
- `96b8297` — Jenny and Jackson lakes (modelled), whole-disc globe fills, page show/hide, manual contour
  interval kept on redraw (Auto button), all-planets Solar System diagram.
- `7216849` — Phase 4: ski resorts, multi-page tiling, SVG import; page-edge stroke and regional-DEM bug fixes.
