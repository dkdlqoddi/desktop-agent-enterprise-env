"""What failed unexpectedly, kept for whoever fixes it. The screens only get a short message, never traces, paths,
addresses or credentials; this log in the app's settings folder keeps the exception, its traceback and the short ID
that ends the message, so a failure on a user's PC can be found again and reported. It holds no request parameters
and no document text, only exceptions, whose messages may name files."""

import errno
import logging
import logging.handlers
import os
import re
import secrets
import sqlite3
import sys
import traceback
from pathlib import Path

from .llm import UserError

LOG = logging.getLogger("private_agent")
LOG.setLevel(logging.INFO)
LOG.propagate = False
# PRIVATE_AGENT_LOG_LEVEL's names: debug adds what helps while developing, warning and error leave out the notes of
# what the backend did (its start, the scratch folders it removed). Failures are errors and are logged at every level.
LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}
FORMAT = "%(asctime)s %(levelname)s %(message)s"
# Two files of 1 MB: the failures of many days, and still small enough to send.
MAX_BYTES = 1_000_000
BACKUPS = 1
MAX_TRACE = 20_000
# The ID a logged failure's message ends with, as report() and worker_failure() write it.
LOG_ID = re.compile(r"\s*\(오류 ID [0-9a-f]{8}\)")
# Secrets as they could appear in an exception's message (a URL, a header, a JSON body): the app's own API tokens
# (pa_…), OpenAI-style keys, bearer tokens and anything named as a password, key, token or cookie. Written down as
# [REDACTED] (electron/diagnostics.cjs does the same for the app's log and the copied report).
SECRETS = (
    (re.compile(r"\bpa_[A-Za-z0-9_-]{6,}"), "pa_[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{6,}"), "sk-[REDACTED]"),
    (
        re.compile(
            r"\b(password|passwd|pwd|api[_-]?key|x-api-key|authorization|token|secret|cookie)\b"
            r"(\"?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)",
            re.I,
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"\b(bearer)\s+[A-Za-z0-9._~+/=-]{6,}", re.I), r"\1 [REDACTED]"),
)

DISK_FULL = "디스크 공간이 부족해 저장하지 못했습니다. 공간을 확보한 뒤 다시 시도해 주세요."


def redact(text):
    """`text` with the secrets it may hold (SECRETS) written as [REDACTED]."""
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


class Redacting(logging.Formatter):
    """Writes every entry with its secrets redacted: a traceback quotes the values an exception carried."""

    def format(self, record):
        return redact(super().format(record))


def level(name=None):
    """The level PRIVATE_AGENT_LOG_LEVEL (or `name`) names; info when it names none of LEVELS."""
    if name is None:
        name = os.environ.get("PRIVATE_AGENT_LOG_LEVEL", "")
    return LEVELS.get(name.strip().lower(), logging.INFO)


def start(directory):
    """Log to <directory>/backend.log at the level PRIVATE_AGENT_LOG_LEVEL names, and to the error output as well with
    PRIVATE_AGENT_LOG_CONSOLE=1, which npm run dev sets so that the terminal shows the log as it is written. Without a
    directory (tests, a backend started by hand) no file is written, and a log that cannot be opened never stops the
    backend."""
    LOG.setLevel(level())
    # A FileHandler is a StreamHandler too: only a plain one is the error output.
    if os.environ.get("PRIVATE_AGENT_LOG_CONSOLE") == "1" and not any(
        type(handler) is logging.StreamHandler for handler in LOG.handlers
    ):
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(Redacting(FORMAT))
        LOG.addHandler(console)
    if not directory or any(isinstance(handler, logging.FileHandler) for handler in LOG.handlers):
        return
    try:
        Path(directory).mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            Path(directory) / "backend.log", maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True
        )
    except OSError:
        return
    handler.setFormatter(Redacting(FORMAT))
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
