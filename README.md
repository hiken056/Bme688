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
git clone <repository-url> bme688
cd bme688
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

Run this after the backend works. It switches `wlan0` to a 2.4 GHz access point and closes the current Wi-Fi and SSH connection.

```bash
bash setup_ap.sh
```

Connect from a phone:

- Wi-Fi: `bme688-01`
- Password: none
- Page: `http://bme688-01.local`
- Fallback: `http://10.42.0.1`

## Disable everything

```bash
bash disable_all.sh
```

This stops the backend, sensor and access point. Saved configuration and measurements stay on the Pi.
