"""Text files read through a hidden Notepad, so a file arrives as the words a person sees in it.

The file opens in a Notepad started without a window (SW_HIDE in its STARTUPINFO), the text Notepad shows is read
from its text box with WM_GETTEXT, which Windows copies across processes, and that Notepad closes. Notepad has
already told the file's encoding (ANSI, UTF-8 or UTF-16), and a file that opens only in Notepad still reads.

Windows 11 Notepad hands a file to the Notepad that is already running: as a tab of a window that is open, or as a
new window. Only a window of the Notepad started here, or one that opened since with the file's name in its title,
is ever read or closed, so a window someone else opened is never touched.
"""
import contextlib
import os
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import NamedTuple

from .llm import UserError
from .markdown_import import MAX_MARKDOWN_CHARS
from .parts import MAX_PARTS

SECONDS = 30  # Notepad starts, opens the file and shows it.
HANDOVER_SECONDS = 5  # The window a running Notepad opens for the file after the one started here ended.
CLOSE_SECONDS = 5
ANSWER_SECONDS = 5  # A text box answers at once unless its Notepad is busy loading.
POLL_SECONDS = 0.2
# Looks at the same text length before it is read; an empty box may still be loading.
STEADY_LOOKS, EMPTY_LOOKS = 2, 5
# Notepad's text box, and that of Windows 11 Notepad.
TEXT_BOXES = ("Edit", "RichEditD2DPT")
# Read at most this much of a text: even with every line break as "\r\n", more than MAX_PARTS Wiki documents hold, so
# the import of a longer text saves that many parts and says the rest was left out (parts.py).
MAX_TEXT_CHARS = 2 * MAX_PARTS * MAX_MARKDOWN_CHARS

SW_HIDE = 0
GWL_STYLE = -16
WS_VISIBLE = 0x10000000
WM_GETTEXT, WM_GETTEXTLENGTH, WM_CLOSE = 0x000D, 0x000E, 0x0010
SMTO_ABORTIFHUNG, SMTO_ERRORONEXIT = 0x0002, 0x0020

CANCELLED = "가져오기를 취소했습니다."
HANDED_OVER = ("메모장이 이 파일을 이미 열려 있던 메모장 창에 탭으로 열었습니다(Windows 11 메모장). 그 탭을 닫고, "
               "열려 있는 메모장 창을 모두 닫은 뒤 다시 가져와 주세요.")
CLOSED = "메모장이 파일을 보여 주기 전에 닫혔습니다. 메모장에서 이 파일이 직접 열리는지, DRM 열람 권한을 확인해 주세요."
ENCRYPTED = ("메모장에 보인 내용이 글이 아니라 암호화된 데이터로 보여 가져오지 않았습니다. 메모장에서 이 파일을 "
             "직접 열어 내용이 보이는지(DRM 열람 권한) 확인해 주세요.")


class Window(NamedTuple):
    handle: int
    title: str
    pid: int
    visible: bool


