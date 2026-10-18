#!/usr/bin/env python3
"""
extract-calibration.py
Extracts the 40,780-byte CalibrationData blob from a Windows SYSTEM registry hive.

Usage:
  python3 extract-calibration.py [path-to-SYSTEM-hive] [output-CalibrationData.blob]

Default search paths:
  /media/*/Windows/System32/config/SYSTEM
  /mnt/*/Windows/System32/config/SYSTEM
"""

import sys
import os
import glob
import struct
import hashlib

class RegHive:
    def __init__(self, data):
        self.data = data
    def read_cell(self, off):
        cell_off = 0x1000 + off
        sz = struct.unpack('<i', self.data[cell_off:cell_off+4])[0]
        return self.data[cell_off+4 : cell_off + abs(sz)]

def find_system_hive():
    candidates = [
        "/media/WIN/Windows/System32/config/SYSTEM",
        "/mnt/windows/Windows/System32/config/SYSTEM",
        "/media/*/Windows/System32/config/SYSTEM",
        "/mnt/*/Windows/System32/config/SYSTEM",
    ]
    for pattern in candidates:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None

def extract_calibration(hive_path, output_path="CalibrationData.blob"):
    print(f"Reading Windows SYSTEM hive: {hive_path}...")
    with open(hive_path, "rb") as f:
        data = f.read()

    idx = data.find(b"CalibrationData")
    if idx == -1:
        print("Error: 'CalibrationData' key not found in this registry hive.", file=sys.stderr)
        return False

    vk_off = idx - 20
    magic, nlen, dlen, doff, dtype, flags = struct.unpack("<2sHIIIH", data[vk_off:vk_off+18])
    if magic != b"vk":
        print("Error: Could not locate valid 'vk' value cell for CalibrationData.", file=sys.stderr)
        return False

    print(f"Found CalibrationData entry: payload length {dlen} bytes, cell offset 0x{doff:x}")
    hive = RegHive(data)
    dcell = hive.read_cell(doff)

    out = bytearray()
    if dcell[:2] == b"db":
        count, list_off = struct.unpack("<HI", dcell[2:8])
        lst = hive.read_cell(list_off)
        for j in range(count):
            boff = struct.unpack("<I", lst[j*4:(j+1)*4])[0]
            bcell = hive.read_cell(boff)
            out.extend(bcell[:16344])
        payload = bytes(out[:dlen])
    else:
        payload = dcell[:dlen]

    if len(payload) < 1000:
        print(f"Warning: Extracted data seems unusually small ({len(payload)} bytes).")

    with open(output_path, "wb") as f:
        f.write(payload)

    print(f"Successfully extracted {len(payload)} bytes to '{output_path}'.")
    print(f"SHA-256: {hashlib.sha256(payload).hexdigest()}")
    return True

def main():
    hive_path = sys.argv[1] if len(sys.argv) > 1 else find_system_hive()
    out_path = sys.argv[2] if len(sys.argv) > 2 else "CalibrationData.blob"

    if not hive_path or not os.path.exists(hive_path):
        print("Error: Windows SYSTEM hive not found.", file=sys.stderr)
        print(f"Usage: {sys.argv[0]} <path-to-SYSTEM-hive> [output-path]")
        sys.exit(1)

    success = extract_calibration(hive_path, out_path)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
