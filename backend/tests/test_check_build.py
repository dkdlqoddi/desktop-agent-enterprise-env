"""main.py --check-build: the frozen backend imports every standard extension module it bundles, so a build that
lacks the DLL of one (conda keeps sqlite3.dll in Library\\bin) stops in scripts/build-python.mjs, not when the app
starts."""
import main


def test_a_module_whose_dll_is_missing_fails_the_check_by_name(tmp_path, monkeypatch, capsys):
    for name in ("_socket.pyd", "_sqlite3.pyd", "python313.dll", "unicodedata.pyd"):
        (tmp_path / name).touch()
    imported = []

    def load(name):
        imported.append(name)
        if name == "_sqlite3":
            raise ImportError("DLL load failed while importing _sqlite3: 지정된 모듈을 찾을 수 없습니다.")

    monkeypatch.setattr("importlib.import_module", load)
    assert main.check_build(tmp_path) == 1
    assert imported == ["_socket", "_sqlite3", "unicodedata"]
    assert capsys.readouterr().err == "_sqlite3: DLL load failed while importing _sqlite3: 지정된 모듈을 찾을 수 없습니다.\n"


def test_a_build_whose_modules_all_load_passes(tmp_path, capsys):
    (tmp_path / "json.pyd").touch()
    assert main.check_build(tmp_path) == 0
    assert capsys.readouterr().err == ""
