#!/usr/bin/env bash
# install.sh
# System-wide installer for Synaptics 06cb:0081 Biometric Support on Linux (Fedora, Debian, Ubuntu, Arch)

set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "Error: This installer requires root privileges. Please run with sudo:" >&2
    echo "  sudo bash $0" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPT_DIR="/opt/synaptics-bio"

# Insert a PAM auth rule before the first matching system auth line and verify the edit succeeded.
insert_pam_auth_rule() {
    local pam_file="$1"
    local rule="$2"

    if [ ! -f "${pam_file}" ]; then
        return 1
    fi

    if grep -q "synaptics-bio-auth" "${pam_file}"; then
        return 0
    fi

    if [ ! -f "${pam_file}.orig-bio" ]; then
        cp -p "${pam_file}" "${pam_file}.orig-bio"
    fi

    if grep -qE '^[[:space:]]*auth[[:space:]]+(include|substack)[[:space:]]+system-auth' "${pam_file}"; then
        sed -i -E "0,/^[[:space:]]*auth[[:space:]]+(include|substack)[[:space:]]+system-auth/s||${rule}\n&|" "${pam_file}"
    elif grep -qE '^[[:space:]]*auth[[:space:]]+(include|substack)[[:space:]]+password-auth' "${pam_file}"; then
        sed -i -E "0,/^[[:space:]]*auth[[:space:]]+(include|substack)[[:space:]]+password-auth/s||${rule}\n&|" "${pam_file}"
    elif grep -qE '^[[:space:]]*@?include[[:space:]]+common-auth' "${pam_file}"; then
        sed -i -E "0,/^[[:space:]]*@?include[[:space:]]+common-auth/s||${rule}\n&|" "${pam_file}"
    elif grep -qE '^[[:space:]]*auth[[:space:]]+(include|substack)[[:space:]]+system-login' "${pam_file}"; then
        sed -i -E "0,/^[[:space:]]*auth[[:space:]]+(include|substack)[[:space:]]+system-login/s||${rule}\n&|" "${pam_file}"
    elif grep -qE '^[[:space:]]*auth[[:space:]]+' "${pam_file}"; then
        sed -i -E "0,/^[[:space:]]*auth[[:space:]]+/s||${rule}\n&|" "${pam_file}"
    else
        echo "Warning: Could not find an auth rule anchor in ${pam_file}; restoring backup." >&2
        mv "${pam_file}.orig-bio" "${pam_file}"
        return 1
    fi

    if ! grep -q "synaptics-bio-auth" "${pam_file}"; then
        echo "Warning: Failed to inject biometric rule into ${pam_file}; restoring backup." >&2
        mv "${pam_file}.orig-bio" "${pam_file}"
        return 1
    fi
    return 0
}

echo "=========================================================="
echo "   Synaptics 06cb:0081 Linux Biometric Driver Setup"
echo "=========================================================="

# 1. Hardware Detection
echo "[1/7] Detecting Synaptics 06cb:0081 USB sensor..."
if ! lsusb | grep -qi "06cb:0081"; then
    echo "Warning: Synaptics 06cb:0081 USB sensor was not found in 'lsusb'."
    echo "Continuing anyway in case the device is temporarily powered off..."
else
    echo "Found sensor: $(lsusb | grep -i '06cb:0081')"
fi

# 2. Prerequisites Check
echo "[2/7] Checking system dependencies..."
MISSING_PKGS=()
for cmd in podman python3 lsusb usbreset zstd fuse-overlayfs innoextract; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        MISSING_PKGS+=("$cmd")
    fi
done
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    MISSING_PKGS+=("curl")
fi

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "Missing required utilities: ${MISSING_PKGS[*]}"
    if command -v dnf >/dev/null 2>&1; then
        echo "Installing missing packages with DNF..."
        dnf install -y podman python3 usbutils zstd fuse-overlayfs innoextract curl
    elif command -v apt-get >/dev/null 2>&1; then
        echo "Installing missing packages with APT..."
        apt-get update && apt-get install -y podman python3 usbutils zstd fuse-overlayfs innoextract curl
    elif command -v pacman >/dev/null 2>&1; then
        echo "Installing missing packages with Pacman..."
        pacman -S --needed --noconfirm podman python usbutils zstd fuse-overlayfs innoextract curl
    else
        echo "Please install ${MISSING_PKGS[*]} using your distribution package manager." >&2
        exit 1
    fi
