import cmath

import matplotlib
matplotlib.use("Agg")  # headless: these tests only check the math + that drawing doesn't raise

import pytest
from matplotlib.figure import Figure

from chart import (
    z_to_gamma, gamma_to_z, draw_smith_chart, plot_point, plot_path,
    plot_sweep_points, draw_vswr_sweep, LIGHT_THEME, DARK_THEME,
)


def test_z_to_gamma_matched_load_is_center():
    assert z_to_gamma(complex(50, 0), 50.0) == complex(0, 0)


def test_z_to_gamma_short_circuit_is_minus_one():
    assert z_to_gamma(complex(0, 0), 50.0) == complex(-1, 0)


def test_gamma_to_z_is_inverse_of_z_to_gamma():
    z = complex(25, -30)
    gamma = z_to_gamma(z, 50.0)
    assert gamma_to_z(gamma, 50.0) == pytest.approx(z)


def test_z_to_gamma_boundary_has_unit_magnitude():
    # a purely reactive load (R=0) reflects everything: |Gamma| == 1
    gamma = z_to_gamma(complex(0, 25), 50.0)
    assert abs(gamma) == pytest.approx(1.0)


@pytest.mark.parametrize("theme", [LIGHT_THEME, DARK_THEME])
def test_draw_smith_chart_does_not_raise(theme):
    fig = Figure()
    ax = fig.add_subplot(111)
    draw_smith_chart(ax, z0=50.0, theme=theme)


def test_plot_point_and_path_do_not_raise():
    fig = Figure()
    ax = fig.add_subplot(111)
    draw_smith_chart(ax, z0=50.0)
    plot_point(ax, complex(25, -30), z0=50.0, marker="s", label="Source")
    plot_path(ax, [complex(25, -30), complex(35, -20), complex(50, 0)], z0=50.0)
    plot_sweep_points(ax, [complex(25, -30), complex(30, -10)], z0=50.0)


def test_draw_vswr_sweep_clips_infinite_values():
    fig = Figure()
    ax = fig.add_subplot(111)
    freqs_hz = [10e6, 14.2e6, 20e6]
    vswr_values = [1.5, float("inf"), 3.0]
    draw_vswr_sweep(ax, freqs_hz, vswr_values, vswr_ceiling=10.0)
    line = ax.lines[-1]
    assert max(line.get_ydata()) <= 10.0
