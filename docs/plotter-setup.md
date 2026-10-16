# Plotter setup

The G-code is written for a **Creality Ender 3 (Marlin firmware) converted into a pen plotter**. The
hotend is removed and a pen is clamped to the carriage. Z moves the pen up and down. Any Marlin machine
with homing switches can use it after you set the values below. Other firmware needs small changes; see
[Other machines](#other-machines).

> **Read this page before your first plot.** The app has no way of knowing how far your pen tip sits from
> the bed. If `PEN_DOWN_ABOVE_HOME_MM` is wrong, the pen will either drag across the sheet or be driven
> into the bed.

## How the coordinates work

Each G-code file starts with this sequence:

```gcode
G21                       ; millimetres
G90                       ; absolute positions
G28                       ; home all axes
G92 X-50 Y-50 Z-5.5       ; "the head is now at (-50, -50) and 5.5 mm below the paper surface"
G0 Z5.0 F1500             ; climb to a safe height before moving over the bed
G0 X0 Y0 F3000            ; move to the plot origin
G0 Z3.0 F1500             ; drop to pen-up height
```

This means:

- **Plot origin (0, 0) is 50 mm in from the home corner in X and Y.** The bottom-left corner of the sheet
  in the app is drawn at this origin. Tape your paper with its front-left corner there.
- **Z = 0 is the paper surface**, which means the pen is down. The pen sits 5.5 mm above its homed
  position when it touches the paper. Between strokes it lifts to Z = 3 mm. For long moves (leaving home,
  parking, pen changes) it climbs to Z = 5 mm.
- The reachable area is the bed size minus the offset: **185 × 185 mm** with the defaults. The
  **200 × 200** paper size is only safe if you change these numbers. The export warns you when any move
  would leave the bed.

## Settings in `MoonPlotter/config.py`

| Setting | Default | What it means |
|---|---|---|
| `BED_MM` | `(235, 235)` | Travel of your X and Y axes |
| `GCODE_OFFSET_MM` | `(50, 50)` | Distance from the home position to the plot origin |
| `PEN_DOWN_ABOVE_HOME_MM` | `5.5` | Height of the paper surface above the homed Z position. **Raise it if the pen drags or hits the bed. Lower it if lines are faint or skip.** |
| `PEN_LIFT_MM` | `3.0` | Pen-up height for travel between strokes. You can also change it in the export dialog. |
| `SAFE_Z_MM` | `5.0` | Clearance for long moves, homing exits and pen changes |
| `DEFAULT_DRAW_FEED` / `TRAVEL_FEED` | `1500` / `3000` mm/min | Pen-down and pen-up speeds. Each pen can override the draw speed. |
| `MIN_SEGMENT_MM` | `2.0` | Strokes shorter than this are dropped from map lines (not from lettering) |

### Finding your pen height

1. Home the printer (`G28`) with no paper on the bed.
2. Lower Z in 0.1 mm steps until the pen tip just touches a sheet of the paper you'll use.
3. Read the Z position. That number is `PEN_DOWN_ABOVE_HOME_MM`.
4. Spring-loaded or rubber-band pen holders tolerate being a few tenths of a millimetre low, which keeps
   gentle pressure on the paper. Rigid holders do not.

Restart the app after editing `config.py`.

## Exporting

**File › Export G-code…** (`Ctrl+E`) opens the export dialog:

- **Pens and plot order.** Pens are ordered from lightest ink to darkest, so dark lines are drawn on top.
  Untick a pen to skip it. Each pen has its own **draw feed**: thick gel pens skip if they move too fast.
  The table estimates the time for each pen and the total.
- **One combined file with a pause (M0) to swap pens.** This writes a single file. The printer parks,
  lifts to Z 25 mm and shows "Load *pen name*" on its screen. Swap the pen and click the knob to continue.
  The machine is not re-homed between pens, so the colours stay registered. If you turn this off, you get
  one file per pen, numbered in plot order (`01_…gcode`, `02_…gcode`, …).
- **Settle after each pen-down** adds a short pause (`G4`) before each stroke so gel ink starts flowing.
- **Pen-up height** overrides `PEN_LIFT_MM` for this export.
- **Prime with the _n_ longest strokes first** draws long lines first to get the ink flowing before the
  fine detail.

Behind the scenes, each pen's lines are written to an SVG at true paper size. [vpype](https://github.com/abey79/vpype)
then simplifies, merges, filters and sorts the map lines to cut travel. Lettering, labels and other fine
marks skip simplification and the length filter, so small letters keep every stroke. Finally the app adds
its own pen lifts and speeds.

**File › Save SVG…** writes the whole plot as one SVG at true size, with the visible layers in their pen
colours. Use it to check a plot in a vector editor, or to drive another plotter or a cutting machine.

## Running a plot

1. Fit the pen, put the paper down at the plot origin, and tape it flat.
2. Run the file from the printer's SD card, or send it with OctoPrint, Pronterface or similar.
3. Before your first full plot, export something small, such as a calibration sheet or a 100 × 100 mm test.
   Watch the first pen-down closely.
4. With a combined file, change pens when the screen asks you to.

## Other machines

- **Other Marlin printers**: set `BED_MM`, `GCODE_OFFSET_MM` and `PEN_DOWN_ABOVE_HOME_MM`.
- **Servo pen-lift plotters (GRBL, AxiDraw-style)**: these don't move Z. Export **SVG** and use your
  plotter's own software or vpype. Alternatively, change `exporter.wrap_vpype_body()` so pen up and down
  send your servo commands (for example `M3`/`M5`) instead of Z moves.
- **No homing switches**: remove `G28` from `exporter.header()` and jog to the origin by hand before
  starting.

The G-code functions live in `MoonPlotter/exporter.py`. They have no GUI code and are covered by unit
tests, so they are safe to change.