fi

# 3. Setup /opt/synaptics-bio
echo "[3/7] Setting up ${OPT_DIR}..."
mkdir -p "${OPT_DIR}"

# Copy harness binaries and shared Python helper library
cp -p "${SCRIPT_DIR}/src/identify.exe" "${OPT_DIR}/identify.exe"
cp -p "${SCRIPT_DIR}/src/enroll.exe" "${OPT_DIR}/enroll.exe"
cp -p "${SCRIPT_DIR}/lib/synaptics_bio_common.py" "${OPT_DIR}/synaptics_bio_common.py"

# Copy configurations
cp -p "${SCRIPT_DIR}/config/usb.txt" "${OPT_DIR}/usb.txt"

if [ ! -f "${OPT_DIR}/identities.json" ]; then
    if [ -f "${SCRIPT_DIR}/config/identities.json.dist" ]; then
        cp -p "${SCRIPT_DIR}/config/identities.json.dist" "${OPT_DIR}/identities.json"
    fi
fi

# Preserve or copy CalibrationData.blob if present in staging
if [ ! -f "${OPT_DIR}/CalibrationData.blob" ]; then
    if [ -f "${SCRIPT_DIR}/CalibrationData.blob" ]; then
        cp -p "${SCRIPT_DIR}/CalibrationData.blob" "${OPT_DIR}/CalibrationData.blob"
    elif [ -f "${SCRIPT_DIR}/data/CalibrationData.blob" ]; then
        cp -p "${SCRIPT_DIR}/data/CalibrationData.blob" "${OPT_DIR}/CalibrationData.blob"
    fi
fi

# 4. Driver Verification and Extraction
echo "[4/7] Checking proprietary driver DLLs..."
if [ ! -f "${OPT_DIR}/synawudfbiousb.dll" ] || [ ! -f "${OPT_DIR}/synaAdvAdapter.dll" ]; then
    echo "Driver DLLs not found in ${OPT_DIR}. Running automated driver fetcher..."
    bash "${SCRIPT_DIR}/scripts/get-driver.sh" "${OPT_DIR}"
else
    echo "Driver DLLs verified in ${OPT_DIR}."
fi

