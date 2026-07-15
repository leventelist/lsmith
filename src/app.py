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

import dataclasses
import json
import math
import os

from engine import MatchingNetwork, Topology, Kind  # noqa: E402
from chart import (  # noqa: E402
    ChartTheme, LIGHT_THEME, DARK_THEME, draw_smith_chart, plot_point, plot_path, gamma_to_z,
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


class SmithMatchWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Smith Chart Matching Tool")
        self.set_default_size(1100, 700)

        self.network = MatchingNetwork(z_source=complex(25, -30), z0=50.0, freq_hz=14.2e6)
        self.rows: list[ElementRow] = []
        self.current_path: str | None = None
        self.theme, self.theme_preset = _load_view_config()

        self._setup_actions()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(outer)
        outer.append(Gtk.PopoverMenuBar.new_from_model(self._build_menu_model()))

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.set_margin_top(8)
        root.set_margin_bottom(8)
        root.set_margin_start(8)
        root.set_margin_end(8)
        outer.append(root)

        # ---- left: controls ----
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        left.set_size_request(420, -1)
        root.append(left)

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

        # ---- right: Smith chart ----
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.set_hexpand(True)
        root.append(right)

        self.figure = Figure(figsize=(6, 6), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.mpl_connect("motion_notify_event", self._on_pointer_move)
        right.append(self.canvas)

        self.pointer_label = Gtk.Label(xalign=0)
        self.pointer_label.add_css_class("dim-label")
        right.append(self.pointer_label)

        self._add_row()  # start with one empty element row
        self._recompute()

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

        menu.append_submenu("View", view_menu)
        return menu

    def _setup_actions(self) -> None:
        for name, callback, accels in (
            ("open", self._do_open, ["<Control>o"]),
            ("save", self._do_save, ["<Control>s"]),
            ("save-as", self._do_save_as, ["<Control><Shift>s"]),
            ("export-png", self._do_export_png, ["<Control>e"]),
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

    # -- serialization --
    def _network_to_dict(self) -> dict:
        return {
            "z0": self.z0_entry.get_value(),
            "freq_mhz": self.freq_entry.get_value(),
            "source_r": self.r_src_entry.get_value(),
            "source_x": self.x_src_entry.get_value(),
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
