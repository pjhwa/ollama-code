from __future__ import annotations
import json
import time
import urllib.request
import urllib.error

from clients.ollama_client import LLMResponse  # reuse dataclass


def call(
    prompt: str,
    model: str = "qwen3:14b",
    bridge_url: str = "http://localhost:9099",
    timeout: int = 180,
    system: str = "You are an expert programmer. Provide only the requested code.",
) -> LLMResponse:
    """Call bridge proxy /v1/messages (Anthropic Messages API format) with streaming."""
    url = f"{bridge_url.rstrip('/')}/v1/messages"
    payload = {
        "model": model,
        "max_tokens": 4096,
        "stream": True,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": "local-ollama-bridge",
            "anthropic-version": "2023-06-01",
        },
    )

    chunks: list[str] = []
    ttft: float = 0.0
    token_count: int = 0
    raw_lines: list[str] = []

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                line = line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                raw_lines.append(data_str)
                if data_str == "[DONE]":
                    break
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = obj.get("type", "")
                if event_type == "content_block_delta":
                    delta = obj.get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        if not ttft:
                            ttft = time.time() - start
                        chunks.append(text)
                elif event_type == "message_delta":
                    usage = obj.get("usage", {})
                    token_count = usage.get("output_tokens", token_count)
                elif event_type == "message_stop":
                    break

    except urllib.error.URLError as e:
        raise ConnectionError(f"Cannot reach bridge at {bridge_url}: {e}")

    total_time = time.time() - start
    return LLMResponse(
        text="".join(chunks),
        ttft=ttft,
        total_time=total_time,
        token_count=token_count,
        raw="\n".join(raw_lines),
    )