# Secure administrative ownership and permissions
chown -R root:root "${OPT_DIR}"
chmod 755 "${OPT_DIR}"
chmod 644 "${OPT_DIR}/usb.txt" "${OPT_DIR}/synaptics_bio_common.py" 2>/dev/null || true
[ -f "${OPT_DIR}/identities.json" ] && chmod 644 "${OPT_DIR}/identities.json"
[ -f "${OPT_DIR}/CalibrationData.blob" ] && chmod 644 "${OPT_DIR}/CalibrationData.blob"
chmod 755 "${OPT_DIR}"/*.dll "${OPT_DIR}"/*.exe 2>/dev/null || true

# 5. Multi-User Static Image Store Setup (/usr/share/synaptics-bio/containers-storage)
echo "[5/7] Setting up shared static container storage in /usr/share/synaptics-bio/containers-storage..."
bash "${SCRIPT_DIR}/scripts/setup-system-image-store.sh"

# 6. Install Udev Rule, Lock Directory, and CLI Tools
echo "[6/7] Installing Udev rules, sleep hooks, and CLI tools..."
install -o root -g root -m 644 "${SCRIPT_DIR}/udev/70-synaptics-bio.rules" /etc/udev/rules.d/70-synaptics-bio.rules
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --action=change --subsystem-match=usb --attr-match=idVendor=06cb --attr-match=idProduct=0081 2>/dev/null || true

install -o root -g root -m 755 "${SCRIPT_DIR}/bin/synaptics-bio-auth" /usr/local/bin/synaptics-bio-auth
install -o root -g root -m 755 "${SCRIPT_DIR}/bin/synaptics-bio-enroll" /usr/local/bin/synaptics-bio-enroll
install -o root -g root -m 755 "${SCRIPT_DIR}/bin/synaptics-bio-delete" /usr/local/bin/synaptics-bio-delete

# Install systemd tmpfiles configuration to ensure /run lock and runtime paths exist after reboot
mkdir -p /etc/tmpfiles.d
cat << 'EOF' > /etc/tmpfiles.d/synaptics-bio.conf
d /run/synaptics-bio 0755 root root -
f /run/synaptics-bio/lock 0666 root root -
f /run/synaptics-bio/meta 0666 root root -
EOF
chown root:root /etc/tmpfiles.d/synaptics-bio.conf
chmod 644 /etc/tmpfiles.d/synaptics-bio.conf
mkdir -p /run/synaptics-bio
chmod 0755 /run/synaptics-bio
for f in /run/synaptics-bio/lock /run/synaptics-bio/meta; do
    [ -L "$f" ] && rm -f "$f"
    touch "$f"
    chown root:root "$f"
    chmod 0666 "$f"
done

# Install systemd suspend/resume hook to keep USB sensor healthy across sleep
SLEEP_HOOK_DIR="/etc/systemd/system-sleep"
[ ! -d "${SLEEP_HOOK_DIR}" ] && SLEEP_HOOK_DIR="/usr/lib/systemd/system-sleep"
if [ -d "${SLEEP_HOOK_DIR}" ]; then
    install -o root -g root -m 755 "${SCRIPT_DIR}/scripts/synaptics-bio-sleep.sh" "${SLEEP_HOOK_DIR}/synaptics-bio-sleep"
fi

# Configure KDE Plasma lockscreen UI message replacement and keepalive if present
install -o root -g root -m 755 "${SCRIPT_DIR}/scripts/patch-lockscreen.py" /usr/local/bin/synaptics-bio-patch-lockscreen
LOCKSCREEN_QML="/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/LockScreenUi.qml"
if [ -f "${LOCKSCREEN_QML}" ]; then
    /usr/local/bin/synaptics-bio-patch-lockscreen --apply --force "${LOCKSCREEN_QML}" || true
fi
if [ -d /etc/systemd/system ] && [ -d "${SCRIPT_DIR}/systemd" ]; then
    install -o root -g root -m 644 "${SCRIPT_DIR}/systemd/synaptics-bio-lockscreen.service" /etc/systemd/system/synaptics-bio-lockscreen.service
    install -o root -g root -m 644 "${SCRIPT_DIR}/systemd/synaptics-bio-lockscreen.path" /etc/systemd/system/synaptics-bio-lockscreen.path
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --now synaptics-bio-lockscreen.path 2>/dev/null || true
    systemctl enable synaptics-bio-lockscreen.service 2>/dev/null || true
fi

# 7. Configure PAM (sudo, KDE lock screen, Polkit)
echo "[7/7] Configuring PAM authentication stack..."
PAM_SUDO="/etc/pam.d/sudo"
if [ ! -f "${PAM_SUDO}" ] && [ -f "/usr/lib/pam.d/sudo" ]; then
    cp -p "/usr/lib/pam.d/sudo" "${PAM_SUDO}"
    touch "${OPT_DIR}/.created-pam-sudo"
fi
if [ -f "${PAM_SUDO}" ]; then
    if grep -q "synaptics-bio-auth" "${PAM_SUDO}"; then
        echo "${PAM_SUDO} is already configured."
    elif insert_pam_auth_rule "${PAM_SUDO}" "auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth"; then
        if [ -f "${OPT_DIR}/.created-pam-sudo" ]; then
            rm -f "${PAM_SUDO}.orig-bio"
            echo "Configured ${PAM_SUDO} from /usr/lib/pam.d/sudo."
        else
            echo "Configured ${PAM_SUDO} (backup: ${PAM_SUDO}.orig-bio)."
        fi
    fi
fi

PAM_KDE_FP="/etc/pam.d/kde-fingerprint"
if [ ! -f "${PAM_KDE_FP}" ] && [ -f "/usr/lib/pam.d/kde-fingerprint" ]; then
    cp -p "/usr/lib/pam.d/kde-fingerprint" "${PAM_KDE_FP}"
    touch "${OPT_DIR}/.created-pam-kde-fp"
fi

if [ -f "${PAM_KDE_FP}" ]; then
    if [ ! -f "${OPT_DIR}/.created-pam-kde-fp" ] && [ ! -f "${PAM_KDE_FP}.orig-bio" ] && ! grep -q "synaptics-bio-auth" "${PAM_KDE_FP}"; then
        cp -p "${PAM_KDE_FP}" "${PAM_KDE_FP}.orig-bio"
    fi

    # Preserve the distribution's existing account/password/session lines in kde-fingerprint
    # while replacing only the auth stack with non-interactive synaptics-bio-auth --lockscreen.
    SRC_KDE_FP="${PAM_KDE_FP}.orig-bio"
    if [ ! -f "${SRC_KDE_FP}" ]; then
        if [ -f "/usr/lib/pam.d/kde-fingerprint" ] && [ -f "${OPT_DIR}/.created-pam-kde-fp" ]; then
            SRC_KDE_FP="/usr/lib/pam.d/kde-fingerprint"
        else
            SRC_KDE_FP="${PAM_KDE_FP}"
        fi
    fi
    {
        echo "#%PAM-1.0"
        echo "auth        required      pam_env.so"
        echo "auth        [success=1 default=ignore] pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth --lockscreen"
        echo "auth        requisite     pam_deny.so"
        if grep -qE '^[[:space:]]*auth[[:space:]]+include[[:space:]]+postlogin' "${SRC_KDE_FP}"; then
            echo "auth        include       postlogin"
        fi
        grep -vE '^[[:space:]]*(#%PAM|auth[[:space:]]+)' "${SRC_KDE_FP}" || true
    } > "${PAM_KDE_FP}.tmp"
    mv "${PAM_KDE_FP}.tmp" "${PAM_KDE_FP}"
    chmod 644 "${PAM_KDE_FP}"
    echo "Configured ${PAM_KDE_FP}."
fi

PAM_POLKIT="/etc/pam.d/polkit-1"
if [ ! -f "${PAM_POLKIT}" ]; then
    if [ -f "/usr/lib/pam.d/polkit-1" ]; then
        cp -p "/usr/lib/pam.d/polkit-1" "${PAM_POLKIT}"
        touch "${OPT_DIR}/.created-pam-polkit"
        if insert_pam_auth_rule "${PAM_POLKIT}" "auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth --compact"; then
            rm -f "${PAM_POLKIT}.orig-bio"
            echo "Configured ${PAM_POLKIT} from /usr/lib/pam.d/polkit-1 (KDE GUI authentication popups)."
        fi
    elif [ -f "/etc/pam.d/common-auth" ]; then
        cat << 'EOF' > "${PAM_POLKIT}"
#%PAM-1.0
auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth --compact
@include common-auth
@include common-account
@include common-password
@include common-session-noninteractive
EOF
        chmod 644 "${PAM_POLKIT}"
        touch "${OPT_DIR}/.created-pam-polkit"
        echo "Configured ${PAM_POLKIT} (Debian/Ubuntu stack)."
    elif [ -f "/etc/pam.d/system-auth" ]; then
        cp "${SCRIPT_DIR}/pam/polkit-1.snippet" "${PAM_POLKIT}"
        chmod 644 "${PAM_POLKIT}"
        touch "${OPT_DIR}/.created-pam-polkit"
        echo "Configured ${PAM_POLKIT} (system-auth stack)."
    else
        echo "Warning: No existing ${PAM_POLKIT} or base PAM stack found; skipping Polkit PAM setup." >&2
    fi
elif ! grep -q "synaptics-bio-auth" "${PAM_POLKIT}"; then
    if insert_pam_auth_rule "${PAM_POLKIT}" "auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth --compact"; then
        echo "Configured ${PAM_POLKIT} (backup: ${PAM_POLKIT}.orig-bio)."
    fi
else
    echo "${PAM_POLKIT} is already configured."
fi

echo ""
echo "=========================================================="
echo "Installation complete!"
echo ""
echo "Next Steps:"
echo "1. Enroll a finger for your user account:"
echo "   sudo synaptics-bio-enroll <username> [slot 1-10]"
echo ""
echo "2. Test authentication:"
echo "   sudo -k && sudo whoami"
echo "=========================================================="
