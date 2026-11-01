#!/usr/bin/env python3
"""
patch-lockscreen.py
Applies or reverts biometric responsiveness enhancements to KDE Plasma's LockScreenUi.qml.

Fixes addressed:
1. Message replacement & non-interactive PAM signal forwarding: forwards messages from
   /etc/pam.d/kde-fingerprint (onNoninteractiveInfo / onNoninteractiveError) to handleMessage()
   and replaces stale fingerprint status lines instead of stacking them.
2. Inactivity fadeout suppression: keeps lockscreen UI active while biometric sensor is initializing,
   scanning, or re-initializing so the screen does not close after a failed attempt.
3. Safe auth trigger & heartbeat pause: avoids invoking authenticator.startAuthenticating() when
   transitioning to invisible, pauses the 1000ms heartbeat timer after fingerprint timeout/exhaustion
   until fadeout or wake, and never calls authenticator.stopAuthenticating() outside onSucceeded().
4. Multi-monitor boundary exit protection: prevents onExited / ActiveScreenMonitor from prematurely
   closing the lockscreen UI when the mouse cursor crosses display edges during biometric operations.
5. Upgrade-safe & idempotent: refreshes .orig-bio whenever plasma-desktop updates LockScreenUi.qml
   and exits cleanly without writing if already patched (safe for systemd.path PathChanged= watches).
"""

import sys
import os
import shutil

DEFAULT_LOCKSCREEN_QML = "/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/LockScreenUi.qml"


