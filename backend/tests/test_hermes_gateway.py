"""The messenger gateway (agent/hermes_gateway.py, and run_gateway of runtime/hermes/worker.py): the bundled Hermes'
own gateway, run against a stand-in for Telegram's Bot API on this PC (telegram_server.py) and a scripted LLM. Discord
and Slack have no such stand-in: what they share with Telegram (who is answered, which commands, the approval, the
process) is tested here, and their connection itself is not."""

import json
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from agent import hermes_gateway
from agent.db import Database
from agent.hermes_llm import completion
from agent.service import Service
from telegram_server import TOKEN, TelegramServer
from test_hermes import bundled, connection, openai_tool_reply

SECRETS = ("test-secret", "header-secret", TOKEN)
ANSWER = "회의록 문서를 찾았습니다."


def turn_on(service, users="42", enabled=True, token=TOKEN, keys=None):
    """What the main process sends once the user has set the gateway up (electron/hermes-settings.cjs, agent())."""
    gateway = {"enabled": enabled, "users": {"telegram": users}, "env": {"TELEGRAM_BOT_TOKEN": token} if token else {}}
    return service.dispatch("agent.configure", {"env": keys or {}, "gateway": gateway})


def status(service):
    return service.dispatch("agent.gateway", {})


def wait(found, seconds=90):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if found():
            return True
        time.sleep(0.1)
    return False


def connected(service):
    """Wait until the bot is connected; the status it then has."""
    wait(lambda: [entry["state"] for entry in status(service)["platforms"]] == ["connected"])
    return status(service)


def listening(pid):
    """The local addresses of the TCP sockets a process listens on (Linux: read from /proc)."""
    sockets = set()
    for entry in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if target.startswith("socket:["):
            sockets.add(target[8:-1])
    found = []
    for table in ("tcp", "tcp6"):
        for line in Path(f"/proc/{pid}/net/{table}").read_text(encoding="ascii").splitlines()[1:]:
            fields = line.split()
            if fields[3] == "0A" and fields[9] in sockets:
                found.append(fields[1])
    return found


def approval_task(request):
    """Hermes' own question to the model about a command it flagged (its smart approval), not a turn of the chat."""
    words = json.dumps(request["messages"], ensure_ascii=False)
    return "APPROVE" in words and "ESCALATE" in words


@pytest.fixture
def gateway(tmp_path, model_server):
    """A Service on a Wiki of its own whose gateway talks to the stand-in Telegram, and that stand-in."""
    service = Service(Database(tmp_path / "wiki.db"), connection(model_server))
    with TelegramServer() as telegram:
        service.agent.gateway.telegram_api = telegram.url
        try:
            yield service, telegram
        finally:
            service.agent.shutdown()


