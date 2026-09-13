#!/bin/bash
set -euo pipefail
export LC_ALL=C

# run backend setup first; use 01, 02, 03, and so on for each unit
# the hostname is the Wi-Fi name; every unit uses the same password
# reconnect and run --confirm within five minutes to save the AP

AP_PASSWORD="utcnbme688"
AP_ADDRESS="10.42.0.1"
STATE="/run/bme688-ap-setup"
LOCK="/run/lock/bme688-ap-setup.lock"
CONF="/etc/bme688-ap"
UNITS="/etc/systemd/system"
WORKER="/usr/local/lib/bme688-ap/setup.sh"
LEASES="/var/lib/bme688-ap"

if (( EUID != 0 )); then
    exec sudo /bin/bash "$0" "$@"
fi

# systemd calls these without the setup lock
case "${1:-}" in
    --prepare)
        nmcli device set wlan0 managed no
        ip link set dev wlan0 down
        ip -4 address flush dev wlan0
        ip address add "${AP_ADDRESS}/24" dev wlan0
        ip link set dev wlan0 up
        iw dev wlan0 set power_save off || true
        exit 0
        ;;
    --release)
        ip address del "${AP_ADDRESS}/24" dev wlan0 2>/dev/null || true
        nmcli device set wlan0 managed yes
        exit 0
        ;;
esac

exec 9>"${LOCK}"
flock -x 9

ap_ready() {
    systemctl is-active --quiet bme688-ap.service &&
        systemctl is-active --quiet bme688-ap-dns.service &&
        hostapd_cli -p /run/bme688-hostapd -i wlan0 status | grep -Fxq 'state=ENABLED'
}

rollback() {
    test -f "${STATE}/pending" || return 0
    local previous_uuid
    previous_uuid="$(<"${STATE}/previous")"
    echo "Restoring the previous network"
    journalctl -b -u bme688-ap.service -u bme688-ap-dns.service \
        -n 200 --no-pager >"${STATE}/ap.log" || true
    systemctl disable bme688-ap.service
    systemctl stop bme688-ap-dns.service bme688-ap.service
    /bin/bash "${WORKER}" --release
    nmcli --wait 45 connection up uuid "${previous_uuid}" ifname wlan0
    rm -f "${STATE}/pending"
    echo "Previous network restored"
}

case "${1:-}" in
    --rollback)
        rollback
        exit 0
        ;;
    --activate)
        test -f "${STATE}/pending"
        if systemctl start bme688-ap.service; then
            for attempt in {1..20}; do
                if ap_ready; then
                    echo "Access point ready. Reconnect and run setup_ap.sh --confirm"
                    exit 0
                fi
                sleep 1
            done
        fi
        journalctl -b -u bme688-ap.service -u bme688-ap-dns.service \
            -n 100 --no-pager >"${STATE}/activation.log" || true
        rollback
        exit 1
        ;;
    --confirm)
        if ! test -f "${STATE}/pending"; then
            echo "No access-point test is waiting for confirmation"
            exit 1
        fi
        if ! ap_ready || ! hostapd_cli -p /run/bme688-hostapd -i wlan0 all_sta |
            grep -q 'flags=.*\[AUTHORIZED\]'; then
            echo "Connect a device to the new access point before confirming"
            exit 1
        fi
        systemctl enable bme688-ap.service
        rm -f "${STATE}/pending"
        systemctl stop bme688-ap-rollback.timer
        echo "Access point saved. It will start automatically after reboot."
        exit 0
        ;;
    "") ;;
    *) echo "Usage: bash setup_ap.sh [--confirm]"; exit 1 ;;
esac

for command in nmcli systemd-run hostnamectl; do
    command -v "${command}" >/dev/null || { echo "Missing ${command}. Run backend setup first."; exit 1; }
done
DEVICE_HOSTNAME="$(hostnamectl --static)"
if [[ ! "${DEVICE_HOSTNAME}" =~ ^bme688-[0-9]{2,}$ ]]; then
    echo "Run setup_backend_service.sh before setup_ap.sh"
    exit 1
fi
if [[ -f "${STATE}/pending" ]]; then
    echo "A test is already pending. Reconnect and confirm, or wait for rollback."
    exit 1
fi
if systemctl is-enabled --quiet bme688-ap.service 2>/dev/null ||
    systemctl is-active --quiet bme688-ap.service; then
    echo "The dedicated access point is already installed and enabled or running."
    echo "Check it with: sudo systemctl status bme688-ap.service --no-pager"
    exit 1
fi
if systemctl is-active --quiet hostapd.service || systemctl is-active --quiet dnsmasq.service; then
    echo "Another hostapd or dnsmasq service is running. Nothing was changed."
    exit 1
