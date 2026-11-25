#!/bin/bash
# /usr/lib/systemd/system-sleep/synaptics-bio-sleep
# Ensures Synaptics 06cb:0081 USB device is cleanly managed across system suspend/resume.

case "$1/$2" in
    pre/*)
        # Mark suspend in progress so auth helpers wait until post-resume usbreset completes
        mkdir -p /run/synaptics-bio 2>/dev/null || true
        chmod 0755 /run/synaptics-bio 2>/dev/null || true
        touch /run/synaptics-bio/suspending 2>/dev/null || true
        # Stop active Wine/sensor processes (across rootful and rootless sessions) so the USB
        # handle is released before suspend while allowing synaptics-bio-auth's CLOCK_BOOTTIME
        # handler to re-arm cleanly after wake.
        pkill -x identify.exe 2>/dev/null || true
        pkill -x wineserver 2>/dev/null || true
        for cid in $(podman ps -q --filter "name=synaptics-bio-auth" 2>/dev/null); do
            podman rm -f "${cid}" 2>/dev/null || true
        done
        ;;
    post/*)
        # On wake-up, ensure USB autosuspend is disabled and cryptoprocessor is reset
        for p in /sys/bus/usb/devices/*; do
            if [ -f "$p/idVendor" ] && [ -f "$p/idProduct" ]; then
                if [ "$(cat "$p/idVendor" 2>/dev/null)" = "06cb" ] && [ "$(cat "$p/idProduct" 2>/dev/null)" = "0081" ]; then
                    if [ -f "$p/power/control" ]; then
                        echo "on" > "$p/power/control"
                    fi
                fi
            fi
        done
        # Ensure root-owned runtime directory and shared lock/meta files have proper permissions
        mkdir -p /run/synaptics-bio 2>/dev/null || true
        chmod 0755 /run/synaptics-bio 2>/dev/null || true
        for f in /run/synaptics-bio/lock /run/synaptics-bio/meta; do
            [ -L "$f" ] && rm -f "$f"
            touch "$f" 2>/dev/null || true
            chown root:root "$f" 2>/dev/null || true
            chmod 0666 "$f" 2>/dev/null || true
        done
        USBRESET_BIN="$(command -v usbreset 2>/dev/null || echo /usr/bin/usbreset)"
        if [ -x "${USBRESET_BIN}" ]; then
            "${USBRESET_BIN}" 06cb:0081 2>/dev/null || true
        fi
        rm -f /run/synaptics-bio/suspending 2>/dev/null || true
        ;;
esac
