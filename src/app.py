"""
GTK4 Smith chart matching tool.

Lets you enter a source impedance, then build a matching network by
adding series/shunt R/L/C elements one at a time. Each element's arc
across the Smith chart is drawn as it's inserted, and the running
Z / Gamma / VSWR / return loss are shown.

Run with:  python3 app.py

"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Gio, Gdk  # noqa: E402

from matplotlib.backends.backend_gtk4agg import FigureCanvasGTK4Agg as FigureCanvas  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

import csv
import dataclasses
import json
import math
import os

from engine import MatchingNetwork, Topology, Kind, sweep  # noqa: E402
from chart import (  # noqa: E402
    ChartTheme, LIGHT_THEME, DARK_THEME, draw_smith_chart, draw_vswr_sweep,
    plot_point, plot_path, plot_sweep_points, gamma_to_z,
)

CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "lsmith")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Unit choices offered per component kind: (label, multiplier to SI base
# unit -- ohms for R, henries for L, farads for C). The engine only ever
# sees the SI value; the multiplier just converts what's typed in the box.
UNIT_OPTIONS: dict[Kind, list[tuple[str, float]]] = {
    Kind.R: [("\u03a9", 1.0), ("k\u03a9", 1e3), ("M\u03a9", 1e6)],
    Kind.L: [("mH", 1e-3), ("\u00b5H", 1e-6), ("nH", 1e-9), ("pH", 1e-12)],
    Kind.C: [("mF", 1e-3), ("\u00b5F", 1e-6), ("nF", 1e-9), ("pF", 1e-12)],
}

# Default unit index selected when a row's kind changes, matching the
# previous fixed units (ohms, nH, pF).
DEFAULT_UNIT_INDEX: dict[Kind, int] = {Kind.R: 0, Kind.L: 2, Kind.C: 3}

COLORS = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf"]


class ElementRow(Gtk.Box):
    """One row in the element list: topology + kind + value + remove button."""

    def __init__(self, on_change, on_remove):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.on_change = on_change

        self.topo_combo = Gtk.DropDown.new_from_strings(["series", "shunt"])
        self.kind_combo = Gtk.DropDown.new_from_strings(["R", "L", "C"])
        self.value_entry = Gtk.SpinButton()
        self.value_entry.set_range(0.0, 1_000_000.0)
        self.value_entry.set_increments(1, 10)
        self.value_entry.set_digits(3)
        self.value_entry.set_value(10.0)
        self.value_entry.set_width_chars(10)
        self.unit_combo = Gtk.DropDown()

        remove_btn = Gtk.Button(icon_name="list-remove-symbolic")
        remove_btn.connect("clicked", lambda *_: on_remove(self))

        self.kind_combo.connect("notify::selected", self._update_unit)
        self.topo_combo.connect("notify::selected", lambda *_: self.on_change())
        self.kind_combo.connect("notify::selected", lambda *_: self.on_change())
        self.value_entry.connect("value-changed", lambda *_: self.on_change())
        self.unit_combo.connect("notify::selected", lambda *_: self.on_change())

        for w in (self.topo_combo, self.kind_combo, self.value_entry, self.unit_combo, remove_btn):
            self.append(w)
        self._update_unit()

    def _update_unit(self, *_):
        kind = self.get_kind()
        labels = [label for label, _ in UNIT_OPTIONS[kind]]
        self.unit_combo.set_model(Gtk.StringList.new(labels))
        self.unit_combo.set_selected(DEFAULT_UNIT_INDEX[kind])
        self.on_change()

    def get_topology(self) -> Topology:
        return Topology.SERIES if self.topo_combo.get_selected() == 0 else Topology.SHUNT

    def get_kind(self) -> Kind:
        return [Kind.R, Kind.L, Kind.C][self.kind_combo.get_selected()]

    def get_unit_label(self) -> str:
        options = UNIT_OPTIONS[self.get_kind()]
        return options[self.unit_combo.get_selected()][0]

    def set_unit_label(self, label: str) -> None:
        for i, (lbl, _) in enumerate(UNIT_OPTIONS[self.get_kind()]):
            if lbl == label:
                self.unit_combo.set_selected(i)
                return

    def get_value_si(self) -> float:
        _, scale = UNIT_OPTIONS[self.get_kind()][self.unit_combo.get_selected()]
        return self.value_entry.get_value() * scale


class SweepWindow(Gtk.Window):
    """Standalone window holding the VSWR-vs-frequency sweep plot and its
    results table, so both can be moved/resized independently of the main
    Smith chart window."""

    def __init__(self, transient_for: Gtk.Window):
        super().__init__(title="Frequency Sweep", transient_for=transient_for)
        self.set_default_size(900, 400)
        self.connect("close-request", self._on_close_request)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_wide_handle(True)
        self.set_child(paned)

        self.figure = Figure(figsize=(6, 3), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.set_size_request(100, 60)
        paned.set_start_child(self.canvas)
        paned.set_resize_start_child(True)
        paned.set_shrink_start_child(False)

        self.table = Gtk.Grid(row_spacing=2, column_spacing=12)
        table_scroller = Gtk.ScrolledWindow()
        table_scroller.set_min_content_width(200)
        table_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        table_scroller.set_child(self.table)
        paned.set_end_child(table_scroller)
        paned.set_resize_end_child(True)
        paned.set_shrink_end_child(True)
        paned.set_position(550)

    def _on_close_request(self, *_) -> bool:
        # hide rather than destroy: _recompute() keeps redrawing this
        # figure whenever the main window's inputs change, so the canvas
        # needs to stay alive even while this window isn't shown.
        self.set_visible(False)
        return True


class SmithMatchWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Smith Chart Matching Tool")
        self.set_default_size(1100, 820)

        self.network = MatchingNetwork(z_source=complex(25, -30), z0=50.0, freq_hz=14.2e6)
        self.rows: list[ElementRow] = []
        self.current_path: str | None = None
        self.theme, self.theme_preset = _load_view_config()
        self.sweep_results: list[tuple[float, complex, float, float]] = []
        self.show_sweep_on_chart = False

        self._setup_actions()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(outer)
        outer.append(Gtk.PopoverMenuBar.new_from_model(self._build_menu_model()))

        main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        main_paned.set_margin_top(8)
        main_paned.set_margin_bottom(8)
        main_paned.set_margin_start(8)
        main_paned.set_margin_end(8)
        main_paned.set_hexpand(True)
        main_paned.set_vexpand(True)
        main_paned.set_wide_handle(True)
        outer.append(main_paned)

        # ---- left: controls ----
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        left.set_size_request(300, -1)
        left_scroller = Gtk.ScrolledWindow()
        left_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left_scroller.set_child(left)
        main_paned.set_start_child(left_scroller)
        main_paned.set_resize_start_child(True)
        main_paned.set_shrink_start_child(False)
        main_paned.set_position(420)

        left.append(Gtk.Label(label="<b>Source / system</b>", use_markup=True, xalign=0))
        grid = Gtk.Grid(row_spacing=4, column_spacing=6)
        left.append(grid)

        self.z0_entry = Gtk.SpinButton()
        self.z0_entry.set_range(1, 1000)
        self.z0_entry.set_increments(1, 10)
        self.z0_entry.set_value(50.0)
        self.z0_entry.set_width_chars(12)
        self.z0_entry.connect("value-changed", self._recompute)

        self.freq_entry = Gtk.SpinButton()
        self.freq_entry.set_range(0.001, 1e5)
        self.freq_entry.set_increments(0.1, 1)
        self.freq_entry.set_digits(3)
        self.freq_entry.set_value(14.2)
        self.freq_entry.set_width_chars(12)
        self.freq_entry.connect("value-changed", self._recompute)

        self.r_src_entry = Gtk.SpinButton()
        self.r_src_entry.set_range(0, 100000)
        self.r_src_entry.set_increments(1, 10)
        self.r_src_entry.set_value(25.0)
        self.r_src_entry.set_width_chars(12)
        self.r_src_entry.connect("value-changed", self._recompute)

        self.x_src_entry = Gtk.SpinButton()
        self.x_src_entry.set_range(-100000, 100000)
        self.x_src_entry.set_increments(1, 10)
        self.x_src_entry.set_value(-30.0)
        self.x_src_entry.set_width_chars(12)
        self.x_src_entry.connect("value-changed", self._recompute)

        grid.attach(Gtk.Label(label="Z0 (\u03a9)", xalign=0), 0, 0, 1, 1)
        grid.attach(self.z0_entry, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Freq (MHz)", xalign=0), 0, 1, 1, 1)
        grid.attach(self.freq_entry, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Source R (\u03a9)", xalign=0), 0, 2, 1, 1)
        grid.attach(self.r_src_entry, 1, 2, 1, 1)
        grid.attach(Gtk.Label(label="Source X (\u03a9)", xalign=0), 0, 3, 1, 1)
        grid.attach(self.x_src_entry, 1, 3, 1, 1)

        left.append(Gtk.Separator())
        left.append(Gtk.Label(label="<b>Matching elements</b>", use_markup=True, xalign=0))

        self.elements_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        left.append(self.elements_box)

        add_btn = Gtk.Button(label="+ Add element")
        add_btn.connect("clicked", self._add_row)
        left.append(add_btn)

        left.append(Gtk.Separator())
        left.append(Gtk.Label(label="<b>Result</b>", use_markup=True, xalign=0))
        self.result_label = Gtk.Label(xalign=0, wrap=True)
        left.append(self.result_label)

        left.append(Gtk.Separator())
        left.append(Gtk.Label(label="<b>Frequency sweep</b>", use_markup=True, xalign=0))
        sweep_grid = Gtk.Grid(row_spacing=4, column_spacing=6)
        left.append(sweep_grid)

        self.sweep_start_entry = Gtk.SpinButton()
        self.sweep_start_entry.set_range(0.001, 1e5)
        self.sweep_start_entry.set_increments(0.1, 1)
        self.sweep_start_entry.set_digits(3)
        self.sweep_start_entry.set_value(10.0)
        self.sweep_start_entry.set_width_chars(12)
        self.sweep_start_entry.connect("value-changed", self._recompute)

        self.sweep_stop_entry = Gtk.SpinButton()
        self.sweep_stop_entry.set_range(0.001, 1e5)
        self.sweep_stop_entry.set_increments(0.1, 1)
        self.sweep_stop_entry.set_digits(3)
        self.sweep_stop_entry.set_value(20.0)
        self.sweep_stop_entry.set_width_chars(12)
        self.sweep_stop_entry.connect("value-changed", self._recompute)

        self.sweep_steps_entry = Gtk.SpinButton()
        self.sweep_steps_entry.set_range(2, 101)
        self.sweep_steps_entry.set_increments(1, 10)
        self.sweep_steps_entry.set_value(21)
        self.sweep_steps_entry.set_width_chars(12)
        self.sweep_steps_entry.connect("value-changed", self._recompute)

        sweep_grid.attach(Gtk.Label(label="Start (MHz)", xalign=0), 0, 0, 1, 1)
        sweep_grid.attach(self.sweep_start_entry, 1, 0, 1, 1)
        sweep_grid.attach(Gtk.Label(label="Stop (MHz)", xalign=0), 0, 1, 1, 1)
        sweep_grid.attach(self.sweep_stop_entry, 1, 1, 1, 1)
        sweep_grid.attach(Gtk.Label(label="Steps", xalign=0), 0, 2, 1, 1)
        sweep_grid.attach(self.sweep_steps_entry, 1, 2, 1, 1)

        # ---- right: main Smith chart ----
        right_top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right_top.set_hexpand(True)
        right_top.set_vexpand(True)
        main_paned.set_end_child(right_top)
        main_paned.set_resize_end_child(True)
        main_paned.set_shrink_end_child(False)

        self.figure = Figure(figsize=(6, 6), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        # a matplotlib figure with aspect="equal" (the Smith chart) raises
        # if ever redrawn at a zero/negative allocated size, so this floor
        # must hold no matter how far a divider gets dragged.
        self.canvas.set_size_request(150, 150)
        self.canvas.mpl_connect("motion_notify_event", self._on_pointer_move)
        right_top.append(self.canvas)

        self.pointer_label = Gtk.Label(xalign=0)
        self.pointer_label.add_css_class("dim-label")
        right_top.append(self.pointer_label)

        # ---- sweep plot + table: its own standalone window ----
        self.sweep_window = SweepWindow(transient_for=self)
        self.sweep_figure = self.sweep_window.figure
        self.sweep_ax = self.sweep_window.ax
        self.sweep_canvas = self.sweep_window.canvas
        self.sweep_table = self.sweep_window.table
        self.sweep_window.connect("notify::visible", self._on_sweep_window_visibility_changed)
        self.connect("close-request", self._on_main_close_request)

        self._add_row()  # start with one empty element row
        self._recompute()
        self.sweep_window.present()

    def _on_main_close_request(self, *_) -> bool:
        self.sweep_window.destroy()
        return False  # allow the main window itself to close normally

    def _on_sweep_window_visibility_changed(self, window: Gtk.Window, _pspec) -> None:
        # keeps the View menu checkbox in sync no matter how visibility
        # changed -- the menu toggle, or the sweep window's own close button
        self.lookup_action("show-sweep-window").set_state(
            GLib.Variant.new_boolean(window.get_visible())
        )

    # ---- element row management ----
    def _add_row(self, *_):
        row = ElementRow(on_change=self._recompute, on_remove=self._remove_row)
        self.rows.append(row)
        self.elements_box.append(row)
        self._recompute()

    def _remove_row(self, row: ElementRow):
        self.rows.remove(row)
        self.elements_box.remove(row)
        self._recompute()

    # ---- core recompute + redraw ----
    def _recompute(self, *_):
        z0 = self.z0_entry.get_value()
        freq_hz = self.freq_entry.get_value() * 1e6
        z_src = complex(self.r_src_entry.get_value(), self.x_src_entry.get_value())

        self.network = MatchingNetwork(z_source=z_src, z0=z0, freq_hz=freq_hz)
        for row in self.rows:
            self.network.add(row.get_topology(), row.get_kind(), row.get_value_si())

        # frequency sweep: same source Z and elements, evaluated across a
        # frequency range instead of at the single Freq (MHz) value above.
        # Computed before the chart is drawn so the swept points can
        # optionally be plotted on it below.
        sweep_start_hz = self.sweep_start_entry.get_value() * 1e6
        sweep_stop_hz = self.sweep_stop_entry.get_value() * 1e6
        sweep_steps = int(self.sweep_steps_entry.get_value())
        if sweep_steps > 1:
            step = (sweep_stop_hz - sweep_start_hz) / (sweep_steps - 1)
            freqs = [sweep_start_hz + step * i for i in range(sweep_steps)]
        else:
            freqs = [sweep_start_hz]
        sweep_results = sweep(z_source=z_src, elements=self.network.elements, freqs_hz=freqs, z0=z0)
        self.sweep_results = sweep_results

        draw_smith_chart(self.ax, z0=z0, theme=self.theme)
        plot_point(self.ax, z_src, z0=z0, color=self.theme.foreground, marker="s", label="Source")

        steps = self.network.steps()
        z = z_src
        for i, step in enumerate(steps):
            color = COLORS[i % len(COLORS)]
            plot_path(self.ax, step.path, z0=z0, color=color)
            plot_point(self.ax, step.z_after, z0=z0, color=color)
            z = step.z_after

        if steps:
            plot_point(self.ax, z, z0=z0, color="lime", marker="*", zorder=6)

        if self.show_sweep_on_chart:
            plot_sweep_points(
                self.ax, [zf for _, zf, _, _ in sweep_results], z0=z0, label="Sweep"
            )

        self.ax.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=self.theme.foreground)
        self.canvas.draw_idle()

        z_final = self.network.final_impedance()
        gamma = self.network.reflection_coefficient(z_final)
        vswr = self.network.vswr(z_final)
        rl = self.network.return_loss_db(z_final)
        vswr_str = f"{vswr:.2f}" if vswr != float("inf") else "\u221e"
        rl_str = f"{rl:.1f} dB" if rl != float("inf") else "\u221e"
        gamma_angle_deg = math.degrees(math.atan2(gamma.imag, gamma.real))
        self.result_label.set_label(
            f"Z = {z_final.real:.2f} {z_final.imag:+.2f}j \u03a9\n"
            f"|\u0393| = {abs(gamma):.3f}   \u2220\u0393 = {gamma_angle_deg:.1f}\u00b0\n"
            f"VSWR = {vswr_str}\n"
            f"Return loss = {rl_str}"
        )

        draw_vswr_sweep(
            self.sweep_ax, [f for f, _, _, _ in sweep_results], [v for _, _, v, _ in sweep_results],
            theme=self.theme,
        )
        self.sweep_canvas.draw_idle()
        self._update_sweep_table(sweep_results)

    def _update_sweep_table(self, results: list[tuple[float, complex, float, float]]) -> None:
        child = self.sweep_table.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.sweep_table.remove(child)
            child = next_child

        for col, text in enumerate(("Freq (MHz)", "VSWR", "RL (dB)")):
            self.sweep_table.attach(
                Gtk.Label(label=f"<b>{text}</b>", use_markup=True, xalign=0), col, 0, 1, 1
            )

        for row, (f, _, vswr_val, rl_val) in enumerate(results, start=1):
            vswr_str = f"{vswr_val:.2f}" if vswr_val != float("inf") else "\u221e"
            rl_str = f"{rl_val:.1f}" if rl_val != float("inf") else "\u221e"
            self.sweep_table.attach(Gtk.Label(label=f"{f / 1e6:.3f}", xalign=0), 0, row, 1, 1)
            self.sweep_table.attach(Gtk.Label(label=vswr_str, xalign=0), 1, row, 1, 1)
            self.sweep_table.attach(Gtk.Label(label=rl_str, xalign=0), 2, row, 1, 1)

    # ---- pointer readout ----
    def _on_pointer_move(self, event) -> None:
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            self.pointer_label.set_label("")
            return

        gamma = complex(event.xdata, event.ydata)
        z0 = self.z0_entry.get_value()
        if abs(1 - gamma) < 1e-9:
            self.pointer_label.set_label("Pointer: Z = \u221e (open circuit)   VSWR = \u221e")
            return

        gamma_mag = abs(gamma)
        vswr_str = "\u221e" if gamma_mag >= 1.0 else f"{(1 + gamma_mag) / (1 - gamma_mag):.2f}"

        z = gamma_to_z(gamma, z0)
        self.pointer_label.set_label(
            f"Pointer: Z = {z.real:.2f}{z.imag:+.2f}j \u03a9   VSWR = {vswr_str}"
        )

    # ---- File menu ----
    def _build_menu_model(self) -> Gio.Menu:
        menu = Gio.Menu()
        file_menu = Gio.Menu()

        io_section = Gio.Menu()
        io_section.append("Open\u2026", "win.open")
        io_section.append("Save", "win.save")
        io_section.append("Save As\u2026", "win.save-as")
        file_menu.append_section(None, io_section)

        export_section = Gio.Menu()
        export_section.append("Export to PNG\u2026", "win.export-png")
        export_section.append("Export Sweep Table to CSV\u2026", "win.export-csv")
        file_menu.append_section(None, export_section)

        quit_section = Gio.Menu()
        quit_section.append("Quit", "app.quit")
        file_menu.append_section(None, quit_section)

        menu.append_submenu("File", file_menu)

        view_menu = Gio.Menu()
        theme_section = Gio.Menu()
        for label, name in (("Light", "light"), ("Dark", "dark")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.theme", GLib.Variant.new_string(name))
            theme_section.append_item(item)
        view_menu.append_section(None, theme_section)

        custom_section = Gio.Menu()
        custom_section.append("Custom Colors…", "win.custom-colors")
        view_menu.append_section(None, custom_section)

        sweep_display_section = Gio.Menu()
        sweep_display_section.append("Show Sweep Points on Chart", "win.show-sweep-on-chart")
        sweep_display_section.append("Show Sweep Window", "win.show-sweep-window")
        view_menu.append_section(None, sweep_display_section)

        menu.append_submenu("View", view_menu)
        return menu

    def _setup_actions(self) -> None:
        for name, callback, accels in (
            ("open", self._do_open, ["<Control>o"]),
            ("save", self._do_save, ["<Control>s"]),
            ("save-as", self._do_save_as, ["<Control><Shift>s"]),
            ("export-png", self._do_export_png, ["<Control>e"]),
            ("export-csv", self._do_export_csv, ["<Control><Shift>e"]),
            ("custom-colors", self._do_custom_colors, []),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if accels:
                self.get_application().set_accels_for_action(f"win.{name}", accels)

        theme_action = Gio.SimpleAction.new_stateful(
            "theme", GLib.VariantType.new("s"), GLib.Variant.new_string(self.theme_preset)
        )
        theme_action.connect("activate", self._on_theme_action)
        self.add_action(theme_action)
        self.theme_action = theme_action

        show_sweep_action = Gio.SimpleAction.new_stateful(
            "show-sweep-on-chart", None, GLib.Variant.new_boolean(self.show_sweep_on_chart)
        )
        show_sweep_action.connect("activate", self._on_toggle_show_sweep)
        self.add_action(show_sweep_action)

        show_sweep_window_action = Gio.SimpleAction.new_stateful(
            "show-sweep-window", None, GLib.Variant.new_boolean(True)
        )
        show_sweep_window_action.connect("activate", self._on_toggle_show_sweep_window)
        self.add_action(show_sweep_window_action)

    def _show_error(self, message: str) -> None:
        dialog = Gtk.AlertDialog(message=message)
        dialog.show(self)

    # -- theme / colors --
    def _on_theme_action(self, action: Gio.SimpleAction, param: GLib.Variant) -> None:
        action.set_state(param)
        self.theme_preset = param.get_string()
        self.theme = DARK_THEME if self.theme_preset == "dark" else LIGHT_THEME
        _save_view_config(self.theme, self.theme_preset)
        self._recompute()

    def _on_toggle_show_sweep(self, action: Gio.SimpleAction, _param=None) -> None:
        new_state = not action.get_state().get_boolean()
        action.set_state(GLib.Variant.new_boolean(new_state))
        self.show_sweep_on_chart = new_state
        self._recompute()

    def _on_toggle_show_sweep_window(self, action: Gio.SimpleAction, _param=None) -> None:
        # don't set the action's state here -- the sweep window's own
        # notify::visible signal (_on_sweep_window_visibility_changed) is
        # the single source of truth, so it stays in sync no matter how
        # visibility changes (this toggle, or the window's own close button)
        if self.sweep_window.get_visible():
            self.sweep_window.set_visible(False)
        else:
            self.sweep_window.present()

    def _do_custom_colors(self, *_):
        dialog = Gtk.Window(transient_for=self, modal=True, title="Custom Colors")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        dialog.set_child(box)

        grid = Gtk.Grid(row_spacing=8, column_spacing=10)
        box.append(grid)

        bg_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        bg_button.set_rgba(_parse_rgba(self.theme.background))
        bg_button.connect(
            "notify::rgba", lambda btn, *_: self._set_theme_color("background", btn.get_rgba())
        )

        fg_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        fg_button.set_rgba(_parse_rgba(self.theme.foreground))
        fg_button.connect(
            "notify::rgba", lambda btn, *_: self._set_theme_color("foreground", btn.get_rgba())
        )

        grid.attach(Gtk.Label(label="Background", xalign=0), 0, 0, 1, 1)
        grid.attach(bg_button, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Chart color", xalign=0), 0, 1, 1, 1)
        grid.attach(fg_button, 1, 1, 1, 1)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda *_: dialog.close())
        box.append(close_btn)

        dialog.present()

    def _set_theme_color(self, field: str, rgba: Gdk.RGBA) -> None:
        self.theme = dataclasses.replace(self.theme, **{field: _rgba_to_hex(rgba)})
        self.theme_preset = "custom"
        self.theme_action.set_state(GLib.Variant.new_string("custom"))
        _save_view_config(self.theme, self.theme_preset)
        self._recompute()

    # -- open --
    def _do_open(self, *_):
        dialog = Gtk.FileDialog(title="Open network")
        dialog.set_filters(_json_filter_list())
        dialog.open(self, None, self._on_open_response)

    def _on_open_response(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error as e:
            if not e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                self._show_error(f"Could not open file:\n{e.message}")
            return
        self._load_from_path(gfile.get_path())

    # -- save / save as --
    def _do_save(self, *_):
        if self.current_path:
            self._save_to_path(self.current_path)
        else:
            self._do_save_as()

    def _do_save_as(self, *_):
        dialog = Gtk.FileDialog(title="Save network", initial_name="network.json")
        dialog.set_filters(_json_filter_list())
        dialog.save(self, None, self._on_save_response)

    def _on_save_response(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error as e:
            if not e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                self._show_error(f"Could not save file:\n{e.message}")
            return
        path = gfile.get_path()
        if not path.endswith(".json"):
            path += ".json"
        self._save_to_path(path)

    # -- export to PNG --
    def _do_export_png(self, *_):
        dialog = Gtk.FileDialog(title="Export to PNG", initial_name="smith_chart.png")
        filt = Gtk.FileFilter(name="PNG image (*.png)")
        filt.add_pattern("*.png")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.save(self, None, self._on_export_response)

    def _on_export_response(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error as e:
            if not e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                self._show_error(f"Could not export PNG:\n{e.message}")
            return
        path = gfile.get_path()
        if not path.endswith(".png"):
            path += ".png"
        try:
            self.figure.savefig(path, dpi=150)
        except OSError as e:
            self._show_error(f"Could not export PNG:\n{e}")

    # -- export sweep table to CSV --
    def _do_export_csv(self, *_):
        dialog = Gtk.FileDialog(title="Export Sweep Table to CSV", initial_name="sweep.csv")
        filt = Gtk.FileFilter(name="CSV file (*.csv)")
        filt.add_pattern("*.csv")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.save(self, None, self._on_export_csv_response)

    def _on_export_csv_response(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error as e:
            if not e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                self._show_error(f"Could not export CSV:\n{e.message}")
            return
        path = gfile.get_path()
        if not path.endswith(".csv"):
            path += ".csv"
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Freq (MHz)", "VSWR", "Return Loss (dB)"])
                for freq_hz, _, vswr_val, rl_val in self.sweep_results:
                    vswr_str = f"{vswr_val:.4f}" if vswr_val != float("inf") else "inf"
                    rl_str = f"{rl_val:.4f}" if rl_val != float("inf") else "inf"
                    writer.writerow([f"{freq_hz / 1e6:.6f}", vswr_str, rl_str])
        except OSError as e:
            self._show_error(f"Could not export CSV:\n{e}")

    # -- serialization --
    def _network_to_dict(self) -> dict:
        return {
            "z0": self.z0_entry.get_value(),
            "freq_mhz": self.freq_entry.get_value(),
            "source_r": self.r_src_entry.get_value(),
            "source_x": self.x_src_entry.get_value(),
            "sweep_start_mhz": self.sweep_start_entry.get_value(),
            "sweep_stop_mhz": self.sweep_stop_entry.get_value(),
            "sweep_steps": int(self.sweep_steps_entry.get_value()),
            "elements": [
                {
                    "topology": row.get_topology().value,
                    "kind": row.get_kind().value,
                    "value": row.value_entry.get_value(),
                    "unit": row.get_unit_label(),
                }
                for row in self.rows
            ],
        }

    def _save_to_path(self, path: str) -> None:
        try:
            with open(path, "w") as f:
                json.dump(self._network_to_dict(), f, indent=2)
        except OSError as e:
            self._show_error(f"Could not save file:\n{e}")
            return
        self.current_path = path

    def _load_from_path(self, path: str) -> None:
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self._show_error(f"Could not open file:\n{e}")
            return

        self.z0_entry.set_value(data.get("z0", 50.0))
        self.freq_entry.set_value(data.get("freq_mhz", 14.2))
        self.r_src_entry.set_value(data.get("source_r", 25.0))
        self.x_src_entry.set_value(data.get("source_x", -30.0))
        self.sweep_start_entry.set_value(data.get("sweep_start_mhz", 10.0))
        self.sweep_stop_entry.set_value(data.get("sweep_stop_mhz", 20.0))
        self.sweep_steps_entry.set_value(data.get("sweep_steps", 21))

        for row in list(self.rows):
            self.elements_box.remove(row)
        self.rows.clear()

        kinds = [k.value for k in Kind]
        for el in data.get("elements", []):
            row = ElementRow(on_change=self._recompute, on_remove=self._remove_row)
            self.rows.append(row)
            self.elements_box.append(row)
            row.topo_combo.set_selected(0 if el.get("topology") == "series" else 1)
            row.kind_combo.set_selected(kinds.index(el.get("kind", "R")))
            row.value_entry.set_value(el.get("value", 0.0))
            if "unit" in el:
                row.set_unit_label(el["unit"])

        if not self.rows:
            self._add_row()

        self.current_path = path
        self._recompute()


def _load_view_config() -> tuple[ChartTheme, str]:
    """Read the persisted view (theme) settings, falling back to the light theme
    if the config file is missing, unreadable, or from a future/incompatible format."""
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return LIGHT_THEME, "light"

    preset = data.get("theme_preset", "light")
    if preset == "dark":
        return DARK_THEME, "dark"
    if preset == "custom":
        theme = ChartTheme(
            background=data.get("background", LIGHT_THEME.background),
            foreground=data.get("foreground", LIGHT_THEME.foreground),
        )
        return theme, "custom"
    return LIGHT_THEME, "light"


def _save_view_config(theme: ChartTheme, preset: str) -> None:
    data = {
        "theme_preset": preset,
        "background": theme.background,
        "foreground": theme.foreground,
    }
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass  # persisting the theme is best-effort; failure shouldn't break the app


def _parse_rgba(color: str) -> Gdk.RGBA:
    rgba = Gdk.RGBA()
    rgba.parse(color)
    return rgba


def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)
    )


def _json_filter_list() -> Gio.ListStore:
    filt = Gtk.FileFilter(name="Smith match network (*.json)")
    filt.add_pattern("*.json")
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(filt)
    return filters


class SmithMatchApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.example.smithmatch")

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self):
        win = self.props.active_window or SmithMatchWindow(self)
        win.present()


def main():
    app = SmithMatchApp()
    app.run()


if __name__ == "__main__":
    main()
