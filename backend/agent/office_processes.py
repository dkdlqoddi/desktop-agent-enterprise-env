"""The hidden Office app of an import that could not close it. A worker ended before it could close its file and quit
(stopped after the grace, killed by antivirus, crashed) leaves the Office app it started running unseen, with the file
open: only that worker could reach it through COM. The backend finds such an app afterwards, from outside, and ends it.

Nothing here talks to Office while a worker does, so the proven DispatchEx sequence stays as it was. Before a worker
starts, the backend notes the processes of the Office application it will use; once the worker has ended without an
answer, or said it could not close its file or quit, a process of that application is the import's when it is new (not
noted, or noted under another start time), started while the worker ran, started for automation (its command line has
-Embedding, as COM starts it) and shows no window but dialog boxes. The user's own Office (already running, started by
hand, or showing a document the user opened in it) never matches. Workers of one application take turns
(word_converter.Turns), so another job's worker is never running meanwhile.

While a PowerPoint worker runs, a password prompt PowerPoint shows for its file is cancelled, as Word and Excel refuse
such a file at once instead of asking (Watch.refuse_prompts)."""
import sys
import time
from dataclasses import dataclass

EXECUTABLES = {"Word": "winword.exe", "Excel": "excel.exe", "PowerPoint": "powerpnt.exe", "Visio": "visio.exe"}
# FILETIME counts 100 ns steps from 1601-01-01; the epoch is this many of them later.
EPOCH_AS_FILETIME = 116444736000000000
# A process's start time and the clock are read apart; a little slack keeps an exact start from being missed.
SLACK = 2.0
# Dialog boxes: Windows' own (message boxes too), Office's dialog manager (bosa_sdm_…) and Office's newer dialogs.
# A hidden Office that shows nothing else still has no window anybody works in: such a dialog asks the worker that
# is gone (a DRM add-in's notice, a prompt), so it does not keep the process from being ended.
DIALOGS = ("#32770", "NUIDialog")
DIALOG_PREFIXES = ("bosa_sdm_",)
# The title of the password prompt of PowerPoint, which asks where Word and Excel take the empty password they are
# given and fail (Korean and English Office; unverified on Windows).
PROMPTS = {"암호", "password"}
WM_CLOSE = 0x0010


def dialog(class_name):
    """Whether a window of this class is a dialog box rather than a window a document shows in."""
    return class_name in DIALOGS or class_name.startswith(DIALOG_PREFIXES)


@dataclass(frozen=True)
class Found:
    pid: int
    name: str
    started: float  # Seconds since the epoch.
    command_line: str


