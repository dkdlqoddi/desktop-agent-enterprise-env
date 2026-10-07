"""What failed unexpectedly, kept for whoever fixes it. The screens only get a short message, never traces, paths,
addresses or credentials; this log in the app's settings folder keeps the exception, its traceback and the short ID
that ends the message, so a failure on a user's PC can be found again and reported. It holds no request parameters
and no document text, only exceptions, whose messages may name files."""
import errno
import logging
import logging.handlers
import re
import secrets
import sqlite3
import traceback
from pathlib import Path

from .llm import UserError

LOG = logging.getLogger("private_agent")
LOG.setLevel(logging.INFO)
LOG.propagate = False
# Two files of 1 MB: the failures of many days, and still small enough to send.
MAX_BYTES = 1_000_000
BACKUPS = 1
MAX_TRACE = 20_000
# The ID a logged failure's message ends with, as report() and worker_failure() write it.
LOG_ID = re.compile(r"\s*\(오류 ID [0-9a-f]{8}\)")

DISK_FULL = "디스크 공간이 부족해 저장하지 못했습니다. 공간을 확보한 뒤 다시 시도해 주세요."


def start(directory):
    """Log to <directory>/backend.log. Without a directory (tests, a backend started by hand) nothing is written, and
    a log that cannot be opened never stops the backend."""
    if not directory or any(isinstance(handler, logging.FileHandler) for handler in LOG.handlers):
        return
    try:
        Path(directory).mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(Path(directory) / "backend.log", maxBytes=MAX_BYTES,
                                                       backupCount=BACKUPS, encoding="utf-8", delay=True)
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)


def known(error):
    """A message of its own for a failure whose cause the user can do something about."""
    text = str(error).lower()
    if isinstance(error, sqlite3.OperationalError):
        if "locked" in text or "busy" in text:
            return "Wiki 데이터베이스를 다른 작업이나 프로그램이 쓰고 있어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."
        if "full" in text:
            return DISK_FULL
        if "disk i/o" in text or "unable to open" in text or "readonly" in text:
            return "Wiki 데이터베이스 파일을 읽거나 쓰지 못했습니다. Wiki 폴더가 있는 드라이브의 연결과 권한을 확인해 주세요."
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return DISK_FULL
    if isinstance(error, PermissionError):
        return "파일이나 폴더에 접근할 권한이 없어 처리하지 못했습니다. 권한과, 다른 프로그램이 그 파일을 쓰고 있는지 확인해 주세요."
    if isinstance(error, MemoryError):
        return "메모리가 부족해 처리하지 못했습니다. 다른 프로그램을 닫거나 작은 파일로 나누어 다시 시도해 주세요."
    return ""


def logged(context, trace):
    """Record `trace` under a new ID and return the ID."""
    ident = secrets.token_hex(4)
    LOG.error("%s · 오류 ID %s\n%s", context, ident, trace.rstrip()[-MAX_TRACE:])
    return ident


def report(error, general, context):
    """The message the user sees for `error`. A UserError says what happened by itself; anything else is logged with
    its traceback, and its message (a clearer one for a known cause, `general` otherwise) ends with the log's ID."""
    if isinstance(error, UserError):
        return str(error)
    ident = logged(context, "".join(traceback.format_exception(type(error), error, error.__traceback__)))
    return f"{known(error) or general} (오류 ID {ident})"


def worker_failure(message, trace, context):
    """The message of a failure a worker process reported, with the ID of its traceback when it sent one."""
    if not trace:
        return message
    return f"{message} (오류 ID {logged(context, trace)})"


def without_ids(message):
    """`message` without the log IDs it holds: the same failure told twice reads the same, though each has its ID."""
    return LOG_ID.sub("", message or "")
