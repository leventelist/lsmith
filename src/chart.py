"""
Manual Smith chart renderer.

Draws the classic normalized-impedance Smith chart (constant-R circles,
constant-X arcs) on a matplotlib Axes, and provides the z -> Gamma mapping
used to plot impedance points and matching paths on it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RESISTANCE_RINGS = (0.2, 0.5, 1.0, 2.0, 5.0)
REACTANCE_ARCS = (0.2, 0.5, 1.0, 2.0, 5.0)

GRID_ALPHA = 0.35  # impedance grid lines, drawn in the foreground color at this alpha
ADMITTANCE_ALPHA = 0.15  # admittance grid overlay -- fainter than the impedance grid
LABEL_ALPHA = 0.65
LABEL_FONTSIZE = 7
LABEL_RADIUS = 1.18  # reactance labels sit just outside the |Gamma|=1 boundary


@dataclass(frozen=True)
class ChartTheme:
    background: str = "white"
    foreground: str = "black"  # boundary circle, grid lines, center marker


LIGHT_THEME = ChartTheme(background="white", foreground="black")
DARK_THEME = ChartTheme(background="#1e1e1e", foreground="#e8e8e8")


def z_to_gamma(z: complex, z0: float = 50.0) -> complex:
    return (z - z0) / (z + z0)


def gamma_to_z(gamma: complex, z0: float = 50.0) -> complex:
    return z0 * (1 + gamma) / (1 - gamma)


def _reactance_arc_points(x: float, n: int = 400):
    """
    Points along the portion of the constant-X circle (center (1, 1/x),
    radius 1/x) that lies inside the unit disk, sampled with a fixed
    point count directly over that visible span -- not over the full
    circle -- so the curve stays smooth no matter how small x is.
    """
    rad = 1.0 / x
    cx, cy = 1.0, rad

    phi1 = -np.pi / 2  # this circle always passes through (1, 0)
    phi2 = np.arctan2(1 - rad ** 2, -2 * rad)

    def point_at(phi):
        return cx + rad * np.cos(phi), cy + rad * np.sin(phi)

    # walk from phi1 to phi2 "the short way" or "the long way" around the
    # circle -- pick whichever keeps the midpoint inside the unit disk
    phi2_alt = phi2 - 2 * np.pi if phi2 > phi1 else phi2 + 2 * np.pi
    mx, my = point_at((phi1 + phi2) / 2)
    phis = np.linspace(phi1, phi2, n) if mx ** 2 + my ** 2 <= 1.0 \
        else np.linspace(phi1, phi2_alt, n)

    return cx + rad * np.cos(phis), cy + rad * np.sin(phis)


def _fmt_ohms(value: float) -> str:
    return f"{value:g}"


def draw_smith_chart(ax, z0: float = 50.0, theme: ChartTheme = LIGHT_THEME) -> None:
    """Clear `ax` and draw the Smith chart grid on it."""
    ax.clear()
    fig = ax.get_figure()
    if fig is not None:
        fig.patch.set_facecolor(theme.background)
        # matplotlib's default subplot margins leave ~20% of the figure as
        # blank border; since there are no tick labels to make room for
        # (axis is off), let the chart use nearly the whole canvas instead.
        fig.subplots_adjust(left=0.02, right=0.98, top=0.98, bottom=0.02)
    ax.set_facecolor(theme.background)
    ax.set_aspect("equal")
    ax.set_xlim(-1.3, 1.3)
    ax.set_ylim(-1.3, 1.3)
    ax.axis("off")

    theta = np.linspace(0, 2 * np.pi, 400)

    # outer |Gamma| = 1 boundary
    ax.plot(np.cos(theta), np.sin(theta), color=theme.foreground, linewidth=1.3, zorder=2)

    # constant-resistance circles (r = R / Z0) -- these lie entirely
    # inside the unit disk (tangent to it at Gamma=1), so a plain full
    # sweep is fine, no clipping/under-sampling issue here.
    for r in RESISTANCE_RINGS:
        cx = r / (r + 1)
        rad = 1 / (r + 1)
        ax.plot(cx + rad * np.cos(theta), rad * np.sin(theta),
                color=theme.foreground, alpha=GRID_ALPHA, linewidth=0.6, zorder=1)

        # admittance mirror: the constant-conductance circle for the same
        # normalized value is this same circle reflected across the
        # imaginary axis (Gamma_y = -Gamma_z), drawn fainter as an overlay.
        ax.plot(-(cx + rad * np.cos(theta)), rad * np.sin(theta),
                color=theme.foreground, alpha=ADMITTANCE_ALPHA, linewidth=0.6, zorder=1)

        # label at this circle's crossing of the real axis (the pure-R point)
        ax.text(cx - rad, 0, _fmt_ohms(r * z0), color=theme.foreground, alpha=LABEL_ALPHA,
                fontsize=LABEL_FONTSIZE, ha="center", va="bottom", zorder=2)

    # constant-reactance arcs (x = X / Z0), above and below the real axis
    for x in REACTANCE_ARCS:
        gx, gy = _reactance_arc_points(x)
        ax.plot(gx, gy, color=theme.foreground, alpha=GRID_ALPHA, linewidth=0.6, zorder=1)
        ax.plot(gx, -gy, color=theme.foreground, alpha=GRID_ALPHA, linewidth=0.6, zorder=1)

        # admittance mirror (constant-susceptance arcs), fainter overlay
        ax.plot(-gx, gy, color=theme.foreground, alpha=ADMITTANCE_ALPHA, linewidth=0.6, zorder=1)
        ax.plot(-gx, -gy, color=theme.foreground, alpha=ADMITTANCE_ALPHA, linewidth=0.6, zorder=1)

        # label just outside the boundary, at the arc's far end (where it
        # meets |Gamma| = 1) pushed radially out -- keeps the inside of the
        # chart uncluttered, matching where printed Smith charts put these.
        ex, ey = gx[-1], gy[-1]
        label = _fmt_ohms(x * z0)
        ax.text(ex * LABEL_RADIUS, ey * LABEL_RADIUS, f"+j{label}", color=theme.foreground,
                alpha=LABEL_ALPHA, fontsize=LABEL_FONTSIZE, ha="center", va="center", zorder=2)
        ax.text(ex * LABEL_RADIUS, -ey * LABEL_RADIUS, f"-j{label}", color=theme.foreground,
                alpha=LABEL_ALPHA, fontsize=LABEL_FONTSIZE, ha="center", va="center", zorder=2)

    # zero-reactance line (real axis, from short at -1 to open at +1)
    ax.plot([-1, 1], [0, 0], color=theme.foreground, alpha=GRID_ALPHA, linewidth=0.6, zorder=1)

    # center marker (Z = Z0, matched point)
    ax.plot(0, 0, marker="+", color=theme.foreground, markersize=8, zorder=3)


def plot_point(ax, z: complex, z0: float = 50.0, color: str = "red",
               marker: str = "o", label: str | None = None, zorder: int = 5):
    g = z_to_gamma(z, z0)
    ax.plot(g.real, g.imag, marker=marker, color=color, markersize=7,
             label=label, zorder=zorder)


def plot_path(ax, points: list[complex], z0: float = 50.0, color: str = "red",
              linewidth: float = 2.0, zorder: int = 4):
    gs = [z_to_gamma(p, z0) for p in points]
    xs = [g.real for g in gs]
    ys = [g.imag for g in gs]
    ax.plot(xs, ys, color=color, linewidth=linewidth, zorder=zorder)