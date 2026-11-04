"""Best-effort desktop notifications using only what the operating system ships.

``notify`` never raises. It returns True only when the platform command ran and
exited 0, and False for everything else (unsupported platform, missing binary,
timeout, non-zero exit). Nothing here touches the game process, its windows or
the screen.

Windows uses Windows PowerShell 5.1 (``powershell.exe``, not ``pwsh``, because
the WinRT projection used for toasts only exists there), macOS uses
``osascript`` and Linux uses ``notify-send``. Standard library only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unicodedata
from xml.sax.saxutils import escape

__all__ = ["notify", "notify_available"]

# The AppUserModelID of Windows PowerShell. It is registered on every Windows
# install, which is what lets the toast appear; an unregistered id fails silently.
_WINDOWS_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_XML_ENV_VAR = "WOWCLAUDE_TOAST_XML"

# The toast XML travels in an environment variable so no user text is ever
# spliced into PowerShell source.
_WINDOWS_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "[Windows.UI.Notifications.ToastNotificationManager, "
    "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
    "[Windows.Data.Xml.Dom.XmlDocument, "
    "Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null; "
    "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
    f"$xml.LoadXml($env:{_XML_ENV_VAR}); "
    "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml; "
    "[Windows.UI.Notifications.ToastNotificationManager]"
    f"::CreateToastNotifier('{_WINDOWS_APP_ID}').Show($toast)"
)

_MAX_TITLE = 200
_MAX_BODY = 1000


def _clean(text: object, limit: int) -> str:
    """Strip control characters (whitespace controls become spaces) and cap length."""
    out: list[str] = []
    for ch in str(text):
        if ch in "\r\n\t":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cs"):
            continue
        else:
            out.append(ch)
    return "".join(out)[:limit]


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def notify_available() -> bool:
    """Return True when this platform has a notifier binary."""
    if sys.platform == "win32":
        return shutil.which("powershell.exe") is not None
    if sys.platform == "darwin":
        return shutil.which("osascript") is not None
    if _is_linux():
        return shutil.which("notify-send") is not None
    return False


def _applescript_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _run(command: list[str], timeout: float, **kwargs: object) -> bool:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            **kwargs,  # type: ignore[arg-type]
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return result.returncode == 0


def notify(title: str, body: str, *, timeout: float = 10.0) -> bool:
    """Show a desktop notification. Best effort: never raises.

    Returns True only when the platform command exited 0.
    """
    try:
        title = _clean(title, _MAX_TITLE)
        body = _clean(body, _MAX_BODY)

        if sys.platform == "win32":
            powershell = shutil.which("powershell.exe")
            if powershell is None:
                return False
            xml = (
                '<toast><visual><binding template="ToastGeneric">'
                f"<text>{escape(title)}</text><text>{escape(body)}</text>"
                "</binding></visual></toast>"
            )
            env = dict(os.environ)
            env[_XML_ENV_VAR] = xml
            return _run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    _WINDOWS_SCRIPT,
                ],
                timeout,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        if sys.platform == "darwin":
            osascript = shutil.which("osascript")
            if osascript is None:
                return False
            script = (
                f"display notification {_applescript_quote(body)} "
                f"with title {_applescript_quote(title)}"
            )
            return _run([osascript, "-e", script], timeout)

        if _is_linux():
            notify_send = shutil.which("notify-send")
            if notify_send is None:
                return False
            return _run([notify_send, "--", title, body], timeout)
    except Exception:
        return False
    return False
