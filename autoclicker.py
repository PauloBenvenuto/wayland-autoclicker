#!/usr/bin/env python3
"""
autoclicker — a Wayland/Hyprland autoclicker with a live HUD.

Drives a virtual mouse through /dev/uinput (kernel input layer, so it works
under Wayland where xdotool/X11 tools don't). Clicks land at the current
cursor position.

Usage:
  autoclicker            Open the HUD (idle).
  autoclicker --toggle   Toggle clicking on/off (used by the F8 keybind).
  autoclicker --start    Start clicking.
  autoclicker --stop     Stop clicking.
  autoclicker --cps N    Set rate (clicks/sec) on launch.
  autoclicker --button {left,right,middle}
  autoclicker --selftest Verify /dev/uinput access (no GUI, no clicks).

Subsequent invocations talk to the already-running HUD over GTK's built-in
single-instance D-Bus channel, so F8 toggles without stealing focus.
"""

import argparse
import sys
import threading
import time

from evdev import UInput, ecodes as e

APP_ID = "dev.local.autoclicker"

BUTTONS = {
    "left": e.BTN_LEFT,
    "right": e.BTN_RIGHT,
    "middle": e.BTN_MIDDLE,
}
BUTTON_ORDER = ["left", "right", "middle"]

MIN_CPS = 1
MAX_CPS = 100


