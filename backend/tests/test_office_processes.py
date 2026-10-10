import sys
import threading
import time
from pathlib import Path

import pytest

from agent import word_converter
from agent.llm import UserError
from agent.office_converter import OfficeConverter
from agent.office_processes import Watch, dialog, system
from agent.word_converter import PASSWORD, WAITING, Turns

AUTOMATION = r'"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE" /Automation -Embedding'


class FakeWindows:
    """A process table and the visible windows in place of the Windows calls; `shown` processes show a document."""

    def __init__(self, table, shown=(), windows=()):
        self.table, self.ended, self.closed = table, [], []
        self.windows = [(1000 + pid, pid, "문서 - Word", "OpusApp") for pid in shown] + list(windows)

    def processes(self):
        return [(pid, entry["name"]) for pid, entry in self.table.items() if entry["alive"]]

    def open(self, pid, end=False):
        return pid if self.table.get(pid, {}).get("alive") else None

    def close(self, handle):
        pass

    def started(self, handle):
        return self.table[handle]["started"]

    def command_line(self, handle):
        return self.table[handle]["command_line"]

    def visible(self):
        return list(self.windows)

    def close_window(self, handle):
        self.closed.append(handle)
        self.windows = [window for window in self.windows if window[0] != handle]

    def end(self, handle, seconds):
        self.table[handle]["alive"] = False
        self.ended.append(handle)
        return True


def process(name, started, command_line=AUTOMATION):
    return {"name": name, "started": started, "command_line": command_line, "alive": True}


def test_only_the_hidden_office_app_the_worker_started_is_ended():
    # The user's Word, here even one started for automation and showing nothing, was running before the import.
    windows = FakeWindows({10: process("WINWORD.EXE", 100.0), 11: process("WINWORD.EXE", 200.0, '"WINWORD.EXE"')})
    clock = iter([1000.0, 1010.0]).__next__
    watch = Watch(["Word"], windows=windows, clock=clock)
    assert watch.before == {10: 100.0, 11: 200.0}
    windows.table.update({
        20: process("WINWORD.EXE", 1001.0),  # The worker's: new, for automation, no window.
        21: process("WINWORD.EXE", 1002.0, r'"WINWORD.EXE" /n "C:\보고서.docx"'),  # Started by hand meanwhile.
        22: process("WINWORD.EXE", 1003.0),  # Shows a document the user opened in it.
        30: process("EXCEL.EXE", 1001.0, '"EXCEL.EXE" /automation -Embedding'),  # Not the app this worker used.
        40: process("winword.exe", 900.0),  # Started before the import, though missed by the first look.
    })
    windows.windows.append((22, 22, "보고서 - Word", "OpusApp"))
    ended = watch.end_leftovers()
    assert [found.pid for found in ended] == [20]
    assert ended[0].command_line == AUTOMATION
    assert windows.ended == [20]
    assert all(windows.table[pid]["alive"] for pid in (10, 11, 21, 22, 30, 40))


def test_a_process_id_used_again_is_told_apart_by_its_start():
    windows = FakeWindows({10: process("WINWORD.EXE", 100.0)})
    watch = Watch(["Word"], windows=windows, clock=iter([1000.0, 1010.0]).__next__)
    # The user's Word ended, and Windows gave its ID to the Word the worker started.
    windows.table[10] = process("WINWORD.EXE", 1004.0)
    assert [found.pid for found in watch.end_leftovers()] == [10]


def test_nothing_is_looked_at_without_an_office_app_or_off_windows():
    windows = FakeWindows({20: process("WINWORD.EXE", 1001.0)})
    assert Watch([], windows=windows).end_leftovers() == []
    assert Watch(["Notepad"], windows=windows).end_leftovers() == []
    assert windows.ended == []
    if sys.platform != "win32":
        assert system() is None and Watch(["Word"]).end_leftovers() == []


