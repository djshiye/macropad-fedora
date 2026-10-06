#!/usr/bin/env bash
# Install the macropad daemon (systemd user service), udev rule and app launcher.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! cmp -s "$DIR/70-macropad.rules" /etc/udev/rules.d/70-macropad.rules; then
    echo "Installing udev rule (needs admin rights)…"
    pkexec sh -c "cp '$DIR/70-macropad.rules' /etc/udev/rules.d/ && udevadm control --reload && udevadm trigger --subsystem-match=input --subsystem-match=misc"
fi

mkdir -p ~/.config/systemd/user ~/.local/share/applications
sed "s|@DIR@|$DIR|g" "$DIR/macropad.service" > ~/.config/systemd/user/macropad.service
sed "s|@DIR@|$DIR|g" "$DIR/macropad.desktop" > ~/.local/share/applications/io.github.dino.Macropad.desktop
systemctl --user daemon-reload
systemctl --user enable --now macropad.service
systemctl --user restart macropad.service
echo "Done. Open “Macropad” from the app grid to configure your keys."
