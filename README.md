# Smith Chart Matching Tool — with Automatic L-Network

A GTK4 app for visualizing impedance matching: enter a source
impedance, add series/shunt R/L/C elements, and watch each one trace
its arc across the Smith chart. Live readouts for Z, Gamma, VSWR, and
return loss. Includes an analytic auto-match solver, a frequency
sweep (with support for importing a real measured antenna sweep from
a file), and PNG/PDF/CSV export.

![Screenshot](doc/screenshot.png)

![Screenshot_sweep](doc/screenshot_sweep.png)

![Screenshot_auto_match](doc/auto_match.png)

## Files

- `engine.py` — core impedance math (no GUI deps).
  - `MatchingNetwork` holds a source Z and an ordered list of
    series/shunt R/L/C elements, and computes the impedance after each
    step, plus Gamma/VSWR/return loss.
  - `solve_l_match()` analytically computes a 1- or 2-element L-network
    that exactly matches a given source Z to Z0.
  - `sweep()` evaluates a network across a list of frequencies, with
    either a fixed source impedance or a per-frequency callable (for a
    real antenna whose impedance varies across the band).
  - `load_impedance_csv()` / `load_touchstone_1port()` /
    `interpolate_impedance()` — read a measured impedance-vs-frequency
    dataset and interpolate it at an arbitrary frequency.

  This module is fully unit-testable on its own — see `tests/`.
- `chart.py` — draws the Smith chart grid: constant-R circles and
  constant-X arcs (impedance), plus a fainter overlay of the mirrored
  constant-G/constant-B admittance grid, both labeled with actual
  ohm values scaled to Z0. Maps Z -> Gamma for plotting points and
  paths. Also draws the VSWR-vs-frequency sweep plot.
- `schematic.py` — draws the matching network as a ladder-network
  schematic (IEC 60617-style symbols: resistor box, inductor coil,
  capacitor plates), series elements inline and shunt elements
  branching to ground. Used for the schematic/report exports, not
  shown live.
- `app.py` — the GTK4 windows: the main window (source Z0/R/X/frequency
  entries, an "Add element" list where each row is (series/shunt, R/L/C,
  value, unit), the Smith chart, and result readouts) plus a separate
  "Frequency Sweep" window (VSWR-vs-frequency plot and results table,
  side by side). All the major panes are separated by draggable
  dividers.

## Setup (Debian/Ubuntu)

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 python3-matplotlib python3-numpy
```

## Run

```bash
python3 app.py
```

## Using it

### 1. Source impedance

Set **Z0** (system impedance, usually 50), **Freq** (the single
frequency the Smith chart and element arcs are drawn at), and
**Source R + jX** — the impedance you're trying to match, e.g. from
an antenna feedpoint measurement.

### 2. Auto-Match to Z0

Click **Auto-Match to Z0…** to have the app solve for you instead of
dialing in values by hand. It analytically computes the L-network (1
or 2 reactive components) that matches the current source exactly:

- If a component's resistance already equals Z0, only one series
  element (to cancel the reactance) is needed.
- Otherwise there are up to two distinct exact solutions (e.g. series
  L + shunt C vs. series L + shunt L) — a dialog lets you pick which
  one to use, then fills in the element list for you.
- If the source is already matched, or its resistance is zero or
  negative (not physically matchable with a lossless L-network), you
  get an explanatory message instead.

### 3. Importing a measured impedance sweep

A real antenna's impedance isn't constant across the band — if you've
measured it with a VNA (NanoVNA, miniVNA, etc.), **File → Import
Impedance Sweep (CSV/Touchstone)…** lets the app use your actual
measured data instead of a single fixed source impedance:

1. Pick a `.csv` or `.s1p` file (formats below). The app plots the
   whole imported range as the initial frequency sweep and reports how
   many points it found.
2. Tick **Use imported impedance sweep** to switch both the
   single-frequency view and the sweep over to the measured data — the
   manual Source R/X fields become disabled (they're not used while
   this is on), and the status line shows the impedance actually being
   used, interpolated at the current Freq.
3. Tick **Sweep at exact imported frequencies** to make the sweep
   evaluate precisely at your file's own frequency points instead of
   an evenly-spaced Start/Stop/Steps grid — this also disables those
   three fields, since they no longer apply.

Between measured points, R and X are linearly interpolated separately;
outside the measured range, the nearest end value is held constant
(no extrapolation) rather than guessed.

#### Supported file formats

- **CSV** — three columns: `Freq (MHz), R (ohm), X (ohm)`. A header
  row is fine, it's simply skipped as unparsable. See
  `tests/data/sample_antenna_sweep.csv` for an example.
- **Touchstone `.s1p`** — the standard one-port VNA export format.
  Supported:
  - Frequency units: Hz, kHz, MHz, GHz (from the `#` option line;
    defaults to GHz if omitted, per the Touchstone spec).
  - Parameter types: `S` (reflection coefficient — by far the most
    common for antenna analyzers), `Y`, and `Z`.
  - Data formats: `RI` (real/imaginary), `MA` (magnitude/angle in
    degrees), `DB` (dB magnitude/angle in degrees).
  - An explicit reference impedance (`R <n>` on the option line;
    defaults to 50 Ω).

  See `tests/data/sample_antenna_sweep.s1p` for an example — it's the
  same synthetic antenna as the sample CSV above, so you can compare
  the two loaders against each other.

