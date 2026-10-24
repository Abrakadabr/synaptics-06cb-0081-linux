#!/usr/bin/env bash
# scripts/get-driver.sh
# Automated downloader, extractor, and patcher for Synaptics 06cb:0081 Windows UMDF driver.
# Legal compliance: Downloads official OEM package directly from Lenovo servers and extracts locally.

set -euo pipefail

LENOVO_URL="https://download.lenovo.com/consumer/mobiles/huy103af07m6.exe"
DRIVER_NAME="huy103af07m6.exe"
EXPECTED_ORIG_SHA256="543cc2d725b8c347b76150c763ca4f991f4f65ff53954518649686a5170e163f"
EXPECTED_ADAPTER_SHA256="3089ff398adb00841abf2f43982ce717a83c2c2f151c292fb110f3c231ccdfc3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${1:-/opt/synaptics-bio}"

echo "=========================================================="
echo "   Synaptics 06cb:0081 Windows UMDF Driver Setup"
echo "=========================================================="
echo "Target Directory: ${TARGET_DIR}"

mkdir -p "${TARGET_DIR}"

TMP_DIR="$(mktemp -d -t syna-driver-XXXXXX)"
cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

# 1. Download OEM driver if not already cached
DRIVER_PATH="${TMP_DIR}/${DRIVER_NAME}"
echo "[1/4] Fetching official Lenovo driver package (${DRIVER_NAME})..."
if command -v curl >/dev/null 2>&1; then
    curl -fL --progress-bar -o "${DRIVER_PATH}" "${LENOVO_URL}"
elif command -v wget >/dev/null 2>&1; then
    wget -q --show-progress -O "${DRIVER_PATH}" "${LENOVO_URL}"
else
    echo "Error: Neither curl nor wget is installed." >&2
    exit 1
fi

# 2. Extract Inno Setup archive
echo "[2/4] Extracting driver files with innoextract..."
EXTRACT_DIR="${TMP_DIR}/extracted"
mkdir -p "${EXTRACT_DIR}"

if command -v innoextract >/dev/null 2>&1; then
    innoextract -q -d "${EXTRACT_DIR}" "${DRIVER_PATH}"
elif command -v podman >/dev/null 2>&1; then
    echo "Host innoextract not found. Running innoextract via Podman container..."
    podman run --rm -v "${TMP_DIR}:/data:z" -w /data docker.io/library/debian:bullseye sh -c \
        "apt-get update -qq && apt-get install -y -qq innoextract >/dev/null && innoextract -q -d /data/extracted /data/${DRIVER_NAME}"
elif command -v docker >/dev/null 2>&1; then
    echo "Host innoextract not found. Running innoextract via Docker container..."
    docker run --rm -v "${TMP_DIR}:/data" -w /data docker.io/library/debian:bullseye sh -c \
        "apt-get update -qq && apt-get install -y -qq innoextract >/dev/null && innoextract -q -d /data/extracted /data/${DRIVER_NAME}"
else
    echo "Error: 'innoextract' is required to unpack the Windows installer." >&2
    echo "Please install it with: sudo dnf install -y innoextract  (or sudo apt install innoextract)" >&2
    exit 1
fi

SOURCE_DIR="${EXTRACT_DIR}/app/Source"
if [ ! -f "${SOURCE_DIR}/synaWudfBioUsb.dll" ] || [ ! -f "${SOURCE_DIR}/synaAdvAdapter.dll" ]; then
    echo "Error: Failed to find required DLLs in extracted package." >&2
    exit 1
fi

# 3. Verify original checksums
echo "[3/4] Verifying extracted file integrity..."
ACTUAL_ORIG_SHA256="$(sha256sum "${SOURCE_DIR}/synaWudfBioUsb.dll" | awk '{print $1}')"
ACTUAL_ADAPTER_SHA256="$(sha256sum "${SOURCE_DIR}/synaAdvAdapter.dll" | awk '{print $1}')"

if [ "${ACTUAL_ORIG_SHA256}" != "${EXPECTED_ORIG_SHA256}" ]; then
    echo "Warning: synaWudfBioUsb.dll SHA256 (${ACTUAL_ORIG_SHA256}) differs from expected (${EXPECTED_ORIG_SHA256})."
fi

if [ "${ACTUAL_ADAPTER_SHA256}" != "${EXPECTED_ADAPTER_SHA256}" ]; then
    echo "Warning: synaAdvAdapter.dll SHA256 (${ACTUAL_ADAPTER_SHA256}) differs from expected (${EXPECTED_ADAPTER_SHA256})."
fi

cp -p "${SOURCE_DIR}/synaAdvAdapter.dll" "${TARGET_DIR}/synaAdvAdapter.dll"
cp -p "${SOURCE_DIR}/synaWudfBioUsb.dll" "${TARGET_DIR}/synawudfbiousb.dll.lenovo_orig"
cp -p "${SOURCE_DIR}/synaWudfBioUsb.dll" "${TARGET_DIR}/synawudfbiousb.dll"

# 4. Apply Wine UMDF null-dereference crash patch
echo "[4/4] Applying Wine UMDF runtime patch..."
python3 "${SCRIPT_DIR}/patch-driver.py" "${TARGET_DIR}/synawudfbiousb.dll"

chmod 755 "${TARGET_DIR}/synawudfbiousb.dll" "${TARGET_DIR}/synaAdvAdapter.dll"

echo "=========================================================="
echo "Driver installation and patching successful!"
echo "Files ready in ${TARGET_DIR}:"
echo "  - synawudfbiousb.dll (Patched Lenovo UMDF driver)"
echo "  - synaAdvAdapter.dll (Synaptics Advanced Biometric Adapter)"
echo "=========================================================="
