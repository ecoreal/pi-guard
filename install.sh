#!/bin/sh
# Install pi-guard on a Raspberry Pi.  Usage: sudo ./install.sh [--no-systemd]
set -eu

NO_SYSTEMD=0
while [ $# -gt 0 ]; do
    case "$1" in
        --no-systemd) NO_SYSTEMD=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "pi-guard: install.sh needs root (or use 'make zipapp' and run from your home)" >&2
    exit 1
fi

echo "==> building zipapp"
make zipapp >/dev/null

echo "==> installing to /usr/local/bin/pi-guard"
install -m 0755 dist/pi-guard /usr/local/bin/pi-guard

if [ "$NO_SYSTEMD" -eq 0 ] && [ -d /etc/systemd/system ]; then
    echo "==> installing systemd service"
    install -m 0644 systemd/pi-guard.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now pi-guard.service
    echo "    service: $(systemctl is-active pi-guard.service)"
fi

echo "==> done. Try: pi-guard once   |   pi-guard report"