@bundled
def test_a_listed_account_is_answered_in_the_messenger_and_the_app_acts_for_it(gateway, model_server, tmp_path):
    service, telegram = gateway
    searched = []
    service.wiki_search = lambda params: (
        searched.append(params) or {"total": 3, "documents": [{"id": "d1", "title": "회의록"}]}
    )
    model_server.reply = openai_tool_reply
    turn_on(service)
    assert connected(service) == {
        "enabled": True,
        "state": "running",
        "message": "",
        "platforms": [{"name": "telegram", "label": "텔레그램", "state": "connected", "message": "연결됨"}],
    }
    # The gateway connects outwards: its process listens on no port.
    child = service.agent.gateway.child
    if sys.platform == "linux":
        assert listening(child.pid) == []

    telegram.write("회의록 문서가 있는지 찾아 줘")
    assert telegram.wait(lambda: ANSWER in " ".join(telegram.sent(42)))
    # The app's own action ran in the backend, checked as the chat's are, and the model read its result.
    assert searched and searched[0]["query"] == "회의록"
    # The agent is the chat's: the app's rules and tools, with a word about the messenger, and Hermes' own setup
    # talk (a home channel to set, a profile to build) left out.
    first = model_server.requests[0]
    system = first["messages"][0]["content"]
    assert "Never click, open or follow an advertisement" in system
    assert "This conversation is in a messenger" in system
    names = {tool["function"]["name"] for tool in first["tools"]}
    assert {"search_documents", "web_capture", "terminal", "read_file"} <= names
    assert not names & {"text_to_speech", "cronjob", "send_message"}
    assert first["messages"][-1]["content"] == "회의록 문서가 있는지 찾아 줘"
    assert not any("home channel" in text.lower() or "홈 채널" in text for text in telegram.sent())
    # Its command menu offers what a listed account may run and none of what changes the agent's limits.
    menu = set(telegram.menu())
    assert {"new", "stop", "approve", "deny", "help"} <= menu
    assert not menu & {"yolo", "approvals", "model", "update", "debug", "restart", "sethome", "platform", "login"}
    # No secret is written into the agent's folder: neither the LLM's key and header nor the bot token.
    home = tmp_path / "hermes"
    written = "".join(path.read_text(encoding="utf-8", errors="ignore") for path in home.rglob("*") if path.is_file())
    assert not any(secret in written for secret in SECRETS)
    assert all(not path.stat().st_size for path in (home / "logs").glob("*.log"))

    # Turned off, the process ends and nothing answers any more.
    turn_on(service, enabled=False)
    assert wait(lambda: status(service)["state"] == "off", 30)
    assert status(service) == {"enabled": False, "state": "off", "message": "", "platforms": []}
    assert child.poll() is not None


@bundled
def test_only_the_listed_accounts_are_answered_whatever_the_agents_folder_says(gateway, model_server, tmp_path):
    service, telegram = gateway
    model_server.reply = lambda request: (200, completion({"content": "네, 했습니다."}, request["model"]), False)
    # What a restored or shared Wiki folder might bring, or a key stored under such a name: an approved pairing of
    # another account, and switches that would let everyone in or start a listener.
    home = tmp_path / "hermes"
    for folder in (home / "pairing", home / "platforms" / "pairing"):
        folder.mkdir(parents=True)
        (folder / "telegram-approved.json").write_text('{"99": {"user_name": "stranger"}}', encoding="utf-8")
    (home / ".env").write_text(
        "GATEWAY_ALLOW_ALL_USERS=true\nTELEGRAM_ALLOW_ALL_USERS=true\nTELEGRAM_ALLOWED_USERS=99\nHERMES_YOLO_MODE=1\n",
        encoding="utf-8",
    )
    keys = {"GATEWAY_ALLOW_ALL_USERS": "true", "API_SERVER_KEY": "k", "API_SERVER_ENABLED": "true"}
    turn_on(service, keys={**keys, "WEBHOOK_ENABLED": "true", "TELEGRAM_WEBHOOK_URL": "https://hook.example.test/t"})
    assert connected(service)["state"] == "running"
    assert not (home / ".env").exists()
    if sys.platform == "linux":
        assert listening(service.agent.gateway.child.pid) == []

    def reply_to(words, sender=42):
        """Write to the bot and wait for its next message to that account; that message ('' when none came)."""
        before = len(telegram.sent(sender))
        telegram.write(words, sender=sender)
        telegram.wait(lambda: len(telegram.sent(sender)) > before, 30 if sender == 42 else 5)
        return " ".join(telegram.sent(sender)[before:])

    # The account nobody listed gets no answer at all, not even a pairing code.
    assert reply_to("나도 써도 되나요?", sender=99) == ""
    # A command that would lift the approvals is refused, in words; the listed account is an ordinary user of Hermes'
    # command tiers.
    for command in ("/yolo", "/approvals off", "/model"):
        refusal = reply_to(command)
        assert refusal.startswith("⛔") and command.split()[0] in refusal
    tier = reply_to("/whoami")
    assert "`42`" in tier and "/new" in tier and "/yolo" not in tier
    assert "네, 했습니다." in reply_to("이것만 해 줘")
    assert telegram.sent(99) == []
    # Only the real message became a turn of the agent.
    asked = [request["messages"][-1]["content"] for request in model_server.requests]
    assert asked == ["이것만 해 줘"]
    # A file of that name written while it runs is removed as well.
    (home / ".env").write_text("GATEWAY_ALLOW_ALL_USERS=true\n", encoding="utf-8")
    assert wait(lambda: not (home / ".env").exists(), 10)


