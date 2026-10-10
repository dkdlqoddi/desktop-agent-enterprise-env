"""The hidden Notepad reader against a stand-in desktop: which window it reads and closes, when it reads, and what it
refuses. The real user32 and Notepad run only on Windows (PRIVATE_AGENT_NOTEPAD_TEST=1)."""

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from agent import notepad
from agent.llm import UserError
from agent.markdown_import import MAX_MARKDOWN_CHARS
from agent.notepad import MAX_TEXT_CHARS, Window, looks_encrypted, read_with_notepad
from agent.parts import MAX_PARTS


class Process:
    """The started Notepad: it runs until its window closes, or ends at once after handing the file over."""

    def __init__(self, pid=100, running=True):
        self.pid, self.code, self.killed = pid, None if running else 0, False

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        if self.code is None:
            raise subprocess.TimeoutExpired("notepad.exe", timeout)
        return self.code

    def kill(self):
        self.killed, self.code = True, 1


class Desktop:
    """Top-level Notepad windows as user32 shows them: {handle: {title, pid, visible, boxes: {box: (shown, text)}}}.
    steps[n] changes them at the n-th look, as Notepad does while it starts and loads the file (the first look is the
    one before Notepad starts). WM_CLOSE closes a window and ends its Notepad, unless a dialog keeps it ("stuck")."""

    def __init__(self, windows=None, steps=None, process=None):
        self.windows, self.steps, self.process = windows or {}, steps or {}, process
        self.looks, self.events = 0, []

    def notepads(self):
        self.looks += 1
        if self.looks in self.steps:
            self.steps[self.looks](self)
        return [Window(handle, item["title"], item["pid"], item["visible"]) for handle, item in self.windows.items()]

    def text_boxes(self, handle):
        return [(box, shown) for box, (shown, _) in self.windows[handle]["boxes"].items()]

    def _text(self, box):
        return next(item["boxes"][box][1] for item in self.windows.values() if box in item["boxes"])

    def length(self, box, seconds):
        self.events.append(("length", box))
        text = self._text(box)
        return None if text is None else len(text)

    def text(self, box, length, seconds):
        self.events.append(("text", box))
        return self._text(box)[:length]

    def hide(self, handle):
        self.events.append(("hide", handle))
        self.windows[handle]["visible"] = False

    def close(self, handle):
        self.events.append(("close", handle))
        if not self.windows[handle].get("stuck"):
            item = self.windows.pop(handle)
            if self.process and item["pid"] == self.process.pid:
                self.process.code = 0


OTHER = {5: {"title": "회의록.txt - Windows 메모장", "pid": 50, "visible": True, "boxes": {50: (True, "원본 창")}}}


