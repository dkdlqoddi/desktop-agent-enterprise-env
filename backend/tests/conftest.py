"""What a test needs beyond Python, as markers (registered in backend/pyproject.toml), and where it is skipped:

- windows(reason): Windows only.
- office: desktop Office on Windows, opted into with PRIVATE_AGENT_OFFICE_TEST=1, since it drives the user's own Office.
- browser: Playwright's Chromium, which npm run setup:browser installs. Without it these tests are skipped, so the
  rest of the suite runs on a PC that never installed the browser; CI sets PRIVATE_AGENT_REQUIRE_BROWSER=1, and they
  fail there instead of being skipped when the browser did not install.

test_web_login.py and test_web_assist.py, which sign in on real Chromium, are marked as a whole (their pytestmark);
elsewhere the tests named test_real_browser_… are, and they are marked here."""

import functools
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest


@functools.cache
def chromium_missing():
    """Why the Chromium build of the installed Playwright cannot be started, or "" when it is installed. Asked once, and
    only when a browser test was collected: it starts Playwright's driver."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            path = playwright.chromium.executable_path
    except Exception as error:  # A driver that does not start: the tests would fail the same way.
        return f"Playwright을 시작하지 못했습니다: {error}"
    return (
        ""
        if path and os.path.exists(path)
        else f"Playwright의 Chromium이 없습니다({path}). npm run setup:browser를 실행하세요."
    )


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.name.startswith("test_real_browser"):
            item.add_marker(pytest.mark.browser)
    office = sys.platform == "win32" and os.environ.get("PRIVATE_AGENT_OFFICE_TEST") == "1"
    require_browser = os.environ.get("PRIVATE_AGENT_REQUIRE_BROWSER") == "1"
    for item in items:
        windows = item.get_closest_marker("windows")
        if windows and sys.platform != "win32":
            item.add_marker(pytest.mark.skip(reason=windows.args[0] if windows.args else "Windows only"))
        if item.get_closest_marker("office") and not office:
            item.add_marker(pytest.mark.skip(reason="Requires Windows Office; opt in with PRIVATE_AGENT_OFFICE_TEST=1"))
        if item.get_closest_marker("browser") and not require_browser and chromium_missing():
            item.add_marker(pytest.mark.skip(reason=chromium_missing()))


@pytest.fixture
def model_server():
    """An LLM server on this PC that a test scripts: `reply(request)` gives (status, body, streaming[, headers]) for
    each request it gets, all of which it keeps (requests, headers). `url` is its chat-completions address."""
    state = SimpleNamespace(requests=[], headers=[], reply=None, delay=0.0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append(request)
            state.headers.append(dict(self.headers))
            time.sleep(state.delay)
            status, value, streaming, *extra = state.reply(request)
            body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
            self.send_response(status)
            for name, header in (extra[0] if extra else {}).items():
                self.send_header(name, header)
            self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    yield state
    server.shutdown()
    server.server_close()
    thread.join()
