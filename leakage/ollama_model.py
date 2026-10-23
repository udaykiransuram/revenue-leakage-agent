"""Small native Ollama adapter. Inference stays on the loopback interface."""
from __future__ import annotations
import os
import uuid
from urllib.parse import urlparse
import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

class OllamaModel:
    def __init__(self, model=None, base_url=None, timeout=None, client=None, context_length=None):
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3:8b")
        if self.model.endswith(":cloud"):
            raise ValueError("Local inference cannot use an Ollama cloud model")
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.username or parsed.password:
            raise ValueError("Local Ollama inference requires an HTTP loopback address")
        self.request_timeout = int(timeout if timeout is not None else os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))
        if not 5 <= self.request_timeout <= 1800:
            raise ValueError("Local inference timeout must be between 5 and 1800 seconds")
        self.client = client or httpx.Client(timeout=self.request_timeout, trust_env=False)
        self.context_length = int(context_length if context_length is not None else os.getenv("OLLAMA_NUM_CTX", "32768"))
        if not 4096 <= self.context_length <= 262144:
            raise ValueError("Local context must be between 4096 and 262144 tokens")
        self.tools = []

    def bind_tools(self, tools, **kwargs):
        self.tools = [convert_to_openai_tool(t) for t in tools]
        return self

    def invoke(self, messages):
        outgoing = []
        for m in messages:
            if isinstance(m, SystemMessage):
                row = {"role": "system", "content": m.content}
            elif isinstance(m, HumanMessage):
                row = {"role": "user", "content": m.content}
            elif isinstance(m, ToolMessage):
                row = {"role": "tool", "content": m.content, "tool_name": m.name or ""}
            elif isinstance(m, AIMessage):
                row = {"role": "assistant", "content": m.content or ""}
                if m.tool_calls:
                    row["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["args"]}} for c in m.tool_calls]
            else:
                raise ValueError("Unsupported local model message type")
            outgoing.append(row)
        response = self.client.post(self.base_url+"/api/chat", json={"model": self.model, "messages": outgoing, "tools": self.tools, "stream": False, "think": False, "keep_alive": "15m", "options": {"temperature": 0, "num_ctx": self.context_length, "num_predict": 2048}})
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise ValueError("Ollama returned an inference error")
        message = payload.get("message") or {}
        calls = []
        for c in message.get("tool_calls", []):
            f = c.get("function", {})
            if not isinstance(f.get("arguments"), dict) or not isinstance(f.get("name"), str):
                raise ValueError("Local model returned invalid tool arguments")
            calls.append({"name": f["name"], "args": f["arguments"], "id": "ollama-"+uuid.uuid4().hex})
        content = message.get("content", "")
        if not isinstance(content, str) or (not content.strip() and not calls):
            raise ValueError("Local model returned no usable response")
        prompt_tokens, output_tokens = int(payload.get("prompt_eval_count", 0)), int(payload.get("eval_count", 0))
        return AIMessage(content=content, tool_calls=calls, usage_metadata={"input_tokens": prompt_tokens, "output_tokens": output_tokens, "total_tokens": prompt_tokens+output_tokens}, response_metadata={"provider": "ollama-local", "model": self.model, "duration_seconds": payload.get("total_duration", 0)/1e9})
