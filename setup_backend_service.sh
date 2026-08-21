#!/bin/bash
set -euo pipefail

# run this as the normal pi user while internet is available
# give every device a zero-padded id: 01, 02, 03, and so on
# example: UNIT_ID=02 bash setup_backend_service.sh

UNIT_ID="${UNIT_ID:-01}"
DEVICE_HOSTNAME="bme688-${UNIT_ID}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="/etc/systemd/system/bme688.service"

if (( EUID == 0 )); then
    echo "Run this as the normal Pi user, without sudo"
    exit 1
fi

if [[ ! "${UNIT_ID}" =~ ^[0-9]{2,}$ ]]; then
    echo "UNIT_ID must use at least two digits, for example 01 or 02"
    exit 1
fi

echo "Setting device name to ${DEVICE_HOSTNAME}"
sudo bash -c '
set -e
device_hostname="$1"
hosts_file="/etc/hosts"

if grep -qE "^[[:space:]]*127\.0\.1\.1([[:space:]]|$)" "${hosts_file}"; then
    sed -i -E "s/^[[:space:]]*127\.0\.1\.1.*/127.0.1.1\t${device_hostname}/" "${hosts_file}"
else
    printf "\n127.0.1.1\t%s\n" "${device_hostname}" >> "${hosts_file}"
fi

hostnamectl set-hostname "${device_hostname}"
' _ "${DEVICE_HOSTNAME}"

echo "Installing backend dependencies"
sudo apt-get update
sudo apt-get install -y \
    avahi-daemon \
    build-essential \
    dnsmasq-base \
    i2c-tools \
    iw \
    libnss-mdns \
    network-manager \
    python3-fastapi \
    python3-uvicorn \
    raspi-config

echo "Enabling hardware"
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_wifi_country RO

echo "Building sensor program"
make -C "${PROJECT_DIR}/bme-code" clean
make -C "${PROJECT_DIR}/bme-code"

echo "Installing backend service"
cat <<EOF | sudo tee "${SERVICE_FILE}" >/dev/null
[Unit]
Description=BME688 backend
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${PROJECT_DIR}/backend
StateDirectory=bme688
Environment=BME_CONFIG_PATH=/var/lib/bme688/config.json
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 80
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable bme688.service
sudo systemctl restart bme688.service
sudo systemctl enable --now avahi-daemon

if ! sudo systemctl is-active --quiet bme688.service; then
    echo "Backend failed to start"
    sudo journalctl -u bme688.service -n 30 --no-pager
    exit 1
fi

echo "Backend ready on port 80"
echo "Reboot once after the first setup"
