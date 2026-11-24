# Ubuntu 26.04 amd64: fprintd for Synaptics 06cb:0081

An alternative to the repository’s Wine container: build the [synaTudor compatibility driver](https://github.com/theaboutbox/synatudor-06cb-0081) against Ubuntu’s libfprint TOD ABI. Do not run both stacks against the reader. Tested on a Lenovo Yoga 920-13IKB with firmware 6.7 build 164. This is not a native rewrite of the Windows driver.

## Build locally

From this directory, as a regular user:

```sh
sudo apt-get install --no-install-recommends build-essential git curl patch innoextract meson ninja-build pkgconf systemd-dev libudev-dev libusb-1.0-0-dev libgusb-dev libfprint-2-dev libfprint-2-tod-dev libglib2.0-dev libjson-glib-dev libdbus-1-dev libssl-dev libgnutls28-dev libcap-dev libseccomp-dev
work=$(mktemp -d)
git clone https://github.com/theaboutbox/synatudor-06cb-0081.git "$work/driver"
git -C "$work/driver" checkout --detach 56dcd295a5b6841324b4866a92ad22c890f19940
curl -fL https://download.lenovo.com/consumer/mobiles/huy103af07m6.exe -o "$work/lenovo.exe"
./build-deb "$work/driver" "$work/lenovo.exe" "$work/synatudor-0081.deb"
```

The recipe checks the source revision, clean checkout and installer SHA-256 before extraction, runs the upstream Meson tests, and stages a local `.deb`. The installer is never executed. **Do not upload the resulting `.deb`, extracted DLLs, or installer:** the `.deb` embeds proprietary code. The setup patch modifies upstream LGPL-2.1 scripts; the package includes upstream LICENSE and NOTICE.

## Install and verify

```sh
sudo apt install "$work/synatudor-0081.deb"
sudo systemctl daemon-reload
sudo udevadm control --reload
synatudor-setup
```

Run setup as the desktop user. First initialization can replace Windows Hello pairing; keep password access available. Keep the reader uncovered until prompted. If setup reports no device during USB re-enumeration, let it settle and run `fprintd-list "$USER"`. If Synaptics Tudor appears but has no enrolled finger, use `fprintd-enroll -f right-index-finger`, then `fprintd-verify -f right-index-finger`. If it does not appear, inspect `journalctl -b -u fprintd -u tudor-host-launcher` locally rather than resetting ownership. Do not post unredacted logs or sensor state.

For optional GNOME screen unlock after verifying your print, install `libpam-fprintd` and run `sudo gdm-config fingerprint --enable`. Global PAM changes are unnecessary. GNOME Keyring still needs the password at initial login. `sudo apt remove synatudor-0081` removes the package, not the reader’s pairing or enrolled data.
