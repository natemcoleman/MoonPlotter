# Solar System mode

Plot the real paths of deep-space missions through the Solar System, in the style of NASA's classic
trajectory charts.

<p align="center"><img src="images/voyager2.png" width="50%" alt="Voyager 2 trajectory plot"></p>

## Making a plot

1. Under **Source › Subject**, choose **Solar System**. The **Missions** section replaces the map and
   terrain settings. The source map shows the true-scale orbits, measured in AU.
2. Tick one or more missions in the table. Each mission has its own pen: click the swatch in the **Pen**
   column to change it.
3. Click **Draw Mission Plot**.

The first time you plot a mission, its trajectory is downloaded from the
[NASA JPL Horizons](https://ssd.jpl.nasa.gov/horizons/) API. This needs an internet connection and takes
a few seconds. It is then cached in `MoonPlotter/missions/<mission>.json`. Cached missions work offline,
and once a mission is downloaded, ticking it updates the plot straight away. The table tooltip shows
whether a mission is downloaded, along with its flybys.

## Missions

| Mission | Launched | Johns Hopkins APL role |
|---|---|---|
| Pioneer 10, Pioneer 11 | 1972, 1973 | |
| Voyager 1, Voyager 2 | 1977 | APL-built instrument (LECP) |
| Galileo | 1989 | APL-built instrument |
| Cassini | 1997 | APL-built instrument |
| Juno | 2011 | APL-built instrument |
| New Horizons | 2006 | Built and operated by APL |
| MESSENGER | 2004 | Built and operated by APL |
| Parker Solar Probe | 2018 | Built and operated by APL |
| NEAR Shoemaker | 1996 | Built and operated by APL |
| DART | 2021 | Built and operated by APL |
| Europa Clipper | 2024 | Spacecraft developed by JPL and APL |
| STEREO-A | 2006 | Built and operated by APL |
| Lucy | 2021 | APL-built instrument |
| Rosetta, Dawn | 2004, 2007 | |

**Show** filters the table: **All missions**, **Johns Hopkins APL missions**, or **APL-built spacecraft
only** (★ = APL-built, ☆ = APL instrument). For missions still in flight, the later part of the path is
JPL's planned trajectory.

Adding a mission means adding an entry to `MISSIONS` in `missions.py`, with its Horizons spacecraft ID,
launch date and the flybys to label. See [Development](development.md).

## Options

- **Trajectory line**: **Dashed**, **Dotted** or **Solid**.
- **Year markers**: dots at regular intervals of years since launch along each path, labelled "5 YR",
  "10 YR" and so on. The step depends on the mission's length.
- **Labels with leader arrows**: each flyby ("JUPITER 1979"), the launch from Earth, the year markers,
  the Sun, and "ESCAPES SOLAR SYSTEM" for missions that leave. Labels are placed so they avoid the
  paths, markers and each other.
- **Asteroid belt (stippled)**: a dotted band from 2.15 to 3.3 AU.
- **Pens**: **Labels, Sun & planets**, **Orbits** and **Asteroid belt** each have their own pen.
- **Use light pens (dark paper)**: for black or dark paper. It sets labels, orbits and the belt to your
  thinnest light pen, and swaps any trajectory pen that wouldn't show for a light one.
- **Title**: leave it blank for an automatic title such as "VOYAGER 2 MISSION TRAJECTORY".

**Map furniture › Text size** and **Neat-line frame** apply here too. **Right-click a layer** (orbits,
trajectories, markers, labels, belt) to recolour the whole layer.

## How it's drawn

- Distances from the Sun are **compressed** on a logarithmic-style scale, so Mercury's orbit and Neptune's
  both fit on one sheet. Directions are true: everything is projected onto the plane of Earth's orbit.
  Distances are not to scale.
- Planet orbits come from NASA JPL's approximate Keplerian elements, which are valid from 1800 to 2050.
- Planet markers are placed where each planet was on the date of the flyby.
- Paths that run past the edge of the sheet are cut at the edge, and the status line names them.
  Missions leaving the Solar System (Voyager, Pioneer, New Horizons) are labelled as escaping.
