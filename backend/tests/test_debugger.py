"""main.py's PRIVATE_AGENT_DEBUGPY: a backend run from source waits for a debugger on 127.0.0.1 before it reads a
request; without the variable, with a port that is no number, or packaged, it never listens."""
import sys
import types

import main


def fake_debugpy(monkeypatch):
    calls = []
    module = types.SimpleNamespace(listen=lambda address: calls.append(("listen", address)),
                                   wait_for_client=lambda: calls.append(("wait",)))
    monkeypatch.setitem(sys.modules, "debugpy", module)
    return calls


def test_a_backend_from_source_waits_for_a_debugger_on_this_pc_only(monkeypatch, capsys):
    calls = fake_debugpy(monkeypatch)
    monkeypatch.setenv("PRIVATE_AGENT_DEBUGPY", "5678")
    main.wait_for_debugger()
    assert calls == [("listen", ("127.0.0.1", 5678)), ("wait",)]
    assert "127.0.0.1:5678" in capsys.readouterr().err


def test_no_port_a_port_that_is_no_number_or_a_packaged_backend_never_listens(monkeypatch, capsys):
    calls = fake_debugpy(monkeypatch)
    monkeypatch.delenv("PRIVATE_AGENT_DEBUGPY", raising=False)
    main.wait_for_debugger()
    monkeypatch.setenv("PRIVATE_AGENT_DEBUGPY", "port")
    main.wait_for_debugger()
    assert "PRIVATE_AGENT_DEBUGPY" in capsys.readouterr().err
    monkeypatch.setenv("PRIVATE_AGENT_DEBUGPY", "5678")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    main.wait_for_debugger()
    assert calls == []
