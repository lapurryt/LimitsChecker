#!/usr/bin/env sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bin_dir="$HOME/.local/bin"
apps_dir="$HOME/.local/share/applications"
autostart_dir="$HOME/.config/autostart"
config_dir="$HOME/.config/claudebar-gnome"

mkdir -p "$bin_dir" "$apps_dir" "$autostart_dir" "$config_dir"

install -m 0755 "$repo_dir/bin/claudebar-gnome-indicator" "$bin_dir/claudebar-gnome-indicator"

# Install the bundled mascot icon (used by the tray and the .desktop launcher).
# Skip if the user already dropped their own icon there.
if [ -f "$repo_dir/share/icon.png" ] && [ ! -f "$config_dir/icon.png" ] && [ ! -f "$config_dir/icon.svg" ]; then
    install -m 0644 "$repo_dir/share/icon.png" "$config_dir/icon.png"
fi

desktop_tmp=$(mktemp)
sed "s|@HOME@|$HOME|g" "$repo_dir/share/claudebar-gnome-indicator.desktop.in" > "$desktop_tmp"
install -m 0644 "$desktop_tmp" "$apps_dir/claudebar-gnome-indicator.desktop"
install -m 0644 "$desktop_tmp" "$autostart_dir/claudebar-gnome-indicator.desktop"
rm -f "$desktop_tmp"

echo "Installed claudebar-gnome-indicator"
echo "Start it now with: gtk-launch claudebar-gnome-indicator"