def test_a_hidden_office_that_shows_only_a_dialog_box_is_still_ended():
    windows = FakeWindows({}, windows=[
        (1, 20, "Microsoft Word", "#32770"),  # A message box of the worker's Word (a DRM add-in's notice).
        (2, 21, "암호", "bosa_sdm_msword"),  # Word's own dialog.
        (3, 22, "보고서 - Word", "OpusApp"), (4, 22, "Microsoft Word", "#32770"),  # A document is shown too.
        (5, 23, "알림", "WindowsForms10.Window.8.app.0.1"),  # A window nobody knows: it may be someone's.
        (6, 24, "옵션", "NUIDialog")])
    watch = Watch(["Word"], windows=windows, clock=iter([1000.0, 1010.0]).__next__)
    windows.table.update({pid: process("WINWORD.EXE", 1001.0 + pid / 100) for pid in (20, 21, 22, 23, 24)})
    assert sorted(found.pid for found in watch.end_leftovers()) == [20, 21, 24]
    assert dialog("#32770") and dialog("bosa_sdm_XL9") and not dialog("OpusApp") and not dialog("XLMAIN")


def test_password_prompts_powerpoint_shows_for_the_worker_are_cancelled():
    windows = FakeWindows({10: process("POWERPNT.EXE", 100.0), 30: process("EXCEL.EXE", 100.0)},
                          windows=[(100, 10, "암호", "#32770")])  # The user was asked before the import began.
    watch = Watch(["PowerPoint"], windows=windows, clock=iter([1000.0, 1010.0]).__next__)
    assert watch.prompting and watch.refuse_prompts() is False and windows.closed == []
    windows.windows += [(101, 10, "Password", "#32770"), (102, 30, "암호", "#32770"),
                        (103, 10, "발표.pptx - PowerPoint", "PPTFrameClass")]
    assert watch.refuse_prompts() is True and windows.closed == [101]
    assert watch.refuse_prompts() is False and windows.closed == [101]
    # Only PowerPoint asks: Word and Excel are given an empty password and fail at once.
    assert not Watch(["Word"], windows=windows).prompting and not Watch(["PowerPoint"], windows=None).prompting
    # Windows that will not list its windows leaves the time limit as it was, and never fails the import.
    windows.visible = lambda: 1 / 0
    assert not Watch(["PowerPoint"], windows=windows).prompting
    watch.windows = windows
    assert watch.refuse_prompts() is False and not watch.prompting


def test_workers_of_one_office_application_take_turns_in_the_order_they_asked():
    turns, order, said = Turns(), [], []
    cancel = threading.Event()
    first = threading.Event()
    release = threading.Event()

    def hold(name, label, entered=None):
        with turns.turn([name], cancel, lambda text: said.append((label, text))):
            order.append(label)
            if entered is not None:
                entered.set()
            release.wait(5)

    a = threading.Thread(target=hold, args=("Excel", "a", first))
    a.start()
    assert first.wait(5)
    b = threading.Thread(target=hold, args=("Excel", "b"))
    b.start()
    time.sleep(0.3)
    c = threading.Thread(target=hold, args=("Excel", "c"))
    c.start()
    # Another application runs at once, beside them.
    with turns.turn(["Word"], cancel, lambda text: said.append(("word", text))):
        order.append("word")
    time.sleep(0.3)
    assert order == ["a", "word"]
    assert said == [("b", WAITING.format(name="Excel")), ("c", WAITING.format(name="Excel"))]
    release.set()
    for thread in (a, b, c):
        thread.join(5)
    assert order == ["a", "word", "b", "c"] and ("b", "") in said and ("c", "") in said
    assert turns.queues == {}


def test_a_worker_waiting_for_its_turn_can_be_cancelled():
    turns, cancel, outcome = Turns(), threading.Event(), []

    def wait_for_turn():
        try:
            with turns.turn(["PowerPoint"], cancel, cancelled="원본 반영을 취소했습니다."):
                outcome.append("ran without its turn")
        except UserError as error:
            outcome.append(str(error))

    with turns.turn(["PowerPoint"], threading.Event()):
        thread = threading.Thread(target=wait_for_turn)
        thread.start()
        time.sleep(0.3)
        cancel.set()
        thread.join(5)
    assert outcome == ["원본 반영을 취소했습니다."] and turns.queues == {}


