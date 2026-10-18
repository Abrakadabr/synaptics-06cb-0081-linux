# Reverse Engineering: Synaptics 06cb:0081 Match-on-Chip Sensor

This document details the reverse-engineered architecture, USB protocol, on-chip database layout, and driver patch mechanics for the **Synaptics / Validity WBDI (`06cb:0081`)** fingerprint sensor.

---

## 1. Hardware Architecture

The Synaptics `06cb:0081` is a **Match-on-Chip (MoC)** biometric security device found on various Lenovo laptops (e.g. ThinkPad L470/L570, IdeaPad, Yoga 920, and Flex Pro-13IKB).

### Key Architectural Characteristics:
1. **On-Chip Processing:** Raw biometric image data never leaves the physical silicon. Fingerprint capture, feature extraction, template matching, and database storage are performed inside the sensor's embedded secure microcontroller.
2. **Hardware Cryptoprocessor:** Communicates with the host OS over an encrypted **TLS 1.2 ECDHE** tunnel using client and host certificates negotiated during initial device pairing.
3. **Template Database in Flash:** Minutiae templates are persisted directly in non-volatile flash memory inside the sensor. Windows and Linux can share the exact same enrolled fingerprints across dual-boot installations without conflict.

---

## 2. USB Interface & Pipe Mechanics

| Endpoint | Type | Description |
|---|---|---|
| `0x01` / `0x81` | Bulk OUT / Bulk IN | Encrypted command payloads, driver handshake, and IOCTL data buffers. |
| `0x83` (Pipe 131) | Interrupt IN | Asynchronous event notifications: finger touch, lift, and capture readiness. |

### Event State Flow:
```
[Sensor Idle] 
      │
      ▼  IOCTL_BIOMETRIC_CAPTURE_DATA (0x440014)
[Active Polling Wait]
      │   libusb_interrupt_transfer = -7 (LIBUSB_ERROR_TIMED_OUT)
      │   (Sensor is armed and actively waiting for finger touch on Pipe 131)
      ▼  Physical finger contact
[Contact Detected]
      │   libusb_interrupt_transfer = 0 (Transfer complete, sz=80)
      │   Packet payload: 02 00 40 10 00 ...
      ▼  Feature extraction & Match-on-Chip comparison
[Result Decrypted]
      │   Returns WINBIO_ID packet (Type=8 Match Result, Subtype=3 SID)
      ▼
[Sensor Reset / Next Scan]
```

---

## 3. Windows Biometric Framework (WBF) Driver IOCTLs

The driver `synawudfbiousb.dll` interfaces with the Windows User-Mode Driver Framework (UMDF) and the Windows Biometric Framework via specialized device I/O control codes:

| IOCTL Code | Function | Direction / Payload |
|---|---|---|
| `0x440014` | `IOCTL_BIOMETRIC_CAPTURE_DATA` | Initiates fingerprint capture on the sensor. |
| `0x442004` | `IdentifyFeatureSet` / `IdentifyUserStorageOnFlash` | Compares captured sample against on-chip templates. Returns 0x888 (or 0x1890) byte buffer containing match identity. |
| `0x44200C` | `EngineAdapterCreateEnrollment` | Creates an enrollment session context (DWORD 1). |
| `0x442010` | `EngineAdapterUpdateEnrollment` | Submits a touch sample to the active enrollment session. Returns 0x2C byte response with progress percentage. |
| `0x442014` | `CheckForDuplicate` | Verifies whether the finger is already enrolled under another slot. |
| `0x442018` | `StorageAdapterAddRecord` | Commits completed template to secure flash memory. |
| `0x442034` | `StorageAdapterDeleteRecord` | Erases template record specified by `Identity` (0x4C bytes) and `SubFactor` (1 byte). |

---

## 4. Multi-User Identity & Slot Mapping

### On-Chip Identity Layout (`WINBIO_IDENTITY`)
When the Match-on-Chip processor confirms a match, it emits a decrypted response packet:

```
[00..03] 08 00 03 00     Type 8 (Match Result), Subtype 3 (Windows SID)
[04..07] SID Header      Revision, Authority (NT Authority = 5)
[08..09] Length          Identity string length in bytes
[10..nn] Identity String Target user string (e.g. "alex\0", "kate\0")
```

### SubFactor Slot Identifiers
The sensor supports up to 10 finger slots per identity, assigned sequentially:
- **Slot 1:** `0xF5` (`WINBIO_FINGER_UNSPECIFIED_POS_01`)
- **Slot 2:** `0xF6` (`WINBIO_FINGER_UNSPECIFIED_POS_02`)
- **Slot 3:** `0xF7` (`WINBIO_FINGER_UNSPECIFIED_POS_03`)
- ...
- **Slot 10:** `0xFE` (`WINBIO_FINGER_UNSPECIFIED_POS_10`)

During enrollment, `synaptics-bio-enroll` dynamically patches `enroll.exe` with the user's Linux UID (stored as the Windows SID RID) and target SubFactor slot before triggering `EngineAdapterCreateEnrollment`.

---

## 5. Wine UMDF Emulation Patch Details

Under standard Wine, the proprietary Lenovo driver crashes on startup due to an unhandled UMDF state discrepancy:

### Crash Location (`synawudfbiousb.dll` v5.5.2731.1050)
At file offset `0x1a84c`:
```asm
; Original code:
8b 8c 24 10 03 00 00    mov  ecx, dword ptr [rsp + 0x310]
48 8b 89 c0 01 00 00    mov  rcx, qword ptr [rcx + 0x1c0]  ; <-- DEREFERENCES NULL POINTER IN WINE!
```

In native Windows, `[rsp + 0x310]` contains an internal UMDF adapter context object. Under Wine UMDF emulation, this context pointer is not yet initialized at this stage, causing `[rcx + 0x1c0]` to segfault.

### Applied Patch:
```asm
; Patched code:
85 c0                   test eax, eax
0f 84 0e 07 00 00       je   +0x70e       ; Jump safely past uninitialized dispatch
48 89 c1                mov  rcx, rax
90 90 90                nop; nop; nop
```
This patch bypasses the null dereference and allows the driver initialization loop to proceed normally, successfully initializing hardware communication and enabling the full biometric engine.

---

## 6. Calibration Data (`CalibrationData.blob`)

The sensor requires a 40,780-byte `CalibrationData.blob` containing factory optical calibration matrix parameters.
- **On Dual-Boot Systems:** Can be extracted directly from the Windows registry at `SYSTEM\ControlSet001\Enum\USB\VID_06CB&PID_0081\<Device>\Device Parameters\Device Data\CalibrationData`.
- **On Standalone Linux:** If missing, the driver automatically performs an on-chip recalibration sequence upon initial run and saves `CalibrationData.blob` to disk for subsequent instant startups (~2.5s).
