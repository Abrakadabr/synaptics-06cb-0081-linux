#!/usr/bin/env python3
"""
Shared utilities for Synaptics 06cb:0081 Linux biometric tools
(synaptics-bio-auth, synaptics-bio-enroll, synaptics-bio-delete).
"""

import fcntl
import glob
import json
import os
import pwd
import re
import shutil
import signal
import struct
import subprocess
import time

RUN_BIO_DIR = "/run/synaptics-bio"
STATIC_STORE_CANDIDATES = (
    "/usr/share/synaptics-bio/containers-storage",
    "/usr/share/containers/storage",
)
IMAGE_CANDIDATES = [
    "localhost/wine:validity",
    "wine:validity",
    "ghcr.io/abrakadabr/synaptics-06cb-0081-linux:latest",
]
IDENTITY_LABEL_RE = re.compile(r"^[a-zA-Z0-9_\-\.]{1,32}$")

# Expected original bytes in src/enroll.exe to prevent silent corruption if binary changes
_ENROLL_EXPECTED_ORIG = {
    0x1413: bytes.fromhex("ed040000"),
    0x143A: bytes.fromhex("f6000000"),
    0x1447: bytes.fromhex("0800000048ba556e69636f726e00"),
}
_DELETE_EXPECTED_ORIG = {
    0x2423: bytes.fromhex("ed040000"),
    0x244A: bytes.fromhex("f6000000"),
    0x3EB8: bytes.fromhex("e802d8ffff"),
}


def resolve_opt_dir(script_file):
    """Locate /opt/synaptics-bio or fall back to a repo-local opt/ directory."""
    opt_dir = "/opt/synaptics-bio"
    if os.path.isdir(opt_dir):
        return opt_dir
    repo_opt = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(script_file))), "opt")
    if os.path.isdir(repo_opt):
        return repo_opt
    return None


