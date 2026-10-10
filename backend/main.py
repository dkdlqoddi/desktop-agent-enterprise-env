"""Private JSON-lines RPC over stdin/stdout. No HTTP listener or exposed local port."""
import importlib
import json
import multiprocessing
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from agent import diagnostics
from agent.db import Database
from agent.folders import sweep_scratch
from agent.llm import BATCH, INTERACTIVE, LLM, UserError, priority
from agent.service import Service

# Requests that wait on the LLM or a browser can take minutes. They get their own lane, so the quick
# reads a screen makes all at once (lists, statuses) are never refused behind them.
SLOW = frozenset({"settings.test", "prompts.run", "wiki.analyze", "assistant.ask", "briefing.generate",
                  "tasks.extract", "schedules.run", "schedules.execute", "confluence.check", "jira.check"})
# Requests a user waits for on the screen (or an AI tool for its answer): their LLM requests go before those of batch
# work (analyses, imports, routines, scheduled runs), which also leaves them a slot of their own (llm.Slots).
INTERACTIVE_METHODS = frozenset({"assistant.ask", "settings.test", "prompts.run", "briefing.generate", "tasks.extract",
                                 "wiki.search", "wiki.analyze", "schedules.run", "confluence.check", "jira.check"})
SLOW_SLOTS = 4
QUICK_SLOTS = 24
# Electron sends a request's parameters as at most 900,000 characters of JSON.
MAX_LINE = 1_000_000
# Electron writes the id first: {"id":"…","method":…}.
REQUEST_ID = re.compile(r'\s*\{\s*"id"\s*:\s*"([^"\\]{1,200})"')


def request_id(line):
    """The id of a request line that could not be read, when it starts with one, so the caller hears back at once
    instead of waiting out its time limit."""
    match = REQUEST_ID.match(line)
    return match.group(1) if match else None


def slow(request):
    params = request.get("params")
    return request.get("method") in SLOW or (
        request.get("method") == "wiki.search" and isinstance(params, dict) and params.get("mode") == "prompt")


def priority_of(request):
    """The priority of a request's LLM requests (llm.PRIORITY)."""
    return INTERACTIVE if request.get("method") in INTERACTIVE_METHODS else BATCH


def sweep_leftovers():
    """Remove the import scratch folders an earlier run left in the temporary folder, beside answering requests."""
    removed = sweep_scratch()
    if removed:
        diagnostics.LOG.info("removed %d scratch folder(s) left by an earlier run", len(removed))


def detach_standard_input():
    """On Windows, starting a process (the Office and Word workers) waits while a synchronous read of the pipe that is
    the standard input is pending, and this process waits there for Electron's next request: an import stood still
    until the app asked something. Requests are still read through file descriptor 0; new processes get NUL."""
    if sys.platform != "win32":
        return
    import ctypes
    import msvcrt
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetStdHandle.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
    nul = os.open(os.devnull, os.O_RDONLY)  # Open for as long as the process runs.
    if not kernel32.SetStdHandle(ctypes.c_uint32(-10).value, msvcrt.get_osfhandle(nul)):  # STD_INPUT_HANDLE
        os.close(nul)


