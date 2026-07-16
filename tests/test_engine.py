import math
import os

import pytest

from engine import (
    Element, Kind, Topology, MatchingNetwork,
    sweep, interpolate_impedance, load_impedance_csv, load_touchstone_1port,
    solve_l_match,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SAMPLE_CSV = os.path.join(DATA_DIR, "sample_antenna_sweep.csv")
SAMPLE_S1P = os.path.join(DATA_DIR, "sample_antenna_sweep.s1p")


# ---- Element ----

def test_element_impedance_resistor():
    assert Element(Topology.SERIES, Kind.R, 50.0).impedance(14.2e6) == complex(50.0, 0.0)


def test_element_impedance_inductor():
    freq_hz = 14.2e6
    inductance_h = 617e-9
    z = Element(Topology.SERIES, Kind.L, inductance_h).impedance(freq_hz)
    assert z.real == 0.0
    assert z.imag == pytest.approx(2 * math.pi * freq_hz * inductance_h)


def test_element_impedance_capacitor():
    freq_hz = 14.2e6
    capacitance_f = 224e-12
    z = Element(Topology.SERIES, Kind.C, capacitance_f).impedance(freq_hz)
    assert z.real == 0.0
    assert z.imag == pytest.approx(-1.0 / (2 * math.pi * freq_hz * capacitance_f))


# ---- MatchingNetwork ----

def test_series_element_adds_impedance():
    net = MatchingNetwork(z_source=complex(25, -30), z0=50.0, freq_hz=14.2e6)
    net.add(Topology.SERIES, Kind.R, 10.0)
    assert net.final_impedance() == complex(35, -30)


def test_matched_network_has_unity_vswr():
    net = MatchingNetwork(z_source=complex(50, 0), z0=50.0)
    assert net.vswr() == pytest.approx(1.0)
    assert net.return_loss_db() == float("inf")


def test_mismatched_network_vswr_matches_hand_formula():
    net = MatchingNetwork(z_source=complex(25, -30), z0=50.0)
    gamma = net.reflection_coefficient()
    expected_vswr = (1 + abs(gamma)) / (1 - abs(gamma))
    assert net.vswr() == pytest.approx(expected_vswr)


def test_steps_path_endpoints_match_before_after():
    net = MatchingNetwork(z_source=complex(25, -30), z0=50.0, freq_hz=14.2e6)
    net.add(Topology.SERIES, Kind.L, 617e-9)
    net.add(Topology.SHUNT, Kind.C, 224e-12)
    steps = net.steps()
    assert len(steps) == 2
    assert steps[0].z_before == complex(25, -30)
    assert steps[0].path[0] == steps[0].z_before
    assert steps[0].path[-1] == steps[0].z_after
    assert steps[1].z_before == steps[0].z_after
    assert steps[1].z_after == pytest.approx(net.final_impedance())


# ---- sweep() ----

def test_sweep_matches_per_frequency_matching_network():
    z_source = complex(25, -30)
    elements = [Element(Topology.SERIES, Kind.L, 617e-9), Element(Topology.SHUNT, Kind.C, 224e-12)]
    freqs_hz = [10e6, 14.2e6, 20e6]
    results = sweep(z_source=z_source, elements=elements, freqs_hz=freqs_hz, z0=50.0)

    assert [f for f, _, _, _ in results] == freqs_hz
    for f, z_final, vswr, rl in results:
        net = MatchingNetwork(z_source=z_source, z0=50.0, freq_hz=f)
        net.elements = elements
        expected_z = net.final_impedance()
        assert z_final == pytest.approx(expected_z)
        assert vswr == pytest.approx(net.vswr(expected_z))
        assert rl == pytest.approx(net.return_loss_db(expected_z))


def test_sweep_accepts_callable_source_for_frequency_varying_impedance():
    elements = [Element(Topology.SERIES, Kind.R, 0.0)]  # no-op element
    data = [(10e6, complex(20, -10)), (20e6, complex(40, 10))]
    results = sweep(
        z_source=lambda f: interpolate_impedance(data, f),
        elements=elements, freqs_hz=[10e6, 15e6, 20e6], z0=50.0,
    )
    z_values = [z for _, z, _, _ in results]
    assert z_values[0] == pytest.approx(complex(20, -10))
    assert z_values[1] == pytest.approx(complex(30, 0))
    assert z_values[2] == pytest.approx(complex(40, 10))


# ---- interpolate_impedance() ----

def test_interpolate_impedance_exact_match():
    data = [(10e6, complex(20, -40)), (14.2e6, complex(25, -30)), (20e6, complex(35, 10))]
    assert interpolate_impedance(data, 14.2e6) == complex(25, -30)


def test_interpolate_impedance_midpoint():
    data = [(10e6, complex(20, -40)), (20e6, complex(40, 0))]
    assert interpolate_impedance(data, 15e6) == pytest.approx(complex(30, -20))


def test_interpolate_impedance_clamps_outside_range():
    data = [(10e6, complex(20, -40)), (20e6, complex(40, 0))]
    assert interpolate_impedance(data, 1e6) == complex(20, -40)
    assert interpolate_impedance(data, 100e6) == complex(40, 0)


# ---- load_impedance_csv() ----

def test_load_impedance_csv_reads_all_rows_and_skips_header():
    data = load_impedance_csv(SAMPLE_CSV)
    assert len(data) == 13
    assert data == sorted(data, key=lambda item: item[0])
    freq_hz, z = data[5]
    assert freq_hz == pytest.approx(14.2e6)
    assert z == pytest.approx(complex(34.8, -14.2))


def test_load_impedance_csv_rejects_file_with_no_numeric_rows(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("not,numeric,data\nalso,not,numeric\n")
    with pytest.raises(ValueError):
        load_impedance_csv(str(bad_csv))


# ---- load_touchstone_1port() ----

def test_load_touchstone_matches_csv_for_the_same_antenna():
    csv_data = load_impedance_csv(SAMPLE_CSV)
    s1p_data = load_touchstone_1port(SAMPLE_S1P)
    assert len(csv_data) == len(s1p_data)
    for (f_csv, z_csv), (f_s1p, z_s1p) in zip(csv_data, s1p_data):
        assert f_s1p == pytest.approx(f_csv)
        assert z_s1p.real == pytest.approx(z_csv.real, abs=0.05)
        assert z_s1p.imag == pytest.approx(z_csv.imag, abs=0.05)


def test_load_touchstone_formats_ri_ma_db_agree(tmp_path):
    # same S11 (0.48373 mag, -108.0857 deg) expressed in all three formats
    ri = tmp_path / "ri.s1p"
    ri.write_text("# MHz S RI R 50\n14.2 -0.1494 -0.4598\n")
    ma = tmp_path / "ma.s1p"
    ma.write_text("# MHz S MA R 50\n14.2 0.48373 -108.0857\n")
    db = tmp_path / "db.s1p"
    db.write_text("# MHz S DB R 50\n14.2 -6.30627 -108.0857\n")

    z_ri = load_touchstone_1port(str(ri))[0][1]
    z_ma = load_touchstone_1port(str(ma))[0][1]
    z_db = load_touchstone_1port(str(db))[0][1]

    # loose tolerance: the MA/DB numbers above are hand-rounded to 5 sig
    # figs, so they won't match the RI reference to more than ~0.05 ohm
    assert z_ma.real == pytest.approx(z_ri.real, abs=0.05)
    assert z_ma.imag == pytest.approx(z_ri.imag, abs=0.05)
    assert z_db.real == pytest.approx(z_ri.real, abs=0.05)
    assert z_db.imag == pytest.approx(z_ri.imag, abs=0.05)


def test_load_touchstone_rejects_file_with_no_data_rows(tmp_path):
    empty_s1p = tmp_path / "empty.s1p"
    empty_s1p.write_text("! just a comment\n# MHz S RI R 50\n")
    with pytest.raises(ValueError):
        load_touchstone_1port(str(empty_s1p))


# ---- solve_l_match() ----

@pytest.mark.parametrize("z_load", [
    complex(25, -30),
    complex(100, 20),
    complex(10, 0),
    complex(200, -150),
])
def test_solve_l_match_solutions_are_exact(z_load):
    z0 = 50.0
    freq_hz = 14.2e6
    solutions = solve_l_match(z_load, z0, freq_hz)
    assert solutions, "expected at least one solution for a mismatched load"
    for solution in solutions:
        net = MatchingNetwork(z_source=z_load, z0=z0, freq_hz=freq_hz)
        for element in solution.elements:
            net.add(element.topology, element.kind, element.value)
        assert net.final_impedance() == pytest.approx(complex(z0, 0), abs=1e-6)


def test_solve_l_match_already_matched_returns_no_solutions():
    assert solve_l_match(complex(50, 0), 50.0, 14.2e6) == []


def test_solve_l_match_rejects_non_positive_resistance():
    with pytest.raises(ValueError):
        solve_l_match(complex(0, -30), 50.0, 14.2e6)
