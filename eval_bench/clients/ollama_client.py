from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str            # final response text (may include think tags)
    ttft: float          # Time To First Token (seconds)
    total_time: float    # total response time (seconds)
    token_count: int     # response token count (eval_count)
    raw: str             # raw response (for debugging)


def call(
    prompt: str,
    model: str = "qwen3:14b",
    ollama_url: str = "http://localhost:11434",
    timeout: int = 600,
    system: str = "You are an expert programmer. Provide only the requested code.",
    think: bool = True,
) -> LLMResponse:
    """Call Ollama /api/chat with streaming. Returns LLMResponse.

    Args:
        think: Enable qwen3 thinking mode. Set False for faster responses.
    """
    url = f"{ollama_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0.1},
        "think": think,
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})

    chunks: list[str] = []
    ttft: float = 0.0
    token_count: int = 0
    raw_lines: list[str] = []

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                line = line.decode("utf-8").strip()
                if not line:
                    continue
                raw_lines.append(line)
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                content = obj.get("message", {}).get("content", "")
                if content:
                    if not ttft:
                        ttft = time.time() - start
                    chunks.append(content)

                if obj.get("done"):
                    token_count = obj.get("eval_count", 0)
                    break

    except urllib.error.URLError as e:
        raise ConnectionError(f"Cannot reach Ollama at {ollama_url}: {e}")

    total_time = time.time() - start
    return LLMResponse(
        text="".join(chunks),
        ttft=ttft,
        total_time=total_time,
        token_count=token_count,
        raw="\n".join(raw_lines),
    )
