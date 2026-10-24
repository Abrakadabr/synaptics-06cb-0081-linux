#!/bin/bash
# /usr/lib/systemd/system-sleep/synaptics-bio-sleep
# Ensures Synaptics 06cb:0081 USB device is cleanly managed across system suspend/resume.

case "$1/$2" in
    pre/*)
        # Terminate any running biometric helper or container before suspend
        pkill -x identify.exe 2>/dev/null || true
        pkill -f /usr/local/bin/synaptics-bio-auth 2>/dev/null || true
        for cid in $(podman ps -q --filter "name=synaptics-bio-auth" 2>/dev/null); do
            podman rm -f "$cid" 2>/dev/null || true
        done
        rm -f /run/lock/synaptics-bio.lock /run/lock/synaptics-bio.meta 2>/dev/null || true
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
        /usr/bin/usbreset 06cb:0081 2>/dev/null || true
        ;;
esac