def ensure_isolated_storage_conf(uid, home_dir, store_candidates=STATIC_STORE_CANDIDATES, runtime_base=None):
    """
    Generate a private CONTAINERS_STORAGE_CONF referencing the read-only shared image store
    so synaptics-bio never mutates /etc/containers/storage.conf.d or user Podman databases.
    Returns the path to storage.conf, or None if no initialized static store is present.
    """
    static_store = None
    for cand in store_candidates:
        if os.path.isdir(os.path.join(cand, "overlay-images")):
            static_store = cand
            break
    if not static_store:
        return None

    try:
        if runtime_base is not None:
            conf_dir = runtime_base
            graph_root = os.path.join(runtime_base, "graph")
            run_root = os.path.join(runtime_base, "run")
            os.makedirs(conf_dir, mode=0o700, exist_ok=True)
        elif uid == 0:
            os.makedirs(RUN_BIO_DIR, mode=0o755, exist_ok=True)
            conf_dir = RUN_BIO_DIR
            run_root = os.path.join(RUN_BIO_DIR, "containers-run")
            if os.path.isdir("/var/lib") and os.access("/var/lib", os.W_OK):
                graph_root = "/var/lib/synaptics-bio/containers-storage"
            else:
                graph_root = os.path.join(RUN_BIO_DIR, "containers-graph")
        else:
            user_run = f"/run/user/{uid}"
            if (
                not os.path.isdir(user_run)
                or os.path.islink(user_run)
                or os.stat(user_run).st_uid != uid
            ):
                return None
            conf_dir = user_run
            run_root = os.path.join(user_run, "synaptics-bio-run")
            local_share = os.path.join(home_dir, ".local", "share")
            if os.path.isdir(home_dir) and os.access(home_dir, os.W_OK):
                graph_root = os.path.join(local_share, "synaptics-bio", "containers-storage")
            else:
                graph_root = os.path.join(user_run, "synaptics-bio-graph")

        os.makedirs(graph_root, mode=0o700, exist_ok=True)
        os.makedirs(run_root, mode=0o700, exist_ok=True)

        fuse_bin = shutil.which("fuse-overlayfs") or "/usr/bin/fuse-overlayfs"
        overlay_section = ""
        if os.path.isfile(fuse_bin):
            overlay_section = f'\n[storage.options.overlay]\nmount_program = "{fuse_bin}"\n'

        desired = (
            '[storage]\n'
            'driver = "overlay"\n'
            f'graphroot = "{graph_root}"\n'
            f'runroot = "{run_root}"\n\n'
            '[storage.options]\n'
            'additionalimagestores = [\n'
            f'    "{static_store}"\n'
            ']\n'
            f'{overlay_section}'
        )

        conf_path = os.path.join(conf_dir, f"synaptics-bio-storage-{uid}.conf")
        if os.path.isfile(conf_path) and not os.path.islink(conf_path):
            try:
                with open(conf_path, "r", encoding="utf-8") as f_cur:
                    if f_cur.read() == desired:
                        return conf_path
            except OSError:
                pass

        tmp_conf = f"{conf_path}.tmp.{os.getpid()}"
        fd = os.open(tmp_conf, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f_out:
                f_out.write(desired)
            os.replace(tmp_conf, conf_path)
        except Exception:
            try:
                os.unlink(tmp_conf)
            except OSError:
                pass
            return None
        return conf_path
    except Exception:
        return None


def get_clean_env(uid=None):
    """Return a sanitized environment dict for isolated Podman & Wine execution."""
    if uid is None:
        uid = os.geteuid()
    clean_env = os.environ.copy()
    for var in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_ID", "XDG_DATA_DIRS"):
        clean_env.pop(var, None)
    try:
        clean_env["HOME"] = "/root" if uid == 0 else pwd.getpwuid(uid).pw_dir
    except Exception:
        clean_env["HOME"] = "/root" if uid == 0 else os.path.expanduser("~")
    clean_env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    storage_conf = ensure_isolated_storage_conf(uid, clean_env["HOME"])
    if storage_conf:
        clean_env["CONTAINERS_STORAGE_CONF"] = storage_conf
    else:
        clean_env.pop("CONTAINERS_STORAGE_CONF", None)
    return clean_env


def secure_append_log(log_file, msg):
    """Append to log_file using O_NOFOLLOW and mode 0600 without a chmod TOCTOU window."""
    try:
        fd = os.open(log_file, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8", errors="replace") as f:
            f.write(msg)
    except Exception:
        pass


def get_lock_paths():
    """
    Returns (lock_path, meta_path) inside a root-owned 0755 runtime directory
    with pre-created 0666 lock/meta regular files so unprivileged desktop sessions
    cannot unlink, symlink, or forge root control flags.
    """
    lock_f = os.path.join(RUN_BIO_DIR, "lock")
    meta_f = os.path.join(RUN_BIO_DIR, "meta")
    uid = os.geteuid()

    if uid == 0:
        try:
            os.makedirs(RUN_BIO_DIR, mode=0o755, exist_ok=True)
            os.chmod(RUN_BIO_DIR, 0o755)
            for p in (lock_f, meta_f):
                if os.path.islink(p):
                    os.unlink(p)
                fd_num = os.open(p, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o666)
                try:
                    os.fchmod(fd_num, 0o666)
                    os.fchown(fd_num, 0, 0)
                finally:
                    os.close(fd_num)
            return lock_f, meta_f
        except Exception:
            pass
    else:
        try:
            if (
                os.path.isdir(RUN_BIO_DIR)
                and not os.path.islink(lock_f)
                and not os.path.islink(meta_f)
                and os.path.isfile(lock_f)
                and os.path.isfile(meta_f)
                and os.access(lock_f, os.W_OK)
                and os.access(meta_f, os.W_OK)
            ):
                return lock_f, meta_f
        except Exception:
            pass

    fallback_lock = "/run/lock/synaptics-bio.lock"
    fallback_meta = "/run/lock/synaptics-bio.meta"
    try:
        if (
            os.path.isfile(fallback_lock)
            and not os.path.islink(fallback_lock)
            and os.access(fallback_lock, os.W_OK)
        ):
            return fallback_lock, fallback_meta
    except Exception:
        pass

    user_run = f"/run/user/{uid}"
    if os.path.isdir(user_run) and os.access(user_run, os.W_OK):
        return os.path.join(user_run, "synaptics-bio.lock"), os.path.join(user_run, "synaptics-bio.meta")

    return lock_f, meta_f


def is_screen_locked(uid=None):
    """Check if the desktop session is currently locked via kscreenlocker or loginctl."""
    if uid is None:
        uid = os.geteuid()
    try:
        cmd = ["pgrep", "-u", str(uid), "-x", "kscreenlocker_g"] if uid != 0 else ["pgrep", "-x", "kscreenlocker_g"]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            return True
    except Exception:
        pass

    try:
        cmd = ["loginctl", "show-user", str(uid), "-p", "Display", "--value"]
        display_session = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        if display_session:
            cmd = ["loginctl", "show-session", display_session, "-p", "LockedHint", "--value"]
            locked = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
            if locked == "yes":
                return True
    except Exception:
        pass

    return False


def get_proc_ppid(pid):
    """Safely parse PPID from /proc/<pid>/stat even when comm contains spaces or parentheses."""
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
            stat_content = f.read()
        rparen = stat_content.rfind(")")
        if rparen == -1:
            return None
        fields = stat_content[rparen + 2 :].split()
        # fields[0] is state, fields[1] is ppid
        return int(fields[1])
    except Exception:
        return None


def get_proc_euid(pid):
    """Read effective UID of pid from kernel-owned /proc/<pid>/status."""
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("Uid:"):
                    parts = line.split()
                    if len(parts) >= 3:
                        return int(parts[2])
    except Exception:
        pass
    return None


def is_lockscreen_auth_process(pid, expected_uid=None):
    """
    Verify via kernel-owned /proc/<pid>/cmdline (and optionally UID) that pid is
    synaptics-bio-auth running in --lockscreen mode.
    """
    try:
        if expected_uid is not None:
            actual_uid = get_proc_euid(pid)
            if actual_uid is not None and actual_uid != expected_uid:
                return False
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = [a.decode("utf-8", errors="ignore") for a in f.read().split(b"\x00") if a]
        if not args:
            return False
        has_auth = any(
            a == "/usr/local/bin/synaptics-bio-auth" or os.path.basename(a) == "synaptics-bio-auth"
            for a in args
        )
        return has_auth and "--lockscreen" in args
    except Exception:
        return False


def _can_create_lock_file(path):
    uid = os.geteuid()
    return uid == 0 or path.startswith(f"/run/user/{uid}/")


def acquire_hardware_lock(mode="interactive", timeout=3.0):
    """
    Acquires an exclusive flock on the hardware lockfile to prevent concurrent
    hardware access collisions between multiple PAM / CLI callers.
    Preempts orphaned lockscreen processes when interactive calls (sudo/polkit/CLI) arrive
    or when the session has been unlocked.
    """
    lock_path, meta_path = get_lock_paths()
    start_t = time.monotonic()
    while True:
        fd = None
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW
            if _can_create_lock_file(lock_path):
                flags |= os.O_CREAT
            fd_num = os.open(lock_path, flags, 0o666)
            fd = os.fdopen(fd_num, "r+")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                mflags = os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW
                if _can_create_lock_file(meta_path):
                    mflags |= os.O_CREAT
                mfd = os.open(meta_path, mflags, 0o666)
                with os.fdopen(mfd, "w", encoding="utf-8") as fm:
                    json.dump({"pid": os.getpid(), "uid": os.geteuid(), "mode": mode, "time": time.time()}, fm)
            except Exception:
                pass
            return fd
        except (BlockingIOError, OSError):
            if fd is not None:
                try:
                    fd.close()
                except Exception:
                    pass

        holder_pid = None
        holder_uid = None
        holder_mode = None
        try:
            mfd = os.open(meta_path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(mfd, "r", encoding="utf-8") as fm:
                raw_meta = fm.read().strip()
            if raw_meta:
                meta = json.loads(raw_meta)
                holder_pid = meta.get("pid")
                holder_uid = meta.get("uid")
                holder_mode = meta.get("mode")
        except Exception:
            pass

        holder_alive = False
        if isinstance(holder_pid, int) and holder_pid > 1 and holder_pid != os.getpid():
            try:
                os.kill(holder_pid, 0)
                holder_alive = True
            except OSError:
                holder_alive = False

        if not holder_alive:
            time.sleep(0.1)
        else:
            should_preempt = False
            if holder_mode == "lockscreen" and is_lockscreen_auth_process(holder_pid, holder_uid):
                if mode != "lockscreen":
                    should_preempt = True
                elif get_proc_ppid(holder_pid) == 1 or not is_screen_locked(holder_uid):
                    should_preempt = True

            if should_preempt:
                # Send SIGTERM first and allow up to 2.0s for the holder's own atexit cleanup
                # (important when holder runs rootless Podman under a different UID).
                try:
                    os.kill(holder_pid, signal.SIGTERM)
                except OSError:
                    pass

                for _ in range(20):
                    try:
                        os.kill(holder_pid, 0)
                        time.sleep(0.1)
                    except OSError:
                        break
                else:
                    try:
                        os.kill(holder_pid, signal.SIGKILL)
                    except OSError:
                        pass
                    if holder_uid == os.geteuid():
                        clean_env = get_clean_env(os.geteuid())
                        subprocess.run(
                            ["podman", "rm", "-f", f"synaptics-bio-auth-{holder_pid}"],
                            env=clean_env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=2,
                        )
                    if isinstance(holder_uid, int):
                        subprocess.run(
                            ["pkill", "-u", str(holder_uid), "-x", "identify.exe"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                time.sleep(0.1)
            elif mode == "lockscreen":
                return None

        if time.monotonic() - start_t >= timeout:
            return None
        time.sleep(0.1)


def release_hardware_lock(lock_fd):
    if lock_fd:
        try:
            _, meta_path = get_lock_paths()
            try:
                mfd = os.open(meta_path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(mfd, "r", encoding="utf-8") as fm:
                    raw_meta = fm.read().strip()
                if raw_meta and json.loads(raw_meta).get("pid") == os.getpid():
                    tfd = os.open(meta_path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
                    os.close(tfd)
            except Exception:
                pass
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass


def ensure_usb_powered():
    """Dynamically discover the 06cb:0081 USB device in sysfs and disable autosuspend."""
    for p in glob.glob("/sys/bus/usb/devices/*"):
        try:
            with open(os.path.join(p, "idVendor"), encoding="utf-8") as fv, open(
                os.path.join(p, "idProduct"), encoding="utf-8"
            ) as fp:
                if fv.read().strip() == "06cb" and fp.read().strip() == "0081":
                    ctrl = os.path.join(p, "power", "control")
                    if os.path.exists(ctrl):
                        with open(ctrl, "w", encoding="utf-8") as fc:
                            fc.write("on\n")
                    return
        except (OSError, IOError):
            continue


def get_usb_dev_path():
    """Locate the /dev/bus/usb/BBB/DDD node for 06cb:0081 via lsusb."""
    try:
        lsusb = subprocess.check_output(["lsusb"], text=True)
    except Exception:
        return None
    for line in lsusb.splitlines():
        if "06cb:0081" in line.lower() or ("synaptics" in line.lower() and "0081" in line.lower()):
            m = re.search(r"Bus\s+(\d+)\s+Device\s+(\d+)", line)
            if m:
                return f"/dev/bus/usb/{m.group(1)}/{m.group(2)}"
    return None


def find_container_image(clean_env, podman_base=None):
    """Return the first available Wine container image tag."""
    if podman_base is None:
        podman_base = ["podman"]
    for candidate in IMAGE_CANDIDATES:
        try:
            res = subprocess.run(
                podman_base + ["image", "exists", candidate],
                env=clean_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if res.returncode == 0:
                return candidate
        except Exception:
            pass
    return IMAGE_CANDIDATES[0]


def parse_decrypted_identity_label(line):
    """
    Extract the biometric payload identity label from a decrypted WinUSB trace line.
    Packet format:
      [+0..+4]   08 00 03 00  (payload record type 8, storage id 3)
      [+4..+8]   record size  (4-byte LE)
      [+8..+10]  00 01        (identity string subtype tag)
      [+10..+12] id_len       (2-byte LE, includes trailing NUL)
      [+12..+12+id_len]       NUL-terminated UTF-8 identity label
    """
    if not line.startswith("Decrypted:"):
        return None
    try:
        parts = line.split()
        if len(parts) < 2:
            return None
        raw_bytes = bytes.fromhex(parts[1])
        idx = raw_bytes.find(b"\x08\x00\x03\x00")
        if idx != -1 and len(raw_bytes) >= idx + 12:
            if raw_bytes[idx + 8 : idx + 10] == b"\x00\x01":
                id_len = int.from_bytes(raw_bytes[idx + 10 : idx + 12], "little")
                if 2 <= id_len <= 32 and len(raw_bytes) >= idx + 12 + id_len:
                    raw_id = raw_bytes[idx + 12 : idx + 12 + id_len]
                    if raw_id.endswith(b"\x00"):
                        ident = raw_id.rstrip(b"\x00").decode("utf-8", errors="strict")
                        if IDENTITY_LABEL_RE.match(ident):
                            return ident
    except Exception:
        pass
    return None


def parse_winbio_sid_rid(winbio_identity):
    """
    Extract the 32-bit RID (Linux UID) from a 0x4c-byte WINBIO_IDENTITY structure.
    Layout:
      +0x00..+0x04: Type (3 = WINBIO_ID_TYPE_SID)
      +0x04..+0x08: SID byte size (8 + 4 * SubAuthorityCount)
      +0x08:        SID Revision (1)
      +0x09:        SubAuthorityCount (1..15)
      +0x0a..+0x10: 6-byte IdentifierAuthority
      +0x10..:      SubAuthorityCount little-endian uint32 values; last entry is RID
    """
    if len(winbio_identity) < 0x4C:
        return None
    id_type, sid_size = struct.unpack_from("<II", winbio_identity, 0)
    if id_type != 3:
        return None
    if sid_size < 12 or sid_size > 0x4C - 8:
        return None
    rev = winbio_identity[8]
    subauth_count = winbio_identity[9]
    if rev != 1 or not (1 <= subauth_count <= 15):
        return None
    if sid_size != 8 + 4 * subauth_count:
        return None
    rid_offset = 8 + 8 + 4 * (subauth_count - 1)
    return struct.unpack_from("<I", winbio_identity, rid_offset)[0]


def parse_identify_buffer(line, all_identities=None):
    """
    Parse the authoritative IdentifyFeatureSet (IOCTL 0x442004) return buffer
    printed as 'Got back 0x888 bytes: <hex>' or 'Got back 0x1890 bytes: <hex>'.

    Returns None if line is not an IdentifyFeatureSet buffer, or a dict:
      {
        "matched": bool,
        "rc": int,
        "rid": Optional[int],
        "label": Optional[str],
        "subfactor": int,
      }
    """
    if "Got back 0x888 bytes:" not in line and "Got back 0x1890 bytes:" not in line:
        return None
    try:
        hex_part = line.split("bytes:", 1)[1].strip()
        raw_bytes = bytes.fromhex(hex_part)
    except Exception:
        return None

    if len(raw_bytes) == 0x1890:
        block = raw_bytes[0x1008:]
    elif len(raw_bytes) == 0x888:
        block = raw_bytes
    else:
        return None

    rc = struct.unpack_from("<I", block, 0x50)[0]
    subfactor = block[0x4C]
    if rc != 0:
        return {
            "matched": False,
            "rc": rc,
            "rid": None,
            "label": None,
            "subfactor": subfactor,
        }

    rid = parse_winbio_sid_rid(block[:0x4C])
    label = None

    # Primary structured payload at +0x78 (8-byte LE length) and +0x80 (NUL-terminated UTF-8 string)
    payload_len = struct.unpack_from("<Q", block, 0x78)[0]
    if 2 <= payload_len <= 32 and 0x80 + payload_len <= len(block):
        raw_id = block[0x80 : 0x80 + payload_len]
        if raw_id.endswith(b"\x00"):
            try:
                cand = raw_id.rstrip(b"\x00").decode("utf-8", errors="strict")
                if IDENTITY_LABEL_RE.match(cand):
                    label = cand
            except UnicodeDecodeError:
                pass

    # Secondary structured scan inside payload region if driver offset differs
    if label is None and all_identities:
        payload_region = block[0x4C:]
        for candidate in sorted(all_identities, key=len, reverse=True):
            cand_bytes = candidate.encode("utf-8")
            null_len = len(cand_bytes) + 1
            tag_pattern = b"\x00\x01" + struct.pack("<H", null_len) + cand_bytes + b"\x00"
            qword_pattern = struct.pack("<Q", null_len) + cand_bytes + b"\x00"
            if tag_pattern in payload_region or qword_pattern in payload_region:
                label = candidate
                break

    return {
        "matched": True,
        "rc": 0,
        "rid": rid,
        "label": label,
        "subfactor": subfactor,
    }


def _default_uid_lookup(username):
    try:
        return pwd.getpwnam(username).pw_uid
    except KeyError:
        return None


def build_auth_policy(ident_map, target_user, uid_lookup=None):
    """
    Build strict authorization rules for target_user from identities.json.
    Fails closed: if target_user has no configured aliases, returns empty rules.

    Returns (rules, all_identities, uid_to_user):
      - rules: list of {"uid": Optional[int], "labels": set[str], "alias": str}
      - all_identities: set[str] of all known hardware labels across identities.json
      - uid_to_user: dict[int, str] mapping Linux UIDs to usernames for diagnostics
    """
    if uid_lookup is None:
        uid_lookup = _default_uid_lookup

    all_identities = set()
    uid_to_user = {}
    rules = []

    if not isinstance(ident_map, dict):
        return rules, all_identities, uid_to_user

    for user, aliases in ident_map.items():
        if not isinstance(user, str) or user.startswith("_"):
            continue
        all_identities.add(user)
        if len(user) > 7:
            all_identities.add(user[:7])
        u_uid = uid_lookup(user)
        if u_uid is not None:
            uid_to_user[u_uid] = user
        if isinstance(aliases, list):
            for alias in aliases:
                if not isinstance(alias, str) or not alias:
                    continue
                all_identities.add(alias)
                if len(alias) > 7:
                    all_identities.add(alias[:7])
                a_uid = uid_lookup(alias)
                if a_uid is not None and a_uid not in uid_to_user:
                    uid_to_user[a_uid] = alias

    target_aliases = ident_map.get(target_user)
    if not isinstance(target_aliases, list) or not target_aliases:
        return rules, all_identities, uid_to_user

    target_uid = uid_lookup(target_user)
    for alias in target_aliases:
        if not isinstance(alias, str) or not alias:
            continue
        labels = {alias}
        if len(alias) > 7:
            labels.add(alias[:7])

        alias_uid = uid_lookup(alias)
        if alias_uid is not None:
            rules.append({"uid": alias_uid, "labels": labels, "alias": alias})
        elif target_uid is not None and alias == target_user[:7]:
            # Truncated hardware label for target_user (e.g. "alexander": ["alexand"])
            # Bind strictly to target_user's Linux UID to prevent 7-char prefix collisions.
            rules.append({"uid": target_uid, "labels": labels, "alias": alias})
        else:
            # Explicitly configured non-Linux legacy hardware label (e.g. "Unicorn")
            rules.append({"uid": None, "labels": labels, "alias": alias})

    return rules, all_identities, uid_to_user


def evaluate_match(rules, matched_rid, matched_label):
    """
    Evaluate whether an on-chip match (rid, label) satisfies any authorization rule.
    For UID-bound rules, matched_rid MUST strictly match the expected Linux UID,
    preventing 7-character username prefix collisions across different accounts.
    """
    if not rules:
        return False
    if matched_rid is None and not matched_label:
        return False

    for rule in rules:
        req_uid = rule["uid"]
        req_labels = rule["labels"]
        if req_uid is not None:
            if matched_rid != req_uid:
                continue
            if matched_label is None or matched_label in req_labels:
                return True
        else:
            if matched_label is not None and matched_label in req_labels:
                return True

    return False


def _verify_binary_signatures(data, signatures, label):
    for offset, expected in signatures.items():
        actual = bytes(data[offset : offset + len(expected)])
        if actual != expected:
            raise ValueError(
                f"Unexpected {label} binary bytes at offset 0x{offset:x}: "
                f"expected {expected.hex()}, got {actual.hex()}. "
                "Refusing to patch an unrecognized enroll.exe binary."
            )


def patch_enroll_binary(src_path, dst_path, target_user, target_uid, finger_id=1):
    """
    Validate expected bytes in enroll.exe and write a patched copy configured for
    (target_user, target_uid, finger_id).
    """
    if not (1 <= finger_id <= 10):
        raise ValueError(f"finger_id must be between 1 and 10, got {finger_id}")

    with open(src_path, "rb") as f:
        data = bytearray(f.read())

    _verify_binary_signatures(data, _ENROLL_EXPECTED_ORIG, "enroll.exe")

    # 1. Patch UID (RID) at 0x1413..0x1417
    data[0x1413:0x1417] = struct.pack("<I", target_uid)

    # 2. Patch SubFactor (Slot 1 = 0xF5 .. Slot 10 = 0xFE) at 0x143a..0x143e
    subfactor = 0xF4 + finger_id
    data[0x143A:0x143E] = struct.pack("<I", subfactor)

    # 3. Patch Identity Name at 0x1447..0x1455 (length dword, 2-byte movabs opcode preserved, 8-byte padded name)
    name_bytes = target_user.encode("utf-8")[:7] + b"\x00"
    name_padded = name_bytes.ljust(8, b"\x00")
    data[0x1447:0x144B] = struct.pack("<I", len(name_bytes))
    data[0x144D:0x1455] = name_padded

    # 4. Patch Sleep(3000) at 0x3eaa: ff d0 -> 90 90
    if data[0x3EAA:0x3EAC] == b"\xff\xd0":
        data[0x3EAA:0x3EAC] = b"\x90\x90"

    # 5. Patch jump over Sleep(10000) + identify() at 0x3ebd: 90 90 -> eb 41
    if data[0x3EBD:0x3EBF] == b"\x90\x90":
        data[0x3EBD:0x3EBF] = b"\xeb\x41"

    with open(dst_path, "wb") as f:
        f.write(data)
    os.chmod(dst_path, 0o755)


def patch_delete_binary(src_path, dst_path, target_uid, finger_id=None):
    """
    Validate expected bytes in enroll.exe and patch it to invoke deleteRecord()
    (IOCTL 0x442034) for (target_uid, finger_id or WINBIO_SUBTYPE_ANY=0xFF).
    """
    if finger_id is not None and finger_id not in (0, 0xFF) and not (1 <= finger_id <= 10):
        raise ValueError(f"finger_id must be 1..10 or None/0/0xff, got {finger_id}")

    with open(src_path, "rb") as f:
        data = bytearray(f.read())

    _verify_binary_signatures(data, _DELETE_EXPECTED_ORIG, "enroll.exe")

    # 1. Patch call in main() at 0x3eb8 from enroll() to deleteRecord()
    # deleteRecord() is at VMA 0x402dd7; call instruction is at VMA 0x4048b8
    rel_delete = 0x402DD7 - (0x4048B8 + 5)
    data[0x3EB8:0x3EBD] = b"\xe8" + struct.pack("<i", rel_delete)

    # 2. Patch target UID (RID) in deleteRecord() at 0x2423..0x2427
    data[0x2423:0x2427] = struct.pack("<I", target_uid)

    # 3. Patch SubFactor in deleteRecord() at 0x244a..0x244e
    if finger_id is None or finger_id == 0 or finger_id == 0xFF:
        subfactor = 0xFF
    else:
        subfactor = 0xF4 + finger_id
    data[0x244A:0x244E] = struct.pack("<I", subfactor)

    # 4. Patch Sleep(3000) at 0x3eaa: ff d0 -> 90 90
    if data[0x3EAA:0x3EAC] == b"\xff\xd0":
        data[0x3EAA:0x3EAC] = b"\x90\x90"

    # 5. Patch jump over Sleep(10000) + identify() at 0x3ebd: 90 90 -> eb 41
    if data[0x3EBD:0x3EBF] == b"\x90\x90":
        data[0x3EBD:0x3EBF] = b"\xeb\x41"

    with open(dst_path, "wb") as f:
        f.write(data)
    os.chmod(dst_path, 0o755)


def atomic_write_json(path, data, mode=0o644):
    """Atomically write JSON to path via a sibling temp file + os.replace."""
    tmp_path = f"{path}.tmp.{os.getpid()}"
    fd = os.open(tmp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            f.write("\n")
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def update_identities_file(opt_dir, target_user, finger_id):
    """Record a newly enrolled finger slot in identities.json atomically."""
    cfg_file = os.path.join(opt_dir, "identities.json")
    data = {}
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    if not isinstance(data, dict):
        data = {}

    if target_user not in data or not isinstance(data[target_user], list):
        data[target_user] = []
    if target_user not in data[target_user]:
        data[target_user].append(target_user)

    if "_metadata" not in data or not isinstance(data["_metadata"], dict):
        data["_metadata"] = {}
    if target_user not in data["_metadata"] or not isinstance(data["_metadata"][target_user], dict):
        data["_metadata"][target_user] = {"enrolled_fingers": []}
    enrolled = data["_metadata"][target_user].setdefault("enrolled_fingers", [])
    if finger_id not in enrolled:
        enrolled.append(finger_id)
        enrolled.sort()

    atomic_write_json(cfg_file, data, mode=0o644)


def update_identities_after_delete(opt_dir, target_user, finger_id=None):
    """
    Update identities.json after deleting a single slot or unenrolling a user.
    If deleting a single slot when _metadata has no record for target_user, preserve
    the user's identity entry rather than wiping it unexpectedly.
    """
    cfg_file = os.path.join(opt_dir, "identities.json")
    if not os.path.exists(cfg_file):
        return

    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    if not isinstance(data, dict):
        return

    meta = data.get("_metadata")
    if not isinstance(meta, dict):
        meta = {}

    if finger_id is None or finger_id == 0 or finger_id == 0xFF:
        meta.pop(target_user, None)
        data.pop(target_user, None)
    else:
        user_meta = meta.get(target_user)
        if isinstance(user_meta, dict) and isinstance(user_meta.get("enrolled_fingers"), list):
            enrolled = user_meta["enrolled_fingers"]
            had_slots = len(enrolled) > 0
            if finger_id in enrolled:
                enrolled.remove(finger_id)
                user_meta["enrolled_fingers"] = sorted(enrolled)
            if had_slots and not enrolled:
                meta.pop(target_user, None)
                data.pop(target_user, None)

    atomic_write_json(cfg_file, data, mode=0o644)
