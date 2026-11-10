"""Lossless tool messages between the bundled Hermes worker and the LLM connection the user set up.

The worker asks the backend for each completion (runtime/hermes/worker.py's ParentTransport); this module sends it with
the app's own connection, whose key, custom headers and CA never enter the worker. A run snapshots the settings once,
so an edit applies from the next run. As every LLM request of the app, TLS is verified and a redirect is not followed
(it would carry the key). No other provider or model is ever tried in its place.
"""

import copy
import json
import time
import uuid

import requests

from . import llm

MAX_RESPONSE = 2_000_000
MAX_CALLS = 4  # Model calls of one plan.
RUN_SECONDS = 120  # A plan's time, or the connection's own limit when shorter.
STOPPED = "채팅 요청을 중지했습니다."


def anthropic_messages(messages):
    """The OpenAI-form messages Hermes writes, as the Messages API takes them: the system prompt apart, tool calls as
    tool_use blocks and tool results as tool_result blocks of a user turn."""
    system, converted = [], []
    for message in messages:
        role, content = message["role"], message.get("content") or ""
        if role == "system":
            system.append(content)
            continue
        if role == "tool":
            role, blocks = "user", [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": content}]
        else:
            blocks = [{"type": "text", "text": content}] if content else []
            for call in message.get("tool_calls", []):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": json.loads(call["function"]["arguments"] or "{}"),
                    }
                )
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
        messages, tools = request.get("messages", []), request.get("tools", [])
        body = llm.request_body(self.target, messages, self.config["temperature"], stream=True)
        if self.target["format"] == "anthropic":
            body["system"], body["messages"] = anthropic_messages(messages)
            if not body["system"]:
                body.pop("system")
            if tools:
                body["tools"] = [
                    {
                        "name": tool["function"]["name"],
                        "description": tool["function"].get("description", ""),
                        "input_schema": tool["function"]["parameters"],
                    }
                    for tool in tools
                ]
        elif tools:
            body["tools"] = tools
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
