#!/usr/bin/env python3
"""Unit tests for Synaptics 06cb:0081 Linux biometric tooling."""

import importlib.util
import json
import os
import stat
import struct
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "lib"))

import synaptics_bio_common as bio  # noqa: E402


def _load_script_module(rel_path, mod_name):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(REPO_ROOT, rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_winbio_identity(rid, subauth_count=5):
    buf = bytearray(0x4C)
    sid_size = 8 + 4 * subauth_count
    struct.pack_into("<II", buf, 0, 3, sid_size)
    buf[8] = 1  # Revision
    buf[9] = subauth_count
    buf[10:16] = b"\x00\x00\x00\x00\x00\x05"  # NT Authority
    for i in range(subauth_count - 1):
        struct.pack_into("<I", buf, 16 + 4 * i, 21 + i)
    struct.pack_into("<I", buf, 16 + 4 * (subauth_count - 1), rid)
    return bytes(buf)


def _make_identify_line(rid, label, rc=0, size=0x888, subfactor=0xF5):
    block = bytearray(0x888)
    block[0:0x4C] = _make_winbio_identity(rid)
    block[0x4C] = subfactor
    struct.pack_into("<I", block, 0x50, rc)
    if label is not None:
        raw = label.encode("utf-8") + b"\x00"
        struct.pack_into("<Q", block, 0x78, len(raw))
        block[0x80 : 0x80 + len(raw)] = raw
    if size == 0x1890:
        payload = bytes(0x1008) + bytes(block)
        return f"Got back 0x1890 bytes: {payload.hex()}"
    return f"Got back 0x888 bytes: {bytes(block).hex()}"


class TestPacketParsing(unittest.TestCase):
    def test_parse_decrypted_identity_label_valid(self):
        name = b"alex\x00"
        pkt = b"\x00\x00\x07\x00" + b"\x08\x00\x03\x00" + b"\x0c\x00\x00\x00" + b"\x00\x01" + struct.pack("<H", len(name)) + name
        self.assertEqual(bio.parse_decrypted_identity_label(f"Decrypted: {pkt.hex()}"), "alex")

    def test_parse_decrypted_identity_label_rejects_bad_packets(self):
        # Missing NUL terminator
        bad = b"\x08\x00\x03\x00\x0c\x00\x00\x00\x00\x01" + struct.pack("<H", 4) + b"alex"
        self.assertIsNone(bio.parse_decrypted_identity_label(f"Decrypted: {bad.hex()}"))
        # Wrong subtype tag
        bad_tag = b"\x08\x00\x03\x00\x0c\x00\x00\x00\x00\x02" + struct.pack("<H", 5) + b"alex\x00"
        self.assertIsNone(bio.parse_decrypted_identity_label(f"Decrypted: {bad_tag.hex()}"))
        # Invalid chars
        bad_chars = b"\x08\x00\x03\x00\x0c\x00\x00\x00\x00\x01" + struct.pack("<H", 6) + b"a;id;\x00"
        self.assertIsNone(bio.parse_decrypted_identity_label(f"Decrypted: {bad_chars.hex()}"))

    def test_parse_winbio_sid_rid(self):
        self.assertEqual(bio.parse_winbio_sid_rid(_make_winbio_identity(1000)), 1000)
        self.assertEqual(bio.parse_winbio_sid_rid(_make_winbio_identity(65534, subauth_count=3)), 65534)
        self.assertIsNone(bio.parse_winbio_sid_rid(b"\x00" * 0x4C))
        self.assertIsNone(bio.parse_winbio_sid_rid(b"\x03\x00\x00\x00"))

    def test_parse_identify_buffer_0x888_and_0x1890(self):
        res888 = bio.parse_identify_buffer(_make_identify_line(1001, "student", rc=0, size=0x888))
        self.assertEqual(res888, {"matched": True, "rc": 0, "rid": 1001, "label": "student", "subfactor": 0xF5})

        res1890 = bio.parse_identify_buffer(_make_identify_line(1002, "kate", rc=0, size=0x1890, subfactor=0xF6))
        self.assertEqual(res1890, {"matched": True, "rc": 0, "rid": 1002, "label": "kate", "subfactor": 0xF6})

    def test_parse_identify_buffer_no_match(self):
        res = bio.parse_identify_buffer(_make_identify_line(1000, "alex", rc=0x8009801F))
        self.assertIsNotNone(res)
        self.assertFalse(res["matched"])
        self.assertEqual(res["rc"], 0x8009801F)
        self.assertIsNone(res["rid"])


class TestAuthorizationIsolation(unittest.TestCase):
    def setUp(self):
        self.uids = {
            "root": 0,
            "alex": 1000,
            "kate": 1001,
            "student1": 1002,
            "student2": 1003,
            "alexander": 1004,
            "alexander2": 1005,
        }
        self.uid_lookup = lambda u: self.uids.get(u)

    def test_fail_closed_for_unenrolled_user(self):
        ident_map = {"alex": ["alex"], "student1": ["student1"]}
        rules, _, _ = bio.build_auth_policy(ident_map, "student2", self.uid_lookup)
        self.assertEqual(rules, [])
        self.assertFalse(bio.evaluate_match(rules, 1002, "student"))
        self.assertFalse(bio.evaluate_match(rules, 1003, "student"))

    def test_seven_char_prefix_collision_blocked(self):
        ident_map = {
            "student1": ["student1"],
            "student2": ["student2"],
        }
        rules_s1, _, _ = bio.build_auth_policy(ident_map, "student1", self.uid_lookup)
        rules_s2, _, _ = bio.build_auth_policy(ident_map, "student2", self.uid_lookup)

        # student1's finger (RID 1002, truncated label "student")
        self.assertTrue(bio.evaluate_match(rules_s1, 1002, "student"))
        self.assertFalse(bio.evaluate_match(rules_s2, 1002, "student"))
        # Without RID, UID-bound rule must not pass on label alone
        self.assertFalse(bio.evaluate_match(rules_s2, None, "student"))

        # student2's finger (RID 1003, truncated label "student")
        self.assertTrue(bio.evaluate_match(rules_s2, 1003, "student"))
        self.assertFalse(bio.evaluate_match(rules_s1, 1003, "student"))

    def test_truncated_alias_in_config_bound_to_target_uid(self):
        ident_map = {"alexander": ["alexand"], "alexander2": ["alexand"]}
        rules_a1, _, _ = bio.build_auth_policy(ident_map, "alexander", self.uid_lookup)
        rules_a2, _, _ = bio.build_auth_policy(ident_map, "alexander2", self.uid_lookup)

        self.assertTrue(bio.evaluate_match(rules_a1, 1004, "alexand"))
        self.assertFalse(bio.evaluate_match(rules_a2, 1004, "alexand"))

    def test_no_hardcoded_alex_unicorn_backdoor(self):
        ident_map = {"alex": ["alex"]}
        rules, _, _ = bio.build_auth_policy(ident_map, "alex", self.uid_lookup)
        self.assertFalse(bio.evaluate_match(rules, 1261, "Unicorn"))
        self.assertTrue(bio.evaluate_match(rules, 1000, "alex"))

    def test_explicit_legacy_alias_and_shared_account(self):
        ident_map = {
            "alex": ["alex", "Unicorn"],
            "root": ["alex", "kate"],
        }
        rules_alex, _, _ = bio.build_auth_policy(ident_map, "alex", self.uid_lookup)
        self.assertTrue(bio.evaluate_match(rules_alex, 1261, "Unicorn"))
        self.assertFalse(bio.evaluate_match(rules_alex, 1001, "kate"))

        rules_root, _, _ = bio.build_auth_policy(ident_map, "root", self.uid_lookup)
        self.assertTrue(bio.evaluate_match(rules_root, 1000, "alex"))
        self.assertTrue(bio.evaluate_match(rules_root, 1001, "kate"))
        self.assertFalse(bio.evaluate_match(rules_root, 1002, "student"))


class TestBinaryPatchersAndIdentities(unittest.TestCase):
    def test_patch_enroll_and_delete_binary(self):
        enroll_src = os.path.join(REPO_ROOT, "src", "enroll.exe")
        with tempfile.TemporaryDirectory() as tmp:
            e_dst = os.path.join(tmp, "enroll-test.exe")
            bio.patch_enroll_binary(enroll_src, e_dst, "alexander", 1004, finger_id=3)
            with open(e_dst, "rb") as f:
                ed = f.read()
            self.assertEqual(struct.unpack_from("<I", ed, 0x1413)[0], 1004)
            self.assertEqual(struct.unpack_from("<I", ed, 0x143A)[0], 0xF7)
            self.assertEqual(struct.unpack_from("<I", ed, 0x1447)[0], 8)
            self.assertEqual(ed[0x144D:0x1455], b"alexand\x00")
            self.assertEqual(ed[0x3EAA:0x3EAC], b"\x90\x90")
            self.assertEqual(ed[0x3EBD:0x3EBF], b"\xeb\x41")

            d_dst = os.path.join(tmp, "delete-test.exe")
            bio.patch_delete_binary(enroll_src, d_dst, 1004, finger_id=None)
            with open(d_dst, "rb") as f:
                dd = f.read()
            self.assertEqual(struct.unpack_from("<I", dd, 0x2423)[0], 1004)
            self.assertEqual(struct.unpack_from("<I", dd, 0x244A)[0], 0xFF)
            self.assertEqual(dd[0x3EB8], 0xE8)

            # Reject corrupted/unrecognized source binary
            bad_src = os.path.join(tmp, "bad.exe")
            with open(bad_src, "wb") as f:
                f.write(b"\x00" * 0x5000)
            with self.assertRaises(ValueError):
                bio.patch_enroll_binary(bad_src, e_dst, "alex", 1000, 1)
            with self.assertRaises(ValueError):
                bio.patch_delete_binary(bad_src, d_dst, 1000, 1)

    def test_identities_update_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            bio.update_identities_file(tmp, "kate", 1)
            bio.update_identities_file(tmp, "kate", 2)
            with open(os.path.join(tmp, "identities.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["kate"], ["kate"])
            self.assertEqual(data["_metadata"]["kate"]["enrolled_fingers"], [1, 2])

            bio.update_identities_after_delete(tmp, "kate", finger_id=2)
            with open(os.path.join(tmp, "identities.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["_metadata"]["kate"]["enrolled_fingers"], [1])

            # Single-slot delete when user has no _metadata should NOT wipe manual data entry
            data["manual"] = ["manual"]
            bio.atomic_write_json(os.path.join(tmp, "identities.json"), data)
            bio.update_identities_after_delete(tmp, "manual", finger_id=1)
            with open(os.path.join(tmp, "identities.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("manual", data)

            # Full unenroll removes entry completely
            bio.update_identities_after_delete(tmp, "manual", finger_id=None)
            with open(os.path.join(tmp, "identities.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertNotIn("manual", data)


class TestHelperScripts(unittest.TestCase):
    def test_patch_driver_idempotent(self):
        mod = _load_script_module("scripts/patch-driver.py", "patch_driver")
        with tempfile.TemporaryDirectory() as tmp:
            dll = os.path.join(tmp, "synaWudfBioUsb.dll")
            buf = bytearray(0x20000)
            buf[mod.EXPECTED_OFFSET : mod.EXPECTED_OFFSET + len(mod.ORIG_PATTERN)] = mod.ORIG_PATTERN
            with open(dll, "wb") as f:
                f.write(buf)

            self.assertTrue(mod.patch_dll(dll))
            with open(dll, "rb") as f:
                patched = f.read()
            self.assertEqual(
                patched[mod.EXPECTED_OFFSET : mod.EXPECTED_OFFSET + len(mod.PATCH_PATTERN)],
                mod.PATCH_PATTERN,
            )
            # Second invocation is idempotent
            self.assertTrue(mod.patch_dll(dll))

            # Unrecognized file fails cleanly
            bad = os.path.join(tmp, "other.dll")
            with open(bad, "wb") as f:
                f.write(b"\x00" * 0x20000)
            self.assertFalse(mod.patch_dll(bad))

    def test_patch_lockscreen_apply_preserve_mode_and_revert(self):
        mod = _load_script_module("scripts/patch-lockscreen.py", "patch_lockscreen")
        sample_qml = """Item {
    function handleMessage(msg) {
        if (!msg) {
            return;
        }
        if (!root.notification) {
            root.notification += msg;
        } else if (root.notification.includes(msg)) {
            root.notificationRepeated();
        } else {
            root.notification += "\\n" + msg
        }
        lockScreenRoot.uiVisible = true
    }
    Connections {
        function onPromptForSecretChanged() {
            mainBlock.showPassword = false;
            mainBlock.mainPasswordBox.forceActiveFocus();
            if (lockScreenUi.showPrompt) {
                lockScreenUi.handleMessage(authenticator.promptForSecret);
            }
        }
    }
    Connections {
        target: sessionManagement
        function onAboutToSuspend() {
            root.clearPassword();
        }
    }
    Item {
        property bool uiVisible: false
        property bool seenPositionChange: false
        property bool blockUI: containsMouse && (mainStack.depth > 1 || mainBlock.mainPasswordBox.text.length > 0 || Keyboards.KWinVirtualKeyboard.visible)
        onUiVisibleChanged: {
            if (uiVisible) {
                Window.window.requestActivate();
            }

            if (blockUI) {
                fadeoutTimer.running = false;
            } else if (uiVisible) {
                fadeoutTimer.restart();
            }
            authenticator.startAuthenticating();
        }
        Timer { // heart beat keeping the backend active for as long as we are in visible state
            interval: 1000
            running: !authenticator.unlocked && parent.uiVisible
            repeat: true
            onTriggered: authenticator.startAuthenticating()
        }
        Timer {
            id: fadeoutTimer
            interval: 10000
            onTriggered: {
                if (!lockScreenRoot.blockUI) {
                    mainBlock.mainPasswordBox.showPassword = false;
                    lockScreenRoot.uiVisible = false;
                }
            }
        }
        Timer {
            id: notificationRemoveTimer
            interval: 3000
            onTriggered: root.notification = ""
        }
        Component.onCompleted: launchAnimation.start();
    }
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            qml = os.path.join(tmp, "LockScreenUi.qml")
            with open(qml, "w", encoding="utf-8") as f:
                f.write(sample_qml)
            os.chmod(qml, 0o644)

            old_umask = os.umask(0o077)
            try:
                self.assertTrue(mod.apply_patch(qml))
                # Permissions must remain 0644 even under umask 0077
                self.assertEqual(stat.S_IMODE(os.stat(qml).st_mode), 0o644)
                # Idempotent second run
                self.assertTrue(mod.apply_patch(qml))
                # Revert restores exact original
                self.assertTrue(mod.revert_patch(qml))
            finally:
                os.umask(old_umask)

            with open(qml, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), sample_qml)

    def test_get_proc_ppid_current_process(self):
        self.assertEqual(bio.get_proc_ppid(os.getpid()), os.getppid())


if __name__ == "__main__":
    unittest.main()