class Windows:
    """The kernel32, ntdll and user32 calls, through ctypes. Libraries of their own keep these signatures from
    changing ctypes.windll for other code."""
    PROCESS_TERMINATE = 0x0001
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    TH32CS_SNAPPROCESS = 0x2
    PROCESS_COMMAND_LINE_INFORMATION = 60
    # STATUS_INFO_LENGTH_MISMATCH, STATUS_BUFFER_TOO_SMALL: asked again with the size Windows names.
    TOO_SMALL = (0xC0000004, 0xC0000023)

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes

        class Entry(ctypes.Structure):  # PROCESSENTRY32W
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        class Text(ctypes.Structure):  # UNICODE_STRING
            _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                        ("Buffer", ctypes.c_void_p)]
        self.Entry, self.Text = Entry, Text
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        for library, name, arguments, result in (
                (kernel32, "CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
                (kernel32, "Process32FirstW", [wintypes.HANDLE, ctypes.POINTER(Entry)], wintypes.BOOL),
                (kernel32, "Process32NextW", [wintypes.HANDLE, ctypes.POINTER(Entry)], wintypes.BOOL),
                (kernel32, "OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
                (kernel32, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
                (kernel32, "GetProcessTimes", [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4,
                 wintypes.BOOL),
                (kernel32, "TerminateProcess", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
                (kernel32, "WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
                (ntdll, "NtQueryInformationProcess",
                 [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)],
                 ctypes.c_long),
                (user32, "EnumWindows", [self.callback, wintypes.LPARAM], wintypes.BOOL),
                (user32, "GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
                (user32, "IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
                (user32, "GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                (user32, "GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                (user32, "PostMessageW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
                 wintypes.BOOL)):
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        self.kernel32, self.user32, self.ntdll = kernel32, user32, ntdll

    def processes(self):
        """(pid, executable name) of every process."""
        invalid = self.wintypes.HANDLE(-1).value
        snapshot = self.kernel32.CreateToolhelp32Snapshot(self.TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == invalid:
            return []
        found = []
        try:
            entry = self.Entry()
            entry.dwSize = self.ctypes.sizeof(self.Entry)
            more = self.kernel32.Process32FirstW(snapshot, self.ctypes.byref(entry))
            while more:
                found.append((entry.th32ProcessID, entry.szExeFile))
                more = self.kernel32.Process32NextW(snapshot, self.ctypes.byref(entry))
        finally:
            self.kernel32.CloseHandle(snapshot)
        return found

    def open(self, pid, end=False):
        access = self.PROCESS_QUERY_LIMITED_INFORMATION | (self.PROCESS_TERMINATE | self.SYNCHRONIZE if end else 0)
        return self.kernel32.OpenProcess(access, False, pid) or None

    def close(self, handle):
        self.kernel32.CloseHandle(handle)

    def started(self, handle):
        times = [self.wintypes.FILETIME() for _ in range(4)]
        if not self.kernel32.GetProcessTimes(handle, *map(self.ctypes.byref, times)):
            return None
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return (ticks - EPOCH_AS_FILETIME) / 10_000_000

    def command_line(self, handle):
        size = self.wintypes.ULONG(4096)
        for _ in range(3):
            buffer = self.ctypes.create_string_buffer(size.value)
            status = self.ntdll.NtQueryInformationProcess(handle, self.PROCESS_COMMAND_LINE_INFORMATION, buffer,
                                                         size.value, self.ctypes.byref(size))
            if status & 0xFFFFFFFF in self.TOO_SMALL:
                continue
            if status < 0:
                return ""
            text = self.Text.from_buffer(buffer)
            return self.ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Buffer else ""
        return ""

    def _string(self, function, handle):
        buffer = self.ctypes.create_unicode_buffer(256)
        function(handle, buffer, 256)
        return buffer.value

    def visible(self):
        """(window, pid, title, class name) of the visible top-level windows. A title is read without asking the
        window (GetWindowText of another process), so a busy Office cannot hold this up."""
        found = []

        def visit(handle, _):
            if self.user32.IsWindowVisible(handle):
                pid = self.wintypes.DWORD()
                self.user32.GetWindowThreadProcessId(handle, self.ctypes.byref(pid))
                found.append((handle, pid.value, self._string(self.user32.GetWindowTextW, handle),
                              self._string(self.user32.GetClassNameW, handle)))
            return True
        self.user32.EnumWindows(self.callback(visit), 0)
        return found

    def close_window(self, handle):
        """WM_CLOSE, as a dialog's close button: it cancels the dialog."""
        self.user32.PostMessageW(handle, WM_CLOSE, 0, 0)

    def end(self, handle, seconds):
        if not self.kernel32.TerminateProcess(handle, 1):
            return False
        self.kernel32.WaitForSingleObject(handle, int(seconds * 1000))
        return True


def system():
    return Windows() if sys.platform == "win32" else None


def running(names, windows):
    """{pid: start time} of the processes of these executables."""
    found = {}
    for pid, name in windows.processes():
        if name.lower() in names:
            handle = windows.open(pid)
            if handle:
                try:
                    found[pid] = windows.started(handle)
                finally:
                    windows.close(handle)
    return found


class Watch:
    """The Office processes before a worker starts, to tell afterwards which one the worker left behind."""

    def __init__(self, applications, windows=None, clock=time.time):
        self.windows = windows if windows is not None else system()
        self.names = {EXECUTABLES[name] for name in applications if name in EXECUTABLES}
        self.clock = clock
        self.since = clock()
        self.before = running(self.names, self.windows) if self.windows and self.names else {}
        # Whether a password prompt for the worker's file may show (PowerPoint), and those showing before it started.
        self.prompting = bool(self.windows) and EXECUTABLES["PowerPoint"] in self.names
        self.prompts_before = set()
        if self.prompting:
            try:
                self.prompts_before = set(self.prompts())
            except Exception:
                self.prompting = False  # Windows would not tell: the time limit stands, as before

    def prompts(self):
        """The password prompts PowerPoint shows."""
        pids = {pid for pid, name in self.windows.processes() if name.lower() == EXECUTABLES["PowerPoint"]}
        return [handle for handle, pid, title, _ in self.windows.visible()
                if pid in pids and " ".join(title.split()).lower() in PROMPTS]

    def refuse_prompts(self):
        """Cancel the password prompts PowerPoint showed since the worker started, which ask for the worker's file;
        whether there was one. One the user had open before stays."""
        try:
            found = [handle for handle in self.prompts() if handle not in self.prompts_before]
            for handle in found:
                self.windows.close_window(handle)
        except Exception:
            self.prompting = False  # Windows would not tell: the time limit stands, as before
            return False
        return bool(found)

    def end_leftovers(self, seconds=5):
        """End the hidden Office processes the worker started and left; what was ended, as Found."""
        if not self.windows or not self.names:
            return []
        until = self.clock() + SLACK
        # A process that shows a window other than a dialog box is someone's: a document the user opened in it.
        shown = {pid for _, pid, _, kind in self.windows.visible() if not dialog(kind)}
        ended = []
        for pid, name in self.windows.processes():
            if name.lower() not in self.names or pid in shown:
                continue
            handle = self.windows.open(pid, end=True)
            if not handle:
                continue
            try:
                # Checked through the handle that ends it, so a process ID used again in between cannot mislead.
                started = self.windows.started(handle)
                if started is None or self.before.get(pid) == started:
                    continue
                if not self.since - SLACK <= started <= until:
                    continue
                command_line = self.windows.command_line(handle)
                if "-embedding" not in command_line.lower():
                    continue
                if self.windows.end(handle, seconds):
                    ended.append(Found(pid, name, started, command_line))
            finally:
                self.windows.close(handle)
        return ended
