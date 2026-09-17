#!/usr/bin/env bash
# Per-user install of Display Tune.   ./install.sh            install or update
#                                      ./install.sh --uninstall
set -euo pipefail

APP_ID=io.github.ahmad_alqattu.DisplayTune
UUIDS=(display-tune-saturation@ahmad-alqattu.github.io display-tune-quicksettings@ahmad-alqattu.github.io)
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN="$HOME/.local/bin"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }

uninstall() {
    say "Removing Display Tune"
    systemctl --user disable --now display-tune.service 2>/dev/null || true
    for uuid in "${UUIDS[@]}"; do
        gnome-extensions disable "$uuid" 2>/dev/null || true
        rm -rf "$DATA/gnome-shell/extensions/$uuid"
    done
    rm -rf "$DATA/display-tune"
    rm -f "$BIN/display-tune" "$CONF/systemd/user/display-tune.service" \
          "$DATA/applications/$APP_ID.desktop" "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
    rm -f "$DATA"/icc/display-tune-*.icc
    systemctl --user daemon-reload 2>/dev/null || true
    say "Done. Your settings are kept in $CONF/display-tune (delete it to remove them too)."
    say "Colour profiles were removed; log out and back in to return every screen to its default."
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
    exit 0
fi

say "Checking dependencies"
python3 - <<'PY' || { warn "Missing: Python GObject bindings with GTK 4 and libadwaita (see README)"; exit 1; }
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw
PY
gdbus introspect --system --dest org.freedesktop.ColorManager --object-path /org/freedesktop/ColorManager >/dev/null 2>&1 \
    || warn "colord is not running: contrast, gamma and warmth will not work"
command -v ddcutil >/dev/null || warn "ddcutil not found (optional): external monitor hardware brightness/contrast disabled"
command -v glib-compile-schemas >/dev/null || { warn "glib-compile-schemas not found (install glib2 / libglib2.0-bin)"; exit 1; }

say "Installing app"
rm -rf "$DATA/display-tune"
mkdir -p "$DATA/display-tune" "$BIN" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps" "$CONF/systemd/user"
cp -r "$SRC/display_tune" "$DATA/display-tune/"
find "$DATA/display-tune" -name __pycache__ -prune -exec rm -rf {} +
cat > "$BIN/display-tune" <<EOF
#!/bin/sh
PYTHONPATH="$DATA/display-tune\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m display_tune "\$@"
EOF
chmod +x "$BIN/display-tune"
sed "s#@BIN@#$BIN#g" "$SRC/data/$APP_ID.desktop.in" > "$DATA/applications/$APP_ID.desktop"
cp "$SRC/data/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/"
gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$DATA/applications" 2>/dev/null || true
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) warn "$BIN is not on your PATH — add it to your shell profile so \`display-tune\` and the Quick Settings toggle work." ;;
esac

say "Installing GNOME Shell extensions (saturation, quick settings)"
NEW_EXTENSION=0
mkdir -p "$DATA/gnome-shell/extensions"
for uuid in "${UUIDS[@]}"; do
    [[ -d "$DATA/gnome-shell/extensions/$uuid" ]] || NEW_EXTENSION=1
    rm -rf "$DATA/gnome-shell/extensions/$uuid"
    cp -r "$SRC/extension/$uuid" "$DATA/gnome-shell/extensions/"
    [[ -d "$DATA/gnome-shell/extensions/$uuid/schemas" ]] \
        && glib-compile-schemas "$DATA/gnome-shell/extensions/$uuid/schemas"
done

say "Installing background service"
sed "s#@BIN@#$BIN#g" "$SRC/data/display-tune.service.in" > "$CONF/systemd/user/display-tune.service"
systemctl --user daemon-reload
systemctl --user enable display-tune.service >/dev/null 2>&1
systemctl --user restart display-tune.service

say "Installed. Open “Display Tune” from the app grid, or run: display-tune"
if [[ $NEW_EXTENSION == 1 ]]; then
    warn "Log out and back in once so GNOME Shell picks up the new extension(s), including the Quick Settings toggle."
else
    warn "An extension was updated; changes to it take effect after logging out and back in."
fi
