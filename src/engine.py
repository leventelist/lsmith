"""
Core impedance-matching engine.

Pure Python, no GUI or plotting dependencies, so it can be unit tested
or reused from a CLI/script independently of the GTK4 front end.
"""
from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field
from enum import Enum


class Topology(Enum):
    SERIES = "series"
    SHUNT = "shunt"


class Kind(Enum):
    R = "R"
    L = "L"
    C = "C"


@dataclass
class Element:
    topology: Topology
    kind: Kind
    value: float  # ohms for R, henries for L, farads for C
    label: str = ""

    def impedance(self, freq_hz: float) -> complex:
        """Impedance this element presents at the given frequency."""
        if self.kind is Kind.R:
            return complex(self.value, 0.0)
        w = 2.0 * math.pi * freq_hz
        if self.kind is Kind.L:
            return complex(0.0, w * self.value)
        if self.kind is Kind.C:
            if self.value == 0:
                return complex(0.0, float("inf"))
            return complex(0.0, -1.0 / (w * self.value))
        raise ValueError(f"unknown kind {self.kind}")

    def describe(self) -> str:
        unit = {"R": "\u03a9", "L": "H", "C": "F"}[self.kind.value]
        return f"{self.topology.value} {self.kind.value} {self.value:g}{unit}"




def _is_inf(c: complex) -> bool:
    return cmath.isinf(c.real) or cmath.isinf(c.imag)


def apply_element(z: complex, element: Element, freq_hz: float, alpha: float = 1.0) -> complex:
    ze_full = element.impedance(freq_hz)

    if element.topology is Topology.SERIES:
        if _is_inf(ze_full):
            ze = ze_full if alpha >= 1.0 else complex(0.0, 0.0)
        else:
            ze = complex(ze_full.real * alpha, ze_full.imag * alpha)
        return z + ze

    # shunt: interpolate in the admittance domain so alpha=0 means
    # "no element yet" (zero admittance), not a short circuit.
    if ze_full == 0:
        y_e_full = complex(float("inf"), 0.0)
    elif _is_inf(ze_full):
        y_e_full = complex(0.0, 0.0)
    else:
        y_e_full = 1.0 / ze_full

    if _is_inf(y_e_full):
        y_e = y_e_full if alpha >= 1.0 else complex(0.0, 0.0)
    else:
        y_e = complex(y_e_full.real * alpha, y_e_full.imag * alpha)

    y_z = complex(float("inf"), 0.0) if z == 0 else 1.0 / z
    y_total = y_z + y_e
    if y_total == 0:
        return complex(float("inf"), 0.0)
    return 1.0 / y_total



@dataclass
class MatchStep:
    element: Element
    z_before: complex
    z_after: complex
    path: list[complex] = field(default_factory=list)  # intermediate points, alpha 0..1


def _adaptive_arc(z_before: complex, element: Element, freq_hz: float, z0: float,
                   tol: float = 0.003, max_depth: int = 16) -> list[complex]:
    """
    Sample the Z-path of `element` as alpha goes 0..1, subdividing wherever
    its image in Gamma space bends too much between two samples.

    Uniform steps in alpha are not uniform in Gamma: for elements whose
    reactance/susceptance is small relative to Z0, almost the entire Gamma
    excursion happens over a tiny slice of alpha (near a near-singular
    admittance/impedance combination), so a fixed step count leaves that
    slice under-sampled -- it renders as a straight chord instead of a
    smooth arc. Subdividing based on actual curvature in Gamma space fixes
    this regardless of how extreme the element's value is.
    """

    def gamma_at(alpha: float) -> complex:
        z = apply_element(z_before, element, freq_hz, alpha)
        return (z - z0) / (z + z0)

    out: list[complex] = [z_before]

    def recurse(a0: float, a1: float, g0: complex, g1: complex, depth: int) -> None:
        am = (a0 + a1) / 2
        gm = gamma_at(am)
        if depth < max_depth and abs(gm - (g0 + g1) / 2) > tol:
            recurse(a0, am, g0, gm, depth + 1)
            recurse(am, a1, gm, g1, depth + 1)
        else:
            out.append(apply_element(z_before, element, freq_hz, a1))

    recurse(0.0, 1.0, gamma_at(0.0), gamma_at(1.0), 0)
    return out


class MatchingNetwork:
    def __init__(self, z_source: complex, z0: float = 50.0, freq_hz: float = 14.2e6):
        self.z_source = z_source
        self.z0 = z0
        self.freq_hz = freq_hz
        self.elements: list[Element] = []

    def add(self, topology: Topology, kind: Kind, value: float, label: str = "") -> None:
        self.elements.append(Element(topology, kind, value, label))

    def insert(self, index: int, topology: Topology, kind: Kind, value: float, label: str = "") -> None:
        self.elements.insert(index, Element(topology, kind, value, label))

    def remove(self, index: int) -> None:
        del self.elements[index]

    def move(self, index: int, new_index: int) -> None:
        e = self.elements.pop(index)
        self.elements.insert(new_index, e)

    def steps(self) -> list[MatchStep]:
        z = self.z_source
        out: list[MatchStep] = []
        for e in self.elements:
            path = _adaptive_arc(z, e, self.freq_hz, self.z0)
            z_new = path[-1]
            out.append(MatchStep(element=e, z_before=z, z_after=z_new, path=path))
            z = z_new
        return out

    def final_impedance(self) -> complex:
        z = self.z_source
        for e in self.elements:
            z = apply_element(z, e, self.freq_hz, 1.0)
        return z

    def reflection_coefficient(self, z: complex | None = None) -> complex:
        z = z if z is not None else self.final_impedance()
        return (z - self.z0) / (z + self.z0)

    def vswr(self, z: complex | None = None) -> float:
        gamma = abs(self.reflection_coefficient(z))
        if gamma >= 1.0:
            return float("inf")
        return (1 + gamma) / (1 - gamma)

    def return_loss_db(self, z: complex | None = None) -> float:
        gamma = abs(self.reflection_coefficient(z))
        if gamma <= 0:
            return float("inf")
        return -20.0 * math.log10(gamma)


