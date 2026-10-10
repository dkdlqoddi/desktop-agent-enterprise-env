import contextlib
import contextvars
import email.utils
import json
import logging
import math
import os
import queue
import random
import re
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.cookiejar import DefaultCookiePolicy
from urllib.parse import urlparse

import requests
from urllib3.exceptions import ReadTimeoutError

# The backend's log, which diagnostics.py (it imports this module) sets up: a failed request is noted there by its host,
# status and time, never with the key, the headers or a body.
LOG = logging.getLogger("private_agent")


class UserError(Exception):
    pass


class BusyError(UserError):
    """A job could not start because one of its kind is running (an import, a login window, a write to an original or
    an analysis): the automatic routines (routines.py) wait for it to end and start theirs then."""


class LLMFormatError(UserError):
    """The LLM answered, but not in the form asked for; a caller may fall back on something of its own."""


class LLMTruncatedError(LLMFormatError):
    """The LLM stopped at its output limit (max_tokens) before its answer was whole. It is a format error, so a caller
    that falls back on something of its own does; the others tell the user to raise the limit."""


class LLMRequestError(UserError):
    """The LLM gave no answer: the server answered with an HTTP status other than 200 (`status`), or not at all (None).
    A `transient` failure (a time-out, a dropped connection, HTTP 429, 408 or a server error) may pass when the request
    is sent again later; the others (a rejected key, a wrong address or setting) wait for the user."""

    def __init__(self, message, status=None, transient=False):
        super().__init__(message)
        self.status, self.transient = status, transient


class Cancelled(UserError):
    """The user stopped the request (assistant.cancel); `partial` holds what the LLM had written of its answer."""

    def __init__(self, partial=""):
        super().__init__("요청을 중지했습니다.")
        self.partial = partial


class StreamFailure(Exception):
    """The server broke off a streamed answer with an error of its own, such as an overloaded server's."""


# A reasoning model's thinking before its answer, or all that is left of it when the reply was cut short.
THINKING = re.compile(r"<think>.*?(?:</think>|\Z)", re.S | re.I)
# The thinking of a model whose gateway leaves out the opening tag: all before a </think> that no <think> opened.
UNOPENED = re.compile(r"\A(?:(?!<think>).)*?</think>", re.S | re.I)
# Each "{" is a place a JSON object may start; a reply is short, so a few tries are enough.
MAX_STARTS = 50
# Raw line breaks and tabs inside strings are taken, as models write them.
DECODER = json.JSONDecoder(strict=False)
# What a reply that is not JSON as it is has repaired: in its strings, a backslash that starts no JSON escape, as a
# Markdown escape copied from a document (API\_SPEC); outside them, a comma before the end of an object or list.
TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|,(?=\s*[}\]])', re.S)
LONE_BACKSLASH = re.compile(r'\\(?:(["\\/bfnrt]|u[0-9a-fA-F]{4})|)')


def answer(reply):
    """A reply without a reasoning model's thinking: its <think>…</think> blocks, one cut short at the end, and what
    comes before a </think> that has no opening tag."""
    return THINKING.sub("", UNOPENED.sub("", reply, count=1)).strip()


def repaired(text):
    """JSON text with a backslash of its own for each backslash in a string that starts no escape, and without commas
    that end an object or list."""

    def fix(match):
        token = match.group(0)
        if token == ",":
            return ""
        return LONE_BACKSLASH.sub(lambda escape: escape.group(0) if escape.group(1) else "\\\\", token)

    return TOKEN.sub(fix, text)


def decoded(text, start=0):
    """The JSON object that starts at `start` of `text`, or None."""
    try:
        value, _ = DECODER.raw_decode(text, start)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def json_object(reply):
    """The JSON object an LLM reply holds, with what models put around it: a reasoning block, code fences, or a
    sentence before or after the object. The first object that reads, as it is or repaired, is the answer, so an object
    inside it is never taken for it."""
    text = answer(reply)
    for count, match in enumerate(re.finditer(r"\{", text)):
        if count >= MAX_STARTS:
            break
        value = decoded(text, match.start())
        if value is None:
            value = decoded(repaired(text[match.start() :]))
        if value is not None:
            return value
    raise LLMFormatError("LLM이 유효한 JSON을 반환하지 않았습니다. 다시 분석해 주세요.")


FORMATS = ("openai", "anthropic")
ANTHROPIC_VERSION = "2023-06-01"
# The Messages API needs a cap on the answer: far above an analysis or an answer, and within every current model's. A
# request names it while max_tokens is left automatic (0).
ANTHROPIC_MAX_TOKENS = 16000
# Where each format puts the answer, for the message when it is missing.
ANSWER = {"openai": "choices[0].message.content", "anthropic": "content의 text"}
# The reasons an answer ended that mean the LLM stopped at its output limit: the text may end mid-sentence, and a JSON
# answer is not whole.
TRUNCATION = {"openai": frozenset({"length"}), "anthropic": frozenset({"max_tokens", "model_context_window_exceeded"})}
REFUSED = "LLM이 이 요청에는 답하지 않았습니다. 내용을 바꿔 다시 시도해 주세요."
UNREACHABLE = "LLM API에 연결할 수 없습니다. 네트워크·프록시, API 엔드포인트, 인증서 경로를 확인해 주세요."

