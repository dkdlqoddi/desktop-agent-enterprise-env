"""A fresh install has no LLM endpoint or sign-in built in: the user sets them up."""
import json
from pathlib import Path

import pytest

from agent.db import Database
from agent.llm import ENVIRONMENT, LLM, UserError
from agent.service import Service


def test_fresh_install_cannot_send_to_an_unconfigured_provider(monkeypatch):
    for name in ENVIRONMENT.values():
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("PRIVATE_AGENT_" + name, raising=False)
    calls = []
    monkeypatch.setattr("agent.llm.requests.Session.post", lambda *args, **kwargs: calls.append(args))
    llm = LLM()
    settings = llm.public()
    assert settings["provider"] == "general"
    assert not settings["configured"]
    assert settings["base_url"] == settings["model_name"] == ""
    assert settings["headers"] == []
    assert not settings["has_api_key"]
    with pytest.raises(UserError, match="엔드포인트"):
        llm.complete([{"role": "user", "content": "test"}])
    assert calls == []


def test_builtin_login_steps_have_no_urls_or_absolute_page_layouts():
    catalog = json.loads((Path(__file__).resolve().parents[2] / "electron/login-methods.json").read_text())
    for method in catalog["methods"]:
        targets = method["detect"] + [step.get("target", "") for step in method["steps"]]
        assert all(not target.startswith("xpath=/html/") for target in targets)
        assert all("://" not in target for target in targets)


def test_the_routine_catalog_and_a_local_file_routine(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), None)
    from agent.routines import RECIPES
    assert set(RECIPES) == {"confluence_watch", "jira_watch", "jira_filter", "jira_project",
                            "source_refresh", "analyze_new", "work_report"}
    refresh = RECIPES["source_refresh"]
    with pytest.raises(UserError, match="경로"):
        refresh.check({"paths": []})
    refresh.check({"paths": [str(tmp_path)]})
    routine = service.dispatch("routines.save", {"kind": "source_refresh", "name": "Local files",
                                                 "config": {"paths": [str(tmp_path)]}, "allow_files": True})
    assert service.dispatch("routines.needs", {"id": routine["id"]}) == []