@bundled
def test_a_dangerous_command_waits_for_the_answer_in_the_messenger(gateway, model_server, tmp_path):
    service, telegram = gateway
    work = tmp_path / "work"
    (work / "keep").mkdir(parents=True)
    service.dispatch("agent.configure", {"workspace": str(work)})

    def reply(request):
        if approval_task(request):
            return 200, completion({"content": "ESCALATE"}, request["model"]), False
        last = request["messages"][-1]
        if last["role"] == "tool":
            words = "지우지 않았습니다." if "denied" in last["content"].lower() else "지웠습니다."
            return 200, completion({"content": words}, request["model"]), False
        command = {"name": "terminal", "arguments": '{"command": "rm -rf keep"}'}
        call = {"id": f"c{len(request['messages'])}", "type": "function", "function": command}
        return 200, completion({"content": None, "tool_calls": [call]}, request["model"]), False

    model_server.reply = reply
    turn_on(service)
    assert connected(service)["state"] == "running"

    def asked():
        return sum("rm -rf keep" in text for text in telegram.sent(42))

    telegram.write("keep 폴더를 지워 줘")
    # The question arrives in the messenger, with the command; nothing ran yet.
    assert telegram.wait(lambda: asked() == 1)
    assert (work / "keep").is_dir()
    telegram.write("/deny")
    assert telegram.wait(lambda: "지우지 않았습니다." in " ".join(telegram.sent(42)))
    assert (work / "keep").is_dir()
    # The model was told the user refused, as the tool's result.
    told = [m["content"] for request in model_server.requests for m in request["messages"] if m["role"] == "tool"]
    assert told and "denied" in told[-1].lower()

    # Asked again and approved, the command runs, in the work folder the user chose.
    telegram.write("그래도 지워 줘")
    assert telegram.wait(lambda: asked() == 2)
    assert (work / "keep").is_dir()
    telegram.write("/approve")
    assert telegram.wait(lambda: "지웠습니다." in " ".join(telegram.sent(42)))
    assert not (work / "keep").exists()


@bundled
def test_status_names_the_model_of_the_connection_as_it_is_now(gateway, model_server, tmp_path):
    service, telegram = gateway
    model_server.reply = lambda request: (200, completion({"content": "네."}, request["model"]), False)
    turn_on(service)
    assert connected(service)["state"] == "running"
    telegram.write("안녕")
    assert telegram.wait(lambda: "네." in " ".join(telegram.sent(42)))
    telegram.write("/status")
    assert telegram.wait(lambda: "test-model" in " ".join(telegram.sent(42)[1:]))
    child = service.agent.gateway.child

    # The model of the app's LLM connection changes while the gateway runs: it is not started again, and /status
    # names the new model at once, though this conversation's agent was made with the old one.
    service.dispatch("settings.update", {"model_name": "other-model"})
    policy = tmp_path / "hermes" / "gateway" / "config.yaml"
    assert wait(lambda: "other-model" in policy.read_text(encoding="utf-8"), 20)
    shown = len(telegram.sent(42))
    telegram.write("/status")
    assert telegram.wait(lambda: "other-model" in " ".join(telegram.sent(42)[shown:]))
    assert "test-model" not in " ".join(telegram.sent(42)[shown:])
    assert service.agent.gateway.child is child
    # The next message goes out to it as before.
    telegram.write("또 안녕")
    assert telegram.wait(lambda: model_server.requests[-1]["model"] == "other-model")


