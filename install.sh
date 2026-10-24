#!/usr/bin/env bash
# install.sh
# System-wide installer for Synaptics 06cb:0081 Biometric Support on Linux (Fedora, Debian, Ubuntu, etc.)

set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "Error: This installer requires root privileges. Please run with sudo:" >&2
    echo "  sudo bash $0" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPT_DIR="/opt/synaptics-bio"
IMAGE_NAME="localhost/wine:validity"
GHCR_IMAGE="ghcr.io/abrakadabr/synaptics-06cb-0081-linux:latest"

echo "=========================================================="
echo "   Synaptics 06cb:0081 Linux Biometric Driver Setup"
echo "=========================================================="

# 1. Hardware Detection
echo "[1/7] Detecting Synaptics 06cb:0081 USB sensor..."
if ! lsusb | grep -qi -E "06cb:0081|Synaptics.*0081"; then
    echo "Warning: Synaptics 06cb:0081 USB sensor was not found in 'lsusb'."
    echo "Continuing anyway in case the device is temporarily powered off..."
else
    echo "Found sensor: $(lsusb | grep -i -E '06cb:0081|Synaptics.*0081')"
fi

# 2. Prerequisites Check
echo "[2/7] Checking system dependencies..."
MISSING_PKGS=()
for cmd in podman python3 lsusb; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        MISSING_PKGS+=("$cmd")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "Missing required utilities: ${MISSING_PKGS[*]}"
    if command -v dnf >/dev/null 2>&1; then
        echo "Installing missing packages with DNF..."
        dnf install -y podman python3 usbutils zstd
    elif command -v apt-get >/dev/null 2>&1; then
        echo "Installing missing packages with APT..."
        apt-get update && apt-get install -y podman python3 usbutils zstd
    else
        echo "Please install ${MISSING_PKGS[*]} using your distribution package manager." >&2
        exit 1
    fi
fi

# 3. Setup /opt/synaptics-bio
echo "[3/7] Setting up ${OPT_DIR}..."
mkdir -p "${OPT_DIR}"

# Copy harness binaries
cp -p "${SCRIPT_DIR}/src/identify.exe" "${OPT_DIR}/identify.exe"
cp -p "${SCRIPT_DIR}/src/enroll.exe" "${OPT_DIR}/enroll.exe"

