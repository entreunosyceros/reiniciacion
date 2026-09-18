#!/usr/bin/env bash
# Genera el paquete Debian reiniciacion_*.deb en el directorio dist/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="${VERSION:-1.0.1}"
PKG_NAME="reiniciacion"
ARCH="all"
BUILD_ROOT="$ROOT/build/deb"
STAGE="$BUILD_ROOT/${PKG_NAME}_${VERSION}_${ARCH}"
DIST_DIR="$ROOT/dist"
DEB_FILE="$DIST_DIR/${PKG_NAME}_${VERSION}_${ARCH}.deb"

rm -rf "$BUILD_ROOT"
mkdir -p \
    "$STAGE/DEBIAN" \
    "$STAGE/usr/bin" \
    "$STAGE/usr/share/reiniciacion/reinicia" \
    "$STAGE/usr/share/reiniciacion/img" \
    "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/pixmaps" \
    "$STAGE/usr/share/doc/reiniciacion" \
    "$DIST_DIR"

# Iconos hicolor en varios tamaños (el dock de Ubuntu los usa)
python3 - <<PY
from pathlib import Path
from PIL import Image

src = Image.open("${ROOT}/img/logo.png").convert("RGBA")
out_root = Path("${STAGE}/usr/share/icons/hicolor")
for size in (16, 24, 32, 48, 64, 128, 256):
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon = src.copy()
    icon.thumbnail((size, size), Image.Resampling.LANCZOS)
    x = (size - icon.width) // 2
    y = (size - icon.height) // 2
    canvas.paste(icon, (x, y), icon)
    dest_dir = out_root / f"{size}x{size}" / "apps"
    dest_dir.mkdir(parents=True, exist_ok=True)
    canvas.save(dest_dir / "reiniciacion.png")
print("icons ok")
PY

install -m 644 "$STAGE/usr/share/icons/hicolor/128x128/apps/reiniciacion.png" \
    "$STAGE/usr/share/pixmaps/reiniciacion.png"
install -m 644 "$STAGE/usr/share/icons/hicolor/128x128/apps/reiniciacion.png" \
    "$ROOT/packaging/reiniciacion.png"

# Metadatos del paquete
install -m 644 "$ROOT/packaging/debian/control" "$STAGE/DEBIAN/control"
sed -i "s/^Version:.*/Version: ${VERSION}/" "$STAGE/DEBIAN/control"
install -m 755 "$ROOT/packaging/debian/postinst" "$STAGE/DEBIAN/postinst"
install -m 755 "$ROOT/packaging/debian/postrm" "$STAGE/DEBIAN/postrm"

install -m 644 "$ROOT/packaging/debian/changelog" "$STAGE/usr/share/doc/reiniciacion/changelog"
gzip -9 -n -f "$STAGE/usr/share/doc/reiniciacion/changelog"
install -m 644 "$ROOT/packaging/debian/copyright" "$STAGE/usr/share/doc/reiniciacion/copyright"
install -m 644 "$ROOT/README.md" "$STAGE/usr/share/doc/reiniciacion/README.md"

# Aplicación
install -m 644 "$ROOT/reiniciacion.py" "$STAGE/usr/share/reiniciacion/reiniciacion.py"
install -m 644 "$ROOT/reinicia/"*.py "$STAGE/usr/share/reiniciacion/reinicia/"
install -m 644 "$ROOT/img/logo.png" "$STAGE/usr/share/reiniciacion/img/logo.png"

# Lanzador en PATH
install -m 755 "$ROOT/packaging/bin/reiniciacion" "$STAGE/usr/bin/reiniciacion"

# Menú
install -m 644 "$ROOT/packaging/reiniciacion.desktop" "$STAGE/usr/share/applications/reiniciacion.desktop"

# Permisos Debian estándar
find "$STAGE" -type d -exec chmod 0755 {} +
find "$STAGE" -type f -exec chmod 0644 {} +
chmod 0755 "$STAGE/usr/bin/reiniciacion"
chmod 0755 "$STAGE/DEBIAN"
chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

# Tamaño instalado (kB)
INSTALLED_SIZE="$(du -sk "$STAGE" | awk '{print $1}')"
if ! grep -q '^Installed-Size:' "$STAGE/DEBIAN/control"; then
    printf 'Installed-Size: %s\n' "$INSTALLED_SIZE" >> "$STAGE/DEBIAN/control"
else
    sed -i "s/^Installed-Size:.*/Installed-Size: ${INSTALLED_SIZE}/" "$STAGE/DEBIAN/control"
fi

dpkg-deb --root-owner-group --build "$STAGE" "$DEB_FILE"

echo
echo "Paquete creado: $DEB_FILE"
echo "Instalar con:"
echo "  sudo apt install ./dist/$(basename "$DEB_FILE")"
echo "o:"
echo "  sudo dpkg -i $DEB_FILE"
