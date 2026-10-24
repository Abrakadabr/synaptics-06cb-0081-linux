#!/usr/bin/env bash
# scripts/setup-system-image-store.sh
# Sets up a dedicated, static read-only container image store in /usr/share/containers/storage
# This allows all users (root, alex, kate, etc.) to share the 1.37GB Wine runtime image
# without duplication and without permission or lock collisions.

set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "Error: This script must be run as root (use sudo)." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE_PATH="${SCRIPT_DIR}/wine-validity.tar.zst"
if [ ! -f "${ARCHIVE_PATH}" ]; then
    ARCHIVE_PATH="/opt/synaptics-bio/wine-validity.tar.zst"
fi

if [ ! -f "${ARCHIVE_PATH}" ]; then
    echo "Error: wine-validity.tar.zst archive not found." >&2
    exit 1
fi

STATIC_STORE="/usr/share/containers/storage"
CONF_DROPIN="/etc/containers/storage.conf.d/99-synaptics-bio.conf"

echo "=========================================================="
echo "   Synaptics 06cb:0081 Multi-User Static Image Store Setup"
echo "=========================================================="
echo "Store Location: ${STATIC_STORE}"
echo "Archive Source: ${ARCHIVE_PATH}"
echo ""

# 1. Clean up any previous conflicting drop-ins
rm -f /etc/containers/storage.conf.d/99-synaptics-bio.conf
rm -f /etc/containers/storage.rootless.conf.d/99-synaptics-bio.conf

# 2. Populate static store
mkdir -p "${STATIC_STORE}"
echo "[1/3] Loading Wine runtime image into ${STATIC_STORE}..."
zstdcat "${ARCHIVE_PATH}" | podman --root "${STATIC_STORE}" --runroot /run/containers/storage load
podman --root "${STATIC_STORE}" tag localhost/wine:validity wine:validity 2>/dev/null || true

# 3. Set global read/traversal permissions
echo "[2/3] Setting static read/execute permissions..."
chmod -R a+rX "${STATIC_STORE}"

# 4. Configure drop-in for all container engines
echo "[3/3] Creating container storage drop-in (${CONF_DROPIN})..."
mkdir -p /etc/containers/storage.conf.d
cat << 'EOF' > "${CONF_DROPIN}"
[storage.options]
additionalimagestores = [
    "/usr/share/containers/storage"
]
EOF
chmod 644 "${CONF_DROPIN}"

echo ""
echo "=========================================================="
echo "Static System Image Store configured successfully!"
echo "Verification:"
echo "  - Store: ${STATIC_STORE} (read-only for non-root users)"
echo "  - Rootless sessions (alex, kate) can now reuse localhost/wine:validity"
echo "=========================================================="
