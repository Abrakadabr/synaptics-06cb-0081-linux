# Biometric Driver Harness (`src/`)

This directory contains the source code for the Windows User-Mode Driver Framework (UMDF) harness originally conceived by Viktor Dragomiretskyy (`uunicorn/synaWudfBioUsb-sandbox`) and adapted for the Synaptics `06cb:0081` Match-on-Chip sensor.

---

## 1. Source Files

- **`hello.cc`**: C++ implementation that emulates the Windows Biometric Framework (WBF) and UMDF host interface. Dynamically loads `synawudfbiousb.dll`, initializes `IDriverEntry`, exposes `IWDFDevice` / `IWDFNamedPropertyStore2`, and drives the sensor through its initialization, enrollment, and identification sequences.
- **`breakpoints.c` / `breakpoints.h`**: Hardware breakpoint hook utilities for x86-64 debugging and dynamic trace interception.
- **`notes.txt`**: Extensive reverse-engineering packet logs, state machine transitions, IOCTL parameters, and payload layouts recorded during sensor interrogation.
- **`identify.exe` / `enroll.exe`**: Pre-built x86-64 PE binaries optimized for fast invocation:
  - Hardcoded initialization sleeps (`Sleep(3000)`, `Sleep(10000)`) are eliminated to bring verification latency down to ~2.5 seconds.
  - Multi-user enrollment uses dynamic SID RID injection and SubFactor slot tagging.

---

## 2. Compilation (Optional)

Precompiled 64-bit binaries are included so cross-compilation is **not** required for standard installation.

To rebuild from source on Linux:
```bash
sudo apt install gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 mingw-w64-tools
make
```
