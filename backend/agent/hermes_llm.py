"""Lossless tool messages between the bundled Hermes worker and the LLM connection the user set up.

The worker asks the backend for each completion (runtime/hermes/worker.py's ParentTransport); this module sends it with
the app's own connection, whose key, custom headers and CA never enter the worker. A run snapshots the settings once,
so an edit applies from the next run. As every LLM request of the app, TLS is verified and a redirect is not followed
(it would carry the key). No other provider or model is ever tried in its place.
"""

import copy
import json
import re
import time
import uuid

import requests

from . import llm

MAX_RESPONSE = 2_000_000
# One model request of a turn (SelectedLLM.renew sets both anew before each): the times it is sent, counting the one
# sent again without a temperature, and its time, or the connection's own limit when that is shorter.
MAX_CALLS = 4
RUN_SECONDS = 120
STOPPED = "채팅 요청을 중지했습니다."
# A picture written into a message, as the OpenAI form carries one: data:image/png;base64,....
DATA_IMAGE = re.compile(r"data:(image/[a-z0-9.+-]+);base64,([a-z0-9+/=\s]+)", re.I)


def anthropic_blocks(content):
    """A message's content as Messages API blocks. Hermes writes the OpenAI form: a string, or a list of parts when a
    picture comes with the words (its vision tool, a screenshot). Text parts become text blocks and image_url parts
    image blocks; a part of any other kind is left out."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "input_text") and isinstance(part.get("text"), str) and part["text"]:
            blocks.append({"type": "text", "text": part["text"]})
        elif kind == "image_url":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(url, str):
                continue
            if found := DATA_IMAGE.fullmatch(url):
                source = {"type": "base64", "media_type": found[1].lower(), "data": "".join(found[2].split())}
                blocks.append({"type": "image", "source": source})
            elif url.startswith(("http://", "https://")):
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
    return blocks


def arguments(call):
    """A tool call's arguments as the object the Messages API takes; {} for arguments that do not read as one."""
    try:
        value = json.loads(call["function"].get("arguments") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def anthropic_messages(messages):
    """The OpenAI-form messages Hermes writes, as the Messages API takes them: the system prompt apart, tool calls as
    tool_use blocks and tool results as tool_result blocks of a user turn. A message with nothing to send (an assistant
    turn without words or calls) is left out, as the API refuses an empty one."""
    system, converted = [], []
    for message in messages:
        role, content = message["role"], message.get("content")
        if role == "system":
            system.extend(block["text"] for block in anthropic_blocks(content) if block["type"] == "text")
            continue
        if role == "tool":
            # A result in words stays the string it is; one with a picture goes as its blocks.
            result = content if isinstance(content, str) else anthropic_blocks(content)
            role, blocks = (
                "user",
                [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": result or ""}],
            )
        else:
            blocks = anthropic_blocks(content)
            for call in message.get("tool_calls") or []:
                blocks.append(
                    {"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": arguments(call)}
                )
        if not blocks:
            continue
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    return "\n\n".join(system), converted


def completion(message, model, usage=None, reason=None):
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", **message},
                "finish_reason": reason or ("tool_calls" if message.get("tool_calls") else "stop"),
            }
        ],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def anthropic_reply(data):
    content, calls = [], []
    for block in data["content"]:
        if block.get("type") == "text":
            content.append(block["text"])
        elif block.get("type") == "tool_use":
            calls.append(
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {"name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)},
                }
            )
    usage = data.get("usage", {})
    tokens = {"prompt_tokens": usage.get("input_tokens", 0), "completion_tokens": usage.get("output_tokens", 0)}
    tokens["total_tokens"] = sum(tokens.values())
    return {"content": "".join(content), **({"tool_calls": calls} if calls else {})}, tokens


def events(response, cancelled, end):
    """The data of each server-sent event, within the response limit and the run's time."""
    size, parts = 0, []
    for line in response.iter_lines(chunk_size=1):
        if cancelled.is_set():
            raise llm.UserError(STOPPED)
        if time.monotonic() > end:
            raise requests.exceptions.Timeout()
        size += len(line)
        if size > MAX_RESPONSE:
            raise llm.LLMFormatError("Hermes 모델 응답이 전달 한도를 넘었습니다.")
        if not line and parts:
            yield b"\n".join(parts)
            parts = []
        elif line.startswith(b"data:"):
            parts.append(line[5:].strip())
    if parts:
        yield b"\n".join(parts)


