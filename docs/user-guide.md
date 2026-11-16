# User guide

The window has three parts:

- **The sidebar** on the left holds all settings, in collapsible sections. The mouse wheel scrolls the
  sidebar. It never changes a drop-down or slider by accident.
- **The source map** shows the whole globe for the chosen body. You choose the area to plot here.
- **The plot preview** shows the sheet exactly as it will be drawn. The editing tools are below it and
  the **Layers** panel is to its right.

Need more room for the preview? Click **Hide** in the top corner of the source map or the Layers panel.
A **Source map** / **Layers** button then appears above the preview to bring it back. **Focus** (F11) hides
both at once, and **View › Source map / Layers panel** (Ctrl+1 / Ctrl+2) does the same from the menu.

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

Solar System mode, city maps and image tracing have their own pages: [Solar System mode](solar-system.md),
[City road maps](city-maps.md) and [Image tracing](image-tracing.md).

---

## Choosing an area

**Source › Subject** picks the **Earth**, **Moon** or **Mars**. The **City**, **Solar System** and **Image**
modes are covered on their own pages. Mars stays greyed out until its data files are present. See
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
  outline.
- **Lakes** (in the same list) are drawn as lake-bed contours below the water surface, with the shoreline
  as an index line: **Lake Tahoe (High-Res Bathymetry)** (bundled 10 m survey, 75 ft interval),
  **Jenny Lake (modelled depth)** (20 ft) and **Jackson Lake (modelled depth)** (40 ft). No public survey
  grid exists for Jenny or Jackson Lake, so their beds are modelled from the real OpenStreetMap shoreline
  and the published maximum (and, for Jenny, average) depth: the outline and depth range are real, the
  exact shape of the bed is not. The first time you pick one it is built in a few seconds (internet needed
  once if its shoreline file is missing). See [Data sources](data-sources.md#lakes).
- **Ski resorts** (in the same **Clip to region** list): Grand Targhee, Deer Valley and Sundance. Choosing
  one frames the resort and overlays its **ski runs** and **lifts** (from OpenStreetMap) on the contours,
  each on its own layer and pen. Lifts are drawn with small tick marks for the towers. **Colour runs by
  difficulty** puts green / blue / black / double-black runs on matching pens when you have them in stock.
  The first time you pick a resort, the app also fetches 30 m elevation for the area around it (needs an
  internet connection once). See [Data sources](data-sources.md#ski-resorts).
- **Circle mode (whole hemisphere)**: plots a full disc of the globe. Drag on the map to turn it. Fills
  on a globe always cover the whole disc: bright areas (ice caps, deserts, lunar highlands) and gaps in
  the colour map get the lightest level of ink instead of being left white, and neighbouring inks overlap
  slightly at coastlines so no white seam shows.
- **Show points of interest**: markers for landing sites, mountains, craters and other good subjects.
  Hover a marker for details. Click it to add it to the plot (a filled marker means it is plotted).
  Double-click it to frame the area around it. **Label plotted sites** writes their names on the plot.
- **Show borders on the source map** is a display aid only. **Plot country / state borders** in the
  Contours section actually draws them.

Then click **Draw Map**. Drawing again always starts fresh: it picks a new automatic interval (unless you
typed one, see [Contours](#contours)), resets the preview zoom and clears hand edits. Changing a setting afterwards re-renders automatically and keeps your
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
- **Split into pages (tiles)**: plot one map across several sheets. Set how many pages **across** and
  **down**; each page is the paper size above, so the map covers the whole mosaic at that scale. **Gap** is
  the space you will leave between the mounted pages: the map skips that strip so features line up across
  it. The preview shades the gaps and labels each page (R1C1 is the top-left). **Cut lines** outlines the
  pages in the preview and ticks **Cut out each page** in the export dialog. G-code export then writes a
  full set of files for every page, named with its page label, e.g. `01_Thin_G2_Black_mymap_r1c2.gcode`.
- **Pages**: one button per page, laid out like the pages. Click a page to hide it: it is greyed out in
  the preview ("hidden - not exported") and left out of the G-code export. Click it again, or **Show
  all**, to bring it back. Handy for re-plotting a single page.

## Contours

Turn the whole layer on or off with the checkbox in the section header.

- **Interval**: the badge shows the automatic interval, in feet, picked for the relief of your selection.
  **Contour density** steps to the next sparser or denser standard interval. **Interval override** sets an
  exact value; a typed interval is kept when you click **Draw Map** again (the badge says "Manual") until
  you click **Auto** next to it or move the density slider.
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

## Layered cut-out (stacked cardstock)

Instead of drawing the map, the knife cuts each height band out of card. Stack and glue the pieces and
you get a 3-D relief map. Tick **On** in the **Layered cut-out** header, then click **Draw Map**.

- **Layers**: how many card layers, the bottom one included. Heights are split into equal steps. Layer 1
  is the whole map rectangle; each layer above is everything higher than its level. Lakes and hollows
  become holes, so a lake bed (Lake Tahoe, Jenny Lake...) comes out as a stepped bowl.
- **Card sheet to cut from**: the size of the card you load (150 x 150 mm by default). This is separate
  from the map size in **Paper**: the map is cut from as many sheets as needed. Pieces are rotated
  (in 45° steps) and packed to waste little card; small pieces can go inside the holes of bigger ones.
  The map size must be smaller than the sheet (by about 10 mm) or its bottom layer won't fit.
- **Smallest detail**: islands, thin necks and gaps narrower than this are dropped or filled - the knife
  can't cut them and they are too small to glue.
- **Marking pen**: on every piece it draws the outline of the layer that goes on top (0.6 mm inside it,
  so the glued piece hides the line) and the layer number where the next layer will cover it. The top
  layer gets nothing drawn on it; its number goes on the waste beside it. Map furniture (title, scale
  bar...) is drawn on layer 1.
- **Different card colour by height** (for later): pieces for different colours never share a sheet, and
  the colour changes every _n_ layers.
- **Preview**: the finished stack, or any sheet with its cuts (red, dashed) and pen marks.

**Export G-code** writes one file per sheet (`..._sheet01.gcode`, ...). For each sheet: load the card at
the plot origin, the pen draws the marks, the plotter pauses for you to fit the knife, then it cuts -
small pieces first, each piece's holes before its outline, with corner loops at sharp corners. **Save
SVG** saves one SVG per sheet (cuts red, marks black). Circle mode and page tiling don't apply here.

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
