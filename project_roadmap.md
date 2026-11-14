# project_roadmap.md

_Last updated: 14 Nov 2026 · `main` @ `5a6fb6c`_

## Immediate Next Steps
1. **Calibrate the machine (owner, at the plotter).**
   - Open **Tools › Calibrate pen & knife heights**.
   - Pen test: save it, plot it on plain paper, enter the first line with ink, click **Use**.
   - Knife test: save it, cut it on scrap 65 lb card on a mat, enter the first square that drops out, click **Use**.
   - Click **Save**.
   - Expect pen-down to rise by about 2 mm from 5.5, the lift to drop to about 1.2 mm, and the knife depth
     to be well under the current -2.0 mm.
   - Report the numbers so they can be sanity-checked.
2. **Physical verification pass (owner).**
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
- Added `project_context.md` and `project_roadmap.md` (these files) as living state documents.
- `5a6fb6c` — `AGENTS.md` handover notes and `CLAUDE.md` pointer; `docs/development.md` module table updated.
- `021070e` — Pen and knife height calibration dialog, `machine_settings.json` per-machine overrides,
  Z-speed-aware time estimates, new tests (38/38 pass).
- `f46e4d7` — Layered cardstock cut-outs: height bands, pen marks, sheet packing, knife paths, sheet preview, per-sheet G-code and SVG.
- `4490743` — Hide/show buttons for the source map and layers panes.
- `96b8297` — Jenny and Jackson lakes (modelled), whole-disc globe fills, page show/hide, manual contour
  interval kept on redraw (Auto button), all-planets Solar System diagram.
- `7216849` — Phase 4: ski resorts, multi-page tiling, SVG import; page-edge stroke and regional-DEM bug fixes.