# Connection settings for any supported LLM endpoint. max_tokens caps each answer (0 leaves it automatic: the server's
# own cap, or ANTHROPIC_MAX_TOKENS for the Messages API, which needs one); context_window is how many tokens the model
# reads and writes in one request, which bounds what a question sends (LLM.prompt_chars); concurrency is how many
# requests go to the server at once (Slots).
DEFAULTS = {
    "provider": "general",
    "api_format": "openai",
    "base_url": "",
    "api_key": "",
    "model_name": "",
    "headers": [],
    "temperature": 0.5,
    "timeout": 300.0,
    "ca_bundle": "",
    "max_tokens": 0,
    "context_window": 64_000,
    "concurrency": 3,
}
SECRETS = ("api_key",)
TEXT = ("provider", "api_format", "base_url", "api_key", "model_name", "ca_bundle")
# The whole numbers among the settings: the lowest and highest value, whether 0 (automatic) is taken too, and what the
# user is told for another value.
WHOLE = {
    "max_tokens": (256, 1_000_000, True, "최대 출력 토큰은 0(자동) 또는 256~1,000,000으로 입력해 주세요."),
    "context_window": (4_096, 10_000_000, False, "컨텍스트 크기는 4,096~10,000,000 토큰으로 입력해 주세요."),
    "concurrency": (1, 8, False, "동시 요청 수는 1~8로 입력해 주세요."),
}
# The environment variable of each setting, as .env.example names them.
ENVIRONMENT = {
    "provider": "PROVIDER",
    "api_format": "API_FORMAT",
    "base_url": "BASE_URL",
    "api_key": "API_KEY",
    "model_name": "MODEL_NAME",
    "headers": "LLM_HEADERS",
    "temperature": "TEMPERATURE",
    "timeout": "LLM_TIMEOUT",
    "ca_bundle": "CA_BUNDLE",
    "max_tokens": "LLM_MAX_TOKENS",
    "context_window": "LLM_CONTEXT_WINDOW",
    "concurrency": "LLM_CONCURRENCY",
}
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}")
# Printable ASCII: what any HTTP header carries as it is, so a value never fails on its way out.
HEADER_TEXT = re.compile(r"[\x20-\x7e]{0,8192}")
# A token copied together with its scheme, as the Authorization header shows it.
BEARER = re.compile(r"\Abearer\s+", re.I)
# The HTTP client writes these from the request itself.
RESERVED = {"host", "content-length", "transfer-encoding", "connection"}
MAX_HEADERS = 30
MOVED_HEADERS = (
    "API 엔드포인트를 다른 서버로 바꿀 때는 custom header 값을 모두 함께 보내 주세요. 저장된 헤더 값은 새 서버로 "
    "보내지 않습니다."
)

# Failures that may pass when the request is sent again a moment later: a server that is busy or overloaded (429, 503,
# Anthropic's 529), a gateway's (502, 504), a server error (500) and a request the server gave up waiting for (408).
RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})
ATTEMPTS = 4  # Requests for one answer at most: the first and three more...
BACKOFF = 0.5  # ...the second this many seconds after a failure and each next one twice as long, with jitter...
MAX_BACKOFF = 8.0
MAX_WAIT = 30.0  # ...waiting no more than this in all, the waits a Retry-After asks for included.
MIN_ATTEMPT = 2.0  # Another request goes only while the call's time limit leaves it this many seconds.
CONNECT_SECONDS = 10
# Connections the session keeps open are closed once no request has used them for this long: a firewall or proxy may
# have dropped them without a word meanwhile, which a request sent on one would learn only at its time limit.
IDLE_SECONDS = 60

# How a text answer the LLM stopped at its output limit ends, for the screens that show it; and why a JSON answer, or
# one that is all thinking, cut short there could not be used.
TRUNCATED_NOTE = (
    "(답변이 LLM의 최대 출력 길이에서 잘렸습니다. 관리 → LLM 연결에서 최대 출력 토큰을 늘리거나 요청을 줄여 다시 "
    "시도해 주세요.)"
)
TRUNCATED_JSON = (
    "LLM 응답이 최대 출력 길이에서 잘려 결과를 읽지 못했습니다. 관리 → LLM 연결에서 최대 출력 토큰을 늘리거나 "
    "내용을 줄여 다시 시도해 주세요."
)
TRUNCATED_THINKING = (
    "LLM이 답을 쓰기 전에 최대 출력 길이에 닿았습니다(추론만 있음). 관리 → LLM 연결에서 최대 출력 토큰을 "
    "늘려 다시 시도해 주세요."
)
RETRY_TOKENS = 16_000  # The cap a JSON answer cut short is asked again with when its request named none...
MAX_RETRY_TOKENS = 64_000  # ...and otherwise twice the cap it named, up to this.

ANSWER_ROOM = 8_000  # Tokens kept for the answer when max_tokens leaves its cap to the server...
CHARS_PER_TOKEN = 1  # ...and the characters a request may carry for each token of the rest of the context window:
# Korean text takes about one token a character, other text fewer.

INTERACTIVE, BATCH = "interactive", "batch"
# Whose LLM requests a thread makes: those of a request the user waits for on the screen (main.py marks them
# INTERACTIVE), or batch work's (analysis, PageIndex, imports, routines, scheduled runs), every thread's default.
PRIORITY = contextvars.ContextVar("llm_priority", default=BATCH)
# The Cancel of a request the user may stop (assistant.ask), for every LLM request it makes.
CANCEL = contextvars.ContextVar("llm_cancel", default=None)
POLL = 0.25  # How often a request waiting for a slot looks whether it was cancelled.


@contextlib.contextmanager
def priority(level):
    """The LLM requests made inside go at `level` (INTERACTIVE or BATCH)."""
    token = PRIORITY.set(level)
    try:
        yield
    finally:
        PRIORITY.reset(token)


@contextlib.contextmanager
def cancellable(cancel):
    """The LLM requests made inside stop when `cancel` (a Cancel) is set."""
    token = CANCEL.set(cancel)
    try:
        yield
    finally:
        CANCEL.reset(token)


