# Synaptics 06cb:0081 Linux Biometric Driver & PAM Integration

[![Container CI](https://github.com/Abrakadabr/synaptics-06cb-0081-linux/actions/workflows/container.yml/badge.svg)](https://github.com/Abrakadabr/synaptics-06cb-0081-linux/actions/workflows/container.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Hardware: 06cb:0081](https://img.shields.io/badge/USB-06cb%3A0081-blue.svg)](https://linux-hardware.org/?id=usb:06cb-0081)
[![Platform: Linux](https://img.shields.io/badge/Platform-Fedora%20%7C%20Ubuntu%20%7C%20Debian%20%7C%20Arch-lightgrey.svg)]()

Turnkey Linux biometric authentication and enrollment solution for the proprietary **Synaptics / Validity Sensors WBDI (`06cb:0081`) Match-on-Chip** fingerprint reader.

---

## Highlights

- ⚡ **Fast & Responsive:** Verification completes in **~2.5 seconds** (initialization sleeps eliminated).
- 🔒 **Dual-Boot Safe:** Operates alongside Windows 10/11 without firmware re-flashes or factory resets; preserves the sensor's existing hardware-enrolled database.
- 👥 **Multi-User Isolation:** Enrolls and tags prints per Linux account (using Windows SID RIDs) with multi-finger slots (1–10). Cross-user authentication attempts are strictly rejected.
- 🛡️ **Zero-Crash UMDF Emulation:** Includes a binary patch for Lenovo driver v5.5.2731.1050 that eliminates NULL pointer adapter crashes under Wine.
- 🪟 **PAM Integration:** Out-of-the-box support for `sudo` CLI authentication and **KDE Plasma 6 Wayland** lock screen unlock, with instant, automatic fallback to password authentication.
- ⚖️ **100% Legally Compliant:** Zero proprietary vendor DLLs are distributed in this repository. Drivers are fetched directly from Lenovo OEM servers and patched locally.

---

## Hardware Compatibility

Confirmed working on laptops equipped with the Synaptics WBDI USB ID `06cb:0081`:
- **Lenovo IdeaPad / Flex Pro:** Flex Pro-13IKB, Yoga 920, IdeaPad 720S
- **Lenovo ThinkPad:** ThinkPad L470, L570, T470, T570
- Any x86-64 Linux system reporting `06cb:0081` in `lsusb`.

---

## Architecture Overview

```
+-----------------------------------------------------------------------------------+
| Linux Userland                                                                    |
|                                                                                   |
|  [ sudo / KDE lock screen ] ---> PAM (/etc/pam.d/sudo, /etc/pam.d/kde)            |
|                                            |                                      |
|                                      pam_exec.so                                  |
|                                            v                                      |
|                            /usr/local/bin/synaptics-bio-auth                      |
|                                            |                                      |
|                                       podman run                                  |
|                                            v                                      |
+--------------------------------------------+--------------------------------------+
| Headless Container Layer (ghcr.io / localhost/wine:validity)                      |
|                                                                                   |
|   wine64 identify.exe identify                                                    |
|           |                                                                       |
|           v                                                                       |
|   synawudfbiousb.dll (Patched Lenovo 5.5.2731.1050 Driver)                        |
|   synaAdvAdapter.dll (Advanced Biometric Adapter)                                 |
|   CalibrationData.blob                                                            |
|           |                                                                       |
|           v                                                                       |
|   winusb.sys / libusb passthrough                                                 |
+-----------+-----------------------------------------------------------------------+
            |
            v  /dev/bus/usb/001/xxx (MODE 0666)
+-----------------------------------------------------------------------------------+
| Physical Silicon (Synaptics 06cb:0081 Match-on-Chip)                              |
|                                                                                   |
|   - Hardware TLS 1.2 ECDHE crypto coprocessor                                     |
|   - Secure on-chip minutiae template storage                                      |
|   - Hardware Match-on-Chip evaluation & SID emission                              |
+-----------------------------------------------------------------------------------+
```

---

## Quickstart (Installation)

### 1. Clone the Repository
```bash
git clone https://github.com/Abrakadabr/synaptics-06cb-0081-linux.git
cd synaptics-06cb-0081-linux
```

### 2. Run the Installer
```bash
sudo bash install.sh
```

The installer will automatically:
1. Verify `06cb:0081` hardware presence.
2. Download the official Lenovo driver (`huy103af07m6.exe`), extract required DLLs, and apply the Wine UMDF crash patch.
3. Pull or load the Wine UMDF runtime container image.
4. Install udev permissions (`/etc/udev/rules.d/70-synaptics-bio.rules`) and disable USB autosuspend.
5. Install CLI helper tools to `/usr/local/bin/`.
6. Configure PAM for `sudo` and KDE lock screen (with automatic backups).

---

## Enrolling Fingers

### Enroll Your Current User
```bash
sudo synaptics-bio-enroll
```

### Enroll Another User or Specific Finger Slot
```bash
sudo synaptics-bio-enroll <username> [slot 1-10]

# Examples:
sudo synaptics-bio-enroll kate 1    # Enroll Kate's primary finger (Slot 1)
sudo synaptics-bio-enroll kate 2    # Enroll Kate's secondary finger (Slot 2)
```

**Instructions during enrollment:**
1. Wait for `[Fingerprint Enrollment] Touch sensor with finger to register...`
2. Touch the sensor firmly, centered.
3. When `Sample accepted. Lift finger` appears, lift your finger and touch again.
4. Repeat 6–8 times until the template commits to on-chip flash memory.

---

## Verifying Authentication

Test `sudo` biometric authentication:
```bash
sudo -k && sudo whoami
```
Touch the sensor when prompted:
```text
[Fingerprint Auth] >>> Place enrolled finger to authenticate as 'alex' <<<
[Fingerprint] Contact detected, scanning...
[Fingerprint verified for alex]
root
```

If you do not touch the sensor or cancel, authentication falls back to your regular Linux password automatically.

---

## Multi-User Configuration (`identities.json`)

User authorizations are defined in `/opt/synaptics-bio/identities.json`:

```json
{
    "alex": [
        "alex",
        "Unicorn"
    ],
    "kate": [
        "kate"
    ],
    "_metadata": {
        "alex": { "enrolled_fingers": [1, 2] },
        "kate": { "enrolled_fingers": [1, 2] }
    }
}
```

- Each Linux user maps to one or more allowed on-chip identity labels.
- If a user tries to authenticate while another user's enrolled finger is touched, access is denied:
  ```text
  [Fingerprint] Scanned finger belongs to 'kate', not 'alex'.
  ```

---

## Useful Commands & Tips

### Temporary Sensor Bypass
To temporarily skip fingerprint prompt and go straight to password:
```bash
touch /tmp/disable-bio-auth
```
To re-enable:
```bash
rm -f /tmp/disable-bio-auth
```

### View Real-Time Authentication Logs
```bash
tail -f /tmp/synaptics-bio-auth.log
```

### Sensor Manual Reset (Hardware Recovery)
```bash
sudo usbreset 06cb:0081
```

---

## Uninstallation

To restore your system completely to its original state:
```bash
sudo bash uninstall.sh
```
This restores original PAM files from `.orig-bio` backups, removes udev rules, and deletes helper scripts.

---

## Technical Documentation

For in-depth analysis of the Match-on-Chip reverse engineering, USB packets, IOCTL structures, and assembly patches, see:
📖 [docs/reverse-engineering.md](docs/reverse-engineering.md)

---

## Acknowledgments & Credits

- **Viktor Dragomiretskyy ([@uunicorn](https://github.com/uunicorn)):** Author of `synaWudfBioUsb-sandbox` and the hacked Wine WinUSB stub driver.
- **Validity90 Project Contributors:** Protocol research on Synaptics / Validity hardware.
- **Linux Biometric Community:** For reverse-engineering modern Match-on-Chip sensors.

---

## License

Distributed under the [MIT License](LICENSE).
Proprietary driver components are copyright © Lenovo Group Limited and Synaptics Incorporated and are extracted locally by the user.