class Watch:
    """The response being read, which a stop closes so the read ends."""

    def __init__(self, response):
        self.response = response

    def cut(self):
        try:
            self.response.close()
        except Exception:  # Closing from another thread may race the reader; the read ends either way.
            pass


class SelectedLLM:
    def __init__(self, connection, cancelled, on_text=lambda value: None, on_watch=lambda value: None):
        with connection.lock:
            self.config = copy.deepcopy(connection.config)
            plain = set(connection.plain_models)
        self.target = llm.profile(self.config)
        if problem := llm.missing(self.config, self.target):
            raise llm.UserError(problem)
        self.connection = connection
        self.plain = (self.target["base_url"], self.target["model_name"]) in plain
        self.cancelled, self.on_text, self.on_watch = cancelled, on_text, on_watch
        self.renew()

    def renew(self):
        """Fresh per-call budget: one chat turn makes many model calls (one per agent iteration), so the time and
        call limits are per request, reset before each, not shared across the whole turn as a single plan was."""
        self.end = time.monotonic() + min(RUN_SECONDS, float(self.config["timeout"]))
        self.calls = 0

    def read(self, response, streaming):
        if not streaming:
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise llm.LLMFormatError("Hermes 모델 응답이 전달 한도를 넘었습니다.")
                chunks.append(chunk)
            return json.loads(b"".join(chunks))
        content, calls, usage, reason, blocks, done = [], {}, {}, None, {}, False
        for payload in events(response, self.cancelled, self.end):
            if payload == b"[DONE]":
                done = True
                break
            try:
                data = json.loads(payload)
            except ValueError:
                continue  # A heartbeat or another event that is not JSON.
            if not isinstance(data, dict):
                continue
            if data.get("error") or data.get("type") == "error":
                raise llm.UserError("Hermes 모델 스트리밍 요청이 실패했습니다.")
            if self.target["format"] == "anthropic":
                kind = data.get("type")
                if kind == "message_start":
                    usage.update(data.get("message", {}).get("usage", {}))
                elif kind == "content_block_start":
                    blocks[data["index"]] = {**data["content_block"], "_json": ""}
                elif kind == "content_block_delta":
                    block, delta = blocks[data["index"]], data["delta"]
                    if delta["type"] == "text_delta":
                        block["text"] = block.get("text", "") + delta["text"]
                        self.on_text(delta["text"])
                    elif delta["type"] == "input_json_delta":
                        block["_json"] += delta["partial_json"]
                elif kind == "message_delta":
                    usage.update(data.get("usage", {}))
                elif kind == "message_stop":
                    done = True
                    break
            else:
                usage = data.get("usage") or usage
                for choice in data.get("choices", []):
                    if choice.get("index", 0) != 0:
                        continue
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        content.append(delta["content"])
                        self.on_text(delta["content"])
                    for call in delta.get("tool_calls", []):
                        item = calls.setdefault(
                            call.get("index", 0),
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if call.get("id"):
                            item["id"] = call["id"]
                        for field in ("name", "arguments"):
                            item["function"][field] += call.get("function", {}).get(field) or ""
                    reason = choice.get("finish_reason") or reason
        if not done and not (self.target["format"] == "openai" and reason):
            raise llm.LLMFormatError("Hermes 모델 스트리밍이 완료 표시 전에 끝났습니다.")
        if self.target["format"] == "anthropic":
            for block in blocks.values():
                raw = block.pop("_json")
                if raw:
                    block["input"] = json.loads(raw)
            return {"content": list(blocks.values()), "usage": usage}
        message = {"content": "".join(content)}
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        return completion(message, self.target["model_name"], usage, reason)

    def send(self, body):
        seconds = self.end - time.monotonic()
        if seconds <= 0:
            raise requests.exceptions.Timeout()
        response = requests.post(
            self.target["base_url"],
            headers=llm.request_headers(self.target),
            json=body,
            timeout=(min(llm.CONNECT_SECONDS, seconds), seconds),
            allow_redirects=False,  # Never follow a redirect with the authentication headers.
            verify=self.config["ca_bundle"] or True,
            stream=True,
        )
        self.on_watch(Watch(response))
        with response:
            if response.status_code != 200:
                return response.status_code, None
            streaming = "text/event-stream" in response.headers.get("Content-Type", "")
            return 200, self.read(response, streaming)

    def complete(self, request):
        if self.cancelled.is_set():
            raise llm.UserError(STOPPED)
        messages, tools = request.get("messages") or [], request.get("tools") or []
        try:
            if self.target["format"] == "anthropic":
                # Converted before the request is written: Hermes' messages may hold lists of parts, which the
                # Messages API takes as blocks of its own form.
                system, converted = anthropic_messages(messages)
                body = llm.request_body(self.target, [], self.config["temperature"], stream=True)
                body["messages"] = converted
                if system:
                    body["system"] = system
                if tools:
                    body["tools"] = [
                        {
                            "name": tool["function"]["name"],
                            "description": tool["function"].get("description", ""),
                            "input_schema": tool["function"]["parameters"],
                        }
                        for tool in tools
                    ]
            else:
                body = llm.request_body(self.target, messages, self.config["temperature"], stream=True)
                if tools:
                    body["tools"] = tools
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise llm.LLMFormatError("Hermes의 모델 요청 형식이 올바르지 않습니다.") from None
        if self.plain:
            body = llm.plain_body(self.target, body)
        try:
            while True:
                self.calls += 1
                if self.calls > MAX_CALLS:
                    raise llm.UserError("Hermes 작업 계획의 모델 호출 한도를 넘었습니다.")
                status, data = self.send(body)
                if self.cancelled.is_set():
                    raise llm.UserError(STOPPED)
                # A reasoning model that refuses a temperature is asked again in the form it takes, on the same
                # endpoint and model, within the same calls and time (as llm.LLM does).
                if status == 400 and "temperature" in body:
                    body = llm.plain_body(self.target, body)
                    self.plain = True
                    continue
                break
            if status in (401, 403):
                raise llm.UserError(
                    f"Hermes 모델 인증에 실패했습니다(HTTP {status}). 관리 → LLM 연결의 API Key와 헤더를 확인해 주세요."
                )
            if status in (400, 404, 422):
                raise llm.UserError(
                    f"API가 도구 호출 형식을 지원하지 않거나 요청 설정이 맞지 않습니다(HTTP {status}). "
                    "선택한 API와 모델이 도구 호출(tool calling)을 지원하는지 확인해 주세요."
                )
            if status != 200:
                raise llm.UserError(f"Hermes 모델 요청이 실패했습니다(HTTP {status}).")
            if self.plain:
                with self.connection.lock:
                    self.connection.plain_models.add((self.target["base_url"], self.target["model_name"]))
            if self.target["format"] == "anthropic":
                message, usage = anthropic_reply(data)
                return completion(message, self.target["model_name"], usage)
            choice = data["choices"][0]
            return completion(
                choice["message"], self.target["model_name"], data.get("usage"), choice.get("finish_reason")
            )
        except requests.exceptions.SSLError:
            raise llm.UserError(
                "LLM 서버의 TLS 인증서를 확인하지 못했습니다. 인증서 경로(CA) 설정을 확인해 주세요."
            ) from None
        except requests.exceptions.Timeout:
            raise llm.UserError(
                STOPPED if self.cancelled.is_set() else "Hermes 모델 응답 제한 시간을 초과했습니다."
            ) from None
        except (requests.exceptions.RequestException, OSError):
            if self.cancelled.is_set():
                raise llm.UserError(STOPPED) from None
            raise llm.UserError(llm.UNREACHABLE) from None
        except (ValueError, KeyError, TypeError, IndexError):
            raise llm.LLMFormatError(
                "Hermes 모델 응답 형식이 올바르지 않습니다. API 형식 설정을 확인해 주세요."
            ) from None
