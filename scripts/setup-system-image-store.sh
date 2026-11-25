#!/usr/bin/env bash
# scripts/setup-system-image-store.sh
# Sets up a dedicated, static read-only container image store in /usr/share/containers/storage
# This allows all users (root and desktop accounts) to share the Wine runtime image
# without duplication and without permission or lock collisions.

set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "Error: This script must be run as root (use sudo)." >&2
    exit 1
fi

FORCE=0
for arg in "$@"; do
    case "${arg}" in
        --force|-f)
            FORCE=1
            ;;
        -h|--help)
            echo "Usage: $0 [--force]"
            exit 0
            ;;
        *)
            echo "Error: Unknown option '${arg}'." >&2
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE_PATH="${SCRIPT_DIR}/wine-validity.tar.zst"
if [ ! -f "${ARCHIVE_PATH}" ]; then
    ARCHIVE_PATH="/opt/synaptics-bio/wine-validity.tar.zst"
fi

GHCR_IMAGE="ghcr.io/abrakadabr/synaptics-06cb-0081-linux:latest"
IMAGE_NAME="localhost/wine:validity"

STATIC_STORE="/usr/share/containers/storage"
CONF_DROPIN="/etc/containers/storage.conf.d/99-synaptics-bio.conf"

echo "=========================================================="
echo "   Synaptics 06cb:0081 Multi-User Static Image Store Setup"
echo "=========================================================="
echo "Store Location: ${STATIC_STORE}"
if [ -f "${ARCHIVE_PATH}" ]; then
    echo "Archive Source: ${ARCHIVE_PATH}"
else
    echo "Archive Source: Remote GHCR (${GHCR_IMAGE})"
fi
echo ""

# 1. Clean up any legacy rootless drop-in
rm -f /etc/containers/storage.rootless.conf.d/99-synaptics-bio.conf

# 2. Populate static store if image is missing (or --force requested)
mkdir -p "${STATIC_STORE}"
if [ "${FORCE}" -eq 1 ] || ! podman --root "${STATIC_STORE}" --runroot /run/containers/storage image exists "${IMAGE_NAME}" 2>/dev/null; then
    if [ -f "${ARCHIVE_PATH}" ]; then
        echo "[1/3] Loading Wine runtime image from ${ARCHIVE_PATH} into ${STATIC_STORE}..."
        zstdcat "${ARCHIVE_PATH}" | podman --root "${STATIC_STORE}" --runroot /run/containers/storage load
    else
        echo "[1/3] Local archive not found. Pulling from GitHub Container Registry (${GHCR_IMAGE})..."
        podman --root "${STATIC_STORE}" --runroot /run/containers/storage pull "${GHCR_IMAGE}"
        podman --root "${STATIC_STORE}" --runroot /run/containers/storage tag "${GHCR_IMAGE}" "${IMAGE_NAME}" 2>/dev/null || true
    fi
    podman --root "${STATIC_STORE}" --runroot /run/containers/storage tag "${IMAGE_NAME}" wine:validity 2>/dev/null || true
else
    echo "[1/3] Wine runtime image (${IMAGE_NAME}) already present in ${STATIC_STORE}."
fi

# 3. Set global read/traversal permissions
echo "[2/3] Setting static read/execute permissions..."
chmod -R a+rX "${STATIC_STORE}"

# 4. Configure drop-in for all container engines
echo "[3/3] Creating container storage drop-in (${CONF_DROPIN})..."
FUSE_OVERLAYFS_BIN="$(command -v fuse-overlayfs || echo /usr/bin/fuse-overlayfs)"
mkdir -p /etc/containers/storage.conf.d
cat << EOF > "${CONF_DROPIN}"
[storage.options]
additionalimagestores = [
    "/usr/share/containers/storage"
]

[storage.options.overlay]
mount_program = "${FUSE_OVERLAYFS_BIN}"
EOF
chmod 644 "${CONF_DROPIN}"

echo ""
echo "=========================================================="
echo "Static System Image Store configured successfully!"
echo "Verification:"
echo "  - Store: ${STATIC_STORE} (read-only for non-root users)"
echo "  - Rootless sessions can now reuse ${IMAGE_NAME}"
echo "=========================================================="
