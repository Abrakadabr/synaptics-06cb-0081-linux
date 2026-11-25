#!/usr/bin/env bash
# scripts/setup-system-image-store.sh
# Sets up a dedicated, static read-only container image store in /usr/share/synaptics-bio/containers-storage
# This allows all users (root and desktop accounts) to share the Wine runtime image via an isolated
# CONTAINERS_STORAGE_CONF without duplicating layers or mutating global Podman configuration.

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

STATIC_STORE="/usr/share/synaptics-bio/containers-storage"
LEGACY_STORE="/usr/share/containers/storage"
LEGACY_RUN_ROOT="/run/containers/storage"
RUN_ROOT="/run/synaptics-bio/containers-setup-run"

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

# 1. Remove legacy global container storage drop-ins so host Podman remains untouched
rm -f /etc/containers/storage.conf.d/99-synaptics-bio.conf
rm -f /etc/containers/storage.rootless.conf.d/99-synaptics-bio.conf

# 2. Populate static store if image is missing (or --force requested)
mkdir -p "${STATIC_STORE}" "${RUN_ROOT}"
if [ "${FORCE}" -eq 1 ] || ! podman --root "${STATIC_STORE}" --runroot "${RUN_ROOT}" image exists "${IMAGE_NAME}" 2>/dev/null; then
    if [ -f "${ARCHIVE_PATH}" ]; then
        echo "[1/3] Loading Wine runtime image from ${ARCHIVE_PATH} into ${STATIC_STORE}..."
        zstdcat "${ARCHIVE_PATH}" | podman --root "${STATIC_STORE}" --runroot "${RUN_ROOT}" load
    elif [ -d "${LEGACY_STORE}/overlay-images" ] && podman --root "${LEGACY_STORE}" --runroot "${LEGACY_RUN_ROOT}" image exists "${IMAGE_NAME}" 2>/dev/null; then
        echo "[1/3] Migrating existing image from legacy ${LEGACY_STORE} into ${STATIC_STORE}..."
        podman --root "${LEGACY_STORE}" --runroot "${LEGACY_RUN_ROOT}" save "${IMAGE_NAME}" \
            | podman --root "${STATIC_STORE}" --runroot "${RUN_ROOT}" load
    else
        echo "[1/3] Local archive not found. Pulling from GitHub Container Registry (${GHCR_IMAGE})..."
        podman --root "${STATIC_STORE}" --runroot "${RUN_ROOT}" pull "${GHCR_IMAGE}"
    fi

    if ! podman --root "${STATIC_STORE}" --runroot "${RUN_ROOT}" image exists "${IMAGE_NAME}" 2>/dev/null; then
        if podman --root "${STATIC_STORE}" --runroot "${RUN_ROOT}" image exists "${GHCR_IMAGE}" 2>/dev/null; then
            podman --root "${STATIC_STORE}" --runroot "${RUN_ROOT}" tag "${GHCR_IMAGE}" "${IMAGE_NAME}"
        fi
    fi
    podman --root "${STATIC_STORE}" --runroot "${RUN_ROOT}" tag "${IMAGE_NAME}" wine:validity 2>/dev/null || true
else
    echo "[1/3] Wine runtime image (${IMAGE_NAME}) already present in ${STATIC_STORE}."
fi

# Clean up our migrated tags from the legacy shared store without touching unrelated images
if [ -d "${LEGACY_STORE}/overlay-images" ]; then
    podman --root "${LEGACY_STORE}" --runroot "${LEGACY_RUN_ROOT}" rmi "${IMAGE_NAME}" wine:validity "${GHCR_IMAGE}" 2>/dev/null || true
fi
rm -rf "${RUN_ROOT}"

# 3. Set global read/traversal permissions
echo "[2/3] Setting static read/execute permissions..."
chmod 755 /usr/share/synaptics-bio
chmod -R a+rX "${STATIC_STORE}"

# 4. Prepare root isolated container graph directory
echo "[3/3] Preparing isolated synaptics-bio container storage state..."
mkdir -p /var/lib/synaptics-bio/containers-storage
chmod 700 /var/lib/synaptics-bio /var/lib/synaptics-bio/containers-storage

echo ""
echo "=========================================================="
echo "Static System Image Store configured successfully!"
echo "Verification:"
echo "  - Store: ${STATIC_STORE} (read-only for non-root users)"
echo "  - Isolated from global /etc/containers/storage.conf.d"
echo "  - Rootless sessions can now reuse ${IMAGE_NAME}"
echo "=========================================================="