def stalled_worker(source, sender, stop, folder):
    time.sleep(30)


def answering_worker(source, sender, stop, folder):
    sender.send({"ok": False, "error": "Excel 파일 열기 실패: 암호가 걸린 파일입니다.", "trace": ""})
    sender.close()


def left_worker(source, sender, stop, folder):
    """A worker whose Excel could not close the file or quit: a dialog rejected every call."""
    ok = source.endswith("read.xlsx")
    sender.send({"ok": ok, "html": "<p>읽음</p>", "left": ["Excel"]} if ok else
                {"ok": False, "error": "Excel 파일 열기 실패: 대화 상자.", "trace": "", "left": ["Excel"]})
    sender.close()


class RecordingWatch:
    made = []
    prompting = False

    def __init__(self, applications):
        self.applications, self.ended = tuple(applications), 0
        RecordingWatch.made.append(self)

    def end_leftovers(self):
        self.ended += 1
        return []


def test_the_office_app_is_looked_for_only_after_a_worker_that_ended_without_an_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(OfficeConverter, "check", lambda *args: None)
    monkeypatch.setattr("agent.word_converter.Watch", RecordingWatch)
    RecordingWatch.made.clear()
    monkeypatch.setattr(OfficeConverter, "worker", staticmethod(stalled_worker))
    with pytest.raises(UserError, match="초과"):
        OfficeConverter(timeout=0.2, grace=0.2)(tmp_path / "budget.xlsx", threading.Event())
    monkeypatch.setattr(OfficeConverter, "worker", staticmethod(answering_worker))
    with pytest.raises(UserError, match="암호"):
        OfficeConverter(timeout=30, grace=5)(tmp_path / "locked.xlsx", threading.Event())
    assert [(watch.applications, watch.ended) for watch in RecordingWatch.made] == [(("Excel",), 1), (("Excel",), 0)]


def test_the_office_app_a_worker_could_not_close_or_quit_is_ended_once_it_answered(tmp_path, monkeypatch):
    monkeypatch.setattr(OfficeConverter, "check", lambda *args: None)
    monkeypatch.setattr("agent.word_converter.Watch", RecordingWatch)
    monkeypatch.setattr(OfficeConverter, "worker", staticmethod(left_worker))
    RecordingWatch.made.clear()
    with pytest.raises(UserError, match="대화 상자"):
        OfficeConverter(timeout=30, grace=5)(tmp_path / "dialog.xlsx", threading.Event())
    assert "읽음" in OfficeConverter(timeout=30, grace=5)(tmp_path / "read.xlsx", threading.Event())
    assert [watch.ended for watch in RecordingWatch.made] == [1, 1]


def test_a_worker_that_cannot_be_closed_keeps_the_outcome_and_its_office_is_still_ended(tmp_path, monkeypatch):
    import multiprocessing.context
    monkeypatch.setattr(OfficeConverter, "check", lambda *args: None)
    monkeypatch.setattr("agent.word_converter.Watch", RecordingWatch)
    RecordingWatch.made.clear()

    def refuse(self):
        raise ValueError("Cannot close a process while it is still running.")
    monkeypatch.setattr(multiprocessing.context.SpawnProcess, "close", refuse)
    monkeypatch.setattr(OfficeConverter, "worker", staticmethod(answering_worker))
    with pytest.raises(UserError, match="암호가 걸린 파일"):
        OfficeConverter(timeout=30, grace=5)(tmp_path / "locked.xlsx", threading.Event())
    monkeypatch.setattr(OfficeConverter, "worker", staticmethod(stalled_worker))
    with pytest.raises(UserError, match="초과"):
        OfficeConverter(timeout=0.2, grace=0.2)(tmp_path / "budget.xlsx", threading.Event())
    assert [watch.ended for watch in RecordingWatch.made] == [0, 1]