def environment(name):
    """A setting's environment variable: its PRIVATE_AGENT_ name (PRIVATE_AGENT_BASE_URL, …) always, and its plain name
    of .env.example only when running from source. A packaged app must not take up a BASE_URL or API_KEY that another
    program set for itself."""
    value = os.environ.get("PRIVATE_AGENT_" + name)
    if value is None and not getattr(sys, "frozen", False):
        value = os.environ.get(name)
    return value


def environment_settings():
    """Starting values from the environment."""
    values = {key: value for key, name in ENVIRONMENT.items() if (value := environment(name)) is not None}
    if "headers" in values:
        try:
            values["headers"] = json.loads(values["headers"] or "[]")
        except ValueError:
            pass  # Not JSON: refused like any other value that is not valid, and the default stays.
    return values


def clean_headers(value):
    """The custom headers as a list of {name, value}; a {name: value} object is taken too. Rows left empty in the form
    are dropped."""
    if isinstance(value, dict):
        value = [{"name": name, "value": text} for name, text in value.items()]
    if not isinstance(value, list) or len(value) > MAX_HEADERS:
        raise UserError(f"헤더는 {MAX_HEADERS}개까지 [{{name, value}}] 목록으로 입력해 주세요.")
    headers, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or not all(isinstance(item.get(key, ""), str) for key in ("name", "value")):
            raise UserError("헤더는 이름과 값을 글자로 입력해 주세요.")
        name, text = item.get("name", "").strip(), item.get("value", "").strip()
        if not name and not text:
            continue
        if not HEADER_NAME.fullmatch(name):
            raise UserError(f"헤더 이름 ‘{name[:40]}’을(를) 확인해 주세요. 영문·숫자와 - 같은 기호만 쓸 수 있습니다.")
        if name.lower() in RESERVED:
            raise UserError(f"{name} 헤더는 앱이 요청에 맞게 정합니다. 목록에서 빼 주세요.")
        if name.lower() in seen:
            raise UserError(f"{name} 헤더가 두 번 있습니다. 하나만 남겨 주세요.")
        if not HEADER_TEXT.fullmatch(text):
            raise UserError(f"{name} 헤더 값은 영문·숫자·기호로 입력해 주세요. HTTP 헤더로 보내는 값입니다.")
        seen.add(name.lower())
        headers.append({"name": name, "value": text})
    return headers


def check_url(value):
    try:
        url = urlparse(value)
        valid = url.scheme in ("https", "http") and url.hostname and not (url.username or url.password or url.fragment)
    except ValueError:
        valid = False
    if value and not valid:
        raise UserError("API 주소는 인증정보가 없는 http(s) 전체 엔드포인트여야 합니다.")


def whole(value, low, high, automatic):
    """`value` (a number, or its text from the environment) as a whole number from `low` to `high`, or 0 when
    `automatic` takes it; ValueError or TypeError for anything else."""
    if isinstance(value, bool):
        raise TypeError()
    number = float(value.strip() if isinstance(value, str) else value)
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError()
    number = int(number)
    if not (low <= number <= high or (automatic and number == 0)):
        raise ValueError()
    return number


def server(value):
    """The server an endpoint is on, as (scheme, host, port), which the API Key and the custom headers are saved for;
    None without one."""
    try:
        url = urlparse(value.strip()) if isinstance(value, str) else None
        port = url.port if url else None
    except ValueError:
        return None
    if not url or not url.hostname:
        return None
    scheme = url.scheme.lower()
    return scheme, url.hostname.lower(), port or {"http": 80, "https": 443}.get(scheme)


def hostname(value):
    """The host (and port) of an endpoint as the log names it: never its path or query, which may carry a key."""
    try:
        url = urlparse(value)
        return f"{url.hostname}:{url.port}" if url.port else url.hostname or "?"
    except ValueError:
        return "?"


def profile(config):
    """The endpoint a request goes to, as the settings name it."""
    return {
        "format": config["api_format"],
        "base_url": config["base_url"],
        "api_key": config["api_key"],
        "model_name": config["model_name"],
        "headers": config["headers"],
    }


def missing(config, target):
    """What the LLM in use still needs before a request, as the message that asks for it, or None."""
    if not target["base_url"] or not target["model_name"]:
        return "관리 → LLM 연결의 LLM 설정에 API 엔드포인트와 모델 이름을 입력해 주세요."
    elif not target["api_key"] and any("{api_key}" in header["value"] for header in target["headers"]):
        return "헤더의 {api_key} 자리에 넣을 API Key를 입력해 주세요."
    return None