def sweep(
    z_source: complex, elements: list[Element], freqs_hz: list[float], z0: float = 50.0
) -> list[tuple[float, complex, float, float]]:
    """
    Evaluate the same source impedance and element chain (values fixed in
    ohms/H/F) at each frequency in freqs_hz, returning a list of
    (freq_hz, z_final, VSWR, return_loss_db) -- useful for checking a
    matching network's bandwidth (and for plotting the swept impedance
    points) rather than just its response at one frequency.
    """
    results = []
    for f in freqs_hz:
        net = MatchingNetwork(z_source=z_source, z0=z0, freq_hz=f)
        net.elements = elements
        z_final = net.final_impedance()
        results.append((f, z_final, net.vswr(z_final), net.return_loss_db(z_final)))
    return results


@dataclass(frozen=True)
class LMatchSolution:
    label: str
    elements: list[Element]


def _reactance_element(topology: Topology, freq_hz: float, x_ohms: float) -> Element:
    """A series/shunt element presenting reactance x_ohms at freq_hz (an
    inductor for positive X, a capacitor for negative X)."""
    w = 2.0 * math.pi * freq_hz
    if x_ohms > 0:
        return Element(topology, Kind.L, x_ohms / w)
    return Element(topology, Kind.C, -1.0 / (w * x_ohms))


def _susceptance_element(topology: Topology, freq_hz: float, b_siemens: float) -> Element:
    """A shunt element presenting susceptance b_siemens at freq_hz (a
    capacitor for positive B, an inductor for negative B)."""
    w = 2.0 * math.pi * freq_hz
    if b_siemens > 0:
        return Element(topology, Kind.C, b_siemens / w)
    return Element(topology, Kind.L, -1.0 / (w * b_siemens))


def solve_l_match(z_load: complex, z0: float, freq_hz: float) -> list[LMatchSolution]:
    """
    Find L-network solutions (at most two reactive elements) that transform
    z_load exactly to z0, evaluated at freq_hz. z_load is the impedance
    looking into the network from the source side (e.g. an antenna
    feedpoint measurement) and must have positive resistance.

    Returns:
      - [] if z_load is already matched (within numerical tolerance).
      - one 1-element solution if R already equals Z0 but X != 0 (just
        cancel the reactance with a single series element).
      - otherwise up to two 2-element solutions (both exact), since the
        classic L-match derivation has a +/- choice at each of the two
        valid topologies (series-then-shunt when R < Z0, shunt-then-series
        when R > Z0) -- exactly one topology is valid for any given load.

    Elements are ordered from the load outward, i.e. the same order
    MatchingNetwork.add() expects to reach z0.
    """
    r_load, x_load = z_load.real, z_load.imag
    if r_load <= 0:
        raise ValueError("z_load must have positive resistance to be L-matched")

    if abs(r_load - z0) < 1e-9 and abs(x_load) < 1e-9:
        return []

    if abs(r_load - z0) < 1e-9:
        elem = _reactance_element(Topology.SERIES, freq_hz, -x_load)
        return [LMatchSolution(f"Series {elem.kind.value} only", [elem])]

    solutions = []

    if r_load < z0:
        # series reactance first (from the load), then a shunt element to
        # cancel the remaining susceptance and land exactly on z0
        q = math.sqrt(r_load * (z0 - r_load))
        for sign in (1.0, -1.0):
            x_total = sign * q  # reactance at the load node after the series element
            xs = x_total - x_load
            b_after_series = -x_total / (r_load * z0)
            bp = -b_after_series
            elem1 = _reactance_element(Topology.SERIES, freq_hz, xs)
            elem2 = _susceptance_element(Topology.SHUNT, freq_hz, bp)
            label = f"Series {elem1.kind.value} → Shunt {elem2.kind.value}"
            solutions.append(LMatchSolution(label, [elem1, elem2]))
    else:
        # shunt susceptance first (from the load), then a series element to
        # cancel the remaining reactance and land exactly on z0
        g_load = r_load / (r_load ** 2 + x_load ** 2)
        b_load = -x_load / (r_load ** 2 + x_load ** 2)
        q = math.sqrt(g_load * (1.0 / z0 - g_load))
        for sign in (1.0, -1.0):
            b_total = sign * q  # susceptance at the load node after the shunt element
            bs = b_total - b_load
            x_after_shunt = -b_total * z0 / g_load
            xs2 = -x_after_shunt
            elem1 = _susceptance_element(Topology.SHUNT, freq_hz, bs)
            elem2 = _reactance_element(Topology.SERIES, freq_hz, xs2)
            label = f"Shunt {elem1.kind.value} → Series {elem2.kind.value}"
            solutions.append(LMatchSolution(label, [elem1, elem2]))

    return solutions
