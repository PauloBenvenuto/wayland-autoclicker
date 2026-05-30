#!/usr/bin/env bash
# Build a self-contained AppImage for the autoclicker (GTK4 + PyGObject + evdev).
#
# Bundles: the CPython interpreter + a pruned stdlib, the `gi`, `evdev` and
# `cairo` (pycairo) packages, every GObject-introspection typelib, and the
# whole GTK4 runtime (via linuxdeploy-plugin-gtk). The result runs without any
# Python/GTK install on the host. It still needs /dev/uinput access on the
# target machine (kernel permission — cannot be bundled).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(cd "$HERE/.." && pwd)"
TOOLS="$HERE/tools"
APPDIR="$HERE/AppDir"
APP_SRC="$PROJ/autoclicker.py"

PYBIN="$(readlink -f "$(command -v python3)")"
PYVER="$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYLIB="/usr/lib/python${PYVER}"
SITE="$(python3 -c 'import site;print(site.getsitepackages()[0])')"

echo ">> python=$PYBIN  ver=$PYVER  site=$SITE"

# --- 1. Fresh AppDir skeleton -------------------------------------------------
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib/girepository-1.0" \
         "$APPDIR/usr/lib/python${PYVER}/site-packages" "$APPDIR/usr/share"

# --- 2. Python interpreter + pruned stdlib ------------------------------------
cp "$PYBIN" "$APPDIR/usr/bin/python${PYVER}"
rsync -a \
  --exclude 'site-packages' \
  --exclude 'test' --exclude 'tests' \
  --exclude 'idlelib' --exclude 'tkinter' --exclude 'turtledemo' \
  --exclude 'ensurepip' --exclude 'lib2to3' --exclude 'pydoc_data' \
  --exclude 'config-*' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '*.pyo' \
  --exclude '_tkinter*' --exclude '_test*' --exclude '_ctypes_test*' \
  --exclude 'xxlimited*' --exclude '_xxtestfuzz*' \
  "$PYLIB/" "$APPDIR/usr/lib/python${PYVER}/"

# --- 3. The Python packages the app actually imports --------------------------
for pkg in gi evdev cairo; do
  if [ -e "$SITE/$pkg" ]; then
    rsync -a --exclude '__pycache__' "$SITE/$pkg" \
          "$APPDIR/usr/lib/python${PYVER}/site-packages/"
  fi
done

# --- 4. All introspection typelibs (small; guarantees nothing is missing) -----
cp -a /usr/lib/girepository-1.0/*.typelib "$APPDIR/usr/lib/girepository-1.0/"

# --- 5. The app itself + a launcher wrapper -----------------------------------
# The wrapper is what `Exec=autoclicker` in the desktop file resolves to, and
# linuxdeploy will symlink AppRun -> ./autoclicker -> usr/bin/autoclicker.
# It sets up the bundled-Python env, sources the gtk plugin's apprun hooks,
# and execs the script.
cp "$APP_SRC" "$APPDIR/usr/bin/autoclicker.py"
cat > "$APPDIR/usr/bin/autoclicker" <<EOF
#!/bin/bash
HERE="\$(dirname "\$(readlink -f "\$0")")"
APPDIR="\$(cd "\$HERE/../.." && pwd)"
export APPDIR
for hook in "\$APPDIR"/apprun-hooks/*.sh; do [ -e "\$hook" ] && . "\$hook"; done
export PYTHONHOME="\$APPDIR/usr"
export PYTHONNOUSERSITE=1
export PYTHONPATH="\$APPDIR/usr/lib/python${PYVER}/site-packages"
export LD_LIBRARY_PATH="\$APPDIR/usr/lib:\${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="\$APPDIR/usr/lib/girepository-1.0:\${GI_TYPELIB_PATH:-}"
exec "\$APPDIR/usr/bin/python${PYVER}" "\$APPDIR/usr/bin/autoclicker.py" "\$@"
EOF
chmod +x "$APPDIR/usr/bin/autoclicker"

# --- 6. Desktop entry + icon (appimagetool/linuxdeploy require these) ---------
cat > "$APPDIR/autoclicker.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Autoclicker
GenericName=Autoclicker HUD
Comment=Wayland autoclicker control panel (F8 toggles clicking)
Exec=autoclicker
Icon=autoclicker
Terminal=false
Categories=Utility;
Keywords=autoclick;clicker;mouse;click;cps;
StartupNotify=false
EOF
ICON_SRC="$(find /usr/share/icons -name 'input-mouse.png' 2>/dev/null | sort | tail -1)"
if [ -n "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$APPDIR/autoclicker.png"
else
  # 1x1 transparent PNG fallback so the tooling has something to embed.
  printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82' \
    > "$APPDIR/autoclicker.png"
fi

# --- 7. Bundle GTK4 runtime + shared-library deps via linuxdeploy -------------
export PATH="$TOOLS:$PATH"
export DEPLOY_GTK_VERSION=4
# linuxdeploy's bundled `strip` predates the .relr.dyn section type emitted by
# modern Arch binutils and dies on every library; skip stripping instead.
export NO_STRIP=1
GI_SO=("$SITE"/gi/_gi*.so)
EVDEV_SO=("$SITE"/evdev/*.so)
LIB_ARGS=()
for so in "${GI_SO[@]}" "${EVDEV_SO[@]}" /usr/lib/libgtk-4.so.1; do
  [ -e "$so" ] && LIB_ARGS+=(--library "$so")
done

"$TOOLS/linuxdeploy.AppImage" --appdir "$APPDIR" \
  --executable "$APPDIR/usr/bin/python${PYVER}" \
  "${LIB_ARGS[@]}" \
  --desktop-file "$APPDIR/autoclicker.desktop" \
  --icon-file "$APPDIR/autoclicker.png" \
  --plugin gtk

# --- 8. Heal libs corrupted by linuxdeploy's old patchelf ---------------------
# The patchelf vendored inside linuxdeploy predates DT_RELR (relative relocs)
# which current Arch binutils emit, so its rpath rewrite quietly damages every
# library it touches (manifests as a SIGSEGV on `from gi.repository import Gtk`).
# The wrapper already sets LD_LIBRARY_PATH, so the $ORIGIN rpath isn't needed
# anyway — replace each bundled .so with the pristine host copy.
echo "Healing libs after linuxdeploy patchelf..."
find "$APPDIR/usr/lib" -type f \( -name '*.so' -o -name '*.so.*' \) | while read -r f; do
  base="$(basename "$f")"
  if [ -e "/usr/lib/$base" ]; then
    cp -fL "/usr/lib/$base" "$f"
  else
    src="$(find /usr/lib -name "$base" -type f -printf '%p\n' 2>/dev/null | head -1)"
    [ -n "$src" ] && cp -fL "$src" "$f"
  fi
done
# Restore the Python binary too (linuxdeploy patches its rpath the same way).
cp -fL "/usr/bin/python${PYVER}" "$APPDIR/usr/bin/python${PYVER}"
chmod +x "$APPDIR/usr/bin/python${PYVER}"

# --- 9. Pack the AppImage -----------------------------------------------------
export ARCH=x86_64
OUT="$PROJ/Autoclicker-x86_64.AppImage"
"$TOOLS/appimagetool.AppImage" "$APPDIR" "$OUT"
echo ">> built: $OUT ($(du -h "$OUT" | cut -f1))"