class Desktop:
    """The user32 calls of the reader, through ctypes: WM_GETTEXT needs a buffer for Windows to copy the text into."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        # A library of its own, so these signatures never change ctypes.windll.user32 for other code.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        for name, arguments, result in (
                ("EnumWindows", [self.callback, wintypes.LPARAM], wintypes.BOOL),
                ("EnumChildWindows", [wintypes.HWND, self.callback, wintypes.LPARAM], wintypes.BOOL),
                ("GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                ("GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
                ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
                ("GetWindowLongW", [wintypes.HWND, ctypes.c_int], wintypes.LONG),
                ("ShowWindowAsync", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
                ("PostMessageW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
                ("SendMessageTimeoutW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT,
                                         wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)], wintypes.LPARAM)):
            function = getattr(user32, name)
            function.argtypes, function.restype = arguments, result
        self.user32 = user32

    def _string(self, function, handle, size):
        buffer = self.ctypes.create_unicode_buffer(size)
        function(handle, buffer, size)
        return buffer.value

    def _handles(self, enumerate_windows, *parent):
        found = []
        callback = self.callback(lambda handle, _: found.append(handle) or True)
        enumerate_windows(*parent, callback, 0)
        return found

    def _class(self, handle):
        return self._string(self.user32.GetClassNameW, handle, 64)

    def notepads(self):
        """The top-level Notepad windows, hidden ones too. A title is read without asking the window
        (GetWindowText of another process), so a busy Notepad cannot hold this up."""
        found = []
        for handle in self._handles(self.user32.EnumWindows):
            if self._class(handle) == "Notepad":
                pid = self.wintypes.DWORD()
                self.user32.GetWindowThreadProcessId(handle, self.ctypes.byref(pid))
                found.append(Window(handle, self._string(self.user32.GetWindowTextW, handle, 1024), pid.value,
                                    bool(self.user32.IsWindowVisible(handle))))
        return found

    def text_boxes(self, handle):
        """The text boxes in a window, each with whether its own style shows it: the hidden window hides them all, and
        Windows 11 Notepad may keep one for each tab."""
        return [(child, bool(self.user32.GetWindowLongW(child, GWL_STYLE) & WS_VISIBLE))
                for child in self._handles(self.user32.EnumChildWindows, handle) if self._class(child) in TEXT_BOXES]

    def _send(self, handle, message, wparam, lparam, seconds):
        result = self.ctypes.c_size_t()
        if not self.user32.SendMessageTimeoutW(handle, message, wparam, lparam, SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT,
                                               int(seconds * 1000), self.ctypes.byref(result)):
            return None
        return result.value

    def length(self, handle, seconds):
        """The length of a text box's text, or None when it does not answer in time."""
        return self._send(handle, WM_GETTEXTLENGTH, 0, 0, seconds)

    def text(self, handle, length, seconds):
        """A text box's text, at most `length` characters, or None when it does not answer in time."""
        buffer = self.ctypes.create_unicode_buffer(length + 1)
        copied = self._send(handle, WM_GETTEXT, length + 1, self.ctypes.addressof(buffer), seconds)
        return None if copied is None else buffer[:copied]

    def hide(self, handle):
        self.user32.ShowWindowAsync(handle, SW_HIDE)

    def close(self, handle):
        """WM_CLOSE, as the window's close button: Notepad closes a file it has not changed without asking."""
        self.user32.PostMessageW(handle, WM_CLOSE, 0, 0)


def notepad_path():
    """Windows' Notepad. Where Windows 11's Notepad app is installed, starting this starts that app."""
    return Path(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "notepad.exe")


