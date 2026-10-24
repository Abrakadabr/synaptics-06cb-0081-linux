# Synaptics 06cb:0081 Linux Biometric Driver & PAM Integration

[![Container CI](https://github.com/Abrakadabr/synaptics-06cb-0081-linux/actions/workflows/container.yml/badge.svg)](https://github.com/Abrakadabr/synaptics-06cb-0081-linux/actions/workflows/container.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Hardware: 06cb:0081](https://img.shields.io/badge/USB-06cb%3A0081-blue.svg)](https://linux-hardware.org/?id=usb:06cb-0081)
[![Platform: Linux](https://img.shields.io/badge/Platform-Fedora%20%7C%20Ubuntu%20%7C%20Debian%20%7C%20Arch-lightgrey.svg)]()

Turnkey Linux biometric authentication and enrollment solution for the proprietary **Synaptics / Validity Sensors WBDI (`06cb:0081`) Match-on-Chip** fingerprint reader.

---

## Highlights

- 👥 **Multi-User Isolation:** Enrolls and tags prints per Linux account (using Windows SID RIDs) with multi-finger slots (1–10). Cross-user authentication attempts are strictly rejected.
- 🛡️ **Zero-Crash UMDF Emulation:** Includes a binary patch for Lenovo driver v5.5.2731.1050 that eliminates NULL pointer adapter crashes under Wine.
- 🪟 **PAM & Polkit Integration:** Out-of-the-box support for `sudo` CLI authentication, Polkit GUI elevation dialogs, and **KDE Plasma 6 Wayland** lock screen unlock, with instant, automatic fallback to password authentication.
- 🔄 **On-Demand Runtime:** Verification runs in an isolated container only when triggered; zero persistent background daemons consuming CPU, memory, or battery.
- ⚖️ **100% Legally Compliant:** Zero proprietary vendor DLLs are distributed in this repository. Drivers are fetched directly from Lenovo OEM servers and patched locally.

> [!WARNING]
> ### Dual-Boot & Windows Hello Notice
> Enrolling fingers under Linux establishes cryptographic host pairing within the sensor's internal flash memory. This pairing overwrites existing Windows Hello enrollments, which will need to be re-enrolled if returning to Windows.

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
|  [ sudo / KDE lock screen / Polkit ] ---> PAM (/etc/pam.d/sudo, /etc/pam.d/kde)   |
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
            v  /dev/bus/usb/001/xxx (MODE 0666, TAG uaccess)
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
1. Verify `06cb:0081` hardware presence via `lsusb`.
2. Check and install system prerequisites (`podman`, `python3`, `usbutils`, `zstd`) across Fedora, Ubuntu, Debian, or Arch Linux.
3. Set up `/opt/synaptics-bio` structure and secure configuration permissions (`0755` directory, `0644` configs).
4. Download the official Lenovo OEM driver (`huy103af07m6.exe`), extract required DLLs, verify SHA-256 checksums, and apply the Wine UMDF crash patch.
5. Configure shared multi-user static container storage in `/usr/share/containers/storage` (`localhost/wine:validity`).
6. Install udev permissions (`/etc/udev/rules.d/70-synaptics-bio.rules`), CLI helper tools to `/usr/local/bin/`, systemd-tmpfiles rule (`/etc/tmpfiles.d/synaptics-bio.conf`), and sleep/resume hooks.
7. Configure PAM authentication stack for `sudo`, KDE Plasma lock screen, and Polkit (`/etc/pam.d/polkit-1`) with automatic `.orig-bio` backups, and apply KDE lockscreen UI responsiveness enhancements if detected.

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

## Managing & Deleting Fingerprints

### List Enrolled Fingerprints
List all registered users, their on-chip SID mappings, and enrolled finger slots:
```bash
synaptics-bio-delete --list
```
Example output:
```text
==========================================================
   Enrolled Synaptics Fingerprint Biometric Profiles
==========================================================
User: alex (UID: 1000)
  Enrolled finger slots : #1
  Hardware SID labels   : alex
----------------------------------------------------------
User: kate (UID: 1001)
  Enrolled finger slots : #1, #2
  Hardware SID labels   : kate
----------------------------------------------------------
```

### Delete a Specific Finger Slot
To delete a single finger template from the sensor's secure chip storage:
```bash
sudo synaptics-bio-delete <username> <slot 1-10>

# Example: Delete finger slot #2 for user 'kate'
sudo synaptics-bio-delete kate 2
```

### Unenroll a User (Purge All Templates)
To completely unenroll a user and remove all their fingerprint templates from the sensor:
```bash
sudo synaptics-bio-delete <username> --all
# Or using the unenroll command:
sudo synaptics-bio-delete unenroll <username>

# Example:
sudo synaptics-bio-delete kate --all
```

> **Note:** To bypass interactive confirmation prompts in automated scripts, pass `--force` (or `-f`).

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

### Test KDE GUI Elevation (Polkit Popup)
```bash
pkexec whoami
```
The native KDE Plasma elevation popup will appear. Touch the sensor to authenticate and the dialog will automatically validate and close as `root`.

---

## Multi-User Configuration (`identities.json`)

User authorizations and enrolled finger slots are defined in `/opt/synaptics-bio/identities.json`:

```json
{
    "alex": [
        "alex"
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
- If a user attempts to authenticate while another user's enrolled finger is touched, access is immediately rejected:
  ```text
  [Fingerprint] Scanned finger belongs to 'kate', not 'alex'.
  ```

### Static System Image Store (`/usr/share/containers/storage`)

When desktop users (e.g., `kate` or `alex` under KDE Plasma lock screen) authenticate, PAM executes within their unprivileged user session. To avoid duplicating the 1.37 GB Wine container image across individual user accounts (`~/.local/share/containers/storage`), the installer configures a static system image store:

1. **Dedicated Read-Only Store:** The image is installed once into `/usr/share/containers/storage` with global read/execute permissions (`a+rX`).
2. **System-Wide Drop-in:** `/etc/containers/storage.conf.d/99-synaptics-bio.conf` declares:
   ```toml
   [storage.options]
   additionalimagestores = [
       "/usr/share/containers/storage"
   ]
   ```
3. **Lock & Collision Free:** Because `/usr/share/containers/storage` is separate from root's active graphroot (`/var/lib/containers/storage`), root never creates volatile layers or runtime lock files there. All users (`alex`, `kate`, and future accounts) share the single container image read-only with zero disk overhead and zero lock collisions.

---

## Useful Commands & Tips

### Temporary Sensor Bypass
To temporarily skip the fingerprint prompt and go straight to password:

```bash
# Per-user bypass (only affects your current desktop user session):
touch "/run/user/$(id -u)/disable-bio-auth"

# System-wide global bypass (requires sudo, affects all users):
sudo touch /run/synaptics-bio/disable-bio-auth
```

To re-enable fingerprint authentication:
```bash
rm -f "/run/user/$(id -u)/disable-bio-auth"
sudo rm -f /run/synaptics-bio/disable-bio-auth
```

### View Real-Time Authentication Logs
```bash
# Sudo & administrative elevation logs:
sudo tail -f /var/log/synaptics-bio-auth.log

# Desktop lock screen session logs:
tail -f "/run/user/$(id -u)/synaptics-bio-auth.log"
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
This restores original PAM files from `.orig-bio` backups, removes udev rules, systemd-tmpfiles rules, sleep hooks, and CLI tools, reverts KDE lockscreen QML modifications, and backs up `/opt/synaptics-bio` keys and calibration data to `/opt/synaptics-bio.backup`.

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
