import matplotlib
matplotlib.use("Agg")  # headless: these are smoke tests, just checking drawing doesn't raise

import pytest
from matplotlib.figure import Figure

from engine import Element, Kind, Topology
from schematic import draw_schematic
from chart import LIGHT_THEME, DARK_THEME


@pytest.mark.parametrize("theme", [LIGHT_THEME, DARK_THEME])
def test_draw_schematic_with_no_elements(theme):
    fig = Figure()
    ax = fig.add_subplot(111)
    draw_schematic(ax, complex(50, 0), [], 50.0, theme=theme)


def test_draw_schematic_with_series_and_shunt_elements():
    fig = Figure()
    ax = fig.add_subplot(111)
    elements = [
        Element(Topology.SERIES, Kind.L, 617e-9),
        Element(Topology.SHUNT, Kind.C, 224e-12),
    ]
    draw_schematic(ax, complex(25, -30), elements, 50.0, theme=LIGHT_THEME)


def test_draw_schematic_with_every_kind_and_topology():
    fig = Figure()
    ax = fig.add_subplot(111)
    elements = [
        Element(Topology.SHUNT, Kind.R, 75.0),
        Element(Topology.SERIES, Kind.C, 100e-12),
        Element(Topology.SHUNT, Kind.L, 300e-9),
        Element(Topology.SERIES, Kind.R, 10.0),
    ]
    draw_schematic(ax, complex(120, 40), elements, 50.0, theme=LIGHT_THEME)