@bundled
def test_a_model_failure_is_told_in_the_messenger_and_the_gateway_goes_on(gateway, model_server):
    service, telegram = gateway
    failing = [True]

    def reply(request):
        if failing[0]:
            return 500, {"error": "down"}, False
        return 200, completion({"content": "이제 됩니다."}, request["model"]), False

    model_server.reply = reply
    turn_on(service)
    assert connected(service)["state"] == "running"
    telegram.write("첫 요청")
    assert telegram.wait(lambda: len(telegram.sent(42)) >= 1)
    assert "이제 됩니다." not in " ".join(telegram.sent(42))
    failing[0] = False
    telegram.write("다시 해 줘")
    assert telegram.wait(lambda: "이제 됩니다." in " ".join(telegram.sent(42)))
    assert status(service)["state"] == "running"


@bundled
def test_a_refused_bot_token_is_told_in_words_without_the_token(tmp_path, model_server):
    service = Service(Database(tmp_path / "wiki.db"), connection(model_server))
    with TelegramServer(token_accepted=False) as telegram:
        service.agent.gateway.telegram_api = telegram.url
        try:
            turn_on(service)
            assert wait(lambda: [entry["state"] for entry in status(service)["platforms"]] == ["failed"])
            shown = status(service)
            assert shown["platforms"][0]["message"] == "봇 토큰이 맞지 않습니다. 토큰을 다시 넣어 주세요."
            assert TOKEN not in json.dumps(shown, ensure_ascii=False)
        finally:
            service.agent.shutdown()


# -- the process, with a worker of the test's own (no Hermes needed) ----------------------------------------------

WORKER = textwrap.dedent(
    """
    import json, os, sys
    frame = json.loads(sys.stdin.readline())
    with open(os.environ["TEST_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"users": frame["users"], "token": os.environ.get("TELEGRAM_BOT_TOKEN"),
                              "key": os.environ.get("EXA_API_KEY"), "system": frame["system"][:40],
                              "limits": [frame.get("max_iterations"), frame.get("answer_seconds")]}) + "\\n")
    names = list(frame["users"])
    print(json.dumps({"type": "gateway", "state": "running",
                      "platforms": {name: {"state": "connected", "error": ""} for name in names}}), flush=True)
    if os.environ.get("TEST_EXIT"):
        sys.exit(0)
    for line in sys.stdin:
        if json.loads(line).get("type") == "shutdown":
            break
    """
)


