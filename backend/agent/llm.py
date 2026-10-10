import json
import math
import os
import re
import sys
import threading
import uuid
from urllib.parse import urlparse

import requests


class UserError(Exception):
    pass


class BusyError(UserError):
    """A job could not start because one of its kind is running (an import, a login window, a write to an original or
    an analysis): the automatic routines (routines.py) wait for it to end and start theirs then."""


class LLMFormatError(UserError):
    """The LLM answered, but not in the form asked for; a caller may fall back on something of its own."""


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
            value = decoded(repaired(text[match.start():]))
        if value is not None:
            return value
    raise LLMFormatError("LLM이 유효한 JSON을 반환하지 않았습니다. 다시 분석해 주세요.")


FORMATS = ("openai", "anthropic")
ANTHROPIC_VERSION = "2023-06-01"
# The Messages API needs a cap on the answer: far above an analysis or an answer, and within every current model's.
ANTHROPIC_MAX_TOKENS = 16000
# Where each format puts the answer, for the message when it is missing.
ANSWER = {"openai": "choices[0].message.content", "anthropic": "content의 text"}

# Connection settings for any supported LLM endpoint.
DEFAULTS = {"provider": "general", "api_format": "openai", "base_url": "", "api_key": "", "model_name": "",
            "headers": [], "temperature": 0.5, "timeout": 300.0, "ca_bundle": ""}
SECRETS = ("api_key",)
TEXT = ("provider", "api_format", "base_url", "api_key", "model_name", "ca_bundle")
# The environment variable of each setting, as .env.example names them.
ENVIRONMENT = {"provider": "PROVIDER", "api_format": "API_FORMAT", "base_url": "BASE_URL", "api_key": "API_KEY",
               "model_name": "MODEL_NAME", "headers": "LLM_HEADERS",
               "temperature": "TEMPERATURE", "timeout": "LLM_TIMEOUT", "ca_bundle": "CA_BUNDLE"}
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}")
# Printable ASCII: what any HTTP header carries as it is, so a value never fails on its way out.
HEADER_TEXT = re.compile(r"[\x20-\x7e]{0,8192}")
# A token copied together with its scheme, as the Authorization header shows it.
BEARER = re.compile(r"\Abearer\s+", re.I)
# The HTTP client writes these from the request itself.
RESERVED = {"host", "content-length", "transfer-encoding", "connection"}
MAX_HEADERS = 30
MOVED_HEADERS = ("API 엔드포인트를 다른 서버로 바꿀 때는 custom header 값을 모두 함께 보내 주세요. 저장된 헤더 값은 새 서버로 "
                 "보내지 않습니다.")


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


def profile(config):
    """The endpoint a request goes to, as the settings name it."""
    return {"format": config["api_format"], "base_url": config["base_url"], "api_key": config["api_key"],
            "model_name": config["model_name"], "headers": config["headers"]}


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


def request_body(target, messages, temperature):
    if target["format"] == "anthropic":
        # The system prompt is a field of its own, and current Claude models refuse a temperature.
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        body = {"model": target["model_name"], "max_tokens": ANTHROPIC_MAX_TOKENS,
                "messages": [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]}
        if system:
            body["system"] = system
        return body
    return {"model": target["model_name"], "messages": messages, "temperature": temperature, "stream": False}


