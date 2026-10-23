import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]


class LLMClient:
    def __init__(self, model=None, temperature=0.0,
                 enable_thinking=False, log_path="logs/llm_calls.jsonl",
                 thinking_budget=None, timeout=45):
        if thinking_budget is not None and (not enable_thinking or not 1 <= thinking_budget <= 32768):
            raise ValueError("thinking_budget 仅在思考模式下使用")
        self.thinking_budget = thinking_budget
        load_dotenv(ROOT / ".env")
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("没有找到 DASHSCOPE_API_KEY，请填写项目 .env")
        workspace_id = os.getenv("DASHSCOPE_WORKSPACE_ID")
        base_url = (
            f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
            if workspace_id
            else "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.model = model or os.getenv("LLM_MODEL", "qwen3.7-flash")
        self.temperature = temperature
        self.enable_thinking = enable_thinking
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
        self.log_path = ROOT / Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def chat(self, messages, response_format=None, max_tokens=None):
        """返回回答文本。"""
        options = {}
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        if response_format is not None:
            options["response_format"] = response_format
        return self._request("chat", messages, **options)["content"]

    def chat_json(self, messages, max_tokens=2048):
        """提示词需明确要求 JSON；返回解析后的 Python 对象。"""
        return json.loads(self.chat(messages, response_format={"type": "json_object"}, max_tokens=max_tokens))

    def chat_with_tools(self, messages, tools, tool_choice="auto"):
        """返回 content 和 tool_calls；只返回工具请求，不执行工具。"""
        return self._request("tool_call", messages, tools=tools, tool_choice=tool_choice, parallel_tool_calls=True)

    def chat_stream(self, messages, max_tokens=3072):
        """Yield model content deltas; log accumulated text, usage and interruption."""
        timestamp = datetime.now(timezone.utc).isoformat()
        started = perf_counter(); first = None; parts = []; usage = None; error = None; stream = None
        try:
            stream = self._client.chat.completions.create(
                model=self.model, messages=messages, temperature=self.temperature,
                extra_body={"enable_thinking": self.enable_thinking, **({"thinking_budget": self.thinking_budget} if self.thinking_budget is not None else {})}, max_tokens=max_tokens,
                stream=True, stream_options={"include_usage": True})
            for chunk in stream:
                if chunk.usage: usage = chunk.usage.model_dump(mode="json")
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    if first is None: first = perf_counter() - started
                    parts.append(delta)
                    yield delta
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            if stream is not None: stream.close()
            record = {"timestamp": timestamp, "request_type": "chat_stream", "model": self.model,
                      "temperature": self.temperature, "thinking": self.enable_thinking,
                      "messages": messages, "output": {"content": "".join(parts), "tool_calls": []},
                      "usage": usage, "latency_seconds": round(perf_counter()-started, 3),
                      "ttft_seconds": round(first,3) if first is not None else None, "error": error}
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False)+"\n")

    def request_stream(self, messages, on_delta, **options):
        """One actual streamed model call, including fragmented function arguments."""
        timestamp = datetime.now(timezone.utc).isoformat()
        started = perf_counter(); first = None; parts = []; calls = {}; usage = None
        output = None; error = None; stream = None; finish = None
        try:
            stream = self._client.chat.completions.create(
                model=self.model, messages=messages, temperature=self.temperature,
                extra_body={"enable_thinking": self.enable_thinking,
                            **({"thinking_budget": self.thinking_budget} if self.thinking_budget is not None else {})},
                stream=True, stream_options={"include_usage": True}, **options)
            for chunk in stream:
                if chunk.usage: usage = chunk.usage.model_dump(mode="json")
                if not chunk.choices: continue
                choice = chunk.choices[0]; delta = choice.delta
                if choice.finish_reason: finish = choice.finish_reason
                for fragment in delta.tool_calls or []:
                    call = calls.setdefault(fragment.index, {"id": "", "name": "", "arguments": ""})
                    if fragment.id: call["id"] += fragment.id
                    if fragment.function:
                        call["name"] += fragment.function.name or ""
                        call["arguments"] += fragment.function.arguments or ""
                if delta.content:
                    if first is None: first = perf_counter() - started
                    parts.append(delta.content)
                    on_delta(delta.content)
            parsed = []
            for _, call in sorted(calls.items()):
                try: call["arguments"] = json.loads(call["arguments"])
                except json.JSONDecodeError: pass
                parsed.append(call)
            output = {"content": "".join(parts), "tool_calls": parsed, "finish_reason": finish}
            return output
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            if stream is not None: stream.close()
            record = {"timestamp": timestamp, "request_type": "runtime_stream", "model": self.model,
                      "temperature": self.temperature, "thinking": self.enable_thinking,
                      "thinking_budget": self.thinking_budget, "messages": messages,
                      "tools": options.get("tools"), "output": output, "usage": usage,
                      "latency_seconds": round(perf_counter()-started, 3),
                      "ttft_seconds": round(first, 3) if first is not None else None, "error": error}
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False)+"\n")

    def _request(self, request_type, messages, **options):
        timestamp = datetime.now(timezone.utc).isoformat()
        started = perf_counter()
        response, output, error = None, None, None
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                extra_body={"enable_thinking": self.enable_thinking, **({"thinking_budget": self.thinking_budget} if self.thinking_budget is not None else {})},
                **options,
            )
            message = response.choices[0].message
            tool_calls = []
            for call in message.tool_calls or []:
                try:
                    arguments = json.loads(call.function.arguments)
                except json.JSONDecodeError:
                    arguments = call.function.arguments
                tool_calls.append({
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": arguments,
                })
            output = {"content": message.content, "tool_calls": tool_calls, "finish_reason": response.choices[0].finish_reason}
            return output
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record = {
                "timestamp": timestamp,
                "request_type": request_type,
                "model": self.model,
                "temperature": self.temperature,
                "thinking": self.enable_thinking,
                "thinking_budget": self.thinking_budget,
                "messages": messages,
                "tools": options.get("tools"),
                "parallel_tool_calls": options.get("parallel_tool_calls"),
                "response_format": options.get("response_format"),
                "tool_choice": options.get("tool_choice"),
                "max_tokens": options.get("max_tokens"),
                "output": output,
                "usage": response.usage.model_dump(mode="json") if response and response.usage else None,
                "response_model": response.model if response else None,
                "response_id": response.id if response else None,
                "latency_seconds": round(perf_counter() - started, 3),
                "error": error,
            }
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