### 4. Matching elements

Click **+ Add element** to add a matching component by hand. Choose:

- **series** or **shunt** (topology)
- **R**, **L**, or **C** (component type)
- the value and a unit — Ω/kΩ/MΩ for R, mH/µH/nH/pH for L,
  mF/µF/nF/pF for C

### 5. Reading the chart

- Each element appears as a colored arc, moving the impedance point
  along a constant-resistance circle (series) or constant-conductance
  circle (shunt) — the same way you'd reason through a match by hand
  on paper.
- The final point (green star) and the readouts on the left show how
  close you are to Z0 — aim for VSWR near 1.0 / |Gamma| near 0.
- The grid shows both the impedance circles (solid) and a fainter
  overlay of the mirrored admittance circles, each labeled with actual
  ohm values scaled to Z0 (not the normalized 0.2/0.5/1/2/5 you'd see
  on a printed chart).
- Hover the mouse over the chart to read off the impedance and VSWR at
  the pointer (e.g. `Pointer: Z = 12.34-56.78j Ω   VSWR = 3.42`) below
  the canvas — handy for arbitrary points on the grid, not just the
  plotted ones.
- **View → Show Sweep Points on Chart** overlays the swept frequency
  points (dots joined by straight lines) directly on the Smith chart.

### 6. Frequency sweep

The **Frequency sweep** section (Start/Stop MHz, Steps) evaluates the
same source and elements across a frequency range instead of just the
single Freq value, so you can check a match's bandwidth (or see how a
real antenna's impedance moves across the band, if you've imported
one — see above). Results show up live in the separate "Frequency
Sweep" window: a VSWR-vs-frequency plot and a results table, side by
side, both with a draggable divider. **View → Show Sweep Window**
shows/hides it — closing it from its own titlebar does the same
thing, and that checkbox always reflects whether it's currently open.

### 7. Resizable layout

Every major pane — the controls column vs. the chart, the sweep
plot vs. its table — is split by a draggable divider, so you can
resize things to taste.

## File menu

- **Open… / Save / Save As…** (`Ctrl+O` / `Ctrl+S` / `Ctrl+Shift+S`) —
  save the source settings (Z0, frequency, source R/X), the frequency
  sweep range (Start/Stop/Steps), and the full element list to a JSON
  file, or reload one later. Handy for keeping a matching network per
  antenna/band around instead of re-entering values by hand. (This
  does *not* include an imported impedance sweep file — re-import that
  separately if needed.)
- **Import Impedance Sweep (CSV/Touchstone)…** — see "Importing a
  measured impedance sweep" above.
- **Export Chart to PNG…** (`Ctrl+E`) — save the current Smith chart
  (grid, arcs, and points as currently drawn) as a PNG image.
- **Export Chart to PDF…** — the same chart as a single-page PDF, sized
  to the paper size chosen in the View menu.
- **Export Schematic to PNG…** — save the matching network's schematic
  diagram as a PNG image.
- **Export Report to PDF (Chart + Schematic + Sweep)…** — a multi-page
  PDF: the Smith chart (portrait), the schematic (landscape), the
  VSWR-vs-frequency sweep plot (landscape), and the sweep results table
  (portrait, paginated if it's long).
- **Export Sweep Table to CSV…** (`Ctrl+Shift+E`) — save the frequency
  sweep results (Freq/VSWR/Return Loss) as a CSV file.
- **Quit** (`Ctrl+Q`).

## View menu

- **Light / Dark** — built-in color presets for the chart (background
  and grid/boundary/marker color).
- **Custom Colors…** — pick your own background and chart color live.
- Whichever theme you land on (a preset or custom colors) is remembered
  across restarts, saved to `~/.config/lsmith/config.json`.
- **Show Sweep Points on Chart** — overlay the swept frequency points
  (dots joined by straight lines) on the Smith chart itself.
- **Show Sweep Window** — show/hide the separate "Frequency Sweep"
  window; closing it from its own titlebar does the same thing, and
  this checkbox always reflects whether it's currently open.
- **PDF Paper Size** — A4 / Letter / Legal for the PDF exports above.
  Defaults to your system's configured paper size (via GTK's print
  settings) the first time you run the app, and is remembered after
  that.

## Testing

```bash
python3 -m pytest tests/ -v
```

`tests/` covers the GUI-independent modules (`engine.py`, `chart.py`,
`schematic.py`) — impedance math, `solve_l_match()` solutions verified
to land exactly on Z0, the CSV/Touchstone loaders and interpolation,
and headless (Agg-backend) smoke tests for the drawing functions.
`tests/data/` has example `sample_antenna_sweep.csv` and `.s1p` files
(the same synthetic antenna in both formats) — handy both as test
fixtures and as something to try the import feature on.
