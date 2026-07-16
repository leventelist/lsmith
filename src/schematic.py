"""
Ladder-network schematic renderer.

Draws the matching network's element chain as a simple schematic: series
elements sit inline on the signal path, shunt elements branch down to a
ground symbol. Meant for exported reports, not for on-screen interaction.
"""
from __future__ import annotations

import numpy as np
from matplotlib.patches import Rectangle

from chart import ChartTheme, LIGHT_THEME
from engine import Element, Kind, Topology

NODE_SPACING = 2.0
SYMBOL_HALF_WIDTH = 0.45
STUB_LENGTH = 1.4
WIRE_Y = 0.0
LEAD_FRAC = 0.28  # fraction of a symbol's span left as plain wire on each side
BOX_W = 0.9
BOX_H = 0.9

# IEC 60617 general symbols: resistor is a slim open rectangle, inductor is
# a row of connected humps (not a sine squiggle), capacitor is two parallel
# plates with a small gap.
RESISTOR_HALF_HEIGHT = 0.11
INDUCTOR_HUMPS = 3
INDUCTOR_HUMP_HEIGHT = 0.22
CAPACITOR_GAP = 0.11
CAPACITOR_PLATE_HALF_LEN = 0.34


def _fmt_component(element: Element) -> str:
    scales = {
        Kind.R: [(1e6, "MΩ"), (1e3, "kΩ"), (1.0, "Ω")],
        Kind.L: [(1e-3, "mH"), (1e-6, "µH"), (1e-9, "nH"), (1e-12, "pH")],
        Kind.C: [(1e-3, "mF"), (1e-6, "µF"), (1e-9, "nF"), (1e-12, "pF")],
    }[element.kind]
    for scale, unit in scales:
        scaled = element.value / scale
        if 1 <= abs(scaled) < 1000:
            return f"{scaled:g} {unit}"
    scale, unit = scales[-1]
    return f"{element.value / scale:g} {unit}"


def _draw_resistor_h(ax, xc, y, half_width, color):
    """IEC 60617: a plain open rectangle inline on the wire."""
    x0, x1 = xc - half_width, xc + half_width
    lead = half_width * 2 * LEAD_FRAC
    ax.plot([x0, x0 + lead], [y, y], color=color, linewidth=1.2, zorder=2)
    ax.plot([x1 - lead, x1], [y, y], color=color, linewidth=1.2, zorder=2)
    ax.add_patch(Rectangle((x0 + lead, y - RESISTOR_HALF_HEIGHT),
                            (x1 - lead) - (x0 + lead), 2 * RESISTOR_HALF_HEIGHT,
                            fill=False, edgecolor=color, linewidth=1.2, zorder=2))


def _draw_resistor_v(ax, x, yc, half_height, color):
    y0, y1 = yc - half_height, yc + half_height
    lead = half_height * 2 * LEAD_FRAC
    ax.plot([x, x], [y0, y0 + lead], color=color, linewidth=1.2, zorder=2)
    ax.plot([x, x], [y1 - lead, y1], color=color, linewidth=1.2, zorder=2)
    ax.add_patch(Rectangle((x - RESISTOR_HALF_HEIGHT, y0 + lead),
                            2 * RESISTOR_HALF_HEIGHT, (y1 - lead) - (y0 + lead),
                            fill=False, edgecolor=color, linewidth=1.2, zorder=2))


def _humps(n: int, height: float):
    """n humps (each a half sine arch from 0 up to `height` and back to 0),
    concatenated end to end over u in [0, 1] -- the IEC coil/inductor symbol.
    All humps bulge the same way (unlike a plain sine, which would alternate)."""
    u = np.linspace(0.0, 1.0, 40 * n)
    return height * np.abs(np.sin(n * np.pi * u))


def _draw_inductor_h(ax, xc, y, half_width, color):
    """IEC 60617: a row of connected humps bulging away from the wire."""
    x0, x1 = xc - half_width, xc + half_width
    lead = half_width * 2 * LEAD_FRAC
    ax.plot([x0, x0 + lead], [y, y], color=color, linewidth=1.2, zorder=2)
    ax.plot([x1 - lead, x1], [y, y], color=color, linewidth=1.2, zorder=2)
    xs = np.linspace(x0 + lead, x1 - lead, 40 * INDUCTOR_HUMPS)
    ys = y + _humps(INDUCTOR_HUMPS, INDUCTOR_HUMP_HEIGHT)
    ax.plot(xs, ys, color=color, linewidth=1.2, zorder=2)


def _draw_inductor_v(ax, x, yc, half_height, color):
    y0, y1 = yc - half_height, yc + half_height
    lead = half_height * 2 * LEAD_FRAC
    ax.plot([x, x], [y0, y0 + lead], color=color, linewidth=1.2, zorder=2)
    ax.plot([x, x], [y1 - lead, y1], color=color, linewidth=1.2, zorder=2)
    ys = np.linspace(y0 + lead, y1 - lead, 40 * INDUCTOR_HUMPS)
    xs = x + _humps(INDUCTOR_HUMPS, INDUCTOR_HUMP_HEIGHT)
    ax.plot(xs, ys, color=color, linewidth=1.2, zorder=2)


