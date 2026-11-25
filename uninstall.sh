#!/usr/bin/env bash
# uninstall.sh
# System uninstaller for Synaptics 06cb:0081 Biometric Support

set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "Error: This uninstaller requires root privileges. Please run with sudo:" >&2
    echo "  sudo bash $0" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================================="
echo "   Uninstalling Synaptics 06cb:0081 Biometric Support"
echo "=========================================================="

# 1. Restore PAM sudo
PAM_SUDO="/etc/pam.d/sudo"
if [ -f "${PAM_SUDO}" ]; then
    if [ -f "${PAM_SUDO}.orig-bio" ]; then
        mv "${PAM_SUDO}.orig-bio" "${PAM_SUDO}"
        echo "Restored ${PAM_SUDO} from original backup."
    elif [ -f "/opt/synaptics-bio/.created-pam-sudo" ] && [ -f "/usr/lib/pam.d/sudo" ]; then
        rm -f "${PAM_SUDO}"
        echo "Removed installer-created ${PAM_SUDO} (restoring /usr/lib/pam.d/sudo)."
    else
        sed -i '/synaptics-bio-auth/d' "${PAM_SUDO}"
        echo "Removed synaptics-bio-auth rule from ${PAM_SUDO}."
    fi
fi

# 2. Restore PAM KDE & KDE Fingerprint
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

PAM_KDE_FP="/etc/pam.d/kde-fingerprint"
if [ -f "${PAM_KDE_FP}" ]; then
    if [ -f "${PAM_KDE_FP}.orig-bio" ]; then
        mv "${PAM_KDE_FP}.orig-bio" "${PAM_KDE_FP}"
        echo "Restored ${PAM_KDE_FP} from original backup."
    elif [ -f "/opt/synaptics-bio/.created-pam-kde-fp" ] && [ -f "/usr/lib/pam.d/kde-fingerprint" ]; then
        rm -f "${PAM_KDE_FP}"
        echo "Removed installer-created ${PAM_KDE_FP} (restoring /usr/lib/pam.d/kde-fingerprint)."
    else
        sed -i '/synaptics-bio-auth/d' "${PAM_KDE_FP}"
        echo "Removed synaptics-bio-auth rule from ${PAM_KDE_FP}."
    fi
fi

# 3. Restore / Remove PAM Polkit-1
PAM_POLKIT="/etc/pam.d/polkit-1"
if [ -f "${PAM_POLKIT}" ]; then
    if [ -f "${PAM_POLKIT}.orig-bio" ]; then
        mv "${PAM_POLKIT}.orig-bio" "${PAM_POLKIT}"
        echo "Restored ${PAM_POLKIT} from original backup."
    elif [ -f "/opt/synaptics-bio/.created-pam-polkit" ]; then
        rm -f "${PAM_POLKIT}"
        echo "Removed installer-created ${PAM_POLKIT}."
    else
        sed -i '/synaptics-bio-auth/d' "${PAM_POLKIT}"
        echo "Removed synaptics-bio-auth rule from ${PAM_POLKIT}."
    fi
fi

# 4. Remove Udev Rule
if [ -f "/etc/udev/rules.d/70-synaptics-bio.rules" ]; then
    rm -f "/etc/udev/rules.d/70-synaptics-bio.rules"
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
    echo "Removed /etc/udev/rules.d/70-synaptics-bio.rules."
fi

# 5. Remove Dedicated Synaptics Static Image Store and Legacy Drop-ins
rm -f "/etc/containers/storage.conf.d/99-synaptics-bio.conf"
rm -f "/etc/containers/storage.rootless.conf.d/99-synaptics-bio.conf"
if [ -d "/usr/share/synaptics-bio" ]; then
    rm -rf "/usr/share/synaptics-bio"
    echo "Removed /usr/share/synaptics-bio."
fi
if [ -d "/var/lib/synaptics-bio" ]; then
    rm -rf "/var/lib/synaptics-bio"
    echo "Removed /var/lib/synaptics-bio."
fi
# Safely untag our images from legacy /usr/share/containers/storage without deleting unrelated images
if [ -d "/usr/share/containers/storage/overlay-images" ] && command -v podman >/dev/null 2>&1; then
    podman --root /usr/share/containers/storage --runroot /run/synaptics-bio/uninstall-run \
        rmi localhost/wine:validity wine:validity ghcr.io/abrakadabr/synaptics-06cb-0081-linux:latest 2>/dev/null || true
    rm -rf /run/synaptics-bio/uninstall-run 2>/dev/null || true
fi
echo "Cleaned up container storage state."

# 6. Remove CLI wrappers, sleep hook, systemd watcher, tmpfiles config, and locks
systemctl disable --now synaptics-bio-lockscreen.path synaptics-bio-lockscreen.service 2>/dev/null || true
rm -f /etc/systemd/system/synaptics-bio-lockscreen.path
rm -f /etc/systemd/system/synaptics-bio-lockscreen.service
systemctl daemon-reload 2>/dev/null || true
rm -f /usr/local/bin/synaptics-bio-auth
rm -f /usr/local/bin/synaptics-bio-enroll
rm -f /usr/local/bin/synaptics-bio-delete
rm -f /usr/local/bin/synaptics-bio-patch-lockscreen
rm -f /etc/systemd/system-sleep/synaptics-bio-sleep
rm -f /usr/lib/systemd/system-sleep/synaptics-bio-sleep
rm -f /etc/tmpfiles.d/synaptics-bio.conf
rm -f /run/lock/synaptics-bio.lock /run/lock/synaptics-bio.meta
rm -rf /run/synaptics-bio
echo "Removed CLI tools, sleep hooks, systemd units, and tmpfiles configuration."

# Restore KDE lockscreen QML
LOCKSCREEN_QML="/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/LockScreenUi.qml"
if [ -f "${LOCKSCREEN_QML}" ]; then
    python3 "${SCRIPT_DIR}/scripts/patch-lockscreen.py" --revert "${LOCKSCREEN_QML}" 2>/dev/null || {
        if [ -f "${LOCKSCREEN_QML}.orig-bio" ]; then
            mv "${LOCKSCREEN_QML}.orig-bio" "${LOCKSCREEN_QML}"
            echo "Restored ${LOCKSCREEN_QML} from backup."
        fi
    }
fi

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
