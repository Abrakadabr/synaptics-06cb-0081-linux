#!/usr/bin/env bash
# uninstall.sh
# System uninstaller for Synaptics 06cb:0081 Biometric Support

set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "Error: This uninstaller requires root privileges. Please run with sudo:" >&2
    echo "  sudo bash $0" >&2
    exit 1
fi

echo "=========================================================="
echo "   Uninstalling Synaptics 06cb:0081 Biometric Support"
echo "=========================================================="

# 1. Restore PAM sudo
PAM_SUDO="/etc/pam.d/sudo"
if [ -f "${PAM_SUDO}" ]; then
    if [ -f "${PAM_SUDO}.orig-bio" ]; then
        mv "${PAM_SUDO}.orig-bio" "${PAM_SUDO}"
        echo "Restored ${PAM_SUDO} from original backup."
    else
        sed -i '/synaptics-bio-auth/d' "${PAM_SUDO}"
        echo "Removed synaptics-bio-auth rule from ${PAM_SUDO}."
    fi
fi

# 2. Restore PAM KDE
PAM_KDE="/etc/pam.d/kde"
if [ -f "${PAM_KDE}" ]; then
    if [ -f "${PAM_KDE}.orig-bio" ]; then
        mv "${PAM_KDE}.orig-bio" "${PAM_KDE}"
        echo "Restored ${PAM_KDE} from original backup."
    else
        sed -i '/synaptics-bio-auth/d' "${PAM_KDE}"
        echo "Removed synaptics-bio-auth rule from ${PAM_KDE}."
    fi
fi

# 3. Restore / Remove PAM Polkit-1
PAM_POLKIT="/etc/pam.d/polkit-1"
if [ -f "${PAM_POLKIT}" ]; then
    if [ -f "${PAM_POLKIT}.orig-bio" ]; then
        mv "${PAM_POLKIT}.orig-bio" "${PAM_POLKIT}"
        echo "Restored ${PAM_POLKIT} from original backup."
    else
        rm -f "${PAM_POLKIT}"
        echo "Removed custom ${PAM_POLKIT}."
    fi
fi

# 4. Remove Udev Rule
if [ -f "/etc/udev/rules.d/70-synaptics-bio.rules" ]; then
    rm -f "/etc/udev/rules.d/70-synaptics-bio.rules"
    udevadm control --reload-rules
    udevadm trigger
    echo "Removed /etc/udev/rules.d/70-synaptics-bio.rules."
fi

# 5. Remove Multi-User Shared Container Storage Drop-in
rm -f "/etc/containers/storage.conf.d/99-synaptics-bio.conf"
rm -f "/etc/containers/storage.rootless.conf.d/99-synaptics-bio.conf"
echo "Removed multi-user container storage drop-ins."

# 6. Remove CLI wrappers
rm -f /usr/local/bin/synaptics-bio-auth
rm -f /usr/local/bin/synaptics-bio-enroll
rm -f /usr/local/bin/synaptics-bio-delete
echo "Removed /usr/local/bin/synaptics-bio-auth, synaptics-bio-enroll, and synaptics-bio-delete."

# 7. Clean /opt/synaptics-bio
OPT_DIR="/opt/synaptics-bio"
if [ -d "${OPT_DIR}" ]; then
    echo "Preserving enrolled database and calibration data in ${OPT_DIR}.backup..."
    mkdir -p "${OPT_DIR}.backup"
    cp -p "${OPT_DIR}"/CalibrationData.blob "${OPT_DIR}"/identities.json "${OPT_DIR}.backup/" 2>/dev/null || true
    rm -rf "${OPT_DIR}"
    echo "Removed ${OPT_DIR} (backup of keys saved in ${OPT_DIR}.backup)."
fi

echo "=========================================================="
echo "Uninstallation complete. System restored to default."
echo "=========================================================="
