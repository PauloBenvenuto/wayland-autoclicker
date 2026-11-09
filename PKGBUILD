# Paquete local: se construye desde esta carpeta (makepkg -si), sin descargar nada.
# La versión sale de git: r<commits>.<hash>.
pkgname=wayland-autoclicker
pkgver=r1.dbd7d98
pkgrel=1
pkgdesc="Wayland/Hyprland autoclicker with a live GTK4 HUD (uinput)"
arch=(any)
license=(GPL-3.0-or-later)
depends=(python python-gobject python-evdev gtk4)
makedepends=(git)

pkgver() {
    cd "$startdir"
    printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

package() {
    cd "$startdir"
    install -Dm755 autoclicker.py "$pkgdir/usr/bin/autoclicker"
    install -Dm644 dev.local.autoclicker.desktop -t "$pkgdir/usr/share/applications"
    install -Dm644 70-autoclicker-uinput.rules -t "$pkgdir/usr/lib/udev/rules.d"
    install -Dm644 README.md -t "$pkgdir/usr/share/doc/$pkgname"
    install -Dm644 LICENSE -t "$pkgdir/usr/share/licenses/$pkgname"
}