def request_headers(target):
    key, custom = target["api_key"], target["headers"]
    headers = {"anthropic-version": ANTHROPIC_VERSION} if target["format"] == "anthropic" else {}
    # The key goes in its format's own header, unless the custom headers put it where it belongs themselves.
    if key and not any("{api_key}" in header["value"] for header in custom):
        if target["format"] == "anthropic":
            headers["x-api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
    for header in custom:
        for name in [name for name in headers if name.lower() == header["name"].lower()]:
            del headers[name]
        # Every {uuid} is a new ID, for request tracing.
        value = re.sub(r"\{uuid\}", lambda _: str(uuid.uuid4()), header["value"])
        headers[header["name"]] = value.replace("{api_key}", key)
    return headers


def request_body(target, messages, temperature, max_tokens=0, stream=False):
    """The request of a format. `max_tokens` caps the answer (0: the server's own cap, or ANTHROPIC_MAX_TOKENS for the
    Messages API, which needs one); `stream` has the answer streamed as server-sent events."""
    if target["format"] == "anthropic":
        # The system prompt is a field of its own, and current Claude models refuse a temperature.
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        body = {
            "model": target["model_name"],
            "max_tokens": max_tokens or ANTHROPIC_MAX_TOKENS,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"],
        }
        if system:
            body["system"] = system
        if stream:
            body["stream"] = True
        return body
    body = {"model": target["model_name"], "messages": messages, "temperature": temperature, "stream": stream}
    if max_tokens:
        body["max_tokens"] = max_tokens
    return body


def plain_body(target, body):
    """A request in the form OpenAI's reasoning models (o-series, GPT-5) take: without a temperature, and with the cap
    of the answer as max_completion_tokens, as they refuse max_tokens."""
    plain = {key: value for key, value in body.items() if key != "temperature"}
    if target["format"] == "openai" and "max_tokens" in plain:
        plain["max_completion_tokens"] = plain.pop("max_tokens")
    return plain


def reply_text(api_format, data):
    """The text of a whole answer and the reason it ended (finish_reason, stop_reason). An answer that is empty because
    the LLM reached its output limit while it thought is ''."""
    if not isinstance(data, dict):
        raise ValueError()
    if api_format == "anthropic":
        finish = data.get("stop_reason")
        if finish == "refusal":
            raise UserError(REFUSED)
        # A model that thinks first answers with a thinking block before the text.
        text = "".join(block["text"] for block in data["content"] if block.get("type") == "text")
    else:
        choice = data["choices"][0]
        text, finish = choice["message"]["content"], choice.get("finish_reason")
    if (text is None or text == "") and finish in TRUNCATION[api_format]:
        return "", finish
    if not isinstance(text, str) or not text.strip():
        raise ValueError()
    return text, finish


def stream_part(api_format, event):
    """What one event of a streamed answer brings: (the text it adds, '' for none; the reason the answer ended, when it
    says). Raises StreamFailure for an error the server sent instead."""
    if not isinstance(event, dict):
        return "", None
    if event.get("type") == "error" or event.get("error") is not None:
        raise StreamFailure()
    if api_format == "anthropic":
        kind, delta = event.get("type"), event.get("delta")
        delta = delta if isinstance(delta, dict) else {}
        # Text deltas only: a thinking block's deltas are no part of the answer.
        if kind == "content_block_delta" and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            return delta["text"], None
        if kind == "message_delta":
            return "", delta.get("stop_reason")
        return "", None
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "", None  # Gateways send events of their own, such as content filter results, without choices.
    delta = choices[0].get("delta")
    # Only the content: a reasoning server's reasoning_content is the model's thinking.
    text = delta.get("content") if isinstance(delta, dict) else None
    return (text if isinstance(text, str) else ""), choices[0].get("finish_reason")


def sse_data(chunks):
    """The data of each `data:` line of a server-sent event stream, as text: every streaming LLM API sends one JSON
    object a line. A line is read once its line feed has come (a carriage return before it dropped), so a character
    split between two chunks stays whole."""
    rest = b""
    for chunk in chunks:
        lines = (rest + chunk).split(b"\n")
        rest = lines.pop()
        for line in lines:
            if line.startswith(b"data:"):
                yield line[5:].strip().decode("utf-8", "replace")
    if rest.startswith(b"data:"):
        yield rest[5:].strip().decode("utf-8", "replace")


END = object()  # The end of what a reader thread hands over (apart).


def apart(lines, cancel):
    """The items of `lines`, read in a thread of their own, so that the caller hears of a cancel at once even while a
    read waits for the server: a socket shut down from another thread does not wake a read on every system. The reader
    stops at its next item once it sees the cancel, and the response the caller closes ends a read still waiting."""
    found = queue.Queue()

    def read():
        try:
            for line in lines:
                found.put((line, None))
                if cancel.is_set():
                    break
        except Exception as error:  # The caller raises it.
            found.put((END, error))
        else:
            found.put((END, None))

    threading.Thread(target=read, name="llm-stream", daemon=True).start()
    while True:
        try:
            line, error = found.get(timeout=POLL)
        except queue.Empty:
            if cancel.is_set():
                raise Cancelled() from None
            continue
        if line is END:
            if error is not None:
                raise error
            return
        yield line


def read_stream(response, api_format, on_text, cancel):
    """(text, finish) of a streamed answer, calling `on_text` with all of the text so far each time it grows. A server
    that answered at once despite the stream (JSON) is read as a whole answer. Raises Cancelled, with the text so far,
    once `cancel` is set: a stream the user may stop is read apart, so the cancel never waits for the server."""
    if "text/event-stream" not in (response.headers.get("Content-Type") or "").lower():
        try:
            data = response.json()
        except ValueError:
            data = None
        text, finish = reply_text(api_format, data)
        on_text(text)
        return text, finish
    text, finish = "", None
    lines = sse_data(response.iter_content(chunk_size=None))
    try:
        for data in lines if cancel is None else apart(lines, cancel):
            if cancel is not None and cancel.is_set():
                raise Cancelled(text)
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue  # A keep-alive or comment a gateway writes its own way.
            piece, ended = stream_part(api_format, event)
            finish = ended or finish
            if piece:
                text += piece
                on_text(text)
            if isinstance(event, dict) and event.get("type") == "message_stop":
                break
    except Cancelled:
        raise Cancelled(text) from None
    except StreamFailure:
        raise
    except Exception as error:
        # A read that Cancel.set() interrupted fails as the socket was shut down under it.
        if cancel is not None and cancel.is_set():
            raise Cancelled(text) from None
        # A time-out while streaming reaches here as a connection error; it is told as the time-out it is.
        if isinstance(error, requests.exceptions.ConnectionError) and any(
            isinstance(arg, ReadTimeoutError) for arg in error.args
        ):
            raise requests.exceptions.ReadTimeout() from None
        raise
    if cancel is not None and cancel.is_set() and finish is None:
        raise Cancelled(text)
    if finish == "refusal":
        raise UserError(REFUSED)
    return text, finish


def retry_after(headers):
    """The seconds a refusal's Retry-After asks to wait (as seconds or an HTTP date; OpenAI's retry-after-ms in
    milliseconds), or None without one that reads."""
    try:
        found = {str(name).lower(): str(value).strip() for name, value in (headers or {}).items()}
        if "retry-after-ms" in found:
            seconds = float(found["retry-after-ms"]) / 1000
        elif "retry-after" in found:
            try:
                seconds = float(found["retry-after"])
            except ValueError:
                when = email.utils.parsedate_to_datetime(found["retry-after"])
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                seconds = (when - datetime.now(timezone.utc)).total_seconds()
        else:
            return None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def backoff(attempt):
    """The wait after failed request number `attempt`: BACKOFF seconds, twice as long for each next one up to
    MAX_BACKOFF, less up to half of it at random, so the requests of several callers spread out."""
    return min(MAX_BACKOFF, BACKOFF * 2 ** (attempt - 1)) * random.uniform(0.5, 1.0)


def note_failure(host, failure, started, attempt, wait):
    """A failed request in the backend's log: the host, the status or failure, the time it took and what came next;
    never the key, the headers or a body."""
    LOG.warning(
        "LLM request failed · host %s · %s · %.1f s · attempt %d/%d · %s",
        host,
        failure,
        time.monotonic() - started,
        attempt,
        ATTEMPTS,
        f"retrying in {wait:.1f} s" if wait is not None else "giving up",
    )


def interrupt(response):
    """Wake a read of `response` that waits for the server in another thread: shutting its socket down for reading ends
    the read at once, where closing the response from here might leave it waiting. urllib3 does it for the socket it
    reads (HTTPResponse.shutdown), a TLS tunnel through a proxy included; an older one has the socket shut down here."""
    raw = getattr(response, "raw", None)
    # Best effort: a socket already closed or released leaves the read to notice the cancel at its next part.
    with contextlib.suppress(OSError, ValueError, AttributeError, RuntimeError):
        if hasattr(raw, "shutdown"):
            raw.shutdown()
            return
        sock = getattr(getattr(raw, "connection", None), "sock", None)
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)