def _draw_capacitor_h(ax, xc, y, half_width, color):
    """IEC 60617: two parallel plates perpendicular to the wire, with a gap."""
    gap = CAPACITOR_GAP
    ax.plot([xc - half_width, xc - gap], [y, y], color=color, linewidth=1.2, zorder=2)
    ax.plot([xc + gap, xc + half_width], [y, y], color=color, linewidth=1.2, zorder=2)
    ax.plot([xc - gap, xc - gap], [y - CAPACITOR_PLATE_HALF_LEN, y + CAPACITOR_PLATE_HALF_LEN],
            color=color, linewidth=1.2, zorder=2)
    ax.plot([xc + gap, xc + gap], [y - CAPACITOR_PLATE_HALF_LEN, y + CAPACITOR_PLATE_HALF_LEN],
            color=color, linewidth=1.2, zorder=2)


def _draw_capacitor_v(ax, x, yc, half_height, color):
    gap = CAPACITOR_GAP
    ax.plot([x, x], [yc - half_height, yc - gap], color=color, linewidth=1.2, zorder=2)
    ax.plot([x, x], [yc + gap, yc + half_height], color=color, linewidth=1.2, zorder=2)
    ax.plot([x - CAPACITOR_PLATE_HALF_LEN, x + CAPACITOR_PLATE_HALF_LEN], [yc - gap, yc - gap],
            color=color, linewidth=1.2, zorder=2)
    ax.plot([x - CAPACITOR_PLATE_HALF_LEN, x + CAPACITOR_PLATE_HALF_LEN], [yc + gap, yc + gap],
            color=color, linewidth=1.2, zorder=2)


_SYMBOLS_H = {Kind.R: _draw_resistor_h, Kind.L: _draw_inductor_h, Kind.C: _draw_capacitor_h}
_SYMBOLS_V = {Kind.R: _draw_resistor_v, Kind.L: _draw_inductor_v, Kind.C: _draw_capacitor_v}


def _draw_ground(ax, x, y, color):
    for i, half_width in enumerate((0.22, 0.14, 0.06)):
        yy = y - i * 0.12
        ax.plot([x - half_width, x + half_width], [yy, yy], color=color, linewidth=1.2, zorder=2)


def draw_schematic(ax, z_source: complex, elements: list[Element], z0: float,
                    theme: ChartTheme = LIGHT_THEME) -> None:
    """Clear `ax` and draw the element chain as a ladder-network schematic,
    source on the left and Z0 on the right."""
    ax.clear()
    fig = ax.get_figure()
    if fig is not None:
        fig.patch.set_facecolor(theme.background)
        fig.subplots_adjust(left=0.02, right=0.98, top=0.9, bottom=0.1)
    ax.set_facecolor(theme.background)
    ax.set_aspect("equal")
    ax.axis("off")

    color = theme.foreground
    n = len(elements)
    x_left = 0.0
    x_right = (n + 1) * NODE_SPACING

    ax.add_patch(Rectangle((x_left - BOX_W, WIRE_Y - BOX_H / 2), BOX_W, BOX_H,
                            fill=False, edgecolor=color, linewidth=1.2, zorder=2))
    ax.text(x_left - BOX_W / 2, WIRE_Y, "Source", color=color, ha="center", va="center",
            fontsize=8, zorder=3)
    ax.text(x_left - BOX_W / 2, WIRE_Y - BOX_H / 2 - 0.3,
            f"{z_source.real:.2f}{z_source.imag:+.2f}j Ω", color=color,
            ha="center", va="top", fontsize=7, zorder=3)

    ax.add_patch(Rectangle((x_right, WIRE_Y - BOX_H / 2), BOX_W, BOX_H,
                            fill=False, edgecolor=color, linewidth=1.2, zorder=2))
    ax.text(x_right + BOX_W / 2, WIRE_Y, "Z0", color=color, ha="center", va="center",
            fontsize=8, zorder=3)
    ax.text(x_right + BOX_W / 2, WIRE_Y - BOX_H / 2 - 0.3, f"{z0:g} Ω", color=color,
            ha="center", va="top", fontsize=7, zorder=3)

    prev_x = x_left
    for i, element in enumerate(elements):
        xc = x_left + (i + 1) * NODE_SPACING
        label = f"{i + 1}: {element.kind.value} {_fmt_component(element)}"

        if element.topology == Topology.SERIES:
            ax.plot([prev_x, xc - SYMBOL_HALF_WIDTH], [WIRE_Y, WIRE_Y],
                    color=color, linewidth=1.2, zorder=1)
            _SYMBOLS_H[element.kind](ax, xc, WIRE_Y, SYMBOL_HALF_WIDTH, color)
            ax.plot([xc + SYMBOL_HALF_WIDTH, xc], [WIRE_Y, WIRE_Y], color=color, linewidth=0)
            ax.text(xc, WIRE_Y + 0.45, label, color=color, ha="center", va="bottom",
                    fontsize=7, zorder=3)
            prev_x = xc + SYMBOL_HALF_WIDTH
        else:
            ax.plot([prev_x, xc], [WIRE_Y, WIRE_Y], color=color, linewidth=1.2, zorder=1)
            ax.plot(xc, WIRE_Y, marker="o", markersize=3, color=color, zorder=2)
            stub_yc = WIRE_Y - STUB_LENGTH / 2
            _SYMBOLS_V[element.kind](ax, xc, stub_yc, STUB_LENGTH / 2, color)
            _draw_ground(ax, xc, WIRE_Y - STUB_LENGTH, color)
            ax.text(xc + 0.5, stub_yc, label, color=color, ha="left", va="center",
                    fontsize=7, zorder=3)
            prev_x = xc

    ax.plot([prev_x, x_right], [WIRE_Y, WIRE_Y], color=color, linewidth=1.2, zorder=1)

    ax.set_xlim(x_left - BOX_W - 0.6, x_right + BOX_W + 2.2)
    ax.set_ylim(-(STUB_LENGTH + 0.7), 1.1)