def apply_patch(qml_path, force=False):
    if not os.path.isfile(qml_path):
        print(f"Error: '{qml_path}' not found.", file=sys.stderr)
        return False

    with open(qml_path, "r", encoding="utf-8") as f:
        live_content = f.read()

    backup_path = qml_path + ".orig-bio"

    # If the live file is already patched and --force was not requested, do nothing (idempotent for systemd.path)
    if "biometricActive" in live_content and "onNoninteractiveInfo" in live_content and not force:
        print(f"'{qml_path}' is already patched.")
        return True

    # If the live file is unpatched (fresh install or overwritten by a plasma-desktop RPM update),
    # refresh the backup from the current unpatched upstream file.
    if "biometricActive" not in live_content or not os.path.isfile(backup_path):
        shutil.copy2(qml_path, backup_path)
        print(f"Updated upstream backup at '{backup_path}'.")

    with open(backup_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update handleMessage to ignore empty strings, replace fingerprint messages, and refresh fadeout timer
    old_handles = [
        """    function handleMessage(msg) {
        if (!root.notification) {
            root.notification += msg;
        } else if (root.notification.includes(msg)) {
            root.notificationRepeated();
        } else {
            root.notification += "\\n" + msg
        }
        lockScreenRoot.uiVisible = true
    }""",
        """    function handleMessage(msg) {
        if (!root.notification) {
            root.notification += msg;
        } else if (root.notification.includes(msg)) {
            root.notificationRepeated();
        } else {
            root.notification += "\\n" + msg
        }
    }""",
        """    function handleMessage(msg) {
        if (!root.notification || msg.startsWith("[Fingerprint") || msg.startsWith("Fingerprint")) {
            root.notification = msg;
        } else if (root.notification.includes(msg)) {
            root.notificationRepeated();
        } else {
            root.notification += "\\n" + msg
        }
    }""",
    ]

    new_handle = """    function handleMessage(msg) {
        if (!msg) {
            return;
        }
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

    for old_handle in old_handles:
        if old_handle in content:
            content = content.replace(old_handle, new_handle, 1)
            break

    # 2. Forward non-interactive fingerprint messages (/etc/pam.d/kde-fingerprint) to handleMessage
    old_prompt_secret = """        function onPromptForSecretChanged() {
            mainBlock.showPassword = false;
            mainBlock.mainPasswordBox.forceActiveFocus();
            if (lockScreenUi.showPrompt) {
                lockScreenUi.handleMessage(authenticator.promptForSecret);
            }
        }"""

    new_prompt_secret = """        function onPromptForSecretChanged() {
            mainBlock.showPassword = false;
            mainBlock.mainPasswordBox.forceActiveFocus();
            if (lockScreenUi.showPrompt) {
                lockScreenUi.handleMessage(authenticator.promptForSecret);
            }
        }

        function onNoninteractiveInfo(kind, noninteractiveAuthenticator) {
            if (noninteractiveAuthenticator.infoMessage) {
                lockScreenUi.handleMessage(noninteractiveAuthenticator.infoMessage);
            }
        }

        function onNoninteractiveError(kind, noninteractiveAuthenticator) {
            if (noninteractiveAuthenticator.errorMessage) {
                lockScreenUi.handleMessage(noninteractiveAuthenticator.errorMessage);
            }
        }"""

    if old_prompt_secret in content:
        content = content.replace(old_prompt_secret, new_prompt_secret, 1)

    # 3. Reset notification state on suspend so waking from sleep starts a fresh fingerprint scan
    old_suspend = """    Connections {
        target: sessionManagement
        function onAboutToSuspend() {
            root.clearPassword();
        }
    }"""

    new_suspend = """    Connections {
        target: sessionManagement
        function onAboutToSuspend() {
            root.clearPassword();
            root.notification = "";
            lockScreenRoot.uiVisible = true;
            fadeoutTimer.restart();
        }
    }"""

    if old_suspend in content:
        content = content.replace(old_suspend, new_suspend, 1)

    # 4. Add biometricActive to blockUI (supports both Plasma 6.7.5+ Keyboards.KWinVirtualKeyboard and older inputPanel)
    old_block_ui_kwin = """        property bool uiVisible: false
        property bool seenPositionChange: false
        property bool blockUI: containsMouse && (mainStack.depth > 1 || mainBlock.mainPasswordBox.text.length > 0 || Keyboards.KWinVirtualKeyboard.visible)"""

    new_block_ui_kwin = """        property bool uiVisible: false
        property bool seenPositionChange: false
        readonly property bool biometricActive: (root.notification.startsWith("[Fingerprint") || root.notification.startsWith("Fingerprint"))
            && !root.notification.includes("Falling back")
            && !root.notification.includes("Adjust finger or use password")
            && !root.notification.includes("not '")
        property bool blockUI: (containsMouse && (mainStack.depth > 1 || mainBlock.mainPasswordBox.text.length > 0 || Keyboards.KWinVirtualKeyboard.visible)) || biometricActive"""

    old_block_ui_legacy = """        property bool uiVisible: false
        property bool seenPositionChange: false
        property bool blockUI: containsMouse && (mainStack.depth > 1 || mainBlock.mainPasswordBox.text.length > 0 || inputPanel.keyboardActive)"""

    new_block_ui_legacy = """        property bool uiVisible: false
        property bool seenPositionChange: false
        readonly property bool biometricActive: (root.notification.startsWith("[Fingerprint") || root.notification.startsWith("Fingerprint"))
            && !root.notification.includes("Falling back")
            && !root.notification.includes("Adjust finger or use password")
            && !root.notification.includes("not '")
        property bool blockUI: (containsMouse && (mainStack.depth > 1 || mainBlock.mainPasswordBox.text.length > 0 || inputPanel.keyboardActive)) || biometricActive"""

    if old_block_ui_kwin in content:
        content = content.replace(old_block_ui_kwin, new_block_ui_kwin, 1)
    elif old_block_ui_legacy in content:
        content = content.replace(old_block_ui_legacy, new_block_ui_legacy, 1)

    # 5. Guard authenticator.startAuthenticating() inside if (uiVisible)
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

    # 6. Pause 1000ms heartbeat Timer once fingerprint scan times out or exhausts attempts until fadeout/wake clears root.notification
    old_heartbeat = """        Timer { // heart beat keeping the backend active for as long as we are in visible state
            interval: 1000
            running: !authenticator.unlocked && parent.uiVisible
            repeat: true
            onTriggered: authenticator.startAuthenticating()
        }"""

    new_heartbeat = """        Timer { // heart beat keeping the backend active for as long as we are in visible state
            interval: 1000
            running: !authenticator.unlocked && parent.uiVisible && !root.notification.includes("Falling back") && !root.notification.includes("Adjust finger or use password")
            repeat: true
            onTriggered: authenticator.startAuthenticating()
        }"""

    if old_heartbeat in content:
        content = content.replace(old_heartbeat, new_heartbeat, 1)

    # 7. Prevent ActiveScreenMonitor (Plasma 6.7.5+) or onExited (older Plasma 6) from closing UI when blockUI is active
    old_active_screen = """            onActiveChanged: {
                if(!active) {
                    lockScreenRoot.uiVisible = false;
                }
            }"""

    new_active_screen = """            onActiveChanged: {
                if(!active && !lockScreenRoot.blockUI) {
                    lockScreenRoot.uiVisible = false;
                }
            }"""

    if old_active_screen in content:
        content = content.replace(old_active_screen, new_active_screen, 1)

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

    # 8. Clear notification on Escape key press so next wake-up starts fresh fingerprint auth
    old_escape_kwin = """        Keys.onEscapePressed: {
            // If the escape key is pressed, kscreenlocker will turn off the screen.
            // We do not want to show the password prompt in this case.
            if (uiVisible) {
                uiVisible = false;
                if (Keyboards.KWinVirtualKeyboard.visible) {
                    Keyboards.KWinVirtualKeyboard.active = false;
                }
                root.clearPassword();
            }
        }"""

    new_escape_kwin = """        Keys.onEscapePressed: {
            // If the escape key is pressed, kscreenlocker will turn off the screen.
            // We do not want to show the password prompt in this case.
            if (uiVisible) {
                uiVisible = false;
                if (Keyboards.KWinVirtualKeyboard.visible) {
                    Keyboards.KWinVirtualKeyboard.active = false;
                }
                root.clearPassword();
                root.notification = "";
            }
        }"""

    if old_escape_kwin in content:
        content = content.replace(old_escape_kwin, new_escape_kwin, 1)

    # 9. Extend fadeoutTimer interval, clear stale notification on fadeout, and protect notificationRemoveTimer
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
                    root.notification = "";
                }
            }
        }
        Timer {
            id: notificationRemoveTimer
            interval: 3000
            onTriggered: {
                if (!lockScreenRoot.biometricActive) {
                    root.notification = "";
                } else if (root.notification.includes("\\n")) {
                    root.notification = root.notification.split("\\n")[0];
                }
            }
        }"""

    if old_timers in content:
        content = content.replace(old_timers, new_timers, 1)

    # 10. Ensure authenticator starts on initial component load
    old_completed = "        Component.onCompleted: launchAnimation.start();"
    new_completed = """        Component.onCompleted: {
            launchAnimation.start();
            authenticator.startAuthenticating();
        }"""
    if old_completed in content:
        content = content.replace(old_completed, new_completed, 1)

    if "biometricActive" not in content or "onNoninteractiveInfo" not in content:
        print(f"Error: Failed to match expected QML blocks in '{qml_path}'.", file=sys.stderr)
        return False

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
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv[1:]

    if len(args) < 1 or args[0] not in ("--apply", "--revert"):
        print("Usage: patch-lockscreen.py <--apply|--revert> [--force] [path/to/LockScreenUi.qml]")
        sys.exit(1)

    action = args[0]
    qml_path = args[1] if len(args) > 1 else DEFAULT_LOCKSCREEN_QML

    if action == "--apply":
        success = apply_patch(qml_path, force=force)
    else:
        success = revert_patch(qml_path)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