class Reply(str):
    """An answer's text, and whether the LLM stopped at its output limit (truncated), so it may end mid-sentence."""

    truncated = False


def reply(text, truncated=False):
    value = Reply(text)
    value.truncated = truncated
    return value


def shown(text):
    """A text answer as a screen shows it: one the LLM stopped at its output limit ends with a note saying so."""
    return f"{text}\n\n{TRUNCATED_NOTE}" if getattr(text, "truncated", False) else text


class Cancel:
    """A request the user may stop. set(), from any thread, ends its waits for a slot or between attempts and the read
    of a streamed answer: the socket of the response being read is shut down, which wakes a read that waits for the
    server."""

    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.response = None

    def is_set(self):
        return self.event.is_set()

    def wait(self, seconds):
        return self.event.wait(seconds)

    def set(self):
        with self.lock:
            self.event.set()
            response = self.response
        if response is not None:
            interrupt(response)

    @contextlib.contextmanager
    def reading(self, response):
        """While `response` is read: set() interrupts it, and one set before interrupts it at once."""
        with self.lock:
            self.response = response
            stopped = self.event.is_set()
        if stopped:
            interrupt(response)
        try:
            yield
        finally:
            with self.lock:
                self.response = None


class Slots:
    """The LLM requests that go to the server at once, at most `limit`. A request a user waits for (INTERACTIVE) goes
    before batch work's (analysis, PageIndex, imports, routines, scheduled runs), which never takes the last slot while
    there are two or more: a question never waits for long analyses to end."""

    def __init__(self, limit):
        self.condition = threading.Condition()
        self.limit = limit
        self.running = {INTERACTIVE: 0, BATCH: 0}
        self.waiting = {INTERACTIVE: 0, BATCH: 0}

    def resize(self, limit):
        with self.condition:
            self.limit = limit
            self.condition.notify_all()

    def free(self, level):
        busy = self.running[INTERACTIVE] + self.running[BATCH]
        if level == INTERACTIVE:
            return busy < self.limit
        reserved = 1 if self.limit > 1 else 0
        return busy < self.limit - reserved and not self.waiting[INTERACTIVE]

    @contextlib.contextmanager
    def hold(self, level, timeout=None, cancel=None):
        """A slot for one request at `level`, waited for at most `timeout` seconds (None: as long as it takes) and
        until `cancel` is set."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self.condition:
            self.waiting[level] += 1
            taken = False
            try:
                while not self.free(level):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    remaining = POLL if deadline is None else min(POLL, deadline - time.monotonic())
                    if remaining <= 0:
                        raise LLMRequestError(
                            "LLM에 보낼 요청이 많아 제한 시간 안에 보내지 못했습니다. 잠시 후 다시 시도해 주세요.",
                            transient=True,
                        )
                    self.condition.wait(remaining)
                self.running[level] += 1
                taken = True
            finally:
                self.waiting[level] -= 1
                if not taken:
                    self.condition.notify_all()  # Batch work waits while a request of the user's waits.
        try:
            yield
        finally:
            with self.condition:
                self.running[level] -= 1
                self.condition.notify_all()


class Outcome:
    """What one request brought: its HTTP status; for a 200, the answer's text, why it ended (finish) and whether the
    stream callback had it as it came (streamed); for another status, the wait its Retry-After asks for."""

    __slots__ = ("status", "text", "finish", "streamed", "wait")

    def __init__(self, status, text="", finish=None, streamed=False, wait=None):
        self.status, self.text, self.finish, self.streamed, self.wait = status, text, finish, streamed, wait


def new_session():
    """The HTTP session of the LLM requests, which keeps their connections open between requests. It keeps and sends
    no cookies, as each request went alone before."""
    session = requests.Session()
    session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    return session


class LLM:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = {**DEFAULTS, "headers": []}
        # Address and model of the LLMs that refused a temperature or max_tokens (OpenAI's reasoning models), asked in
        # their own form (plain_body) from then on; and of those whose server refused to stream, asked for whole
        # answers.
        self.plain_models, self.unstreamed = set(), set()
        self.slots = Slots(DEFAULTS["concurrency"])
        self.session = new_session()
        # The requests in progress, and when the session's connections were last used (IDLE_SECONDS).
        self.sending, self.used = 0, time.monotonic()
        self.load(environment_settings())

    def load(self, values):
        """Settings saved earlier or set in the environment, one at a time: a value that is not valid keeps its
        default instead of stopping the backend before it can answer, and the admin page can set it right."""
        for key, value in values.items():
            try:
                self._apply({key: value})
            except UserError:
                pass
        return self.public()

    def update(self, values):
        """A change from the admin page or an AI tool. The API Key and the custom header values stay with the server
        they were saved for: when the endpoint moves to another server (scheme, host, port), the key is
        forgotten unless a new one comes with the change, and each saved header value goes along only when the change
        sends it again. A header sent without its value keeps the one saved under its name, as AI tools read the
        headers' names only."""
        with self.lock:
            values = dict(values)
            moved = "base_url" in values and server(values["base_url"]) != server(self.config["base_url"])
            if moved and not values.get("api_key"):
                values["api_key"] = ""
            if "headers" in values:
                values["headers"] = self._headers(values["headers"], moved)
            elif moved and any(header["value"] for header in self.config["headers"]):
                raise UserError(MOVED_HEADERS)
            return self._apply(values)

    def _headers(self, headers, moved):
        """The custom headers of an update, each sent without its value given the value saved under its name; on
        another server than the saved one (`moved`) a saved value must be sent again."""
        if not isinstance(headers, list):
            return headers  # A {name: value} object, or not a list: clean_headers() takes it or says why not.
        saved = {header["name"].lower(): header["value"] for header in self.config["headers"]}
        found = []
        for item in headers:
            if isinstance(item, dict) and "value" not in item and isinstance(item.get("name"), str):
                value = saved.get(item["name"].strip().lower(), "")
                if moved and value:
                    raise UserError(MOVED_HEADERS)
                item = {**item, "value": value}
            found.append(item)
        return found

    def _apply(self, values):
        with self.lock:
            config = {**self.config, **{k: v for k, v in values.items() if k in DEFAULTS}}
            for key in TEXT:
                if not isinstance(config[key], str) or len(config[key]) > 8192:
                    raise UserError("설정 값의 형식이나 길이를 확인해 주세요.")
                config[key] = config[key].strip()
                if "\n" in config[key] or "\r" in config[key]:
                    raise UserError("설정에는 줄바꿈을 사용할 수 없습니다.")
            if config["provider"] != "general":
                raise UserError("사용할 LLM은 general로 정해 주세요.")
            if config["api_format"] not in FORMATS:
                raise UserError("API 형식은 openai나 anthropic으로 정해 주세요.")
            for key in SECRETS:
                config[key] = BEARER.sub("", config[key])
                if not HEADER_TEXT.fullmatch(config[key]):
                    raise UserError("API Key는 영문·숫자·기호로 입력해 주세요. HTTP 헤더로 보내는 값입니다.")
            config["headers"] = clean_headers(config["headers"])
            # Explorer's "Copy as path" puts the certificate's path in double quotes.
            if len(config["ca_bundle"]) > 1 and config["ca_bundle"][0] == config["ca_bundle"][-1] == '"':
                config["ca_bundle"] = config["ca_bundle"][1:-1].strip()
            check_url(config["base_url"])
            try:
                config["temperature"] = float(config["temperature"])
                config["timeout"] = float(config["timeout"])
                if not math.isfinite(config["temperature"]) or not 0 <= config["temperature"] <= 2:
                    raise ValueError()
                if not math.isfinite(config["timeout"]) or not 5 <= config["timeout"] <= 300:
                    raise ValueError()
            except (TypeError, ValueError):
                raise UserError("Temperature는 0~2, 제한 시간은 5~300초로 입력해 주세요.") from None
            for key, (low, high, automatic, message) in WHOLE.items():
                try:
                    config[key] = whole(config[key], low, high, automatic)
                except (TypeError, ValueError):
                    raise UserError(message) from None
            self.config = config
            self.slots.resize(config["concurrency"])
            return self.public()

    def public(self):
        """The settings as the screens and AI tools read them: without the API Key and the custom headers' values,
        which may be secrets of their own (a gateway's token); each header says whether it has a value and whether that
        value takes the key ({api_key})."""
        with self.lock:
            config, target = self.config, profile(self.config)
            return {
                **{k: v for k, v in config.items() if k not in SECRETS},
                "headers": [
                    {
                        "name": header["name"],
                        "has_value": bool(header["value"]),
                        "uses_api_key": "{api_key}" in header["value"],
                    }
                    for header in config["headers"]
                ],
                "has_api_key": bool(config["api_key"]),
                "configured": missing(config, target) is None,
                "active_model": target["model_name"],
            }

    def secrets(self):
        """The API Key and the custom headers with their values, for the main process alone, which keeps them
        encrypted and hands them back at the next start (settings.load)."""
        with self.lock:
            return {"api_key": self.config["api_key"], "headers": [dict(header) for header in self.config["headers"]]}

    def prompt_chars(self):
        """How many characters a request may carry within the model's context window (context_window): the window less
        the room kept for the answer (max_tokens, at most half the window; else ANSWER_ROOM, at most a quarter), at
        CHARS_PER_TOKEN. The defaults give 56,000, what a question sent before the window was a setting."""
        with self.lock:
            window, cap = self.config["context_window"], self.config["max_tokens"]
        room = min(cap, window // 2) if cap else min(ANSWER_ROOM, window // 4)
        return (window - room) * CHARS_PER_TOKEN

    def complete(self, messages, *, temperature=None, timeout=None, stream=None, max_tokens=None, cancel=None):
        """The LLM's answer as a Reply, without a reasoning model's thinking (answer), so no caller stores or shows it;
        its `truncated` says whether the LLM stopped at its output limit.

        `timeout` shortens the settings' time limit, which bounds the whole call: a failure that may pass (RETRY_STATUS,
        a connection that failed, dropped or timed out) is sent again only while the limit leaves room (_attempts).
        `stream`, a callable, has the answer streamed and is called with all of the answer so far each time it grows
        (with '' when a failed attempt starts over); a server that does not stream gives it the whole answer at once.
        `max_tokens` overrides the settings' cap. The request waits for a slot (Slots) at its PRIORITY, at most
        `timeout` seconds when given. `cancel`, or the CANCEL of the context, stops it: Cancelled is raised with what
        had come."""
        with self.lock:
            config = self.config.copy()
            target = profile(config)
            model = (target["base_url"], target["model_name"])
            plain_only, unstreamed = model in self.plain_models, model in self.unstreamed
        if problem := missing(config, target):
            raise UserError(problem)
        limit = config["timeout"] if timeout is None else min(config["timeout"], timeout)
        cancel = CANCEL.get() if cancel is None else cancel
        streamed = stream is not None and not unstreamed
        body = request_body(
            target,
            messages,
            config["temperature"] if temperature is None else temperature,
            config["max_tokens"] if max_tokens is None else max_tokens,
            streamed,
        )
        plain = plain_body(target, body)
        with self.slots.hold(PRIORITY.get(), timeout=timeout, cancel=cancel):
            deadline = time.monotonic() + limit
            try:
                sent = plain if plain_only else body
                outcome = self._attempts(target, config, sent, deadline, stream if streamed else None, cancel)
                if outcome.status == 400 and sent is not plain and plain != body:
                    # OpenAI's reasoning models (o-series, GPT-5) take only their own temperature, and their cap as
                    # max_completion_tokens, and refuse the request otherwise.
                    sent = plain
                    outcome = self._attempts(target, config, sent, deadline, stream if streamed else None, cancel)
                    if outcome.status == 200:
                        with self.lock:
                            self.plain_models.add(model)
                if streamed and outcome.status in (400, 422):
                    # A gateway that cannot stream refuses the request: the answer is asked for whole, from now on too.
                    whole_body = {key: value for key, value in sent.items() if key != "stream"}
                    if target["format"] == "openai":
                        whole_body["stream"] = False
                    outcome = self._attempts(target, config, whole_body, deadline, None, cancel)
                    if outcome.status == 200:
                        with self.lock:
                            self.unstreamed.add(model)
                status = outcome.status
                if status in (401, 403):
                    raise LLMRequestError(
                        f"LLM API가 인증을 거부했습니다(HTTP {status}). API Key와 헤더를 확인해 주세요.", status
                    )
                if status == 429:
                    raise LLMRequestError(
                        "LLM API가 요청이 너무 많다고 응답했습니다(HTTP 429). 잠시 후 다시 시도해 주세요.",
                        status,
                        transient=True,
                    )
                if status in RETRY_STATUS:
                    raise LLMRequestError(
                        f"LLM API가 HTTP {status}를 반환했습니다. 서버가 바쁘거나 일시적인 오류일 수 있으니 "
                        "잠시 후 다시 시도해 주세요.",
                        status,
                        transient=True,
                    )
                if status != 200:
                    raise LLMRequestError(
                        f"LLM API가 HTTP {status}를 반환했습니다. 주소와 설정을 확인해 주세요.", status
                    )
            except requests.exceptions.SSLError:
                raise LLMRequestError(
                    "LLM 서버의 인증서를 확인할 수 없습니다. 사설·자체 서명 인증서를 쓰는 서버라면 고급 설정에서 CA 인증서 PEM 경로를 지정해 주세요."
                ) from None
            except requests.exceptions.Timeout:
                raise LLMRequestError(
                    "LLM 응답 제한 시간을 초과했습니다. 잠시 후 다시 시도해 주세요.", transient=True
                ) from None
            except UnicodeEncodeError:
                raise LLMRequestError(
                    "요청에 HTTP로 보낼 수 없는 글자가 있습니다. API 엔드포인트, API Key와 헤더를 확인해 주세요."
                ) from None
            except StreamFailure:
                raise LLMRequestError(
                    "LLM 서버가 답을 보내는 중에 오류를 알렸습니다. 잠시 후 다시 시도해 주세요.", transient=True
                ) from None
            except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError):
                raise LLMRequestError(UNREACHABLE, transient=True) from None
            except requests.exceptions.RequestException:
                # Every requests error is an OSError too: one of the request itself (an address it cannot use) stays.
                raise LLMRequestError(UNREACHABLE) from None
            except OSError:
                raise LLMRequestError(UNREACHABLE, transient=True) from None
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                raise UserError(
                    f"LLM 응답 형식이 올바르지 않습니다. API 형식 설정과 응답의 {ANSWER[target['format']]}를 확인해 주세요."
                ) from None
        if stream is not None and not outcome.streamed:
            stream(outcome.text)
        truncated = isinstance(outcome.finish, str) and outcome.finish in TRUNCATION[target["format"]]
        if truncated:
            LOG.info("LLM answer stopped at its output limit · host %s", hostname(target["base_url"]))
        text = answer(outcome.text)
        if not text:
            if truncated:
                raise LLMTruncatedError(TRUNCATED_THINKING)
            # All thinking: a reply cut short before the answer, or a model that only thought aloud.
            raise LLMFormatError("LLM이 답을 쓰기 전에 응답이 끝났습니다(추론만 있음). 다시 시도해 주세요.")
        return reply(text, truncated)

    def _attempts(self, target, config, body, deadline, stream, cancel):
        """The Outcome of `body`, sent again after a failure that may pass by itself (RETRY_STATUS; a connection that
        failed, dropped or timed out; an error the server sent while streaming): at most ATTEMPTS requests, the waits
        between them as backoff() or a Retry-After has them, MAX_WAIT seconds in all, and only while the call's time
        limit (`deadline`) leaves MIN_ATTEMPT seconds. A status that is no such failure, or one still there at the end,
        is returned; the error of a last request that failed is raised."""
        host, waited = hostname(target["base_url"]), 0.0

        def again(attempt, wait):
            return (
                attempt < ATTEMPTS and waited + wait <= MAX_WAIT and time.monotonic() + wait + MIN_ATTEMPT <= deadline
            )

        for attempt in range(1, ATTEMPTS + 1):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if stream is not None and attempt > 1:
                stream("")  # The answer starts over.
            started = time.monotonic()
            try:
                outcome = self._send(target, config, body, max(1.0, deadline - started), stream, cancel)
            except requests.exceptions.SSLError:
                note_failure(host, "TLS error", started, attempt, None)
                raise  # A certificate that does not verify stays so.
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                StreamFailure,
            ) as error:
                failure = (
                    "time-out"
                    if isinstance(error, requests.exceptions.Timeout)
                    else "server error while streaming"
                    if isinstance(error, StreamFailure)
                    else "connection failed"
                )
                wait = backoff(attempt)
                if not again(attempt, wait):
                    note_failure(host, failure, started, attempt, None)
                    raise
                note_failure(host, failure, started, attempt, wait)
            else:
                if outcome.status == 200:
                    return outcome
                wait = backoff(attempt) if outcome.wait is None else outcome.wait
                if outcome.status not in RETRY_STATUS or not again(attempt, wait):
                    note_failure(host, f"HTTP {outcome.status}", started, attempt, None)
                    return outcome
                note_failure(host, f"HTTP {outcome.status}", started, attempt, wait)
            self.pause(wait, cancel)
            waited += wait
        raise AssertionError("unreachable")  # The last attempt always returns or raises.

    def _send(self, target, config, body, seconds, stream, cancel):
        """One request, with `seconds` to answer: its Outcome. `stream` has the answer read as the server sends it."""
        with self.lock:
            if not self.sending and time.monotonic() - self.used > IDLE_SECONDS:
                self.session.close()  # Its connections; the session opens new ones as it needs them.
            self.sending += 1
        try:
            # Never follow redirects carrying the authentication headers.
            response = self.session.post(
                target["base_url"],
                headers=request_headers(target),
                json=body,
                timeout=(min(CONNECT_SECONDS, seconds), seconds),
                allow_redirects=False,
                verify=config["ca_bundle"] or True,
                stream=stream is not None,
            )
            with response:
                if response.status_code != 200:
                    return Outcome(response.status_code, wait=retry_after(getattr(response, "headers", None)))
                if stream is None:
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                    return Outcome(200, *reply_text(target["format"], data))
                with cancel.reading(response) if cancel is not None else contextlib.nullcontext():
                    return Outcome(200, *read_stream(response, target["format"], stream, cancel), streamed=True)
        finally:
            with self.lock:
                self.sending -= 1
                self.used = time.monotonic()

    @staticmethod
    def pause(seconds, cancel=None):
        """The wait between two attempts; `cancel` ends it, and the request."""
        if cancel is None:
            time.sleep(seconds)
        elif cancel.wait(seconds):
            raise Cancelled()

    def larger_cap(self):
        """The cap a JSON answer cut short is asked again with: twice the one its request named, RETRY_TOKENS when it
        named none, up to MAX_RETRY_TOKENS and half the context window; None when that is no larger."""
        with self.lock:
            config = self.config.copy()
        sent = config["max_tokens"] or (ANTHROPIC_MAX_TOKENS if config["api_format"] == "anthropic" else 0)
        larger = min(sent * 2 if sent else RETRY_TOKENS, MAX_RETRY_TOKENS, config["context_window"] // 2)
        return larger if larger > sent else None

    def structured(self, system, content, *, timeout=None):
        """The JSON object the LLM answers with. A JSON answer cut short at the output limit cannot be read (or would
        read as an object inside it), so it is asked once more with room for an answer twice as long (larger_cap);
        cut short again, or refused for asking that much, it raises LLMTruncatedError. Callers with a fallback of their
        own for a format error use it then (PageIndex, the AI search); the others tell the user to raise max_tokens."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        try:
            output = self.complete(messages, temperature=0.1, timeout=timeout)
            truncated = getattr(output, "truncated", False)
        except LLMTruncatedError:
            output, truncated = None, True
        if truncated:
            larger = self.larger_cap()
            if larger is None:
                raise LLMTruncatedError(TRUNCATED_JSON)
            try:
                output = self.complete(messages, temperature=0.1, timeout=timeout, max_tokens=larger)
            except LLMTruncatedError:
                raise LLMTruncatedError(TRUNCATED_JSON) from None
            except LLMRequestError as error:
                if error.status == 400:  # More than the model writes at once.
                    raise LLMTruncatedError(TRUNCATED_JSON) from None
                raise
            if getattr(output, "truncated", False):
                raise LLMTruncatedError(TRUNCATED_JSON)
        return json_object(output)