def launch(path):
    """Notepad with the file, its window hidden from the start."""
    notepad = notepad_path()
    startup = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=SW_HIDE)
    try:
        # Standard handles of its own, so Notepad never holds the backend's stdin and stdout.
        return subprocess.Popen([str(notepad), str(path)], startupinfo=startup, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        raise UserError(f"메모장({notepad})을 시작하지 못했습니다.") from None


def showing(title, path):
    """Whether a Notepad title shows the file: "name - 메모장", the extension hidden as Explorer may hide it, and
    "*" in front once the text changed."""
    title = title.lstrip("*").strip().lower()
    return any(title == name or title.startswith(name + " - ") for name in (path.name.lower(), path.stem.lower()))


def text_box(desktop, handle):
    boxes = desktop.text_boxes(handle)
    return next((box for box, shown in boxes if shown), boxes[0][0] if boxes else None)


def looks_encrypted(text):
    """Whether the text is encrypted bytes Notepad could only show as characters: control characters other than tabs,
    line and page breaks and escapes, surrogates, private-use and unassigned code points and replacement characters
    make up a tenth to a half of such text, and next to nothing of a written one. The private-use characters of symbol
    fonts (U+F000-U+F0FF, such as the Wingdings bullets Office pastes) are writing; they are 256 of the 6,400
    private-use code points, so encrypted text still has plenty of the others."""
    odd = sum(1 for char in text if char == "\ufffd" or (
        unicodedata.category(char) in ("Cc", "Cs", "Co", "Cn") and char not in "\t\n\r\f\v\x1b"
        and not "\uf000" <= char <= "\uf0ff"))
    return odd >= 8 and odd >= len(text) * 0.02


class Reading:
    """One file in the Notepad started for it: waits until a window shows the file and its text length has held,
    then reads the text."""

    def __init__(self, desktop, process, before, path, seconds):
        self.desktop, self.process, self.before, self.path, self.seconds = desktop, process, before, path, seconds
        self.window = None  # The window for the file, once there is one; it closes however the reading ends.
        self.ended = None  # When the started Notepad ended, having handed the file to a running one.
        self.length, self.looks = None, 0
        self.problem = (f"메모장이 {seconds:g}초 안에 파일을 열지 못했습니다. 메모장에서 이 파일이 직접 열리는지, "
                        "DRM 열람 권한을 확인해 주세요.")

    def text(self, stop):
        deadline = time.monotonic() + self.seconds
        while True:
            if stop is not None and stop.is_set():
                raise UserError(CANCELLED)
            clock = time.monotonic()
            if self.ended is None and self.process.poll() is not None:
                self.ended = clock
                if self.window is not None:  # It had a window of its own, so it closed rather than handed over.
                    raise UserError(CLOSED)
            window = self.find()
            if window is not None:
                text = self.look(window)
                if text is not None:
                    return text
            elif self.ended is not None and clock - self.ended >= HANDOVER_SECONDS:
                raise UserError(HANDED_OVER)
            if clock >= deadline:
                raise UserError(self.problem)
            time.sleep(POLL_SECONDS)

    def find(self):
        """A window of the started Notepad or, once that one ended, a window that opened since with the file's name
        in its title."""
        for window in self.desktop.notepads():
            if self.ended is None:
                if window.pid == self.process.pid:
                    return window
            elif window.handle not in self.before and showing(window.title, self.path):
                return window
        return None

    def look(self, window):
        """The window's text once its title shows the file and the length has held, otherwise None."""
        self.window = window
        if window.visible:  # Windows 11 Notepad may show its window all the same.
            self.desktop.hide(window.handle)
        if not showing(window.title, self.path):
            return None  # Still loading the file.
        box = text_box(self.desktop, window.handle)
        if box is None:
            self.problem = "메모장 창에서 글 상자를 찾지 못했습니다."
            return None
        length = self.desktop.length(box, ANSWER_SECONDS)
        if length is None:
            self.problem = "메모장이 응답하지 않아 글을 읽지 못했습니다."
            return None
        length = min(length, MAX_TEXT_CHARS)  # WM_GETTEXT copies the beginning of a longer text
        self.looks = self.looks + 1 if length == self.length else 1
        self.length = length
        if self.looks < (STEADY_LOOKS if length else EMPTY_LOOKS):
            return None
        return self.desktop.text(box, length, ANSWER_SECONDS)


def close(desktop, process, window):
    """Close what was opened for the file: its window by WM_CLOSE. The Notepad started here is ended if it still runs
    after that, as one stuck at a dialog nobody can see would."""
    if window is not None:
        with contextlib.suppress(Exception):
            desktop.close(window.handle)
    if process.poll() is None:
        try:
            process.wait(CLOSE_SECONDS if window is not None else 0)
        except subprocess.TimeoutExpired:
            process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(CLOSE_SECONDS)


def read_with_notepad(path, stop=None, *, seconds=SECONDS, desktop=None, start=launch):
    """The text of a file as Notepad shows it, with "\\n" line breaks. `stop` cancels; `desktop` and `start` stand in
    for user32 and Notepad in contract tests."""
    if desktop is None:
        if sys.platform != "win32":
            raise UserError("텍스트 파일은 메모장이 있는 Windows에서 가져올 수 있습니다.")
        desktop = Desktop()
    path = Path(path).resolve()
    before = {window.handle for window in desktop.notepads()}
    process = start(path)
    reading = Reading(desktop, process, before, path, seconds)
    try:
        text = reading.text(stop)
    finally:
        close(desktop, process, reading.window)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if looks_encrypted(text):
        raise UserError(ENCRYPTED)
    return text