@pytest.fixture
def stub(tmp_path, monkeypatch):
    """A Service whose gateway starts a small worker of the test's instead of Hermes; `starts()` are its launches."""
    worker, log = tmp_path / "worker.py", tmp_path / "launches.jsonl"
    worker.write_text(WORKER, encoding="utf-8")
    monkeypatch.setenv("PRIVATE_AGENT_HERMES_DIR", str(tmp_path / "no-hermes"))

    class Unconnected:
        def public(self):
            return {"configured": False}

    service = Service(Database(tmp_path / "wiki.db"), Unconnected())
    gateway = service.agent.gateway
    monkeypatch.setattr(gateway.hermes, "launch_paths", lambda: (Path(sys.executable), worker, tmp_path))
    monkeypatch.setattr(hermes_gateway, "child_environment", lambda home, extra: {**os.environ, **extra})
    monkeypatch.setenv("TEST_LOG", str(log))
    log.write_text("", encoding="utf-8")
    service.starts = lambda: [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    yield service
    service.agent.shutdown()


def test_the_gateway_runs_only_while_it_is_on_and_a_messenger_is_complete(stub):
    assert status(stub) == {"enabled": False, "state": "off", "message": "", "platforms": []}
    # On, but no messenger has both its token and an account to answer: nothing is started, and it says why.
    for incomplete in ({"users": ""}, {"token": ""}):
        turn_on(stub, **incomplete)
        shown = status(stub)
        assert shown["enabled"] and shown["state"] == "off" and "봇 토큰과 허용할 계정" in shown["message"]
    assert stub.agent.gateway.child is None
    # Slack needs both of its tokens.
    slack = {"enabled": True, "users": {"slack": "U1"}, "env": {"SLACK_BOT_TOKEN": "xoxb-1"}}
    stub.dispatch("agent.configure", {"gateway": slack})
    assert status(stub)["state"] == "off"

    turn_on(stub, keys={"EXA_API_KEY": "exa-key"})
    assert wait(lambda: status(stub)["state"] == "running", 20)
    assert status(stub)["platforms"][0] == {
        "name": "telegram",
        "label": "텔레그램",
        "state": "connected",
        "message": "연결됨",
    }
    # It was started with the accounts, the bot token and the chat's own keys, and the chat's prompt.
    assert stub.starts() == [
        {
            "users": {"telegram": "42"},
            "token": TOKEN,
            "key": "exa-key",
            "system": "You are the agent of Private Agent, a de",
            "limits": [24, 300],
        }
    ]
    first = stub.agent.gateway.child
    # The same settings again leave it running; a changed list of accounts starts it again with the new list.
    turn_on(stub, keys={"EXA_API_KEY": "exa-key"})
    assert stub.agent.gateway.child is first and len(stub.starts()) == 1
    turn_on(stub, users="42,43", keys={"EXA_API_KEY": "exa-key"})
    assert wait(lambda: len(stub.starts()) == 2 and status(stub)["state"] == "running", 20)
    assert first.poll() is not None and stub.starts()[1]["users"] == {"telegram": "42,43"}
    # The bot tokens are the gateway's alone: the chat's own worker is not given them.
    assert "TELEGRAM_BOT_TOKEN" not in (stub.agent.hermes.extra_env or {})
    second = stub.agent.gateway.child
    turn_on(stub, enabled=False)
    # It is off once its process has gone, not before.
    assert wait(lambda: status(stub)["state"] == "off", 20) and second.poll() is not None


def test_a_gateway_that_ends_by_itself_is_started_again_a_few_times(stub, monkeypatch):
    monkeypatch.setenv("TEST_EXIT", "1")
    monkeypatch.setattr(hermes_gateway, "RESTART_DELAYS", (0.05, 0.05))
    turn_on(stub)
    # The first start and one for each delay; then it is left stopped, and says so.
    assert wait(lambda: len(stub.starts()) == 3 and "거듭 종료" in status(stub)["message"], 30)
    time.sleep(0.5)
    assert len(stub.starts()) == 3 and status(stub)["state"] == "failed"
    assert status(stub)["platforms"][0]["state"] == "failed"
    # Saving the settings again tries once more from the start.
    monkeypatch.delenv("TEST_EXIT")
    turn_on(stub)
    assert wait(lambda: status(stub)["state"] == "running", 20) and len(stub.starts()) == 4


def test_the_stand_in_address_of_the_bot_api_is_on_this_pc_or_nothing(tmp_path, monkeypatch):
    class Unconnected:
        def public(self):
            return {"configured": False}

    for address, taken in (
        ("http://127.0.0.1:8081/bot", "http://127.0.0.1:8081/bot"),
        ("https://api.example.test/bot", ""),
        ("http://127.0.0.1.example.test:80/bot", ""),
        ("http://127.0.0.1:8081/bot?x=http://elsewhere.example.test", ""),
    ):
        monkeypatch.setenv("PRIVATE_AGENT_TELEGRAM_API", address)
        service = Service(Database(tmp_path / f"{len(address)}.db"), Unconnected())
        assert service.agent.gateway.telegram_api == taken


def test_the_gateway_goes_by_the_turn_limits_and_starts_again_when_they_change(stub):
    turn_on(stub)
    assert wait(lambda: status(stub)["state"] == "running", 20)
    assert stub.starts()[0]["limits"] == [24, 300]
    stub.dispatch("agent.configure", {"turn_limits": {"iterations": 60, "answer_minutes": 10}})
    assert wait(lambda: len(stub.starts()) == 2 and status(stub)["state"] == "running", 20)
    assert stub.starts()[1]["limits"] == [60, 600]
    # A model request's own time is read as each is sent: it starts nothing again.
    stub.dispatch("agent.configure", {"turn_limits": {"iterations": 60, "answer_minutes": 10, "request_seconds": 60}})
    time.sleep(0.5)
    assert len(stub.starts()) == 2