fi
if [[ "$(nmcli radio wifi)" != enabled ||
      "$(nmcli -g WIFI-PROPERTIES.AP device show wlan0)" != yes ]]; then
    echo "wlan0 must be enabled and support access-point mode"
    exit 1
fi
PREVIOUS_UUID="$(nmcli -g GENERAL.CON-UUID device show wlan0)"
if [[ -z "${PREVIOUS_UUID}" || "${PREVIOUS_UUID}" == -- ||
      "$(nmcli -g connection.autoconnect connection show uuid "${PREVIOUS_UUID}")" != yes ]]; then
    echo "Connect wlan0 to a saved network with autoconnect enabled before setup."
    exit 1
fi

# install only missing tools, while the working connection is still available
if ! command -v hostapd >/dev/null || ! command -v hostapd_cli >/dev/null ||
    ! command -v dnsmasq >/dev/null || ! command -v iw >/dev/null; then
    apt-get update
    apt-get install -y --no-upgrade hostapd dnsmasq-base iw
fi

systemctl stop bme688-ap-activate.timer bme688-ap-rollback.timer \
    bme688-ap-activate.service bme688-ap-rollback.service 2>/dev/null || true
install -d -m 700 "${STATE}" "${CONF}"
install -D -m 700 "${BASH_SOURCE[0]}" "${WORKER}"
install -m 700 "${BASH_SOURCE[0]}" "${STATE}/worker.sh"
install -d -m 755 -o dnsmasq -g "$(id -gn dnsmasq)" "${LEASES}"
printf '%s\n' "${PREVIOUS_UUID}" >"${STATE}/previous"

# advertise only WPA2-PSK with AES, without the SHA256 authentication variant
tee "${CONF}/hostapd.conf" >/dev/null <<EOF
interface=wlan0
driver=nl80211
ctrl_interface=/run/bme688-hostapd
ssid=${DEVICE_HOSTNAME}
country_code=RO
hw_mode=g
channel=6
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
ieee80211w=0
wpa_passphrase=${AP_PASSWORD}
EOF
chmod 600 "${CONF}/hostapd.conf"

tee "${CONF}/dnsmasq.conf" >/dev/null <<EOF
interface=wlan0
except-interface=lo
bind-dynamic
user=dnsmasq
no-resolv
no-hosts
dhcp-authoritative
dhcp-range=10.42.0.10,10.42.0.254,255.255.255.0,1h
dhcp-option=3,${AP_ADDRESS}
dhcp-option=6,${AP_ADDRESS}
dhcp-leasefile=${LEASES}/dnsmasq.leases
address=/#/${AP_ADDRESS}
local=/#/
log-dhcp
EOF
dnsmasq --test --conf-file="${CONF}/dnsmasq.conf"

tee "${UNITS}/bme688-ap.service" >/dev/null <<EOF
[Unit]
Description=BME688 access point
Wants=NetworkManager.service bme688-ap-dns.service
After=NetworkManager.service
StartLimitIntervalSec=0

[Service]
Type=simple
RuntimeDirectory=bme688-hostapd
ExecStartPre=/bin/bash ${WORKER} --prepare
ExecStart=/usr/sbin/hostapd ${CONF}/hostapd.conf
ExecStopPost=/bin/bash ${WORKER} --release
Restart=on-failure
RestartSec=3
TimeoutStartSec=30
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF

tee "${UNITS}/bme688-ap-dns.service" >/dev/null <<EOF
[Unit]
Description=BME688 access point DHCP and DNS
After=bme688-ap.service
PartOf=bme688-ap.service
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --pid-file= --conf-file=${CONF}/dnsmasq.conf
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
EOF

systemctl daemon-reload
touch "${STATE}/pending"

# both tasks run under systemd, so closing SSH cannot interrupt them
if ! systemd-run --unit=bme688-ap-rollback --collect --on-active=300s \
    --timer-property=AccuracySec=1s --property=Restart=on-failure --property=RestartSec=10s \
    /bin/bash "${STATE}/worker.sh" --rollback; then
    rm -f "${STATE}/pending"
    echo "Could not arm rollback. The existing network is still active."
    exit 1
fi

echo "Wi-Fi: ${DEVICE_HOSTNAME}"
echo "Password: ${AP_PASSWORD}"
echo "Page: http://${DEVICE_HOSTNAME}.local or http://${AP_ADDRESS}"
echo "Reconnect within five minutes and run: bash setup_ap.sh --confirm"
echo "Without confirmation, the previous network returns. Do not reboot during the test."

systemd-run --unit=bme688-ap-activate --collect --on-active=5s \
    --timer-property=AccuracySec=1s /bin/bash "${STATE}/worker.sh" --activate