def main():
    if not getattr(sys, "frozen", False):
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    detach_standard_input()
    # Path.home() fails without USERPROFILE on Windows, so it is only asked when Electron names no folder.
    data_dir = Path(os.environ.get("PRIVATE_AGENT_DATA_DIR") or Path.home() / ".private-agent")
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Electron names its settings folder's logs/; a backend started by hand writes no log.
    diagnostics.start(os.environ.get("PRIVATE_AGENT_LOG_DIR"))
    diagnostics.LOG.info("backend started · Python %s%s", sys.version.split()[0],
                         " · packaged" if getattr(sys, "frozen", False) else "")
    threading.Thread(target=sweep_leftovers, name="sweep-scratch", daemon=True).start()
    service = Service(Database(data_dir / "wiki.db"), LLM())
    write_lock = threading.Lock()
    lanes = {True: threading.BoundedSemaphore(SLOW_SLOTS), False: threading.BoundedSemaphore(QUICK_SLOTS)}

    def write(message):
        with write_lock:
            print(json.dumps(message, ensure_ascii=False), flush=True)

    def emit(event, params):
        """An event of the backend's own (an answer as the LLM writes it): a line without an id, which Electron passes
        to the window instead of taking it for an answer."""
        write({"event": event, "params": params})

    service.emit = emit

    def handle(request, slots):
        ident = request.get("id")
        try:
            with priority(priority_of(request)):
                result = service.dispatch(request.get("method"), request.get("params", {}))
            write({"id": ident, "result": result})
        except UserError as exc:
            write({"id": ident, "error": str(exc)})
        except Exception as exc:
            # Never send traces, credentials, or endpoint details to the renderer: they go to the local log,
            # under the ID the message ends with.
            write({"id": ident, "error": diagnostics.report(
                exc, "요청을 처리하지 못했습니다. 입력을 확인하고 다시 시도해 주세요.", f"요청 {request.get('method')}")})
        finally:
            slots.release()

    with ThreadPoolExecutor(max_workers=SLOW_SLOTS + QUICK_SLOTS) as pool:
        for line in sys.stdin:
            ident = None
            try:
                if len(line) > MAX_LINE:
                    write({"id": request_id(line), "error": "요청이 너무 큽니다."})
                    continue
                request = json.loads(line)
                if not isinstance(request, dict) or not isinstance(request.get("id"), str):
                    raise ValueError()
                ident = request["id"]
                if not isinstance(request.get("method"), str):
                    raise ValueError()
                slots = lanes[slow(request)]
                if not slots.acquire(blocking=False):
                    write({"id": ident, "error": "진행 중인 요청이 많습니다. 잠시 후 다시 시도해 주세요."})
                    continue
                pool.submit(handle, request, slots)
            except Exception:
                # Whatever a line holds (not JSON, nesting deeper than the parser goes, the wrong shape), it is refused
                # and the next one is read: one request must never end the backend.
                write({"id": ident or request_id(line), "error": "잘못된 RPC 요청입니다."})
        # End of input (the app is closing): stop the answers being written, the imports, browsers and analysis as
        # backend.shutdown does, before leaving this block waits for the requests still running.
        service.assistant.cancel_all()
        service.shutdown()


def read_text(path):
    """A text file of the user's (a demo's list or captions) as a hidden Notepad shows it, for Electron when the file's
    bytes are no text it can read: JSON {"text": …} on the standard output, or the reason on the error output and exit
    status 1."""
    from agent.notepad import read_with_notepad
    try:
        text = read_with_notepad(path)
        # Notepad's text is UTF-16 and may end in half of a surrogate pair where its length cut it, which UTF-8 cannot
        # hold: that half becomes U+FFFD.
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
        output = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    except UserError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("메모장으로 파일을 읽지 못했습니다.", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


def migrate_storage(source, destination):
    """An earlier version's Wiki copied into the folder the user chose, for Electron, which stops a copy that makes no
    progress for a minute: a line on the standard output says it is going on, at most once a second. The reason of a
    failure goes to the error output, with exit status 1."""
    from agent.storage import migrate_database
    last = 0.0

    def progress():
        nonlocal last
        now = time.monotonic()
        if now - last >= 1:
            last = now
            print("copying", flush=True)

    try:
        progress()
        migrate_database(source, destination, progress)
    except Exception:
        print("기존 저장소를 복사하지 못했습니다. 대상 폴더, 기존 파일, 권한과 디스크 공간을 확인해 주세요.", file=sys.stderr)
        return 1
    return 0


def check_build(folder):
    """For scripts/build-python.mjs: import every standard extension module the frozen backend has in its folder (the
    .pyd files beside python3X.dll). One whose DLL PyInstaller did not collect, as sqlite3.dll that conda keeps apart
    from _sqlite3.pyd, fails the build with its name instead of ending the app at its start."""
    failed = False
    for module in sorted(Path(folder).glob("*.pyd")):
        name = module.name.split(".")[0]
        try:
            importlib.import_module(name)
        except ImportError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) == 4 and sys.argv[1] == "--migrate-storage":
        sys.stderr.reconfigure(encoding="utf-8")
        sys.exit(migrate_storage(sys.argv[2], sys.argv[3]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--read-text":
        sys.stderr.reconfigure(encoding="utf-8")
        sys.exit(read_text(sys.argv[2]))
    elif len(sys.argv) == 2 and sys.argv[1] == "--check-build":
        sys.exit(check_build(sys._MEIPASS))
    else:
        main()
