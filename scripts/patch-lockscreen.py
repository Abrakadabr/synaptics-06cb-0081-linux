#!/usr/bin/env python3
"""
patch-lockscreen.py
Applies or reverts biometric responsiveness enhancements to KDE Plasma's LockScreenUi.qml.

Fixes addressed:
1. Message replacement: prevents notification stacking of sensor init and auth messages.
2. Inactivity fadeout suppression: keeps lockscreen UI active while biometric sensor is initializing,
   scanning, or re-initializing so the screen does not close after a failed attempt.
3. Safe auth trigger: avoids invoking authenticator.startAuthenticating() when transitioning to invisible.
4. Multi-monitor boundary exit protection: prevents onExited from prematurely closing the lockscreen UI
   when the mouse cursor crosses display edges during biometric operations.
"""

import sys
import os
import shutil

DEFAULT_LOCKSCREEN_QML = "/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/LockScreenUi.qml"

def apply_patch(qml_path):
    if not os.path.isfile(qml_path):
        print(f"Error: '{qml_path}' not found.", file=sys.stderr)
        return False

    backup_path = qml_path + ".orig-bio"
    if not os.path.isfile(backup_path):
        shutil.copy2(qml_path, backup_path)
        print(f"Created backup at '{backup_path}'.")

    with open(backup_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update handleMessage to replace fingerprint messages and refresh fadeout timer
    old_handle = """    function handleMessage(msg) {
        if (!root.notification) {
            root.notification += msg;
        } else if (root.notification.includes(msg)) {
            root.notificationRepeated();
        } else {
            root.notification += "\\n" + msg
        }
    }"""

    new_handle = """    function handleMessage(msg) {
        if (!root.notification || msg.startsWith("[Fingerprint") || msg.startsWith("Fingerprint")) {
            root.notification = msg;
        } else if (root.notification.includes(msg)) {
            root.notificationRepeated();
        } else {
            root.notification += "\\n" + msg
        }
        lockScreenRoot.uiVisible = true;
        fadeoutTimer.restart();
    }"""

    if old_handle in content:
        content = content.replace(old_handle, new_handle, 1)
    else:
        # Fallback if previous simple sed was applied
        prev_sed_handle = """    function handleMessage(msg) {
        if (!root.notification || msg.startsWith("[Fingerprint") || msg.startsWith("Fingerprint")) {
            root.notification = msg;
        } else if (root.notification.includes(msg)) {
            root.notificationRepeated();
        } else {
            root.notification += "\\n" + msg
        }
    }"""
        if prev_sed_handle in content:
            content = content.replace(prev_sed_handle, new_handle, 1)

    # 2. Add biometricActive to blockUI
    old_block_ui = """        property bool uiVisible: false
        property bool seenPositionChange: false
        property bool blockUI: containsMouse && (mainStack.depth > 1 || mainBlock.mainPasswordBox.text.length > 0 || inputPanel.keyboardActive)"""

    new_block_ui = """        property bool uiVisible: false
        property bool seenPositionChange: false
        readonly property bool biometricActive: (root.notification.startsWith("[Fingerprint") || root.notification.startsWith("Fingerprint"))
            && !root.notification.includes("Falling back")
            && !root.notification.includes("Adjust finger or use password")
        property bool blockUI: (containsMouse && (mainStack.depth > 1 || mainBlock.mainPasswordBox.text.length > 0 || inputPanel.keyboardActive)) || biometricActive"""

    if old_block_ui in content:
        content = content.replace(old_block_ui, new_block_ui, 1)

    # 3. Guard authenticator.startAuthenticating() inside if (uiVisible)
    old_on_ui_visible = """        onUiVisibleChanged: {
            if (uiVisible) {
                Window.window.requestActivate();
            }

            if (blockUI) {
                fadeoutTimer.running = false;
            } else if (uiVisible) {
                fadeoutTimer.restart();
            }
            authenticator.startAuthenticating();
        }"""

    new_on_ui_visible = """        onUiVisibleChanged: {
            if (uiVisible) {
                Window.window.requestActivate();
                authenticator.startAuthenticating();
            }

            if (blockUI) {
                fadeoutTimer.running = false;
            } else if (uiVisible) {
                fadeoutTimer.restart();
            }
        }"""

    if old_on_ui_visible in content:
        content = content.replace(old_on_ui_visible, new_on_ui_visible, 1)

    # 4. Prevent onExited from closing UI when blockUI is active
    old_on_exited = """        onExited: {
            uiVisible = false;
        }"""

    new_on_exited = """        onExited: {
            if (!blockUI) {
                uiVisible = false;
            }
        }"""

    if old_on_exited in content:
        content = content.replace(old_on_exited, new_on_exited, 1)

    # 5. Extend fadeoutTimer interval and protect notificationRemoveTimer
    old_timers = """        Timer {
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
        }"""

    new_timers = """        Timer {
            id: fadeoutTimer
            interval: 15000
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
            onTriggered: {
                if (!lockScreenRoot.biometricActive) {
                    root.notification = "";
                }
            }
        }"""

    if old_timers in content:
        content = content.replace(old_timers, new_timers, 1)

    # 6. Ensure authenticator starts on initial component load
    old_completed = "        Component.onCompleted: launchAnimation.start();"
    new_completed = """        Component.onCompleted: {
            launchAnimation.start();
            authenticator.startAuthenticating();
        }"""
    if old_completed in content:
        content = content.replace(old_completed, new_completed, 1)

    tmp_path = qml_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)

    os.replace(tmp_path, qml_path)
    print(f"Successfully applied biometric enhancements to '{qml_path}'.")
    return True

def revert_patch(qml_path):
    backup_path = qml_path + ".orig-bio"
    if not os.path.isfile(backup_path):
        print(f"No backup file found at '{backup_path}'. Cannot revert.", file=sys.stderr)
        return False

    shutil.copy2(backup_path, qml_path)
    os.remove(backup_path)
    print(f"Successfully restored '{qml_path}' from backup.")
    return True

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("--apply", "--revert"):
        print("Usage: patch-lockscreen.py <--apply|--revert> [path/to/LockScreenUi.qml]")
        sys.exit(1)

    action = sys.argv[1]
    qml_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LOCKSCREEN_QML

    if action == "--apply":
        success = apply_patch(qml_path)
    else:
        success = revert_patch(qml_path)

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
