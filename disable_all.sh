#!/bin/bash
set -euo pipefail

# stops the backend, sensor process, and access point
# saved configuration and measurement files are not deleted
# run setup_backend_service.sh and setup_ap.sh to enable them again
# restarting NetworkManager may close the current SSH session

DNS_FILE="/etc/NetworkManager/dnsmasq-shared.d/bme688.conf"

echo "Stopping backend"
sudo systemctl disable --now bme688.service 2>/dev/null || true
sudo pkill -x main 2>/dev/null || true

echo "Stopping access point"
sudo systemctl stop bme688-ap-activate.timer bme688-ap-rollback.timer \
    bme688-ap-activate.service bme688-ap-rollback.service 2>/dev/null || true
sudo rm -f /run/bme688-ap-setup/pending
sudo systemctl disable --now bme688-ap.service 2>/dev/null || true
sudo systemctl stop bme688-ap-dns.service 2>/dev/null || true
if nmcli -t -f NAME connection show | grep -Fxq "bme688-ap-test"; then
    sudo nmcli connection modify "bme688-ap-test" connection.autoconnect no
    sudo nmcli connection down "bme688-ap-test" 2>/dev/null || true
fi
if nmcli -t -f NAME connection show | grep -Fxq "bme688-ap"; then
    sudo nmcli connection down "bme688-ap" 2>/dev/null || true
    sudo nmcli connection modify "bme688-ap" connection.autoconnect no
fi

sudo rm -f "${DNS_FILE}"
sudo rm -f /etc/NetworkManager/dnsmasq-shared.d/captive_portal.conf
sudo ip address del 10.42.0.1/24 dev wlan0 2>/dev/null || true
sudo nmcli device set wlan0 managed yes
sudo systemctl restart NetworkManager

echo "Backend and access point disabled"
