# PAM Biometric Authentication Integration Guide

This directory contains configuration snippets and documentation for integrating the Synaptics 06cb:0081 Match-on-Sensor biometric helper (`synaptics-bio-auth`) into the Linux Pluggable Authentication Modules (PAM) stack.

---

## 1. How PAM Integration Operates

The script `/usr/local/bin/synaptics-bio-auth` is registered as a PAM module using `pam_exec.so`:

```pam
auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth
```

### Control Flag Semantics (`sufficient`):
- **Success (`exit 0`):** If an enrolled finger matching the authenticating user is recognized, `synaptics-bio-auth` returns exit code `0`. PAM immediately marks the authentication module as successful and skips subsequent password prompts.
- **Failure / Timeout / Fallback (`exit 1`):** If no finger is touched within 30 seconds, if the sensor is cancelled, or if an unrecognized finger is scanned, `synaptics-bio-auth` returns exit code `1`. Because the control flag is `sufficient` (not `required`), PAM does **not** abort authentication; instead, it gracefully proceeds to the next authentication module (`system-auth` or `password-auth`), prompting the user for their normal password.

### Parameters:
- `quiet`: Suppresses informational noise generated directly by the `pam_exec` C module itself.
- `stdout`: Allows `synaptics-bio-auth` to output human-readable touch prompts (`[Fingerprint Auth] >>> Place enrolled finger ...`) directly to the active terminal or display manager prompt.

---

## 2. Target Services

### A. CLI Authentication (`/etc/pam.d/sudo`)
Place the rule right before the default system authentication include:
```pam
#%PAM-1.0
auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth
auth       include      system-auth
account    include      system-auth
password   include      system-auth
session    optional     pam_keyinit.so revoke
session    required     pam_limits.so
session    include      system-auth
```

### B. KDE Plasma Lock Screen (`/etc/pam.d/kde`)
Place the rule right before `password-auth` (use `--lockscreen` to present a single clean prompt when the sensor is armed, avoiding startup noise or multi-line stacking):
```pam
#%PAM-1.0
auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth --lockscreen
auth       substack     password-auth
auth       optional     pam_kwallet5.so
auth       optional     pam_kwallet.so
account    include      password-auth
password   include      password-auth
session    include      password-auth
```

### C. KDE GUI Password Popups / Polkit (`/etc/pam.d/polkit-1`)
Place the rule in `/etc/pam.d/polkit-1` to authenticate GUI elevation dialogs (e.g. Partition Manager, Discover, `pkexec`):
```pam
#%PAM-1.0
auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth --compact
auth       include      system-auth
account    include      system-auth
password   include      system-auth
session    include      system-auth
```

### D. Display Managers (SDDM / GDM)
For graphical login managers, add the same snippet to `/etc/pam.d/sddm` or `/etc/pam.d/gdm-password`.
Note: Biometric login on fresh boot will authenticate the user, but does not unlock the user's GNOME Keyring or KDE KWallet (which require the cleartext user password).

---

## 3. Temporary Sensor Bypass

If you want to skip fingerprint authentication and go straight to password input:

```bash
# Per-user bypass (only affects your desktop user session):
touch "/run/user/$(id -u)/disable-bio-auth"

# System-wide global bypass (requires sudo, affects all users):
sudo touch /run/synaptics-bio/disable-bio-auth
```

To re-enable:
```bash
rm -f "/run/user/$(id -u)/disable-bio-auth"
sudo rm -f /run/synaptics-bio/disable-bio-auth
```
