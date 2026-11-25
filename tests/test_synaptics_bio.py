#!/usr/bin/env python3
"""Unit tests for Synaptics 06cb:0081 Linux biometric tooling."""

import importlib.machinery
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
    full_path = os.path.join(REPO_ROOT, rel_path)
    loader = importlib.machinery.SourceFileLoader(mod_name, full_path)
    spec = importlib.util.spec_from_loader(mod_name, loader)
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

            # Reject UIDs >= 2^31 (prevents 64-bit sign-extension in mov QWORD [rbp+0x40], imm32) or negative UIDs
            with self.assertRaises(ValueError):
                bio.patch_enroll_binary(enroll_src, e_dst, "alex", 0x80000000, 1)
            with self.assertRaises(ValueError):
                bio.patch_delete_binary(enroll_src, d_dst, -1, 1)

            # UTF-8 codepoint boundary safe truncation + domain '@' username support
            bio.patch_enroll_binary(enroll_src, e_dst, "alex@corp.local", 1006, finger_id=1)
            with open(e_dst, "rb") as f:
                ed_dom = f.read()
            self.assertEqual(ed_dom[0x144D:0x1455], b"alex@co\x00")
            self.assertEqual(bio.truncate_utf8_label("aabbcc\u00e9", 7), "aabbcc")
            res_dom = bio.parse_identify_buffer(_make_identify_line(1006, "alex@co", rc=0))
            self.assertEqual(res_dom["label"], "alex@co")

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
                with open(qml, "r", encoding="utf-8") as f:
                    patched_qml = f.read()
                self.assertIn("property bool biometricPaused: false", patched_qml)
                self.assertIn("!parent.biometricPaused", patched_qml)
                self.assertIn('msg.includes("not \'")', patched_qml)
                # Idempotent second run
                self.assertTrue(mod.apply_patch(qml))
                # Legacy-patched file (having biometricActive without biometricPaused) upgrades cleanly from backup
                legacy_qml = patched_qml.replace("property bool biometricPaused: false\n", "").replace(
                    "!parent.biometricPaused", '!root.notification.includes("Falling back")'
                )
                with open(qml, "w", encoding="utf-8") as f:
                    f.write(legacy_qml)
                self.assertTrue(mod.apply_patch(qml))
                with open(qml, "r", encoding="utf-8") as f:
                    self.assertIn("!parent.biometricPaused", f.read())
                # Revert restores exact original
                self.assertTrue(mod.revert_patch(qml))
            finally:
                os.umask(old_umask)

            with open(qml, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), sample_qml)

    def test_isolated_storage_conf_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_store = os.path.join(tmp, "static-store")
            runtime_dir = os.path.join(tmp, "runtime")
            # Uninitialized store returns None
            self.assertIsNone(
                bio.ensure_isolated_storage_conf(1000, tmp, store_candidates=(fake_store,), runtime_base=runtime_dir)
            )
            os.makedirs(os.path.join(fake_store, "overlay-images"))
            conf_path = bio.ensure_isolated_storage_conf(
                1000, tmp, store_candidates=(fake_store,), runtime_base=runtime_dir
            )
            self.assertIsNotNone(conf_path)
            self.assertEqual(stat.S_IMODE(os.stat(conf_path).st_mode), 0o600)
            with open(conf_path, "r", encoding="utf-8") as f:
                cfg = f.read()
            self.assertIn(f'"{fake_store}"', cfg)
            self.assertIn('driver = "overlay"', cfg)

    def test_get_proc_ppid_current_process(self):
        self.assertEqual(bio.get_proc_ppid(os.getpid()), os.getppid())

    def test_is_lockscreen_auth_process_strict_argv_and_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_dir = os.path.join(tmp, "4242")
            os.makedirs(pid_dir)
            with open(os.path.join(pid_dir, "status"), "w", encoding="utf-8") as f:
                f.write("Name:\tpython3\nUid:\t1000\t1000\t1000\t1000\n")

            # Legitimate lockscreen helper invocation
            with open(os.path.join(pid_dir, "cmdline"), "wb") as f:
                f.write(b"/usr/bin/python3\x00/usr/local/bin/synaptics-bio-auth\x00--lockscreen\x00")
            self.assertTrue(bio.is_lockscreen_auth_process(4242, expected_uid=1000, proc_root=tmp))
            self.assertFalse(bio.is_lockscreen_auth_process(4242, expected_uid=1001, proc_root=tmp))
            self.assertFalse(bio.is_lockscreen_auth_process(4242, expected_uid="1000", proc_root=tmp))

            # Unrelated editor/command mentioning synaptics-bio-auth and --lockscreen must be rejected
            with open(os.path.join(pid_dir, "cmdline"), "wb") as f:
                f.write(b"/usr/bin/vim\x00/usr/local/bin/synaptics-bio-auth\x00--lockscreen\x00")
            self.assertFalse(bio.is_lockscreen_auth_process(4242, expected_uid=1000, proc_root=tmp))

            # Missing status file must fail closed
            os.remove(os.path.join(pid_dir, "status"))
            self.assertFalse(bio.is_lockscreen_auth_process(4242, expected_uid=1000, proc_root=tmp))

    def test_hardware_lock_acquire_release_and_preemption(self):
        import subprocess as sp

        with tempfile.TemporaryDirectory() as tmp:
            paths = (os.path.join(tmp, "lock"), os.path.join(tmp, "meta"))
            fd1 = bio.acquire_hardware_lock(mode="interactive", timeout=0.5, lock_paths=paths)
            self.assertIsNotNone(fd1)

            # Lockscreen caller fails immediately when lock is already held
            self.assertIsNone(bio.acquire_hardware_lock(mode="lockscreen", timeout=0.5, lock_paths=paths))
            bio.release_hardware_lock(fd1, lock_paths=paths)

            # Spawn a child simulating a lockscreen holder that releases on SIGTERM after 0.25s,
            # while caller passes timeout=0.1s to verify post-preemption retry still succeeds!
            helper_dir = os.path.join(tmp, "bin")
            os.makedirs(helper_dir)
            fake_auth = os.path.join(helper_dir, "synaptics-bio-auth")
            with open(fake_auth, "w", encoding="utf-8") as f:
                f.write(
                    "#!/usr/bin/env python3\n"
                    "import fcntl, json, os, signal, sys, time\n"
                    "lp, mp = sys.argv[2], sys.argv[3]\n"
                    "fd = open(lp, 'r+')\n"
                    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                    "with open(mp, 'w') as fm:\n"
                    "    json.dump({'pid': os.getpid(), 'uid': os.geteuid(), 'mode': 'lockscreen'}, fm)\n"
                    "sys.stdout.write('LOCKED\\n'); sys.stdout.flush()\n"
                    "def _term(s, f):\n"
                    "    time.sleep(0.25)\n"
                    "    fcntl.flock(fd, fcntl.LOCK_UN); fd.close(); sys.exit(0)\n"
                    "signal.signal(signal.SIGTERM, _term)\n"
                    "time.sleep(10)\n"
                )
            os.chmod(fake_auth, 0o755)
            child = sp.Popen([sys.executable, fake_auth, "--lockscreen", paths[0], paths[1]], stdout=sp.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "LOCKED")
                fd2 = bio.acquire_hardware_lock(mode="interactive", timeout=0.1, lock_paths=paths)
                self.assertIsNotNone(fd2)
                bio.release_hardware_lock(fd2, lock_paths=paths)
            finally:
                if child.stdout:
                    child.stdout.close()
                if child.poll() is None:
                    child.kill()
                child.wait()

    def test_calibration_permissions_and_container_mounts(self):
        auth_mod = _load_script_module("bin/synaptics-bio-auth", "synaptics_bio_auth")
        with tempfile.TemporaryDirectory() as tmp:
            cal = os.path.join(tmp, "CalibrationData.blob")
            ts = os.path.join(tmp, "LastUpdateSystemTimeStamp.uint")
            bad_link = os.path.join(tmp, "Symlink.uint")

            # Before CalibrationData.blob exists, non-root still mounts completely :ro
            self.assertEqual(auth_mod._build_container_mounts(tmp, uid=1000), ["-v", f"{tmp}:{tmp}:ro"])
            self.assertEqual(auth_mod._build_container_mounts(tmp, uid=0), ["-v", f"{tmp}:{tmp}"])

            with open(cal, "wb") as f:
                f.write(b"\x01" * 16)
            with open(ts, "w", encoding="utf-8") as f:
                f.write("123\n")
            os.chmod(cal, 0o600)
            os.chmod(ts, 0o600)
            os.symlink(cal, bad_link)

            bio.ensure_calibration_permissions(tmp)
            self.assertEqual(stat.S_IMODE(os.stat(cal).st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(os.stat(ts).st_mode), 0o644)
            self.assertEqual(auth_mod._build_container_mounts(tmp, uid=0), ["-v", f"{tmp}:{tmp}:ro"])

    def test_sysfs_usb_discovery_and_calibration_extractor(self):
        ext_mod = _load_script_module("scripts/extract-calibration.py", "extract_calibration")
        with tempfile.TemporaryDirectory() as tmp:
            # 1. Verify sysfs USB device path discovery
            other_dev = os.path.join(tmp, "1-1")
            syna_dev = os.path.join(tmp, "1-4")
            os.makedirs(other_dev)
            os.makedirs(syna_dev)
            for d, vid, pid_s, bus, dev in (
                (other_dev, "06cb", "c008", "1", "81"),
                (syna_dev, "06cb", "0081", "1", "5"),
            ):
                with open(os.path.join(d, "idVendor"), "w", encoding="utf-8") as f:
                    f.write(vid + "\n")
                with open(os.path.join(d, "idProduct"), "w", encoding="utf-8") as f:
                    f.write(pid_s + "\n")
                with open(os.path.join(d, "busnum"), "w", encoding="utf-8") as f:
                    f.write(bus + "\n")
                with open(os.path.join(d, "devnum"), "w", encoding="utf-8") as f:
                    f.write(dev + "\n")
            self.assertEqual(bio.get_usb_dev_path(os.path.join(tmp, "*")), "/dev/bus/usb/001/005")

            # 2. Verify extract-calibration skips prefix inline DWORDs ('CalibrationDataVersion')
            #    and extracts the real allocated binary 'CalibrationData' cell
            hive = bytearray(0x4000)
            # Fake allocated inline DWORD vk cell at offset 0x1100: CalibrationDataVersion = 1
            off1 = 0x1100
            struct.pack_into("<i", hive, off1 - 4, -48)
            struct.pack_into("<2sHIIIHH", hive, off1, b"vk", 22, 0x80000004, 1, 4, 1, 0)
            hive[off1 + 20 : off1 + 42] = b"CalibrationDataVersion"

            # Real data cell at hive cell offset 0x0500 (file offset 0x1500)
            payload = b"\xaa\x55" * 600  # 1200 bytes
            data_cell_file_off = 0x1000 + 0x0500
            struct.pack_into("<i", hive, data_cell_file_off, -(len(payload) + 4))
            hive[data_cell_file_off + 4 : data_cell_file_off + 4 + len(payload)] = payload

            # Real allocated vk cell at file offset 0x1200
            off2 = 0x1200
            struct.pack_into("<i", hive, off2 - 4, -40)
            struct.pack_into("<2sHIIIHH", hive, off2, b"vk", 15, len(payload), 0x0500, 3, 1, 0)
            hive[off2 + 20 : off2 + 35] = b"CalibrationData"

            hive_path = os.path.join(tmp, "SYSTEM")
            out_blob = os.path.join(tmp, "out.blob")
            with open(hive_path, "wb") as f:
                f.write(hive)
            self.assertTrue(ext_mod.extract_calibration(hive_path, out_blob))
            with open(out_blob, "rb") as f:
                self.assertEqual(f.read(), payload)


if __name__ == "__main__":
    unittest.main()
