#!/usr/bin/env python3
"""
patch-driver.py
Applies the UMDF adapter null-dereference patch to synaWudfBioUsb.dll.

Fix details:
Under Wine UMDF emulation, calling the adapter dispatch function before the
internal context is fully initialized causes a null pointer dereference crash.
Original code at offset 0x1a84c:
  8b 8c 24 10 03 00 00    mov  ecx, dword ptr [rsp + 0x310]
  48 8b 89 c0 01 00 00    mov  rcx, qword ptr [rcx + 0x1c0]

Patched code:
  85 c0                   test eax, eax
  0f 84 0e 07 00 00       je   +0x70e  (jump to safe cleanup)
  48 89 c1                mov  rcx, rax
  90 90 90                nop; nop; nop
"""

import sys
import os
import hashlib

ORIG_PATTERN = bytes.fromhex("8b8c2410030000488b89c0010000")
PATCH_PATTERN = bytes.fromhex("85c00f840e0700004889c1909090")
EXPECTED_OFFSET = 0x1a84c

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def patch_dll(input_path, output_path=None):
    if output_path is None:
        output_path = input_path

    if not os.path.exists(input_path):
        print(f"Error: Input file '{input_path}' not found.", file=sys.stderr)
        return False

    with open(input_path, "rb") as f:
        data = bytearray(f.read())

    # Check if already patched
    if data[EXPECTED_OFFSET:EXPECTED_OFFSET + len(PATCH_PATTERN)] == PATCH_PATTERN:
        print(f"File '{input_path}' is already patched.")
        return True

    # Check for original pattern at expected offset
    if data[EXPECTED_OFFSET:EXPECTED_OFFSET + len(ORIG_PATTERN)] == ORIG_PATTERN:
        offset = EXPECTED_OFFSET
    else:
        # Search anywhere in file
        offset = data.find(ORIG_PATTERN)
        if offset == -1:
            print("Error: Target pattern not found in file. Is this Lenovo driver 5.5.2731.1050?", file=sys.stderr)
            return False

    print(f"Applying null-dereference patch at offset 0x{offset:x}...")
    data[offset:offset + len(PATCH_PATTERN)] = PATCH_PATTERN

    with open(output_path, "wb") as f:
        f.write(data)

    print(f"Successfully patched: {output_path}")
    print(f"SHA-256: {sha256_file(output_path)}")
    return True

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path-to-synaWudfBioUsb.dll> [output-path]")
        sys.exit(1)

    inp = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else inp
    success = patch_dll(inp, out)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
