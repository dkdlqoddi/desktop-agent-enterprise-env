import logging
import re
import sqlite3

import pytest

from agent import diagnostics
from agent.db import Database
from agent.llm import UserError
from agent.rtf_import import RtfImporter
from agent.service import Service

ID = re.compile(r"\(오류 ID ([0-9a-f]{8})\)$")


@pytest.fixture
def log(tmp_path):
    """The backend's log in a folder of the test's own, as Electron names its settings folder's logs/."""
    folder = tmp_path / "logs"
    diagnostics.start(folder)
    yield folder / "backend.log"
    for handler in list(diagnostics.LOG.handlers):
        if isinstance(handler, logging.FileHandler):
            diagnostics.LOG.removeHandler(handler)
            handler.close()


def text(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_a_user_error_speaks_for_itself_and_is_not_logged(log):
    assert diagnostics.report(UserError("파일이 없습니다."), "일반", "가져오기") == "파일이 없습니다."
    assert text(log) == ""


def test_an_unexpected_failure_is_logged_with_its_traceback_under_the_id_its_message_ends_with(log):
    def convert():
        return {}["page"]
    try:
        convert()
    except KeyError as error:
        message = diagnostics.report(error, "파일을 읽지 못했습니다.", "Office 가져오기")
    ident = ID.search(message).group(1)
    assert message.startswith("파일을 읽지 못했습니다. (오류 ID ")
    logged = text(log)
    assert f"Office 가져오기 · 오류 ID {ident}" in logged
    assert "KeyError: 'page'" in logged and "in convert" in logged
    # Another failure gets another ID; without their IDs, two of the same cause read the same.
    again = diagnostics.report(KeyError("page"), "파일을 읽지 못했습니다.", "Office 가져오기")
    assert ID.search(again).group(1) != ident
    assert diagnostics.without_ids(again) == diagnostics.without_ids(message) == "파일을 읽지 못했습니다."
    assert diagnostics.without_ids(f"가져오지 못했습니다: {message}") == "가져오지 못했습니다: 파일을 읽지 못했습니다."


@pytest.mark.parametrize("error, says", [
    (sqlite3.OperationalError("database is locked"), "다른 작업이나 프로그램"),
    (sqlite3.OperationalError("database or disk is full"), "디스크 공간"),
    (sqlite3.OperationalError("disk I/O error"), "드라이브의 연결과 권한"),
    (OSError(28, "No space left on device"), "디스크 공간"),
    (PermissionError(13, "Permission denied"), "권한"),
])
def test_a_failure_the_user_can_act_on_gets_its_own_message(log, error, says):
    message = diagnostics.report(error, "일반", "저장")
    assert says in message and ID.search(message)


def test_a_worker_failure_is_logged_only_with_a_traceback(log):
    assert diagnostics.worker_failure("Word 파일 열기 실패: 암호", "", "Word 변환") == "Word 파일 열기 실패: 암호"
    message = diagnostics.worker_failure("Word 본문 변환 실패: KeyError", "Traceback …\nKeyError: 'w:t'", "Word 변환")
    ident = ID.search(message).group(1)
    assert f"Word 변환 · 오류 ID {ident}" in text(log) and "KeyError: 'w:t'" in text(log)


def test_without_a_folder_nothing_is_written(tmp_path):
    diagnostics.start(None)
    assert not any(isinstance(handler, logging.FileHandler) for handler in diagnostics.LOG.handlers)
    assert ID.search(diagnostics.report(RuntimeError("x"), "일반", "작업"))


def test_an_import_that_breaks_unexpectedly_says_where_to_look(tmp_path, log):
    class FakeLLM:
        def public(self):
            return {"configured": True}
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    importer = RtfImporter(service, converter=lambda *args: "", capability_check=lambda: {"available": True})

    def broken(file, *_):
        raise RuntimeError("converter bug")
    importer._one = broken
    source = tmp_path / "report.rtf"
    source.write_bytes(b"x")
    importer.start({"path": str(source), "mode": "file"})
    importer.thread.join(5)
    [result] = importer.status()["results"]
    ident = ID.search(result["message"]).group(1)
    assert result["status"] == "failed" and result["message"].startswith("파일을 읽거나 Wiki에 저장하지 못했습니다.")
    assert f"RTF 문서 가져오기 · 오류 ID {ident}" in text(log) and "RuntimeError: converter bug" in text(log)
