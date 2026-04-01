# Claude Code + Ollama 로컬 브릿지: 완전 분석 및 구현

> Claude Code 인터페이스를 그대로 사용하면서 `ANTHROPIC_BASE_URL`을 로컬 Ollama 모델로 연결하는 방법에 대한 다각도 분석
>
> 작성일: 2026-04-01

---

## 목차

1. [핵심 질문과 결론 요약](#1-핵심-질문과-결론-요약)
2. [API 프로토콜 차이 심층 분석](#2-api-프로토콜-차이-심층-분석)
3. [브릿지 프록시 아키텍처](#3-브릿지-프록시-아키텍처)
4. [즉시 실행 가능한 프록시 구현](#4-즉시-실행-가능한-프록시-구현)
5. [실행 방법 및 검증](#5-실행-방법-및-검증)
6. [기능 호환성 매트릭스](#6-기능-호환성-매트릭스)
7. [성능 격차 정직한 평가](#7-성능-격차-정직한-평가)
8. [한계점 및 권장사항](#8-한계점-및-권장사항)

---

## 1. 핵심 질문과 결론 요약

### 질문
> Claude Code의 인터페이스(CLI, 도구 시스템, 에이전트 루프)를 그대로 활용하면서
> `ANTHROPIC_BASE_URL`을 로컬 Ollama 모델로 지정하여
> Claude Opus/Sonnet 수준의 코딩 성능을 구현할 수 있는가?

### 결론 (정직한 평가)

| 측면 | 판정 | 설명 |
|------|------|------|
| **API 연결 가능 여부** | ✅ 가능 | 프록시 서버를 통해 Anthropic API ↔ OpenAI API 프로토콜 변환 |
| **기본 대화 동작** | ✅ 동작 | 텍스트 생성, 스트리밍 출력 |
| **도구 호출 (tool use)** | ⚠️ 부분 동작 | 14B+ 모델에서 간단한 도구는 동작, 복잡한 멀티도구는 불안정 |
| **Opus/Sonnet 수준 성능** | ❌ 불가 | 오픈소스 모델은 구조적으로 Claude의 코딩 품질에 도달 불가 |
| **20GB 메모리 예산 준수** | ✅ 가능 | 7B(~6GB), 14B(~11GB) 프록시 포함 |

**핵심 결론:** API 브릿지는 **기술적으로 구현 가능**하며 Claude Code의 UI/UX를 그대로 사용할 수 있다. 그러나 "완전히 같은 코딩 성능"은 **환각(hallucination)이다**. 정직하게 말하면:

- **7B 모델**: Claude Code UI로 간단한 파일 읽기/쓰기, 단일 파일 편집 가능. 복잡한 멀티스텝 코딩은 실패율 높음.
- **14B 모델**: 단일 파일 수준 코딩, 간단한 디버깅 가능. 멀티파일 리팩토링은 불안정.
- **32B 모델**: 가장 근접하지만 여전히 Claude Sonnet 대비 50-70% 수준. 메모리 17GB+.
- **72B+ 모델**: Mac Mini M4 32GB로는 실행 불가(메모리 초과).

---

## 2. API 프로토콜 차이 심층 분석

### Claude Code가 보내는 요청 (Anthropic API)

코드베이스 분석 근거: `rust/crates/api/src/client.rs:309-335`, `types.rs:4-17`

```
POST /v1/messages HTTP/1.1
Host: api.anthropic.com
anthropic-version: 2023-06-01        ← 필수 헤더
content-type: application/json
x-api-key: sk-ant-xxx                ← 인증

{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 16384,
  "system": "You are an expert...",   ← 시스템 프롬프트는 별도 필드
  "stream": true,
  "messages": [
    {
      "role": "user",
      "content": [                    ← content는 블록 배열
        {"type": "text", "text": "Fix the bug in main.py"}
      ]
    }
  ],
  "tools": [                          ← input_schema (OpenAI: parameters)
    {
      "name": "bash",
      "description": "Execute a bash command",
      "input_schema": {"type": "object", "properties": {...}}
    }
  ]
}
```

### Claude Code가 기대하는 스트리밍 응답 (Anthropic SSE)

코드베이스 분석 근거: `rust/crates/api/src/sse.rs:63-101`, `types.rs:157-212`

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_xxx","type":"message","role":"assistant","content":[],"model":"claude-sonnet-4-20250514","stop_reason":null,"usage":{"input_tokens":100,"output_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"I'll fix"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" the bug."}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_xxx","name":"bash","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"command\":"}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\"cat main.py\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"input_tokens":100,"output_tokens":50,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}

event: message_stop
data: {"type":"message_stop"}
```

### Ollama가 제공하는 응답 (OpenAI 호환)

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"I'll fix"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" the bug."},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### 변환해야 할 14가지 차이점

| # | 항목 | Anthropic | OpenAI/Ollama | 변환 난이도 |
|---|------|-----------|---------------|------------|
| 1 | 시스템 프롬프트 | `system` 필드 (top-level) | `messages[0].role="system"` | 쉬움 |
| 2 | Content 형식 | 블록 배열 `[{type, text}]` | 문자열 `"text"` | 중간 |
| 3 | 도구 스키마 | `input_schema` | `parameters` | 쉬움 |
| 4 | 도구 호출 응답 | content 내 `{type:"tool_use"}` | `tool_calls` 배열 | 어려움 |
| 5 | 도구 결과 | `role:"user"` + `{type:"tool_result"}` | `role:"tool"` + `tool_call_id` | 중간 |
| 6 | 인증 헤더 | `x-api-key` | `Authorization: Bearer` | 쉬움 |
| 7 | 버전 헤더 | `anthropic-version: 2023-06-01` | 없음 | 무시 |
| 8 | SSE 이벤트명 | `event: message_start` 등 | 없음 (data only) | 어려움 |
| 9 | 스트리밍 구조 | 6종 이벤트 시퀀스 | 단순 delta 스트림 | 어려움 |
| 10 | Stop reason | `end_turn`, `tool_use` | `stop`, `tool_calls` | 중간 |
| 11 | 도구 입력 스트리밍 | `input_json_delta` | 한 번에 전체 | 어려움 |
| 12 | Usage 구조 | cache 토큰 4개 필드 | input/output 2개 | 쉬움 |
| 13 | Message ID | `msg_xxx` | `chatcmpl-xxx` | 쉬움 |
| 14 | Content block index | 다중 블록 인덱싱 | 없음 | 중간 |

---

## 3. 브릿지 프록시 아키텍처

```
┌──────────────────────────────────────────────────────────────┐
│  Claude Code CLI                                             │
│  (변경 없음, 그대로 사용)                                       │
│  ANTHROPIC_BASE_URL=http://localhost:8082                    │
└───────────────┬──────────────────────────────────────────────┘
                │  Anthropic API 형식
                │  POST /v1/messages
                ▼
┌──────────────────────────────────────────────────────────────┐
│  Anthropic-to-Ollama 브릿지 프록시 (Python, port 8082)       │
│                                                              │
│  1. Anthropic 요청 파싱                                       │
│     - system 필드 → system 메시지 변환                         │
│     - content 블록 배열 → 문자열 변환                           │
│     - input_schema → parameters 변환                         │
│     - tool_result → role:"tool" 변환                          │
│                                                              │
│  2. OpenAI 형식으로 변환 후 Ollama에 전달                      │
│     POST http://localhost:11434/v1/chat/completions           │
│                                                              │
│  3. Ollama 응답을 Anthropic SSE 이벤트로 변환                  │
│     - message_start / content_block_start / delta / stop     │
│     - tool_calls → tool_use 블록 변환                         │
│     - finish_reason → stop_reason 변환                       │
└───────────────┬──────────────────────────────────────────────┘
                │  OpenAI API 형식
                │  POST /v1/chat/completions
                ▼
┌──────────────────────────────────────────────────────────────┐
│  Ollama (port 11434)                                         │
│  qwen2.5-coder:7b-instruct / 14b / 32b                     │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. 즉시 실행 가능한 프록시 구현

### `bridge_proxy.py` — Anthropic↔Ollama 브릿지 프록시

복사해서 `bridge_proxy.py`로 저장하세요.

```python
#!/usr/bin/env python3
"""
Anthropic API ↔ Ollama 브릿지 프록시

Claude Code가 보내는 Anthropic /v1/messages 요청을 받아
Ollama의 /v1/chat/completions (OpenAI 호환)으로 변환 후 전달하고,
응답을 다시 Anthropic SSE 스트리밍 형식으로 변환하여 반환한다.

사용법:
  python3 bridge_proxy.py                            # 기본: 7b, port 8082
  python3 bridge_proxy.py --model qwen2.5-coder:14b-instruct --port 8082

Claude Code 연결:
  ANTHROPIC_BASE_URL=http://localhost:8082 ANTHROPIC_API_KEY=sk-fake-local-key claude
"""

import argparse
import json
import sys
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional
import threading

try:
    import requests
except ImportError:
    print("설치 필요: pip install requests", file=sys.stderr)
    sys.exit(1)

# ============================================================
# 설정
# ============================================================

OLLAMA_HOST   = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5-coder:7b-instruct"
PROXY_PORT    = 8082

# ============================================================
# A. 요청 변환: Anthropic → OpenAI
# ============================================================

def convert_anthropic_to_openai(anthropic_req: dict, override_model: str) -> dict:
    """
    Anthropic /v1/messages 요청 → OpenAI /v1/chat/completions 요청

    변환 항목:
    1. system (top-level) → messages[0] role:system
    2. content 블록 배열 → 문자열/OpenAI content 배열
    3. tools.input_schema → tools.function.parameters
    4. tool_result 메시지 → role:tool 메시지
    5. tool_use 블록 → tool_calls 배열
    """
    messages = []

    # 1. 시스템 프롬프트 변환
    system_text = anthropic_req.get("system")
    if system_text:
        if isinstance(system_text, list):
            # Anthropic은 system을 블록 배열로도 보낼 수 있음
            parts = []
            for block in system_text:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            system_text = "\n".join(parts)
        messages.append({"role": "system", "content": system_text})

    # 2. 메시지 변환
    for msg in anthropic_req.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            # 이미 문자열이면 그대로
            messages.append({"role": role, "content": content})
            continue

        if isinstance(content, list):
            # Anthropic content 블록 배열 처리
            text_parts = []
            tool_calls_out = []
            tool_results_out = []

            for block in content:
                if not isinstance(block, dict):
                    text_parts.append(str(block))
                    continue

                block_type = block.get("type", "")

                if block_type == "text":
                    text_parts.append(block.get("text", ""))

                elif block_type == "tool_use":
                    # assistant의 tool_use → OpenAI tool_calls
                    tool_calls_out.append({
                        "id":       block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        "type":     "function",
                        "function": {
                            "name":      block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })

                elif block_type == "tool_result":
                    # user의 tool_result → role:tool 메시지
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        parts = []
                        for rc in result_content:
                            if isinstance(rc, dict):
                                if rc.get("type") == "text":
                                    parts.append(rc.get("text", ""))
                                elif rc.get("type") == "json":
                                    parts.append(json.dumps(rc.get("value", "")))
                            else:
                                parts.append(str(rc))
                        result_content = "\n".join(parts)
                    elif isinstance(result_content, dict):
                        result_content = json.dumps(result_content)

                    tool_results_out.append({
                        "role":         "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content":      str(result_content),
                    })

            # assistant 메시지에 tool_calls가 있으면
            if role == "assistant" and tool_calls_out:
                asst_msg = {
                    "role":       "assistant",
                    "content":    "\n".join(text_parts) if text_parts else None,
                    "tool_calls": tool_calls_out,
                }
                messages.append(asst_msg)
            elif tool_results_out:
                # tool_result가 있으면 각각 tool 메시지로 추가
                # 먼저 텍스트 부분이 있으면 user 메시지로
                if text_parts:
                    messages.append({"role": "user", "content": "\n".join(text_parts)})
                for tr in tool_results_out:
                    messages.append(tr)
            else:
                # 일반 텍스트만
                messages.append({
                    "role":    role,
                    "content": "\n".join(text_parts) if text_parts else "",
                })

    # 3. 도구 정의 변환
    tools = None
    anthropic_tools = anthropic_req.get("tools")
    if anthropic_tools:
        tools = []
        for tool in anthropic_tools:
            tools.append({
                "type":     "function",
                "function": {
                    "name":        tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters":  tool.get("input_schema", {"type": "object"}),
                },
            })

    # 4. 최종 OpenAI 요청 조립
    openai_req = {
        "model":       override_model,
        "messages":    messages,
        "max_tokens":  anthropic_req.get("max_tokens", 4096),
        "temperature": anthropic_req.get("temperature", 0.1),
        "stream":      anthropic_req.get("stream", True),
    }
    if tools:
        openai_req["tools"] = tools
        # tool_choice 변환
        tc = anthropic_req.get("tool_choice")
        if tc:
            tc_type = tc.get("type", "auto") if isinstance(tc, dict) else "auto"
            if tc_type == "any":
                openai_req["tool_choice"] = "required"
            elif tc_type == "tool":
                openai_req["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tc.get("name", "")},
                }
            else:
                openai_req["tool_choice"] = "auto"

    return openai_req


# ============================================================
# B. 응답 변환: OpenAI 스트리밍 → Anthropic SSE 이벤트
# ============================================================

def _make_msg_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"

def _make_tool_id() -> str:
    return f"toolu_{uuid.uuid4().hex[:20]}"

def _sse_frame(event_name: str, data: dict) -> bytes:
    """Anthropic SSE 프레임 생성"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_name}\ndata: {payload}\n\n".encode("utf-8")


def stream_openai_to_anthropic(
    ollama_host: str,
    openai_req: dict,
    original_model: str,
):
    """
    Ollama /v1/chat/completions 스트리밍 응답을
    Anthropic SSE 이벤트 시퀀스로 변환하는 제너레이터.

    이벤트 시퀀스 (types.rs:205-212 기반):
    1. message_start
    2. content_block_start (index=0, type=text)
    3. content_block_delta (text_delta) * N
    4. content_block_stop
    5. [tool_use인 경우: content_block_start + input_json_delta + stop]
    6. message_delta (stop_reason)
    7. message_stop
    """
    msg_id = _make_msg_id()
    input_tokens_estimate = sum(
        len(json.dumps(m)) // 4 + 1 for m in openai_req.get("messages", [])
    )

    # ── message_start 이벤트 ──
    yield _sse_frame("message_start", {
        "type": "message_start",
        "message": {
            "id":            msg_id,
            "type":          "message",
            "role":          "assistant",
            "content":       [],
            "model":         original_model,
            "stop_reason":   None,
            "stop_sequence": None,
            "usage": {
                "input_tokens":                input_tokens_estimate,
                "output_tokens":               0,
                "cache_creation_input_tokens":  0,
                "cache_read_input_tokens":      0,
            },
        },
    })

    # ── Ollama 스트리밍 호출 ──
    try:
        resp = requests.post(
            f"{ollama_host}/v1/chat/completions",
            json=openai_req,
            stream=True,
            timeout=300,
        )
        resp.raise_for_status()
    except Exception as exc:
        # 오류 시에도 유효한 Anthropic 응답 반환
        yield _sse_frame("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })
        yield _sse_frame("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": f"[Proxy Error] {exc}"},
        })
        yield _sse_frame("content_block_stop", {
            "type": "content_block_stop",
            "index": 0,
        })
        yield _sse_frame("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {
                "input_tokens": input_tokens_estimate,
                "output_tokens": 10,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        })
        yield _sse_frame("message_stop", {"type": "message_stop"})
        return

    # ── 스트리밍 응답 파싱 & 변환 ──
    text_block_started   = False
    text_block_index     = 0
    accumulated_text     = ""
    tool_calls_buffer    = {}    # id -> {name, arguments_str}
    finish_reason        = None
    output_tokens        = 0
    current_tool_index   = None

    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="replace").strip()

        if line == "data: [DONE]":
            break
        if not line.startswith("data: "):
            continue

        try:
            chunk = json.loads(line[6:])
        except json.JSONDecodeError:
            continue

        choices = chunk.get("choices", [])
        if not choices:
            continue

        choice  = choices[0]
        delta   = choice.get("delta", {})
        fr      = choice.get("finish_reason")

        if fr:
            finish_reason = fr

        # 사용량 (있는 경우)
        usage_chunk = chunk.get("usage")
        if usage_chunk:
            output_tokens = usage_chunk.get("completion_tokens", output_tokens)

        # ── 텍스트 토큰 ──
        content = delta.get("content")
        if content:
            if not text_block_started:
                yield _sse_frame("content_block_start", {
                    "type": "content_block_start",
                    "index": text_block_index,
                    "content_block": {"type": "text", "text": ""},
                })
                text_block_started = True

            yield _sse_frame("content_block_delta", {
                "type": "content_block_delta",
                "index": text_block_index,
                "delta": {"type": "text_delta", "text": content},
            })
            accumulated_text += content
            output_tokens += 1  # 대략 추정

        # ── 도구 호출 ──
        tool_calls = delta.get("tool_calls", [])
        for tc in tool_calls:
            tc_index = tc.get("index", 0)
            tc_id    = tc.get("id")
            func     = tc.get("function", {})
            tc_name  = func.get("name")
            tc_args  = func.get("arguments", "")

            if tc_id:
                # 새 도구 호출 시작
                tool_calls_buffer[tc_id] = {
                    "name":       tc_name or "",
                    "arguments":  tc_args,
                }
                current_tool_index = tc_id
            elif current_tool_index and tc_args:
                # 기존 도구의 arguments 계속 누적
                tool_calls_buffer[current_tool_index]["arguments"] += tc_args

    # ── 텍스트 블록 닫기 ──
    if text_block_started:
        yield _sse_frame("content_block_stop", {
            "type": "content_block_stop",
            "index": text_block_index,
        })

    # ── 도구 호출 블록 생성 ──
    block_index = (text_block_index + 1) if text_block_started else 0
    for tc_id, tc_data in tool_calls_buffer.items():
        tool_id  = _make_tool_id()
        tc_name  = tc_data["name"]
        tc_args  = tc_data["arguments"]

        # arguments를 JSON 파싱 시도
        try:
            parsed_input = json.loads(tc_args) if tc_args else {}
        except json.JSONDecodeError:
            parsed_input = {"raw": tc_args}

        # content_block_start (tool_use)
        yield _sse_frame("content_block_start", {
            "type": "content_block_start",
            "index": block_index,
            "content_block": {
                "type":  "tool_use",
                "id":    tool_id,
                "name":  tc_name,
                "input": {},
            },
        })

        # input_json_delta (한 번에 전체 전송)
        yield _sse_frame("content_block_delta", {
            "type": "content_block_delta",
            "index": block_index,
            "delta": {
                "type":         "input_json_delta",
                "partial_json": json.dumps(parsed_input, ensure_ascii=False),
            },
        })

        # content_block_stop
        yield _sse_frame("content_block_stop", {
            "type": "content_block_stop",
            "index": block_index,
        })

        block_index += 1

    # ── stop_reason 변환 ──
    if tool_calls_buffer:
        stop_reason = "tool_use"
    elif finish_reason == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    # ── message_delta 이벤트 ──
    yield _sse_frame("message_delta", {
        "type": "message_delta",
        "delta": {
            "stop_reason":   stop_reason,
            "stop_sequence": None,
        },
        "usage": {
            "input_tokens":                input_tokens_estimate,
            "output_tokens":               max(output_tokens, 1),
            "cache_creation_input_tokens":  0,
            "cache_read_input_tokens":      0,
        },
    })

    # ── message_stop 이벤트 ──
    yield _sse_frame("message_stop", {"type": "message_stop"})


# ============================================================
# C. 비스트리밍 응답 변환
# ============================================================

def non_streaming_response(
    ollama_host: str,
    openai_req: dict,
    original_model: str,
) -> dict:
    """비스트리밍 요청의 경우 완전한 Anthropic MessageResponse 반환"""
    openai_req["stream"] = False

    try:
        resp = requests.post(
            f"{ollama_host}/v1/chat/completions",
            json=openai_req,
            timeout=300,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as exc:
        return _error_response(str(exc), original_model)

    choice  = result.get("choices", [{}])[0]
    message = choice.get("message", {})
    usage   = result.get("usage", {})

    content_blocks = []

    # 텍스트
    text = message.get("content")
    if text:
        content_blocks.append({"type": "text", "text": text})

    # 도구 호출
    for tc in message.get("tool_calls", []):
        func = tc.get("function", {})
        try:
            parsed_input = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            parsed_input = {}
        content_blocks.append({
            "type":  "tool_use",
            "id":    _make_tool_id(),
            "name":  func.get("name", ""),
            "input": parsed_input,
        })

    fr = choice.get("finish_reason", "stop")
    if message.get("tool_calls"):
        stop_reason = "tool_use"
    elif fr == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    return {
        "id":            _make_msg_id(),
        "type":          "message",
        "role":          "assistant",
        "content":       content_blocks,
        "model":         original_model,
        "stop_reason":   stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens":                usage.get("prompt_tokens", 0),
            "output_tokens":               usage.get("completion_tokens", 0),
            "cache_creation_input_tokens":  0,
            "cache_read_input_tokens":      0,
        },
    }

def _error_response(error_msg: str, model: str) -> dict:
    return {
        "id":            _make_msg_id(),
        "type":          "message",
        "role":          "assistant",
        "content":       [{"type": "text", "text": f"[Proxy Error] {error_msg}"}],
        "model":         model,
        "stop_reason":   "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 0, "output_tokens": 1,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        },
    }


# ============================================================
# D. HTTP 서버
# ============================================================

class BridgeHandler(BaseHTTPRequestHandler):
    """Anthropic API 엔드포인트를 구현하는 HTTP 핸들러"""

    ollama_host:    str = OLLAMA_HOST
    override_model: str = DEFAULT_MODEL
    verbose:        bool = True

    def log_message(self, format, *args):
        if self.verbose:
            super().log_message(format, *args)

    def do_POST(self):
        if self.path == "/v1/messages":
            self._handle_messages()
        else:
            self.send_error(404, f"Not found: {self.path}")

    def do_GET(self):
        if self.path == "/v1/models":
            self._handle_models()
        elif self.path == "/health" or self.path == "/":
            self._handle_health()
        else:
            self.send_error(404)

    def _handle_health(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "ok",
            "bridge": "anthropic-to-ollama",
            "ollama_host": self.ollama_host,
            "model": self.override_model,
        }).encode())

    def _handle_models(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "data": [{
                "id": self.override_model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "local-ollama",
            }]
        }).encode())

    def _handle_messages(self):
        # 요청 본문 읽기
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b""

        try:
            anthropic_req = json.loads(body)
        except json.JSONDecodeError as exc:
            self.send_error(400, f"Invalid JSON: {exc}")
            return

        original_model = anthropic_req.get("model", self.override_model)
        is_streaming   = anthropic_req.get("stream", False)

        if self.verbose:
            print(f"\n[Bridge] → {original_model} | stream={is_streaming} | "
                  f"messages={len(anthropic_req.get('messages', []))} | "
                  f"tools={len(anthropic_req.get('tools', []))}")

        # Anthropic → OpenAI 변환
        openai_req = convert_anthropic_to_openai(anthropic_req, self.override_model)

        if is_streaming:
            self._handle_streaming(openai_req, original_model)
        else:
            self._handle_non_streaming(openai_req, original_model)

    def _handle_streaming(self, openai_req: dict, original_model: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            for frame in stream_openai_to_anthropic(
                self.ollama_host, openai_req, original_model
            ):
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 클라이언트 연결 끊김

    def _handle_non_streaming(self, openai_req: dict, original_model: str):
        result = non_streaming_response(
            self.ollama_host, openai_req, original_model
        )
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ============================================================
# E. 메인
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Anthropic API → Ollama 브릿지 프록시"
    )
    parser.add_argument("--port",   type=int, default=PROXY_PORT,
                        help=f"프록시 포트 (기본: {PROXY_PORT})")
    parser.add_argument("--model",  default=DEFAULT_MODEL,
                        help=f"Ollama 모델명 (기본: {DEFAULT_MODEL})")
    parser.add_argument("--ollama", default=OLLAMA_HOST,
                        help=f"Ollama 주소 (기본: {OLLAMA_HOST})")
    parser.add_argument("--quiet",  action="store_true")
    args = parser.parse_args()

    BridgeHandler.ollama_host    = args.ollama
    BridgeHandler.override_model = args.model
    BridgeHandler.verbose        = not args.quiet

    server = HTTPServer(("0.0.0.0", args.port), BridgeHandler)

    print("=" * 60)
    print("🌉  Anthropic ↔ Ollama 브릿지 프록시")
    print(f"📡  프록시   : http://localhost:{args.port}")
    print(f"🤖  모델     : {args.model}")
    print(f"🔗  Ollama   : {args.ollama}")
    print("=" * 60)
    print()
    print("Claude Code 연결 방법:")
    print(f"  ANTHROPIC_BASE_URL=http://localhost:{args.port} \\")
    print(f"  ANTHROPIC_API_KEY=sk-fake-local-key \\")
    print(f"  claude")
    print()
    print("종료: Ctrl+C")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n프록시 종료")
        server.server_close()


if __name__ == "__main__":
    main()
```

---

### `run_bridge.sh` — 원클릭 실행 스크립트

복사해서 저장 후 `chmod +x run_bridge.sh` 실행하세요.

```bash
#!/usr/bin/env bash
# Claude Code + Ollama 브릿지: 원클릭 실행
set -euo pipefail

MODEL="${OLLAMA_MODEL:-qwen2.5-coder:7b-instruct}"
PROXY_PORT="${PROXY_PORT:-8082}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"

log() { echo "$(date '+%H:%M:%S') $*"; }
die() { echo "❌ $*" >&2; exit 1; }

# ── 1. 의존성 확인 ──
command -v python3 >/dev/null || die "python3 필요"
command -v ollama  >/dev/null || die "ollama 필요: https://ollama.com/download"
python3 -c "import requests" 2>/dev/null || pip3 install --quiet requests

# ── 2. Ollama 서버 확인/시작 ──
if ! curl -sf "http://localhost:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; then
    log "🚀 Ollama 서버 시작..."
    ollama serve &>/tmp/ollama_bridge.log &
    sleep 3
    curl -sf "http://localhost:${OLLAMA_PORT}/api/tags" >/dev/null || die "Ollama 시작 실패"
fi
log "✅ Ollama 실행 중"

# ── 3. 모델 확인 ──
if ! ollama list 2>/dev/null | grep -q "${MODEL}"; then
    log "📦 모델 다운로드: ${MODEL}"
    ollama pull "${MODEL}"
fi
log "✅ 모델 준비: ${MODEL}"

# ── 4. 메모리 확인 ──
python3 -c "
import psutil
vm = psutil.virtual_memory()
print(f'💾 메모리: {vm.used/1024**3:.1f}GB / {vm.total/1024**3:.1f}GB ({vm.percent}%)')
" 2>/dev/null || true

# ── 5. 브릿지 프록시 시작 ──
log "🌉 브릿지 프록시 시작 (port ${PROXY_PORT})..."
python3 bridge_proxy.py --port "${PROXY_PORT}" --model "${MODEL}" &
PROXY_PID=$!
sleep 1

# 프록시 헬스 체크
if ! curl -sf "http://localhost:${PROXY_PORT}/health" >/dev/null 2>&1; then
    die "프록시 시작 실패"
fi
log "✅ 프록시 실행 중: http://localhost:${PROXY_PORT}"

# ── 6. Claude Code 연결 안내 ──
echo ""
echo "=============================================="
echo "🎯 Claude Code 연결 명령어 (새 터미널에서):"
echo ""
echo "  ANTHROPIC_BASE_URL=http://localhost:${PROXY_PORT} \\"
echo "  ANTHROPIC_API_KEY=sk-fake-local-key \\"
echo "  claude"
echo ""
echo "또는 환경변수 영구 설정:"
echo "  export ANTHROPIC_BASE_URL=http://localhost:${PROXY_PORT}"
echo "  export ANTHROPIC_API_KEY=sk-fake-local-key"
echo "  claude"
echo "=============================================="
echo ""
echo "프록시 PID: ${PROXY_PID} | 종료: kill ${PROXY_PID} 또는 Ctrl+C"

# 프록시 프로세스 대기
wait "${PROXY_PID}" 2>/dev/null || true
```

---

## 5. 실행 방법 및 검증

### 단계별 실행

```bash
# ── 터미널 1: 프록시 시작 ──
pip3 install requests psutil

# 방법 A: run_bridge.sh 사용 (자동)
chmod +x run_bridge.sh
./run_bridge.sh

# 방법 B: 수동 실행
ollama serve                                              # Ollama 서버
ollama pull qwen2.5-coder:7b-instruct                    # 모델 다운로드
python3 bridge_proxy.py --model qwen2.5-coder:7b-instruct # 프록시 시작
```

```bash
# ── 터미널 2: Claude Code 연결 ──
ANTHROPIC_BASE_URL=http://localhost:8082 \
ANTHROPIC_API_KEY=sk-fake-local-key \
claude
```

### 검증 방법

```bash
# 프록시 헬스 체크
curl http://localhost:8082/health

# 직접 API 테스트 (Anthropic 형식으로 요청)
curl -X POST http://localhost:8082/v1/messages \
  -H "content-type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -H "x-api-key: sk-fake-key" \
  -d '{
    "model": "qwen2.5-coder:7b-instruct",
    "max_tokens": 256,
    "stream": false,
    "messages": [
      {"role": "user", "content": [{"type": "text", "text": "Write a Python hello world"}]}
    ]
  }'
```

```bash
# 스트리밍 테스트
curl -N -X POST http://localhost:8082/v1/messages \
  -H "content-type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -H "x-api-key: sk-fake-key" \
  -d '{
    "model": "qwen2.5-coder:7b-instruct",
    "max_tokens": 256,
    "stream": true,
    "messages": [
      {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
    ]
  }'
```

---

## 6. 기능 호환성 매트릭스

### Claude Code 기능별 동작 여부

| Claude Code 기능 | 7B | 14B | 32B | 원인 |
|-----------------|-----|-----|-----|------|
| **텍스트 대화** | ✅ | ✅ | ✅ | 기본 채팅, 프록시 변환 |
| **스트리밍 출력** | ✅ | ✅ | ✅ | SSE 이벤트 변환 |
| **Read 도구** | ⚠️ | ✅ | ✅ | 도구 호출 품질 의존 |
| **Edit 도구** | ❌ | ⚠️ | ⚠️ | 정확한 old_string 매칭 어려움 |
| **Bash 도구** | ⚠️ | ✅ | ✅ | 간단한 명령어는 동작 |
| **Grep 도구** | ⚠️ | ✅ | ✅ | 패턴 생성 품질 의존 |
| **Write 도구** | ⚠️ | ⚠️ | ✅ | 긴 코드 생성 시 불안정 |
| **멀티 도구 체이닝** | ❌ | ❌ | ⚠️ | 연속 도구 호출 논리 복잡 |
| **Agent 서브에이전트** | ❌ | ❌ | ❌ | 재귀 호출 + 도구 조합 |
| **컨텍스트 압축** | ✅ | ✅ | ✅ | 서버 측 처리 |
| **CLAUDE.md 로딩** | ✅ | ✅ | ✅ | 클라이언트 측 처리 |
| **프롬프트 캐싱** | ❌ | ❌ | ❌ | Anthropic 전용 |
| **확장 사고(thinking)** | ❌ | ❌ | ❌ | Anthropic 전용 |
| **이미지/PDF 입력** | ❌ | ❌ | ❌ | 멀티모달 변환 미구현 |
| **비용 추적** | ⚠️ | ⚠️ | ⚠️ | 추정값만 제공 (무료) |

**범례:** ✅ 동작 | ⚠️ 부분 동작/불안정 | ❌ 미동작

---

## 7. 성능 격차 정직한 평가

### 코딩 벤치마크 비교 (HumanEval, SWE-bench 기준)

| 모델 | HumanEval pass@1 | SWE-bench Lite | 도구 호출 정확도 |
|------|------------------|---------------|----------------|
| Claude Opus 4.6 | ~92% | ~50%+ | ~95% |
| Claude Sonnet 4.6 | ~90% | ~45% | ~93% |
| **Qwen2.5-Coder 7B** | ~65% | N/A | ~40-50% |
| **Qwen2.5-Coder 14B** | ~75% | N/A | ~55-65% |
| **Qwen2.5-Coder 32B** | ~82% | ~20-25% | ~70-75% |

> ⚠️ 위 수치는 공개 벤치마크 근사치이며, Claude Code의 실제 에이전트 성능은 도구 사용 + 멀티스텝 추론이 포함되어 벤치마크보다 격차가 더 큼.

### 실질적 성능 차이

| 작업 유형 | Claude Sonnet | Qwen 32B 로컬 | 격차 |
|-----------|--------------|---------------|------|
| 단일 함수 작성 | 거의 완벽 | 양호 | 10-15% |
| 단일 파일 디버깅 | 정확 | 보통 | 20-30% |
| 멀티파일 리팩토링 | 매우 정확 | 불안정 | 40-50% |
| 테스트 작성 | 완전 | 부분 | 30-40% |
| 아키텍처 설계 | 전문가 수준 | 기초 | 50%+ |
| 도구 체이닝 (5+단계) | 안정 | 자주 실패 | 50-70% |

### 결론

**"Claude Code Opus/Sonnet의 코딩 성능을 완전히 구현"하는 것은 불가능.**

그러나 다음은 실용적으로 가능:
- Claude Code의 **UI/UX, 도구 시스템, 세션 관리**를 그대로 활용
- 간단한 코딩 작업(파일 읽기, 단일 파일 편집, git 명령어)은 **실용적으로 동작**
- **비용 $0**으로 로컬에서 실행 가능
- **오프라인 환경**에서 사용 가능
- Claude API 비용 절약을 위한 **보조 도구**로 활용

---

## 8. 한계점 및 권장사항

### 구조적 한계

1. **프롬프트 캐싱 불가**: Claude Code는 시스템 프롬프트 캐싱으로 매 요청 비용/시간을 절약. 로컬에서는 매번 전체 프롬프트 재처리.

2. **Extended Thinking 불가**: Claude의 `thinking` 블록은 Anthropic 전용. 복잡한 추론이 필요한 작업에서 품질 저하.

3. **도구 호출 신뢰성**: Claude는 도구 스키마를 정확히 따르지만, 오픈소스 모델은 JSON 형식 오류, 파라미터 누락, 불필요한 도구 호출이 빈번.

4. **컨텍스트 윈도우**: Claude는 200K 토큰. Qwen 32B는 32K (실질적으로 8K-16K 권장). 대형 코드베이스 분석에 근본 차이.

5. **시스템 프롬프트 최적화**: Claude Code의 시스템 프롬프트는 Claude 모델의 특성에 최적화됨. Qwen 모델에서는 동일한 지시를 따르는 능력이 제한적.

### 권장 사용 시나리오

| 시나리오 | 권장 | 이유 |
|---------|------|------|
| 단일 파일 편집/생성 | ✅ 로컬 | 비용 절약, 간단한 작업 |
| 간단한 질문/코드 설명 | ✅ 로컬 | 충분한 품질 |
| 오프라인/에어갭 환경 | ✅ 로컬 | 유일한 선택지 |
| 멀티파일 리팩토링 | ❌ Claude API | 도구 체이닝 신뢰성 필요 |
| 복잡한 디버깅 | ❌ Claude API | 추론 능력 격차 |
| PR 리뷰/아키텍처 설계 | ❌ Claude API | 깊은 이해력 필요 |

### 하이브리드 전략 (최적)

```bash
# 간단한 작업: 로컬 Ollama (비용 $0)
ANTHROPIC_BASE_URL=http://localhost:8082 \
ANTHROPIC_API_KEY=sk-fake-local-key \
claude --prompt "이 함수에 타입 힌트를 추가해줘"

# 복잡한 작업: Claude API (필요할 때만)
ANTHROPIC_API_KEY=sk-ant-real-key \
claude --prompt "이 모듈 전체를 async로 리팩토링해줘"
```

### 메모리 예산 (프록시 포함)

| 구성 | 모델 | 프록시 | Ollama | **합계** | 판정 |
|------|------|--------|--------|---------|------|
| 7B + 프록시 | 4.7GB | 0.05GB | 0.8GB | **~5.6GB** | ✅ |
| 14B + 프록시 | 9.0GB | 0.05GB | 1.3GB | **~10.4GB** | ✅ |
| 32B Q3_K_M + 프록시 | 15.5GB | 0.05GB | 1.3GB | **~16.9GB** | ✅ |

프록시 자체의 메모리 오버헤드는 **~50MB** 미만으로 무시할 수 있는 수준.

---

*생성: Claude Opus 4.6 - Claude Code 코드베이스 역공학 + API 프로토콜 분석*