def starting(text, title="회의록 - Windows 메모장", **window):
    """Notepad as it starts: a hidden window without a file first, then the file's name once the text has loaded."""

    def appear(desktop):
        desktop.windows[7] = {
            "title": "제목 없음 - Windows 메모장",
            "pid": 100,
            "visible": False,
            "boxes": {70: (True, "")},
            **window,
        }

    def load(desktop):
        desktop.windows[7].update(title=title, boxes={70: (True, text)})

    return {3: appear, 5: load}


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(notepad, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(notepad, "HANDOVER_SECONDS", 0.2)
    path = tmp_path / "회의록.txt"
    path.write_bytes(b"\x9b\x01 encrypted bytes Python must not decode")
    return path


def read(source, desktop, process, stop=None, seconds=5):
    started = []
    text = read_with_notepad(
        source, stop, seconds=seconds, desktop=desktop, start=lambda path: started.append(path) or process
    )
    assert started == [source.resolve()]
    return text


def test_reads_the_text_of_the_hidden_notepad_it_started_and_closes_it(source):
    process = Process()
    desktop = Desktop(dict(OTHER), starting("가나다\r\n둘째 줄\r\n<끝>"), process)
    assert read(source, desktop, process) == "가나다\n둘째 줄\n<끝>"
    # The text is read once the title shows the file and its length has held; the window was hidden all along.
    assert desktop.events == [("length", 70), ("length", 70), ("text", 70), ("close", 7)]
    assert 7 not in desktop.windows and process.code == 0 and not process.killed
    assert 5 in desktop.windows


def test_windows_11_notepad_is_hidden_and_the_tab_of_the_file_is_read(source):
    # Windows 11 Notepad may show its window all the same, and restore earlier tabs beside the file's.
    process = Process()
    window = {
        "title": "회의록.txt - 메모장",
        "pid": 100,
        "visible": True,
        "boxes": {80: (False, "지난 세션의 탭"), 81: (True, "이 파일의 글")},
    }
    desktop = Desktop(steps={3: lambda d: d.windows.update({8: window})}, process=process)
    assert read(source, desktop, process) == "이 파일의 글"
    assert desktop.events[0] == ("hide", 8) and desktop.events[-1] == ("close", 8)
    assert {event[1] for event in desktop.events if event[0] in ("length", "text")} == {81}


def test_a_running_notepad_that_opens_the_file_in_a_new_window_is_read_there(source):
    # Windows 11: the started Notepad hands the file to the running one and ends; that one opens a window for it.
    process = Process(running=False)
    window = {"title": "회의록.txt - 메모장", "pid": 50, "visible": True, "boxes": {90: (True, "새 창의 글")}}
    user = {6: {"title": "메모 - 메모장", "pid": 50, "visible": True, "boxes": {60: (True, "사용자 메모")}}}
    desktop = Desktop(user, {4: lambda d: d.windows.update({9: window})})
    assert read(source, desktop, process) == "새 창의 글"
    assert ("hide", 9) in desktop.events and desktop.events[-1] == ("close", 9)
    assert all(event[1] not in (6, 60) for event in desktop.events) and 6 in desktop.windows


def test_a_file_that_became_a_tab_of_an_open_window_is_left_to_the_user(source):
    # Windows 11: the file became a tab of a window that was already open; that window is never read or closed.
    process = Process(running=False)
    user = {6: {"title": "메모 - 메모장", "pid": 50, "visible": True, "boxes": {60: (True, "사용자 메모")}}}
    desktop = Desktop(user, {2: lambda d: d.windows[6].update(title="회의록.txt - 메모장")})
    with pytest.raises(UserError, match="이미 열려 있던 메모장 창에 탭으로 열었습니다"):
        read(source, desktop, process)
    assert desktop.events == [] and 6 in desktop.windows


def test_a_notepad_that_never_shows_the_file_is_ended(source):
    # A dialog nobody sees (a DRM refusal, say) keeps the window without the file; WM_CLOSE does not close it.
    process = Process()
    desktop = Desktop(steps=starting("", stuck=True), process=process)
    desktop.steps.pop(5)
    with pytest.raises(UserError, match=r"메모장이 0.3초 안에 파일을 열지 못했습니다\. .*DRM 열람 권한"):
        read(source, desktop, process, seconds=0.3)
    assert desktop.events == [("close", 7)] and process.killed

    # Without any window there is nothing to close: the Notepad is ended at once.
    process = Process()
    with pytest.raises(UserError, match="파일을 열지 못했습니다"):
        read(source, Desktop(process=process), process, seconds=0.2)
    assert process.killed


def test_a_notepad_that_closes_before_showing_the_file_says_so(source):
    # Its own window was there, so it did not hand the file to another Notepad: DRM software may have ended it.
    process = Process()
    steps = starting("글")
    steps[4] = lambda d: (d.windows.pop(7), setattr(process, "code", 1))
    with pytest.raises(UserError, match="파일을 보여 주기 전에 닫혔습니다"):
        read(source, Desktop(steps=steps, process=process), process)
    assert not process.killed


def test_cancelling_closes_the_notepad(source):
    stop = threading.Event()
    process = Process()
    steps = starting("글")
    steps[4] = lambda d: stop.set()
    desktop = Desktop(steps=steps, process=process)
    with pytest.raises(UserError, match="취소"):
        read(source, desktop, process, stop)
    assert desktop.events == [("close", 7)] and not process.killed


def test_text_that_is_not_the_document_is_refused_and_the_window_still_closes(source):
    # Encrypted bytes Notepad could only show as characters are not the document.
    process = Process()
    desktop = Desktop(steps=starting(bytes(range(256)).decode("cp949", errors="replace") * 4), process=process)
    with pytest.raises(UserError, match="암호화된 데이터"):
        read(source, desktop, process)
    assert desktop.events[-1] == ("close", 7) and not process.killed


def test_a_text_longer_than_the_import_keeps_is_read_up_to_that_length(source, monkeypatch):
    # More than the parts of a document hold even with "\r\n" line breaks; the import cuts the rest (parts.py).
    assert MAX_TEXT_CHARS == 2 * MAX_PARTS * MAX_MARKDOWN_CHARS
    monkeypatch.setattr(notepad, "MAX_TEXT_CHARS", 10)
    process = Process()
    desktop = Desktop(steps=starting("첫 줄\r\n둘째 줄\r\n셋째 줄"), process=process)
    assert read(source, desktop, process) == "첫 줄\n둘째 줄\n"
    assert desktop.events[-1] == ("close", 7) and not process.killed


def test_an_empty_text_box_is_read_once_it_has_stayed_empty(source):
    process = Process()
    desktop = Desktop(steps=starting(""), process=process)
    assert read(source, desktop, process) == ""
    assert [event for event in desktop.events if event[0] == "length"] == [("length", 70)] * notepad.EMPTY_LOOKS


def test_a_window_without_a_text_box_or_answer_says_so(source):
    process = Process()
    desktop = Desktop(steps=starting("글"), process=process)
    desktop.steps[6] = lambda d: d.windows[7].update(boxes={})
    with pytest.raises(UserError, match="글 상자를 찾지 못했습니다"):
        read(source, desktop, process, seconds=0.3)
    process = Process()
    desktop = Desktop(steps=starting(None), process=process)
    with pytest.raises(UserError, match="응답하지 않아"):
        read(source, desktop, process, seconds=0.3)


def test_titles_that_show_the_file():
    path = Path("C:/temp/회의록.v2.txt")
    for title in (
        "회의록.v2.txt - Windows 메모장",
        "회의록.v2 - Windows 메모장",
        "*회의록.v2.txt - 메모장",
        "회의록.V2.TXT - Notepad",
        "회의록.v2.txt",
    ):
        assert notepad.showing(title, path), title
    for title in ("제목 없음 - Windows 메모장", "회의록 - Windows 메모장", "회의록.v2.txt.bak - 메모장", ""):
        assert not notepad.showing(title, path), title
    # A file named like Notepad itself is not taken for the window before it has loaded.
    assert not notepad.showing("제목 없음 - Windows 메모장", Path("C:/temp/메모장.txt"))


def test_encrypted_bytes_shown_as_text_are_told_from_writing():
    data = bytes((index * 167 + 13) % 256 for index in range(8192))
    for encoding in ("cp949", "utf-8", "utf-16-le"):
        assert looks_encrypted(data.decode(encoding, errors="replace")), encoding
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    log = "".join(f"\x1b[32mINFO\x1b[0m {index}번 줄\tok\f\n" for index in range(200))
    # A note whose bullets came from Wingdings, as Office pastes them: symbol fonts' private-use characters.
    bullets = "".join(f" {index}번 항목: 확인 완료\n" for index in range(20))[:329]
    for text in (readme, log, "메모\r\n\x1a", readme[:4000] + "\x07" * 20, bullets):
        assert not looks_encrypted(text)
    # Encrypted bytes Notepad read as UTF-16 still are, symbol characters among them or not.
    assert looks_encrypted(data.decode("utf-16-le", errors="replace").replace("�", ""))


def test_only_windows_has_notepad(source, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(UserError, match="메모장이 있는 Windows"):
        read_with_notepad(source)


@pytest.mark.skipif(
    sys.platform != "win32" or os.getenv("PRIVATE_AGENT_NOTEPAD_TEST") != "1",
    reason="Requires Windows Notepad; opt in with PRIVATE_AGENT_NOTEPAD_TEST=1",
)
def test_real_notepad(tmp_path):
    files = {
        "메모장 ANSI.txt": ("메모장 한글\r\n둘째 줄", "cp949"),
        "utf8.txt": ("UTF-8 한글 글", "utf-8"),
        "wide.txt": ("유니코드 글", "utf-16"),
        "표.csv": ('이름,설명\r\n김개발,"두 줄"', "utf-8"),
    }
    for name, (text, encoding) in files.items():
        (tmp_path / name).write_bytes(text.encode(encoding))
        assert read_with_notepad(tmp_path / name, threading.Event()) == text.replace("\r\n", "\n"), name
    # Every Notepad it started has closed.
    left = [
        window.title
        for window in notepad.Desktop().notepads()
        if any(notepad.showing(window.title, tmp_path / name) for name in files)
    ]
    assert left == []