# --------------------------------------------------------------------------
# Click engine
# --------------------------------------------------------------------------
class Clicker:
    """Owns the virtual mouse and the background click loop."""

    def __init__(self, cps=10.0, button="left"):
        self.cps = float(cps)
        self.button = button
        self.active = False          # clicking enabled?
        self.pointer_inside = False  # cursor over the HUD -> suppress clicks
        self.ui = None
        self.error = None
        self._running = True
        self._thread = None

        try:
            # Declare buttons + relative axes so libinput treats us as a pointer.
            cap = {
                e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
                e.EV_REL: [e.REL_X, e.REL_Y],
            }
            self.ui = UInput(cap, name="autoclicker-virtual-mouse")
            time.sleep(0.3)  # let libinput pick up the new device
        except Exception as exc:  # noqa: BLE001
            self.error = exc
            return

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def interval(self):
        return 1.0 / max(self.cps, MIN_CPS)

    def _emit(self, code, value):
        self.ui.write(e.EV_KEY, code, value)
        self.ui.syn()

    def _click(self):
        code = BUTTONS.get(self.button, e.BTN_LEFT)
        interval = self.interval
        hold = min(0.008, interval * 0.4)
        self._emit(code, 1)   # press
        time.sleep(hold)
        self._emit(code, 0)   # release
        # Sleep the remainder of the interval, but stay responsive to stop/hover.
        end = time.monotonic() + (interval - hold)
        while self._running and self.active and not self.pointer_inside:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.02))

    def _loop(self):
        while self._running:
            if self.active and not self.pointer_inside:
                self._click()
            else:
                time.sleep(0.02)

    def shutdown(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self.ui:
            try:
                self.ui.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------
# Self-test (no GUI) — just confirm we can open /dev/uinput.
# --------------------------------------------------------------------------
def selftest():
    try:
        ui = UInput({e.EV_KEY: [e.BTN_LEFT], e.EV_REL: [e.REL_X, e.REL_Y]},
                    name="autoclicker-selftest")
        dev = ui.device.path if ui.device else "(unknown)"
        ui.close()
        print(f"OK: /dev/uinput writable, created virtual device at {dev}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: cannot create uinput device: {exc}", file=sys.stderr)
        print("Ensure your user can write /dev/uinput (input group / uaccess ACL).",
              file=sys.stderr)
        return 1


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
def build_app(initial_cps, initial_button):
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib, Gio, Gdk

    # GTK derives the Wayland app_id from the program name when nothing else
    # forces it. Without this, AppImage launches show up as "autoclicker.py"
    # and miss the Hyprland window rule keyed on the application id.
    GLib.set_prgname(APP_ID)

    CSS = b"""
    window { background: transparent; }
    .hud {
        background: rgba(24,24,32,0.92);
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 0;
        padding: 14px 16px;
        color: #e8e8ef;
    }
    .title { font-weight: 700; font-size: 13px; }
    .pill {
        font-size: 11px; font-weight: 700;
        padding: 2px 10px; border-radius: 999px;
        background: rgba(255,80,80,0.18); color: #ff8a8a;
    }
    .pill.on { background: rgba(80,220,120,0.20); color: #7dffa6; }
    .toggle {
        font-weight: 700; font-size: 14px;
        padding: 8px; border-radius: 10px;
    }
    .toggle:checked { background: #2ec27e; color: #06210f; }
    .hint { font-size: 10px; color: rgba(232,232,239,0.55); }
    .xbtn { padding: 0 6px; min-width: 22px; }
    """

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.win = None
            self.clicker = None
            self._syncing = False

        # --- IPC entry point: runs on the primary instance for every launch.
        def do_command_line(self, command_line):
            argv = command_line.get_arguments()
            opts = parse_args(argv[1:])
            first_launch = self.win is None

            if first_launch:
                if opts.toggle:
                    return 0  # HUD closed: F8 (--toggle) does nothing
                self._startup(opts.cps, opts.button)
                if opts.start:
                    self.set_active(True)
                self.win.present()
            else:
                if opts.toggle:
                    self.set_active(not self.clicker.active)  # no present: keep focus
                elif opts.start:
                    self.set_active(True)
                elif opts.stop:
                    self.set_active(False)
                else:
                    self.win.present()
            return 0

        def _startup(self, cps, button):
            self.clicker = Clicker(cps=cps, button=button)

            display = Gdk.Display.get_default()
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            # A normal toplevel window (not a layer-shell overlay): Hyprland
            # binds it to the workspace it opens on and lets you drag it.
            # The matching `o.window` rule in hyprland.lua floats + positions it.
            self.win = Gtk.ApplicationWindow(application=self)
            self.win.set_decorated(False)
            self.win.set_title("Autoclicker")
            self.win.set_resizable(False)
            self._build_ui()
            self._track_pointer()
            self._bind_escape()
            self._refresh_ui()

        def _build_ui(self):
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            outer.add_css_class("hud")
            self.win.set_child(outer)

            # Header: title + status pill + close.
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            title = Gtk.Label(label="🖱  Autoclicker")
            title.add_css_class("title")
            title.set_hexpand(True)
            title.set_xalign(0.0)
            self.pill = Gtk.Label(label="IDLE")
            self.pill.add_css_class("pill")
            xbtn = Gtk.Button(label="✕")
            xbtn.add_css_class("xbtn")
            xbtn.connect("clicked", lambda *_: self.quit())
            header.append(title)
            header.append(self.pill)
            header.append(xbtn)
            # Wrap the header in a WindowHandle so dragging it asks the
            # compositor to move the window (the Wayland way to move a window).
            handle = Gtk.WindowHandle()
            handle.set_child(header)
            outer.append(handle)

            # Toggle button.
            self.toggle_btn = Gtk.ToggleButton(label="Start  (F8)")
            self.toggle_btn.add_css_class("toggle")
            self.toggle_btn.connect("toggled", self._on_toggle)
            outer.append(self.toggle_btn)

            # Rate: slider + spin button sharing one adjustment.
            self.rate_label = Gtk.Label(xalign=0.0)
            outer.append(self.rate_label)

            self.adj = Gtk.Adjustment(value=self.clicker.cps,
                                      lower=MIN_CPS, upper=MAX_CPS,
                                      step_increment=1, page_increment=10)
            self.adj.connect("value-changed", self._on_rate)
            rate_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                              adjustment=self.adj)
            scale.set_draw_value(False)
            scale.set_hexpand(True)
            scale.set_size_request(180, -1)
            spin = Gtk.SpinButton(adjustment=self.adj, climb_rate=1, digits=0)
            rate_row.append(scale)
            rate_row.append(spin)
            outer.append(rate_row)

            # Button selector.
            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_row.append(Gtk.Label(label="Button:"))
            self.btn_dropdown = Gtk.DropDown.new_from_strings(
                ["Left", "Right", "Middle"])
            self.btn_dropdown.set_selected(
                BUTTON_ORDER.index(self.clicker.button))
            self.btn_dropdown.connect("notify::selected", self._on_button)
            self.btn_dropdown.set_hexpand(True)
            btn_row.append(self.btn_dropdown)
            outer.append(btn_row)

            hint = Gtk.Label(
                label="F8 toggles  ·  hover pauses  ·  drag title  ·  Esc closes")
            hint.add_css_class("hint")
            outer.append(hint)

            if self.clicker.error:
                err = Gtk.Label(
                    label=f"⚠ no /dev/uinput access: {self.clicker.error}")
                err.set_wrap(True)
                err.add_css_class("hint")
                outer.append(err)
                self.toggle_btn.set_sensitive(False)

        def _track_pointer(self):
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_: self._set_inside(True))
            motion.connect("leave", lambda *_: self._set_inside(False))
            self.win.add_controller(motion)

        def _bind_escape(self):
            keys = Gtk.EventControllerKey()

            def on_key(_ctrl, keyval, _code, _state):
                if keyval == Gdk.KEY_Escape:
                    self.quit()
                    return True
                return False

            keys.connect("key-pressed", on_key)
            self.win.add_controller(keys)

        # --- state helpers
        def _set_inside(self, inside):
            if self.clicker:
                self.clicker.pointer_inside = inside

        def set_active(self, on):
            if self.clicker.error:
                return
            self.clicker.active = on
            self._refresh_ui()

        def _on_toggle(self, btn):
            if self._syncing:
                return
            self.set_active(btn.get_active())

        def _on_rate(self, adj):
            self.clicker.cps = adj.get_value()
            self._refresh_rate_label()

        def _on_button(self, dropdown, _param):
            self.clicker.button = BUTTON_ORDER[dropdown.get_selected()]

        def _refresh_rate_label(self):
            cps = self.clicker.cps
            self.rate_label.set_text(
                f"Rate: {cps:.0f} clicks/sec  ·  {1000.0 / cps:.0f} ms")

        def _refresh_ui(self):
            on = self.clicker.active
            self._syncing = True
            self.toggle_btn.set_active(on)
            self._syncing = False
            self.toggle_btn.set_label("Stop  (F8)" if on else "Start  (F8)")
            self.pill.set_text("CLICKING" if on else "IDLE")
            if on:
                self.pill.add_css_class("on")
            else:
                self.pill.remove_css_class("on")
            self._refresh_rate_label()

        def do_shutdown(self):
            if self.clicker:
                self.clicker.shutdown()
            Gtk.Application.do_shutdown(self)

    return App()


# --------------------------------------------------------------------------
def parse_args(argv):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--toggle", action="store_true")
    p.add_argument("--start", action="store_true")
    p.add_argument("--stop", action="store_true")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--cps", type=float, default=10.0)
    p.add_argument("--button", choices=BUTTON_ORDER, default="left")
    opts, _ = p.parse_known_args(argv)
    opts.cps = max(MIN_CPS, min(MAX_CPS, opts.cps))
    return opts


def main():
    opts = parse_args(sys.argv[1:])
    if opts.selftest:
        return selftest()
    app = build_app(opts.cps, opts.button)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