def reply_text(api_format, data):
    if not isinstance(data, dict):
        raise ValueError()
    if api_format == "anthropic":
        if data.get("stop_reason") == "refusal":
            raise UserError("LLM이 이 요청에는 답하지 않았습니다. 내용을 바꿔 다시 시도해 주세요.")
        # A model that thinks first answers with a thinking block before the text.
        text = "".join(block["text"] for block in data["content"] if block.get("type") == "text")
    else:
        text = data["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError()
    return text


def send(target, body, config):
    """The status and, for a 200, the JSON answer (None when it is not JSON)."""
    # Never follow redirects carrying the authentication headers.
    with requests.post(target["base_url"], headers=request_headers(target), json=body,
                       timeout=(10, config["timeout"]), allow_redirects=False,
                       verify=config["ca_bundle"] or True) as response:
        if response.status_code != 200:
            return response.status_code, None
        try:
            return 200, response.json()
        except ValueError:
            return 200, None


class LLM:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = {**DEFAULTS, "headers": []}
        # Address and model of the LLMs that refused a temperature (OpenAI's reasoning models): asked without one.
        self.fixed_temperature = set()
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
            self.config = config
            return self.public()

    def public(self):
        """The settings as the screens and AI tools read them: without the API Key and the custom headers' values,
        which may be secrets of their own (a gateway's token); each header says whether it has a value and whether that
        value takes the key ({api_key})."""
        with self.lock:
            config, target = self.config, profile(self.config)
            return {**{k: v for k, v in config.items() if k not in SECRETS},
                    "headers": [{"name": header["name"], "has_value": bool(header["value"]),
                                 "uses_api_key": "{api_key}" in header["value"]} for header in config["headers"]],
                    "has_api_key": bool(config["api_key"]),
                    "configured": missing(config, target) is None, "active_model": target["model_name"]}

    def secrets(self):
        """The API Key and the custom headers with their values, for the main process alone, which keeps them
        encrypted and hands them back at the next start (settings.load)."""
        with self.lock:
            return {"api_key": self.config["api_key"], "headers": [dict(header) for header in self.config["headers"]]}

    def complete(self, messages, *, temperature=None, timeout=None):
        """The LLM's answer, without a reasoning model's thinking (answer), so no caller stores or shows it. `timeout`
        shortens the settings' time limit for a request that must not wait that long."""
        with self.lock:
            config = self.config.copy()
            target = profile(config)
            fixed = (target["base_url"], target["model_name"]) in self.fixed_temperature
        if timeout is not None:
            config["timeout"] = min(config["timeout"], timeout)
        if problem := missing(config, target):
            raise UserError(problem)
        body = request_body(target, messages, config["temperature"] if temperature is None else temperature)
        plain = {key: value for key, value in body.items() if key != "temperature"}
        try:
            status, data = send(target, plain if fixed else body, config)
            if status == 400 and not fixed and plain != body:
                # OpenAI's reasoning models (o-series, GPT-5) take only their own temperature and refuse the request.
                status, data = send(target, plain, config)
                if status == 200:
                    with self.lock:
                        self.fixed_temperature.add((target["base_url"], target["model_name"]))
            if status in (401, 403):
                raise UserError(f"LLM API가 인증을 거부했습니다(HTTP {status}). API Key와 헤더를 확인해 주세요.")
            if status != 200:
                raise UserError(f"LLM API가 HTTP {status}를 반환했습니다. 주소와 설정을 확인해 주세요.")
            reply = reply_text(target["format"], data)
        except requests.exceptions.SSLError:
            raise UserError("LLM 서버의 인증서를 확인할 수 없습니다. 사설·자체 서명 인증서를 쓰는 서버라면 고급 설정에서 CA 인증서 PEM 경로를 지정해 주세요.") from None
        except requests.exceptions.Timeout:
            raise UserError("LLM 응답 제한 시간을 초과했습니다. 잠시 후 다시 시도해 주세요.") from None
        except UnicodeEncodeError:
            raise UserError("요청에 HTTP로 보낼 수 없는 글자가 있습니다. API 엔드포인트, API Key와 헤더를 확인해 주세요.") from None
        except (requests.exceptions.RequestException, OSError):
            raise UserError("LLM API에 연결할 수 없습니다. 네트워크·프록시, API 엔드포인트, 인증서 경로를 확인해 주세요.") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise UserError(f"LLM 응답 형식이 올바르지 않습니다. API 형식 설정과 응답의 {ANSWER[target['format']]}를 확인해 주세요.") from None
        text = answer(reply)
        if not text:
            # All thinking: a reply cut short before the answer, or a model that only thought aloud.
            raise LLMFormatError("LLM이 답을 쓰기 전에 응답이 끝났습니다(추론만 있음). 다시 시도해 주세요.")
        return text

    def structured(self, system, content, *, timeout=None):
        output = self.complete([{"role": "system", "content": system}, {"role": "user", "content": content}],
                               temperature=0.1, timeout=timeout)
        return json_object(output)
