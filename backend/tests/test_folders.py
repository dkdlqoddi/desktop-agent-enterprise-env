import os
import stat
import sys
import threading
import time

import pytest

from agent.folders import remove_tree


def test_read_only_files_go_and_a_missing_folder_is_no_error(tmp_path):
    folder = tmp_path / "scratch"
    (folder / "inner").mkdir(parents=True)
    copy = folder / "inner" / "보고서.docx"
    copy.write_bytes(b"copy")
    os.chmod(copy, stat.S_IREAD)  # A copy keeps the read-only attribute of its original.
    remove_tree(folder)
    assert not folder.exists()
    remove_tree(folder)


@pytest.mark.skipif(sys.platform != "win32", reason="Only Windows refuses to delete an open file")
def test_a_file_held_for_a_moment_goes_once_it_is_released(tmp_path):
    folder = tmp_path / "scratch"
    folder.mkdir()
    held = open(folder / "picture.png", "wb")
    timer = threading.Timer(0.5, held.close)
    timer.start()
    remove_tree(folder)
    timer.join()
    assert not folder.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Only Windows refuses to delete an open file")
def test_a_file_held_longer_is_left_after_the_wait(tmp_path):
    folder = tmp_path / "scratch"
    folder.mkdir()
    with open(folder / "picture.png", "wb"):
        started = time.monotonic()
        remove_tree(folder, seconds=0.5)
        assert time.monotonic() - started < 3
        assert folder.exists()


def test_scratch_folders_an_earlier_run_left_go_at_the_next_start(tmp_path):
    from agent.folders import sweep_scratch
    now = time.time()

    def folder(name, age, inner=True):
        path = tmp_path / name
        path.mkdir()
        if inner:
            (path / "page.htm").write_text("시트 전체", encoding="utf-8")
        os.utime(path, (now - age, now - age))
        return path

    office = folder("private-agent-office-abc", 3600)
    pictures = folder("private-agent-pictures-ghi", 700, inner=False)
    # Young enough to belong to a backend that is still closing.
    recent = folder("private-agent-office-new", 60)
    # Not the importers': test data, other programs.
    other = folder("private-agent-api-xyz", 3600)
    (tmp_path / "private-agent-office-file").write_text("x")
    removed = sweep_scratch(tmp_path, older_than=600, now=now)
    assert sorted(removed) == sorted([office.name, pictures.name])
    assert not office.exists() and not pictures.exists()
    assert recent.exists() and other.exists() and (tmp_path / "private-agent-office-file").exists()
    assert sweep_scratch(tmp_path / "missing") == []
