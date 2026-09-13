# BME688

Setup for one Raspberry Pi.

## Device names

Use the same number everywhere:

- unit 1: `bme688-01`
- unit 2: `bme688-02`
- unit 3: `bme688-03`

Keep increasing the number for new units.

## 1. Copy the project

The Pi needs internet for this step.

```bash
git clone https://github.com/hiken056/Bme688.git
cd Bme688
```

## 2. Install the backend

Run this as the normal Pi user. Change `01` for each unit.

```bash
UNIT_ID=01 bash setup_backend_service.sh
sudo reboot
```

After reconnecting:

```bash
systemctl status bme688.service --no-pager
sudo i2cdetect -y 1
```

The I2C scan should show `20`.

## 3. Start the access point

Run this after the backend works, while connected through the Pi's current Wi-Fi.
Keep internet available for any missing dependencies.
The setup uses hostapd with WPA2/AES on 2.4 GHz and dnsmasq for local addresses.
Starting the access point disconnects the current Wi-Fi and SSH session.

```bash
bash setup_ap.sh
```

Connect from a device:

- Wi-Fi: `bme688-01`
- Password: `utcnbme688`
- Page: `http://bme688-01.local`
- Fallback: `http://10.42.0.1`

Every unit uses the same password. Change `01` to the unit number when reconnecting.
The SSH examples use the Pi user `automatica`; replace it if yours is different.

Within five minutes, reconnect and confirm:

```bash
ssh automatica@bme688-01.local
cd ~/Bme688
bash setup_ap.sh --confirm
```

Wait for `Access point saved` before rebooting. Without confirmation, the setup
attempts to restore the previous Wi-Fi connection after five minutes.
Do not reboot during the test.

The access point provides access to the Pi, not an internet connection.

## 4. Check after reboot

After confirming:

```bash
sudo reboot
```

Reconnect your device to `bme688-01` with `utcnbme688` and open
`http://bme688-01.local` or `http://10.42.0.1`.
The access point should start automatically. No setup or confirmation is needed
on later boots.

## Disable everything

```bash
bash disable_all.sh
```

This stops the backend, sensor and access point. Saved configuration and measurements stay on the Pi.