def test_a_conversion_waits_for_the_turn_of_its_office_application(tmp_path, monkeypatch):
    monkeypatch.setattr(OfficeConverter, "check", lambda *args: None)
    monkeypatch.setattr("agent.word_converter.Watch", RecordingWatch)
    monkeypatch.setattr(OfficeConverter, "worker", staticmethod(answering_worker))
    said, outcome = [], []
    converter = OfficeConverter(timeout=30, grace=5)
    converter.waiting = said.append

    def convert():
        try:
            converter(tmp_path / "a.xlsx", threading.Event())
        except UserError as error:
            outcome.append(str(error))

    with word_converter.TURNS.turn(["Excel"], threading.Event()):
        RecordingWatch.made.clear()
        thread = threading.Thread(target=convert)
        thread.start()
        time.sleep(0.5)
        # No worker has started for it, nor has its Office been looked at.
        assert said == [WAITING.format(name="Excel")] and RecordingWatch.made == [] and thread.is_alive()
    thread.join(30)
    assert said == [WAITING.format(name="Excel"), ""] and "암호" in outcome[0]


def prompting_worker(source, sender, stop, folder):
    """A worker stuck in Presentations.Open while PowerPoint asks a password: it fails once the prompt is cancelled."""
    cancelled = Path(source + ".cancelled")
    deadline = time.monotonic() + 30
    while not cancelled.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not source.endswith("silent.pptx"):
        sender.send({"ok": False, "error": "PowerPoint 파일 열기 실패: PowerPoint에서 파일을 열 수 없습니다.", "trace": ""})
    else:
        time.sleep(30)
    sender.close()


def test_a_presentation_with_a_password_fails_once_its_prompt_is_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(OfficeConverter, "check", lambda *args: None)
    monkeypatch.setattr(OfficeConverter, "worker", staticmethod(prompting_worker))
    made = []

    class Prompted(FakeWindows):
        def __init__(self, source):
            super().__init__({10: process("POWERPNT.EXE", 1.0)})
            self.source, self.looks = source, 0

        def visible(self):
            # PowerPoint asks once the worker has been at it for a moment.
            self.looks += 1
            return [(500, 10, "암호", "#32770")] if not self.closed and self.looks > 2 else []

        def close_window(self, handle):
            super().close_window(handle)
            Path(str(self.source) + ".cancelled").touch()

    for name in ("locked.pptx", "silent.pptx"):
        source = tmp_path / name

        def watch(applications, source=source):
            found = Watch(applications, windows=Prompted(source))
            found.ended = 0
            found.end_leftovers = lambda: setattr(found, "ended", found.ended + 1) or []
            made.append(found)
            return found
        monkeypatch.setattr("agent.word_converter.Watch", watch)
        monkeypatch.setattr("agent.word_converter.PROMPT_SECONDS", 1)
        started = time.monotonic()
        with pytest.raises(UserError) as failed:
            OfficeConverter(timeout=60, grace=1)(source, threading.Event())
        assert str(failed.value) == PASSWORD.format(name="PowerPoint") and time.monotonic() - started < 20
        assert made[-1].windows.closed == [500]
    # A worker that still gives no answer is ended, and so is the PowerPoint it may leave.
    assert [watch.ended for watch in made] == [0, 1]


@pytest.mark.office
def test_real_hidden_word_left_by_a_killed_client_is_ended():
    import subprocess
    watch = Watch(["Word"])
    # A client that starts a hidden Word the way the worker does, then is killed before it can quit it.
    client = subprocess.Popen([sys.executable, "-c", (
        "import time, win32com.client; word = win32com.client.DispatchEx('Word.Application'); "
        "word.Visible = False; print('ready', flush=True); time.sleep(120)")], stdout=subprocess.PIPE, text=True)
    try:
        assert client.stdout.readline().strip() == "ready"
    finally:
        client.kill()
        client.wait(10)
    ended = watch.end_leftovers()
    assert len(ended) == 1 and ended[0].name.lower() == "winword.exe"
    assert "-embedding" in ended[0].command_line.lower()
