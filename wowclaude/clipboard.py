"""Read-only OS clipboard access and a prefix-gated clipboard watcher.

The companion uses the clipboard as a one-way channel out of the game: the addon
shows an envelope string the player copies, and the watcher here picks it up.
Design rules, all deliberate:

* The clipboard is only ever read, never written.
* Text that does not start with the configured prefix is discarded immediately.
  No copy, hash or length of it is kept, and clipboard content is never logged.
* Nothing here touches the game process, its windows or the screen. Only the
  operating system clipboard is read.

Standard library only. Windows uses ctypes against user32/kernel32, macOS uses
``pbpaste`` and Linux uses ``wl-paste``, ``xclip`` or ``xsel``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

__all__ = ["read_clipboard_text", "clipboard_sequence", "ClipboardWatcher"]

_CF_UNICODETEXT = 13
_OPEN_ATTEMPTS = 5
_OPEN_RETRY_DELAY = 0.02
_SUBPROCESS_TIMEOUT = 2

_IS_WINDOWS = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"

_win_api = None  # lazily bound (user32, kernel32) pair


def _windows_api():
    """Bind the Win32 functions once, with full argtypes/restype declarations."""
    global _win_api
    if _win_api is not None:
        return _win_api

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD

    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    _win_api = (user32, kernel32)
    return _win_api


def _read_windows() -> tuple[str | None, bool]:
    """Return ``(text, definitive)``.

    ``definitive`` is False when the read failed for a transient reason (the
    clipboard stayed locked by another app), so a caller may retry the same
    clipboard generation. It is True when the answer is final: either text, or
    a clipboard that holds no text.
    """
    import ctypes

    user32, kernel32 = _windows_api()

    if not user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
        return None, True

    opened = False
    for attempt in range(_OPEN_ATTEMPTS):
        if user32.OpenClipboard(None):
            opened = True
            break
        if attempt < _OPEN_ATTEMPTS - 1:
            time.sleep(_OPEN_RETRY_DELAY)
    if not opened:
        return None, False

    try:
        handle = user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return None, False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None, False
        try:
            text = ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
        return (text or None), True
    finally:
        user32.CloseClipboard()


def _run_reader(command: list[str]) -> str | None:
    """Run a clipboard reader command; return its stdout text or None."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", errors="replace")
    return text or None


def _read_linux() -> str | None:
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        return _run_reader(["wl-paste", "-n"])
    if shutil.which("xclip"):
        return _run_reader(["xclip", "-o", "-selection", "clipboard"])
    if shutil.which("xsel"):
        return _run_reader(["xsel", "-b", "-o"])
    return None


def _read_checked() -> tuple[str | None, bool]:
    """Platform dispatch returning ``(text, definitive)``; see _read_windows."""
    try:
        if _IS_WINDOWS:
            return _read_windows()
        if _IS_MAC:
            text = _run_reader(["pbpaste"])
            return text, text is not None
        if sys.platform.startswith("linux"):
            text = _read_linux()
            return text, text is not None
    except Exception:
        return None, False
    return None, True


def read_clipboard_text() -> str | None:
    """Return the current clipboard text, or None when there is none.

    None is also returned when the platform has no supported reader or the read
    fails. The clipboard is never modified.
    """
    return _read_checked()[0]


def clipboard_sequence() -> int | None:
    """Return the Windows clipboard sequence number, or None elsewhere.

    The number changes every time the clipboard content changes, so a caller can
    poll it cheaply without opening the clipboard.
    """
    if not _IS_WINDOWS:
        return None
    try:
        return int(_windows_api()[0].GetClipboardSequenceNumber())
    except Exception:
        return None


class ClipboardWatcher:
    """Poll the clipboard and hand prefix-matching text to a callback.

    Text that does not start with ``prefix`` is dropped on the spot and nothing
    about it is retained. Matching text is delivered once: an envelope identical
    to the last one delivered is not delivered again until a different envelope
    has gone through.
    """

    def __init__(
        self,
        prefix: str,
        on_envelope: Callable[[str], None],
        interval: float = 0.5,
    ) -> None:
        self._prefix = prefix
        self._on_envelope = on_envelope
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_envelope: str | None = None
        self._last_sequence: int | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start the watcher thread. Calling it while running does nothing."""
        with self._lock:
            if self.running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="wowclaude-clipboard", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Ask the thread to stop and wait up to two seconds for it."""
        with self._lock:
            thread = self._thread
            self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop_event.wait(self._interval)

    def _tick(self) -> None:
        sequence = None
        if _IS_WINDOWS:
            sequence = clipboard_sequence()
            if sequence is not None and sequence == self._last_sequence:
                return

        text, definitive = _read_checked()

        if _IS_WINDOWS and definitive:
            # Only remember the generation once it was read for real, so a
            # clipboard that was briefly locked is retried on the next tick.
            self._last_sequence = sequence

        if text is None:
            return
        if not text.startswith(self._prefix):
            # Not ours: drop it without keeping anything derived from it.
            text = None
            return
        if text == self._last_envelope:
            return

        self._last_envelope = text
        try:
            self._on_envelope(text)
        except Exception:
            pass
