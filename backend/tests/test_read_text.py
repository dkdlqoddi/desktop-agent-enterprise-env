"""main.py --read-text: a text file the user wrote, read for Electron through a hidden Notepad, as a demo recording's
list or captions whose bytes are no text Electron can read."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import main
from agent.llm import UserError

BACKEND = Path(__file__).resolve().parents[1]


def test_the_text_goes_out_as_json(monkeypatch, capsys):
    monkeypatch.setattr("agent.notepad.read_with_notepad", lambda path: f"D:\\자료\\설계서.docx\n# {Path(path).name}")
    assert main.read_text("목록.txt") == 0
    assert json.loads(capsys.readouterr().out) == {"text": "D:\\자료\\설계서.docx\n# 목록.txt"}


def test_half_of_a_surrogate_pair_where_notepad_s_length_cut_the_text_goes_out_as_a_replacement(monkeypatch, capsys):
    # Notepad's text is UTF-16: a cut may end in half a pair, which UTF-8 cannot hold; a whole pair is one character.
    monkeypatch.setattr("agent.notepad.read_with_notepad", lambda path: "자막 😀 끝\ud83d")
    assert main.read_text("자막.txt") == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"text": "자막 😀 끝�"} and captured.err == ""


def test_a_failure_says_why_on_the_error_output(monkeypatch, capsys):
    def refuse(path):
        raise UserError("메모장이 30초 안에 파일을 열지 못했습니다.")

    monkeypatch.setattr("agent.notepad.read_with_notepad", refuse)
    assert main.read_text("목록.txt") == 1
    captured = capsys.readouterr()
    assert (captured.out, captured.err.strip()) == ("", "메모장이 30초 안에 파일을 열지 못했습니다.")
    monkeypatch.setattr("agent.notepad.read_with_notepad", lambda path: 1 / 0)
    assert main.read_text("목록.txt") == 1
    assert capsys.readouterr().err.strip() == "메모장으로 파일을 읽지 못했습니다."


@pytest.mark.skipif(sys.platform == "win32", reason="Windows opens the file in a real Notepad")
def test_the_command_line_as_electron_runs_it(tmp_path):
    file = tmp_path / "목록.txt"
    file.write_text("D:\\자료", encoding="utf-8")
    result = subprocess.run([sys.executable, str(BACKEND / "main.py"), "--read-text", str(file)], cwd=BACKEND,
                            capture_output=True, env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": ""})
    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr.decode("utf-8").strip() == "텍스트 파일은 메모장이 있는 Windows에서 가져올 수 있습니다."
