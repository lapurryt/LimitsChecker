#!/usr/bin/env sh
set -eu

# Match any interpreter/path whose command ends with the indicator name.
pkill -f 'claudebar-gnome-indicator$' 2>/dev/null || true
rm -f "$HOME/.local/bin/claudebar-gnome-indicator"
rm -f "$HOME/.local/share/applications/claudebar-gnome-indicator.desktop"
rm -f "$HOME/.config/autostart/claudebar-gnome-indicator.desktop"
rm -f "$HOME/.config/claudebar-gnome/icon.png"
rmdir "$HOME/.config/claudebar-gnome" 2>/dev/null || true

echo "Removed claudebar-gnome-indicator"
