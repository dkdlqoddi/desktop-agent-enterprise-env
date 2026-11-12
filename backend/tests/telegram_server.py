"""A stand-in for Telegram's Bot API on this PC, for the tests of the messenger gateway: it hands the bot the messages
a test writes (as long polling does) and keeps what the bot sends back. No request leaves the PC.

The gateway is pointed at it with PRIVATE_AGENT_TELEGRAM_API (or Gateway.telegram_api), which takes only such an
address on 127.0.0.1. Run as a program it serves until its input closes and prints its address first, which is how
tests/gateway.spec.ts uses it: a line read from its input is {"from": id, "text": words} to hand over or {"sent": true}
to print what the bot sent so far.
"""

import json
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "123456:stand-in-token"


class TelegramServer:
    def __init__(self, token_accepted=True):
        self.token_accepted = token_accepted
        self.lock = threading.Lock()
        self.calls = []  # (method, fields) of every request but the polls
        self.pending = []  # Messages a test wrote that the bot has not read yet.
        self.polls = 0
        self.count = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                method = self.path.rsplit("/", 1)[-1]
                kind = self.headers.get("Content-Type", "")
                if "json" in kind:
                    fields = json.loads(raw or b"{}")
                elif "urlencoded" in kind:
                    fields = {key: value[0] for key, value in urllib.parse.parse_qs(raw.decode()).items()}
                else:
                    fields = {}
                status, result = outer.answer(method, fields)
                body = json.dumps(
                    {"ok": True, "result": result}
                    if status == 200
                    else {"ok": False, "error_code": status, "description": "Unauthorized"}
                ).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/bot"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def answer(self, method, fields):
        if not self.token_accepted:
            return 401, None
        now = int(time.time())
        with self.lock:
            self.count += 1
            number = self.count
            if method == "getUpdates":
                self.polls += 1
                update = self.pending.pop(0) if self.pending else None
            else:
                self.calls.append((method, fields))
        if method == "getMe":
            return 200, {
                "id": 123456,
                "is_bot": True,
                "first_name": "Stand-in",
                "username": "stand_in_bot",
                "can_join_groups": True,
                "can_read_all_group_messages": False,
                "supports_inline_queries": False,
            }
        if method == "getUpdates":
            if update is None:
                time.sleep(0.3)
                return 200, []
            sender = update["from"]
            chat = {"id": sender, "type": "private", "first_name": "User"}
            person = {"id": sender, "is_bot": False, "first_name": "User", "language_code": "ko"}
            if "press" in update:
                # The press of a button under one of the bot's messages.
                pressed = {"id": str(number), "from": person, "chat_instance": "1", "data": update["press"]}
                pressed["message"] = {"message_id": update["message_id"], "date": now, "chat": chat, "text": "?"}
                return 200, [{"update_id": 1000 + number, "callback_query": pressed}]
            message = {"message_id": number, "date": now, "chat": chat, "from": person, "text": update["text"]}
            return 200, [{"update_id": 1000 + number, "message": message}]
        if method in ("sendMessage", "editMessageText"):
            chat = {"id": int(fields.get("chat_id") or 0), "type": "private"}
            return 200, {"message_id": 5000 + number, "date": now, "chat": chat, "text": str(fields.get("text", ""))}
        if method == "getChat":
            return 200, {"id": int(fields.get("chat_id") or 0), "type": "private"}
        if method == "getMyCommands":
            return 200, []
        return 200, True

    # -- what a test does ------------------------------------------------------------

    def write(self, text, sender=42):
        """A message to the bot from the account `sender`."""
        with self.lock:
            self.pending.append({"from": sender, "text": text})

    def sent(self, chat=None):
        """The texts the bot sent (to `chat`, or to anyone), in order, as their reader sees them: without the
        backslashes Telegram's MarkdownV2 wants before a full stop and its like."""
        with self.lock:
            return [
                re.sub(r"\\(.)", r"\1", str(fields.get("text", "")))
                if fields.get("parse_mode") == "MarkdownV2"
                else str(fields.get("text", ""))
                for method, fields in self.calls
                if method == "sendMessage" and (chat is None or str(fields.get("chat_id")) == str(chat))
            ]

    def menu(self):
        """The commands the bot listed for its menu, as last set."""
        with self.lock:
            lists = [fields.get("commands") for method, fields in self.calls if method == "setMyCommands"]
        return [entry["command"] for entry in json.loads(lists[-1])] if lists else []

    def wait(self, found, seconds=60):
        """Wait until found() is true; False when it never was."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if found():
                return True
            time.sleep(0.1)
        return False


if __name__ == "__main__":
    with TelegramServer() as telegram:
        print(json.dumps({"url": telegram.url, "token": TOKEN}), flush=True)
        for line in sys.stdin:
            try:
                request = json.loads(line)
            except ValueError:
                continue
            if request.get("sent"):
                print(json.dumps({"sent": telegram.sent()}, ensure_ascii=False), flush=True)
            elif isinstance(request.get("text"), str):
                telegram.write(request["text"], int(request.get("from") or 42))