# Copy configurations
cp -p "${SCRIPT_DIR}/config/usb.txt" "${OPT_DIR}/usb.txt"
cp -p "${SCRIPT_DIR}/config/.env" "${OPT_DIR}/.env"

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
chmod 666 "${OPT_DIR}/usb.txt" "${OPT_DIR}/.env" 2>/dev/null || true
[ -f "${OPT_DIR}/identities.json" ] && chmod 644 "${OPT_DIR}/identities.json"
[ -f "${OPT_DIR}/CalibrationData.blob" ] && chmod 644 "${OPT_DIR}/CalibrationData.blob"
chmod 755 "${OPT_DIR}"/*.dll "${OPT_DIR}"/*.exe 2>/dev/null || true

# 5. Container Image Setup
echo "[5/7] Verifying container execution environment..."
IMAGE_READY=false
if podman image exists "${IMAGE_NAME}" 2>/dev/null || podman image exists "wine:validity" 2>/dev/null; then
    echo "Found local Wine runtime container image."
    podman tag localhost/wine:validity wine:validity 2>/dev/null || true
    IMAGE_READY=true
fi

if [ "$IMAGE_READY" = false ]; then
    # Check for local compressed backup
    if [ -f "${SCRIPT_DIR}/wine-validity.tar.zst" ]; then
        echo "Loading ${SCRIPT_DIR}/wine-validity.tar.zst into Podman storage..."
        zstdcat "${SCRIPT_DIR}/wine-validity.tar.zst" | podman load
        IMAGE_READY=true
    else
        echo "Attempting to pull pre-built container from GitHub Container Registry (${GHCR_IMAGE})..."
        if podman pull "${GHCR_IMAGE}" 2>/dev/null; then
            podman tag "${GHCR_IMAGE}" "${IMAGE_NAME}"
            podman tag "${GHCR_IMAGE}" wine:validity
            IMAGE_READY=true
        else
            echo "Note: Container registry image not yet published. Building container locally..."
            podman build -t "${IMAGE_NAME}" -f "${SCRIPT_DIR}/container/Containerfile" "${SCRIPT_DIR}"
            podman tag "${IMAGE_NAME}" wine:validity
            IMAGE_READY=true
        fi
    fi
fi

# Configure multi-user shared container storage for rootless sessions (Rule A compliant)
# Note: Must be placed in storage.rootless.conf.d to prevent root graphroot self-deadlock
echo "Configuring multi-user shared container storage for rootless users..."
rm -f /etc/containers/storage.conf.d/99-synaptics-bio.conf
mkdir -p /etc/containers/storage.rootless.conf.d
cat << 'EOF' > /etc/containers/storage.rootless.conf.d/99-synaptics-bio.conf
[storage.options]
additionalimagestores = [
    "/var/lib/containers/storage"
]
EOF
chmod 644 /etc/containers/storage.rootless.conf.d/99-synaptics-bio.conf
chmod -R +rX /var/lib/containers/storage 2>/dev/null || true

# 6. Install Udev Rule and CLI Tools
echo "[6/7] Installing Udev rules and CLI tools..."
rm -f /etc/udev/rules.d/99-synaptics-fingerprint.rules
cp "${SCRIPT_DIR}/udev/70-synaptics-bio.rules" /etc/udev/rules.d/70-synaptics-bio.rules
udevadm control --reload-rules
udevadm trigger

cp "${SCRIPT_DIR}/bin/synaptics-bio-auth" /usr/local/bin/synaptics-bio-auth
cp "${SCRIPT_DIR}/bin/synaptics-bio-enroll" /usr/local/bin/synaptics-bio-enroll
cp "${SCRIPT_DIR}/bin/synaptics-bio-delete" /usr/local/bin/synaptics-bio-delete
chmod 755 /usr/local/bin/synaptics-bio-auth /usr/local/bin/synaptics-bio-enroll /usr/local/bin/synaptics-bio-delete

# 7. Configure PAM (sudo & KDE lock screen)
echo "[7/7] Configuring PAM authentication stack..."
PAM_SUDO="/etc/pam.d/sudo"
if [ -f "${PAM_SUDO}" ]; then
    if ! grep -q "synaptics-bio-auth" "${PAM_SUDO}"; then
        cp "${PAM_SUDO}" "${PAM_SUDO}.orig-bio"
        sed -i '/auth[[:space:]]\+include[[:space:]]\+system-auth/i auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth' "${PAM_SUDO}"
        echo "Configured ${PAM_SUDO} (backup: ${PAM_SUDO}.orig-bio)."
    else
        echo "${PAM_SUDO} is already configured."
    fi
fi

PAM_KDE="/etc/pam.d/kde"
if [ -f "${PAM_KDE}" ]; then
    if ! grep -q "synaptics-bio-auth" "${PAM_KDE}"; then
        cp "${PAM_KDE}" "${PAM_KDE}.orig-bio"
        sed -i '/auth[[:space:]]\+substack[[:space:]]\+password-auth/i auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth --lockscreen' "${PAM_KDE}"
        echo "Configured ${PAM_KDE} (backup: ${PAM_KDE}.orig-bio)."
    else
        sed -i 's|/usr/local/bin/synaptics-bio-auth.*|/usr/local/bin/synaptics-bio-auth --lockscreen|' "${PAM_KDE}"
        echo "${PAM_KDE} is already configured."
    fi
fi

PAM_POLKIT="/etc/pam.d/polkit-1"
if [ ! -f "${PAM_POLKIT}" ]; then
    cp "${SCRIPT_DIR}/pam/polkit-1.snippet" "${PAM_POLKIT}"
    chmod 644 "${PAM_POLKIT}"
    echo "Configured ${PAM_POLKIT} (KDE GUI authentication popups)."
elif ! grep -q "synaptics-bio-auth" "${PAM_POLKIT}"; then
    cp "${PAM_POLKIT}" "${PAM_POLKIT}.orig-bio"
    sed -i '/auth[[:space:]]\+include[[:space:]]\+system-auth/i auth       sufficient   pam_exec.so quiet stdout /usr/local/bin/synaptics-bio-auth' "${PAM_POLKIT}"
    echo "Configured ${PAM_POLKIT} (backup: ${PAM_POLKIT}.orig-bio)."
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
