#!/bin/bash
set -euo pipefail

# run the backend setup before this file
# this switches wlan0 to access-point mode and closes the current Wi-Fi session
# the hostname is also used as the Wi-Fi name
# the Wi-Fi network is open and has no password
# every device can use 10.42.0.1 because each access point is its own network

DEVICE_HOSTNAME="$(hostnamectl --static)"
AP_SSID="${DEVICE_HOSTNAME}"
AP_CONNECTION="bme688-ap"
AP_ADDRESS="10.42.0.1"
DNS_FILE="/etc/NetworkManager/dnsmasq-shared.d/bme688.conf"

if [[ ! "${DEVICE_HOSTNAME}" =~ ^bme688-[0-9]{2,}$ ]]; then
    echo "Run setup_backend_service.sh before setup_ap.sh"
    exit 1
fi

if ! command -v nmcli >/dev/null; then
    echo "NetworkManager is not installed"
    exit 1
fi

sudo raspi-config nonint do_wifi_country RO

echo "Creating ${AP_SSID}"

if nmcli -t -f NAME connection show | grep -Fxq "${AP_CONNECTION}"; then
    sudo nmcli connection down "${AP_CONNECTION}" 2>/dev/null || true
    sudo nmcli connection delete "${AP_CONNECTION}"
fi

sudo nmcli connection add \
    type wifi \
    ifname wlan0 \
    con-name "${AP_CONNECTION}" \
    ssid "${AP_SSID}"

sudo nmcli connection modify "${AP_CONNECTION}" \
    connection.autoconnect yes \
    connection.autoconnect-priority 999 \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    ipv4.addresses "${AP_ADDRESS}/24" \
    ipv4.method shared \
    ipv6.method disabled

sudo mkdir -p "$(dirname "${DNS_FILE}")"
sudo rm -f /etc/NetworkManager/dnsmasq-shared.d/captive_portal.conf
echo "address=/#/${AP_ADDRESS}" | sudo tee "${DNS_FILE}" >/dev/null

echo "Wi-Fi: ${AP_SSID}"
echo "Password: none"
echo "Address: http://${DEVICE_HOSTNAME}.local"
echo "Fallback: http://${AP_ADDRESS}"
echo "Run sudo reboot to start the access point"
