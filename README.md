# Wayland Autoclicker

A Wayland-native autoclicker for Hyprland (and other Wayland compositors) with a live GTK4 HUD. Toggles clicking via a global F8 keybind by driving a virtual mouse through `/dev/uinput`, so it works under Wayland where `xdotool` and other X11 tools can't.

> Clicks land at the current cursor position. Hover the HUD itself to pause without stopping — useful for fine-tuning the rate mid-session.

## Features

- **Wayland-native** — uses the kernel's `uinput` subsystem instead of X11, so clicks reach the compositor as genuine input events.
- **Live GTK4 HUD** — IDLE / CLICKING pill, rate slider (1–100 cps), button selector (left / right / middle).
- **Global F8 toggle** — bind once in Hyprland; switching state doesn't steal keyboard focus from the active window.
- **Single-instance** — subsequent invocations talk to the running HUD over GTK's built-in D-Bus channel.
- **Hover-to-pause** — clicks suppress automatically while the cursor is over the HUD.
- **Self-contained AppImage** — bundles CPython, GTK4, PyGObject and python-evdev; no host Python or GTK install required.

## Install

Download `Autoclicker-x86_64.AppImage` from the [latest release](https://github.com/PauloBenvenuto/wayland-autoclicker/releases/latest):

```bash
chmod +x Autoclicker-x86_64.AppImage
./Autoclicker-x86_64.AppImage --selftest   # check /dev/uinput access
./Autoclicker-x86_64.AppImage              # open the HUD
```

### `/dev/uinput` permissions

The autoclicker writes to `/dev/uinput` to create the virtual mouse. By default this device is `root:root 0600` and your user can't open it — kernel permission, the AppImage can't bundle around it.

Pick one:

**Option A — udev rule (recommended, no relogin needed):**

```bash
echo 'KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules
sudo modprobe uinput
sudo usermod -aG input "$USER"
# log out and back in for the group to take effect
```

**Option B — quick check without persistence:**

```bash
sudo chmod 666 /dev/uinput   # resets on reboot
```

Verify with `./Autoclicker-x86_64.AppImage --selftest`.

## Usage

| Command                                | Action                                                              |
| -------------------------------------- | ------------------------------------------------------------------- |
| `autoclicker`                          | Open the HUD (idle).                                                |
| `autoclicker --toggle`                 | Toggle clicking on/off — bind this to your hotkey.                  |
| `autoclicker --start` / `--stop`       | Explicitly start or stop clicking.                                  |
| `autoclicker --cps N`                  | Set rate on launch (1–100).                                         |
| `autoclicker --button {left,right,middle}` | Set which button to click.                                      |
| `autoclicker --selftest`               | Check `/dev/uinput` access without opening the GUI.                 |

While the HUD is open: **F8** toggles, **Esc** closes, the title bar drags the window, and the toggle button / sliders work as expected.

## Hyprland setup

The HUD reports its Wayland `app_id` as `dev.local.autoclicker`. Add to `~/.config/hypr/hyprland.conf`:

```conf
# F8 toggles clicking (no-op if the HUD isn't running)
bind = , F8, exec, /path/to/Autoclicker-x86_64.AppImage --toggle

# Float the HUD, keep it on top, pin it to a corner
windowrulev2 = float,                class:^(dev\.local\.autoclicker)$
windowrulev2 = pin,                  class:^(dev\.local\.autoclicker)$
windowrulev2 = move 100%-340 50,     class:^(dev\.local\.autoclicker)$
windowrulev2 = noborder,             class:^(dev\.local\.autoclicker)$
```

The HUD is a regular toplevel window (not a layer-shell overlay), so the compositor manages it normally — it lives on a workspace, you can drag it with the title bar, and any `windowrulev2` you write applies.

## Building from source

Tested on Arch Linux with Python 3.13, GTK 4, PyGObject and python-evdev installed on the host.

Host packages (Arch names):

```bash
sudo pacman -S --needed python python-gobject python-cairo python-evdev gtk4 rsync patchelf
```

The build script expects two helpers under `build/tools/` that are gitignored — fetch them once:

```bash
cd build/tools
curl -L -o linuxdeploy.AppImage \
  https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage
curl -L -o appimagetool.AppImage \
  https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x linuxdeploy.AppImage appimagetool.AppImage
cd ../..
```

Then build:

```bash
./build/build-appimage.sh
```

Output: `Autoclicker-x86_64.AppImage` at the repo root (~54 MB).

The build script bundles the CPython interpreter, a pruned stdlib, `gi` / `evdev` / `cairo`, every GObject typelib, and the GTK 4 runtime, then "heals" libraries afterwards because `linuxdeploy`'s vendored `patchelf` predates the `DT_RELR` relocation type emitted by current Arch binutils and corrupts every `.so` it rewrites.

## How it works

The autoclicker registers a virtual pointer through `/dev/uinput` (declaring `BTN_LEFT/RIGHT/MIDDLE` + `REL_X/REL_Y`), so `libinput` treats it like any USB mouse. Click events therefore arrive at the compositor as real input — no compositor-specific protocol, no X11 fallback, no `XTEST`.

A background thread emits press / release pairs at the configured rate. The release-to-next-press sleep stays responsive (≤20 ms granularity) so toggling off or hovering the HUD interrupts the loop almost immediately.

The HUD is GTK 4. Subsequent `--toggle` invocations are forwarded to the primary instance over GTK's built-in D-Bus single-instance channel (`G_APPLICATION_HANDLES_COMMAND_LINE`), which is why the keybind switches state without raising or focusing the window.

## License

GPL-3.0 — see [LICENSE](LICENSE).
