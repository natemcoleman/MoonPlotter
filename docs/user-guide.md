# User guide

The window has three parts:

- **The sidebar** on the left holds all settings, in collapsible sections. The mouse wheel scrolls the
  sidebar. It never changes a drop-down or slider by accident.
- **The source map** shows the whole globe for the chosen body. You choose the area to plot here.
- **The plot preview** shows the sheet exactly as it will be drawn. The editing tools are below it and
  the **Layers** panel is to its right.

The buttons at the bottom of the sidebar are **Draw Map**, **Save SVG** and **Export G-code**. The status
line above them tells you what the app is doing.

- [Choosing an area](#choosing-an-area)
- [Paper](#paper)
- [Contours](#contours)
- [Fill / Hatching](#fill--hatching)
- [Map furniture](#map-furniture)
- [The preview, layers and editing tools](#the-preview-layers-and-editing-tools)
- [Projects and style presets](#projects-and-style-presets)
- [Keyboard shortcuts](#keyboard-shortcuts)

Solar System mode and image tracing have their own pages: [Solar System mode](solar-system.md) and
[Image tracing](image-tracing.md).

---

## Choosing an area

**Source › Subject** picks the **Earth**, **Moon** or **Mars**. The **Solar System** and **Image** modes are
covered on their own pages. Mars stays greyed out until its data files are present. See
[Data sources](data-sources.md).

On the **source map**:

| Action | Result |
|---|---|
| Drag with the left button | Select the area to plot. Its shape follows the paper's aspect ratio. |
| Scroll | Zoom in or out around the cursor |
| Middle-drag | Pan |
| **Zoom box** | Drag a box to zoom to it |
| **Reset view** / **Clear** | Show the whole globe again / remove the selection |

Other source options:

- **Clip to region** (Earth only, needs `geopandas`): type a country or US state. The map is clipped to its
  outline. **Lake Tahoe (High-Res Bathymetry)** uses the bundled 10 m lake-bed model with a fixed
  75 ft interval.
- **Circle mode (whole hemisphere)**: plots a full disc of the globe. Drag on the map to turn it.
- **Show points of interest**: markers for landing sites, mountains, craters and other good subjects.
  Hover a marker for details. Click it to add it to the plot (a filled marker means it is plotted).
  Double-click it to frame the area around it. **Label plotted sites** writes their names on the plot.
- **Show borders on the source map** is a display aid only. **Plot country / state borders** in the
  Contours section actually draws them.

Then click **Draw Map**. Drawing again always starts fresh: it picks a new automatic interval, resets the
preview zoom and clears hand edits. Changing a setting afterwards re-renders automatically and keeps your
edits.

### Sharper data for small areas

The global files are coarse: about 1.8 km per pixel for Earth and about 1.9 km for the Moon. For a small
area, open **Tools › Data sources (sharper regional data)…** and click **Fetch for selection**. Only your
window is downloaded, and it is used automatically whenever a selection falls inside it. See
[Data sources](data-sources.md#regional-data).

## Paper

- **Bed / paper size**: 100 × 100 mm (default), 200 × 200 mm (the safe area of an Ender 3), A4, Letter, or
  **Custom (mm)**. **Rotate** switches between portrait and landscape.
- **Paper colour**: white, black, cream, kraft, greys and the common construction-paper colours. You can
  also choose a custom colour. The preview shows the sheet in this colour. Colour fills plan their ink mix
  for it, and the app warns you when a contour pen would barely show on it. See
  [Pens and paper](pens-and-paper.md).

## Contours

Turn the whole layer on or off with the checkbox in the section header.

- **Interval**: the badge shows the automatic interval, in feet, picked for the relief of your selection.
  **Contour density** steps to the next sparser or denser standard interval. **Interval override** sets an
  exact value.
- **Index line every _n_ lines**: every _n_-th contour is drawn with the **Index lines** pen. This is
  usually a thicker pen.
- **Pens**: separate pens for **contour lines**, **index lines**, **below sea level** (when **Separate pen
  below sea level** is on) and **borders**. If one of them would barely show on your paper colour, a
  warning appears. On dark paper the warning has a **Use light pens** button that switches them to your
  lightest in-stock pens.
- **Generalisation**:
  - **Raster blur** smooths the elevation data before contouring.
  - **Simplify tolerance** removes points that don't change a line's shape.
  - **Smoothing** rounds off jagged lines.

  Raise them for cleaner, faster plots. Lower them for more detail.
- **Illuminated contours (Tanaka)**: slopes facing a light from the north-west get the thin contour pen,
  and shadowed slopes get the index pen. This gives a 3-D relief effect.
- **Plot ocean / crater depths** includes contours below the datum. **Plot country / state borders** draws
  Natural Earth outlines.

## Fill / Hatching

Turn it on with the section checkbox. A fill turns the map's tone into lines or dots.

- **Pattern**:
  - **Multi-Pass Crosshatch**: layers of hatching at different angles. More layers make a darker tone.
  - **Variable Spacing Scanlines**: parallel lines spaced by tone.
  - **Modulated Sine Squiggles**: wavy lines whose amplitude follows the tone.
  - **Stippling**: dots.
  - **Hachures (Slope)**: short strokes down the slope, the classic engraved-map look.
- **Tone source**: the **Color map**, a **Hillshade** computed from the elevation, or both multiplied
  together.
- **Inks**:
  - **Monochrome** uses one pen.
  - **Color separation** reproduces the colour map with the pens you tick under **Inks to use**. It
    finds the best mix of up to three of them for each colour, given your paper colour. The preview
    switches to a true-to-paper ink simulation. See [Pens and paper](pens-and-paper.md#colour-fills).
- **Line / dot spacing**: wider spacing gives thick pens more tonal steps.
- **Tone**: gel ink spreads into the paper, so move this left if plots come out too dark.
- **Auto contrast** stretches the tones of the selection to the full range.
- **Join neighbouring lines (zigzag)** links each stroke to the next, which saves many pen lifts.

## Map furniture

All lettering uses single-stroke (Hershey) fonts, so it plots cleanly with a pen.

- **Title block**: the title text plus an automatic subtitle (body, coordinates, interval). The title and
  subtitle sit in separate boxes so they cover as little of the map as possible.
- **Scale bar**: **Miles**, **Kilometres** or **Miles + km**. Short bars switch to feet or metres.
- **North arrow**, **Coordinate grid** (**Edge ticks** or **Full lines**) and **Neat-line frame**.
- **Pen** and **Text size** (Small, Medium, Large, Extra large). Sizes are scaled to the sheet and never
  go below what the chosen pen can write legibly.

Map lines are blanked behind the title, scale bar and labels.

## The preview, layers and editing tools

**Preview**: scroll to zoom, middle-drag to pan, use **Zoom box**, and **Fit page** to show the whole sheet.
Contour-only plots use a clean line view. Plots with a fill use an ink simulation that renders every pen at
its real line width, colour and opacity on your paper colour.

**Layers panel**:

- Tick or untick a layer to show or hide it. **Hidden layers are left out of the SVG and G-code.**
- **Right-click a layer** to give every stroke in it a different pen.
- **Plot order · est. time** lists the pens in the order they will be plotted and estimates the plotting
  time. Lighter inks are plotted first so dark lines go on top.

**Editing tools** (below the preview). Pick a pen from the palette, then a tool:

| Tool | How to use it |
|---|---|
| **Paint** | Click a line, or hold the button and drag across lines, to give them the active pen. **Shift**-click paints every line at that elevation. |
| **Draw line** | Click and drag to draw a freehand line. |
| **Add text** | Click where the text should go, then type the text in the box that opens. |
| **Move / rotate text** | Drag text to move it. Scroll to rotate it. **Shift**+scroll to scale it. |

**Blank lines behind text** keeps map lines out from under your own text. **Pens…** opens the pen manager.
**Undo** / **Redo** cover paint, drawing, text and layer pen changes.

## Projects and style presets

**Projects** (`*.topo.json`) save everything: subject, selection, paper, every setting, painted pens, hand
edits and annotations. Mission and image settings are saved too. Use **File › New / Open / Open recent /
Save / Save as**. Projects are saved to and opened from the `MoonPlotter/projects/` folder by default.
Opening a project re-renders the map as it was. The folder includes a Lake Tahoe example.

**Style presets** (top of the sidebar) save just the look, so you can apply it to any area. The built-in
presets are:

- Classic contours
- Illuminated relief (Tanaka)
- Hachures + contours
- Hillshade crosshatch
- Colour stipple

**Save…** stores your own preset in `presets.json`.

## Keyboard shortcuts

| Keys | Action |
|---|---|
| `Ctrl+N` / `Ctrl+O` / `Ctrl+S` / `Ctrl+Shift+S` | New / open / save / save project as |
| `Ctrl+E` | Export G-code |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` or `Ctrl+Shift+Z` | Redo |
| `Ctrl+Q` | Quit |
