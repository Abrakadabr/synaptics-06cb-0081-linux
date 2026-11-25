# Biometric Driver Harness (`src/`)

This directory contains the reference source code and pre-built Windows User-Mode Driver Framework (UMDF) harness executables originally authored by Viktor Dragomiretskyy ([`uunicorn/synaWudfBioUsb-sandbox`](https://github.com/uunicorn/synaWudfBioUsb-sandbox)) and adapted for the Synaptics `06cb:0081` Match-on-Chip sensor.

---

## 1. Files

- **`hello.cc`**: Upstream reference C++ UMDF/WBF host harness. Dynamically loads `synawudfbiousb.dll`, initializes `IDriverEntry`, exposes `IWDFDevice` / `IWDFNamedPropertyStore2`, and drives initialization, enrollment, identification, and record deletion IOCTLs. Note that CLI mode dispatch is disabled (`#if 0`) in this reference source; see notes below before rebuilding.
- **`breakpoints.c` / `breakpoints.h`**: Hardware breakpoint hook utilities for x86-64 debugging and dynamic trace interception.
- **`notes.txt`**: Reverse-engineering packet logs, state machine transitions, IOCTL parameters, and payload layouts recorded during sensor interrogation.
- **`identify.exe` / `enroll.exe`**: Pre-built x86-64 PE binaries derived from the harness:
  - Hardcoded initialization sleeps are eliminated for low-latency PAM verification.
  - Multi-user enrollment (`synaptics-bio-enroll`) and deletion (`synaptics-bio-delete`) validate expected byte signatures in `enroll.exe` before injecting the target Linux UID (Windows SID RID), SubFactor slot, and identity label at runtime.

---

## 2. Rebuilding From Source (Advanced / Optional)

Precompiled 64-bit binaries (`identify.exe` and `enroll.exe`) are included so cross-compilation is **not** required for standard installation.

Rebuilding `hello.cc` into `a.exe` requires MinGW-w64 **plus** external Windows Driver Kit (WDK 10) UMDF 1.11 headers (`wudfddi.h`, `winbio_ioctl.h`) and Wine's `libpropsys.def`:

```bash
sudo apt install gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 mingw-w64-tools
make WDK_INCLUDE=/path/to/wdk-10/Include/wdf/umdf/1.11
```

> **Note:** Because `synaptics-bio-enroll` and `synaptics-bio-delete` verify exact instruction/data offsets in the bundled `src/enroll.exe`, rebuilding from modified source will require updating the expected offsets in `lib/synaptics_bio_common.py`.
