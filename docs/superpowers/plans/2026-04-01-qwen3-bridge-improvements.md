# Qwen3 Bridge Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** qwen3를 기본 로컬 모델로 전환하고, Context Compaction · Qwen3 추론모드 · 캐시 경계 마커 · 병렬 MCP 도구 실행 4가지를 `bridge_proxy_full.py`에 추가한 뒤 문서를 최신화한다.

**Architecture:**
- 모든 신규 기능은 `ProxyConfig`에 옵트인 플래그를 추가해 `--no-*` 또는 `--enable-*` 로 제어한다.
- `bridge_proxy_full.py` 단일 파일을 수정하며, 각 기능은 독립 클래스·함수로 분리한다.
- 하위 호환: 기존 모델(qwen2.5-coder 등)에서도 정상 동작해야 한다.

**Tech Stack:** Python 3.9+, stdlib only (threading, concurrent.futures, hashlib, re, json, urllib)

---

## File Map

| 파일 | 변경 유형 | 내용 |
|---|---|---|
| `bridge_proxy_full.py` | Modify | 4개 기능 추가, 기본 모델 변경 |
| `run_full_bridge.sh` | Modify | 기본 모델 → qwen3:8b, 신규 feature 표시 |
| `README.md` | Modify | qwen3 기준 업데이트 |
| `BRIDGE_FULL_GUIDE.md` | Modify | 신규 기능 섹션 추가, qwen3 설정 추가 |
| `CLAUDE_CODE_LOCAL_OLLAMA_BRIDGE.md` | Modify | qwen3 설치/사용 가이드 업데이트 |

---

## Task 1: Qwen3 추론모드 활성화

**Files:**
- Modify: `bridge_proxy_full.py` — ProxyConfig, LocalModelOptimizer, convert_anthropic_to_openai

### 구현

- [ ] **Step 1: ProxyConfig에 thinking 관련 필드 추가 (line 57 근처)**

`bridge_proxy_full.py`의 `ProxyConfig` dataclass에 다음 필드를 추가한다:

```python
    # Qwen3 / Thinking models
    enable_thinking: bool = True
    thinking_budget_tokens: int = 8192
    thinking_models: list[str] = field(
        default_factory=lambda: ["qwen3", "deepseek-r1", "qwq", "marco-o1"]
    )
```

`__post_init__` 직전(line 107 앞)에 아무것도 추가하지 않아도 된다 — dataclass가 처리한다.

- [ ] **Step 2: is_thinking_model() 헬퍼 함수 추가**

`PromptCacheLayer` 클래스 바로 위(line 163 앞)에 추가:

```python
def is_thinking_model(model_name: str, thinking_models: list[str]) -> bool:
    """Return True if model supports native thinking/reasoning mode."""
    lower = model_name.lower()
    return any(tm.lower() in lower for tm in thinking_models)
```

- [ ] **Step 3: LocalModelOptimizer에 thinking 메서드 2개 추가 (line 769 근처)**

`LocalModelOptimizer` 클래스 내부, `force_cot` 아래에 추가:

```python
    @staticmethod
    def apply_thinking_mode(messages: list[dict]) -> list[dict]:
        """Prepend /think to last user message to activate Qwen3 thinking."""
        msgs = [dict(m) for m in messages]
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == "user":
                content = msgs[i]["content"]
                if isinstance(content, str) and not content.startswith("/think"):
                    msgs[i]["content"] = "/think\n" + content
                break
        return msgs

    @staticmethod
    def strip_thinking_tags(text: str) -> str:
        """Remove <think>...</think> blocks from model output before returning."""
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        return cleaned.strip()
```

- [ ] **Step 4: convert_anthropic_to_openai 에서 thinking 옵션 적용**

`convert_anthropic_to_openai` 함수 시그니처를 변경하고 thinking 옵션을 추가한다.

현재 (line 824):
```python
def convert_anthropic_to_openai(req: dict, model: str) -> dict:
```

변경 후:
```python
def convert_anthropic_to_openai(req: dict, model: str, config: Optional["ProxyConfig"] = None) -> dict:
```

`openai_req` 딕셔너리 구성부(line 911-920) 변경:

기존:
```python
    openai_req: dict = {
        "model": model,
        "messages": messages,
        "stream": req.get("stream", False),
        "options": {
            "num_ctx": min(req.get("max_tokens", 4096) * 4, 32768),
            "temperature": req.get("temperature", 0.3),
            "keep_alive": -1,
        },
    }
```

변경 후:
```python
    _use_thinking = (
        config is not None
        and config.enable_thinking
        and is_thinking_model(model, config.thinking_models)
    )
    openai_req: dict = {
        "model": model,
        "messages": messages,
        "stream": req.get("stream", False),
        "options": {
            "num_ctx": min(req.get("max_tokens", 4096) * 4, 65536 if _use_thinking else 32768),
            "temperature": req.get("temperature", 0.3),
            "keep_alive": -1,
            **({"think": True, "num_predict": config.thinking_budget_tokens} if _use_thinking else {}),
        },
    }
```

- [ ] **Step 5: 핸들러에서 thinking 적용 — messages 전처리 + 응답 후처리**

`do_POST` 내 `convert_anthropic_to_openai` 호출부(line 1312)를 수정:

기존:
```python
            openai_req = convert_anthropic_to_openai(req, config.primary_model)
```

변경 후:
```python
            openai_req = convert_anthropic_to_openai(req, config.primary_model, config)
            # Qwen3 thinking mode: activate via /think prefix
            if (config.enable_thinking
                    and is_thinking_model(config.primary_model, config.thinking_models)):
                openai_req["messages"] = optimizer.apply_thinking_mode(openai_req["messages"])
                log.debug("THINKING: activated for model %s", config.primary_model)
```

- [ ] **Step 6: 응답에서 <think> 태그 제거**

`non_streaming_response` 함수 내 content_blocks 구성부(line 1131-1133) 수정:

기존:
```python
    text = msg.get("content") or ""
    if text:
        content_blocks.append({"type": "text", "text": text})
```

변경 후:
```python
    text = msg.get("content") or ""
    if text:
        # Strip <think>...</think> blocks from thinking models
        text = LocalModelOptimizer.strip_thinking_tags(text)
    if text:
        content_blocks.append({"type": "text", "text": text})
```

스트리밍 경로(`stream_openai_to_anthropic`)의 텍스트 델타 부분(line 1017-1024)도 수정:

기존:
```python
                    # Text delta
                    text = delta.get("content") or ""
                    if text:
                        output_tokens += len(text) // 4 + 1
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": text},
                        })
```

변경 후:
```python
                    # Text delta — buffer to strip <think> tags
                    text = delta.get("content") or ""
                    if text:
                        # Simple streaming strip: accumulate think blocks, emit rest
                        if "<think>" in text or "</think>" in text or _in_think_block:
                            _think_buffer += text
                            # Try to strip completed think blocks
                            cleaned = re.sub(r"<think>.*?</think>", "", _think_buffer, flags=re.DOTALL)
                            if cleaned != _think_buffer and not re.search(r"<think>(?!.*</think>)", cleaned, re.DOTALL):
                                text = cleaned
                                _think_buffer = ""
                                _in_think_block = False
                            else:
                                _in_think_block = "<think>" in _think_buffer and "</think>" not in _think_buffer
                                continue  # don't emit yet
                        if text:
                            output_tokens += len(text) // 4 + 1
                            yield _sse("content_block_delta", {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": text},
                            })
```

`stream_openai_to_anthropic` 함수 진입부 직후(line 982 근처)에 로컬 변수 초기화 추가:

```python
    _think_buffer = ""
    _in_think_block = False
```

- [ ] **Step 7: argparse에 thinking 관련 인수 추가 (line 1401 근처)**

`parse_args()` 함수 내 다음 추가:

```python
    p.add_argument("--no-thinking", action="store_true", help="Disable thinking mode for Qwen3/DeepSeek-R1")
    p.add_argument("--thinking-budget", type=int, default=8192, help="Max thinking tokens (default: 8192)")
```

`main()` 의 `ProxyConfig(...)` 생성부에 추가:

```python
        enable_thinking=not args.no_thinking,
        thinking_budget_tokens=args.thinking_budget,
```

- [ ] **Step 8: 기본 모델 qwen3:8b로 변경**

`ProxyConfig` (line 59):
```python
    primary_model: str = "qwen3:8b"
```

`parse_args()` `--model` 기본값 (line 1406):
```python
    p.add_argument("--model", default="qwen3:8b", help="Primary Ollama model")
```

---

## Task 2: Context Compaction

**Files:**
- Modify: `bridge_proxy_full.py` — ProxyConfig + new ConversationCompactor class + do_POST

### 구현

- [ ] **Step 1: ProxyConfig에 compaction 필드 추가**

`ProxyConfig`의 `# VERIFICATION` 섹션 아래에:

```python
    # COMPACTION
    enable_compaction: bool = True
    compaction_max_tokens: int = 24000   # trigger above this estimate
    compaction_target_tokens: int = 8000  # aim to retain this much
    compaction_min_turns: int = 6         # minimum turns before compaction
```

- [ ] **Step 2: ConversationCompactor 클래스 추가**

`VerificationAgent` 클래스 바로 뒤(line 764 아래)에 추가:

```python
# ---------------------------------------------------------------------------
# ConversationCompactor — summarize old turns to stay within context window
# ---------------------------------------------------------------------------
class ConversationCompactor:
    def __init__(self, config: ProxyConfig, ollama_host: str, model: str):
        self._cfg = config
        self._ollama_host = ollama_host
        self._model = model

    def estimate_tokens(self, messages: list[dict]) -> int:
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += len(content) // 4 + 4
            elif isinstance(content, list):
                total += sum(len(str(b)) // 4 + 4 for b in content)
        return total

    def should_compact(self, messages: list[dict]) -> bool:
        non_system = [m for m in messages if m.get("role") != "system"]
        if len(non_system) < self._cfg.compaction_min_turns * 2:
            return False
        return self.estimate_tokens(messages) > self._cfg.compaction_max_tokens

    def compact(self, messages: list[dict]) -> list[dict]:
        """Summarize old messages; keep system + recent 4 turns + inject summary."""
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        keep_n = 8  # keep last 4 user/assistant pairs
        old_msgs = non_system[:-keep_n] if len(non_system) > keep_n else []
        recent_msgs = non_system[-keep_n:] if len(non_system) >= keep_n else non_system

        if not old_msgs:
            return messages

        conv_text = "\n".join(
            f"{m['role'].upper()}: {(m['content'] if isinstance(m.get('content'), str) else str(m.get('content', '')))[:400]}"
            for m in old_msgs
        )
        summary_prompt = (
            "Summarize the following conversation history into a concise context block "
            "(max 400 words). Preserve: key decisions made, code written or changed, "
            "errors encountered, and the current goal.\n\n" + conv_text
        )
        try:
            body = json.dumps({
                "model": self._model,
                "messages": [{"role": "user", "content": summary_prompt}],
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 4096},
            }).encode()
            req_obj = urllib.request.Request(
                f"{self._ollama_host}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req_obj, timeout=60) as resp:
                data = json.loads(resp.read())
            summary = data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            log.warning("COMPACTION: summarization failed: %s — skipping", e)
            return messages

        compacted = system_msgs + [
            {"role": "user", "content": f"[Prior Conversation Summary]\n{summary}"},
            {"role": "assistant", "content": "Understood. I have context from our earlier conversation."},
        ] + recent_msgs

        before = len(messages)
        after = len(compacted)
        log.info(
            "COMPACTION: %d→%d messages (est. tokens %d→%d)",
            before, after,
            self.estimate_tokens(messages),
            self.estimate_tokens(compacted),
        )
        return compacted
```

- [ ] **Step 3: make_handler_class 시그니처에 compactor 파라미터 추가 (line 1168)**

기존:
```python
def make_handler_class(
    config: ProxyConfig,
    cache_layer: PromptCacheLayer,
    mcp: McpServerManager,
    rag: RagContextInjector,
    kairos: Optional[KairosDaemon],
    coordinator: CoordinatorMode,
    classifier: TranscriptClassifier,
    ultraplan: UltraPlan,
    verifier: VerificationAgent,
    team_mem: TeamMemory,
    optimizer: LocalModelOptimizer,
) -> type:
```

변경 후:
```python
def make_handler_class(
    config: ProxyConfig,
    cache_layer: PromptCacheLayer,
    mcp: McpServerManager,
    rag: RagContextInjector,
    kairos: Optional[KairosDaemon],
    coordinator: CoordinatorMode,
    classifier: TranscriptClassifier,
    ultraplan: UltraPlan,
    verifier: VerificationAgent,
    team_mem: TeamMemory,
    optimizer: LocalModelOptimizer,
    compactor: "ConversationCompactor",
) -> type:
```

- [ ] **Step 4: do_POST에서 compaction 적용**

`do_POST` 내 COORDINATOR 블록 바로 앞(line 1285 근처)에 삽입:

```python
            # --- COMPACTION: summarize old turns if context is too long ---
            if config.enable_compaction:
                msgs_raw = req.get("messages", [])
                if compactor.should_compact(msgs_raw):
                    log.info("COMPACTION: context too long, compacting...")
                    req = dict(req)
                    req["messages"] = compactor.compact(msgs_raw)
```

- [ ] **Step 5: main()에서 compactor 초기화 및 전달**

`main()` 내 `coord = CoordinatorMode(...)` 줄 아래에:

```python
    compactor = ConversationCompactor(cfg, cfg.ollama_host, cfg.primary_model)
```

`handler_class = make_handler_class(...)` 호출에 `compactor` 추가:

```python
    handler_class = make_handler_class(
        cfg, cache_layer, mcp, rag, kairos_daemon,
        coord, clf, uplan, verif, team_mem, optim, compactor,
    )
```

- [ ] **Step 6: argparse에 compaction 인수 추가**

```python
    p.add_argument("--no-compaction", action="store_true", help="Disable context compaction")
    p.add_argument("--compaction-max-tokens", type=int, default=24000,
                   help="Token threshold to trigger compaction (default: 24000)")
```

`ProxyConfig(...)` 생성부에:
```python
        enable_compaction=not args.no_compaction,
        compaction_max_tokens=args.compaction_max_tokens,
```

---

## Task 3: 캐시 경계 마커 도입

**Files:**
- Modify: `bridge_proxy_full.py` — PromptCacheLayer, do_POST system prompt assembly

### 구현

- [ ] **Step 1: 캐시 경계 상수 추가**

`PromptCacheLayer` 클래스 정의 바로 위(line 163 앞)에:

```python
# Separator between static (cacheable) and dynamic (per-request) system prompt parts.
# PromptCacheLayer hashes only the static part so RAG/KAIROS changes don't break cache.
_CACHE_BOUNDARY = "\n\n<!-- BRIDGE:DYNAMIC_START -->\n"
```

- [ ] **Step 2: PromptCacheLayer.record() 시그니처 변경**

기존 (line 178):
```python
    def record(self, system_text: str, approx_tokens: int) -> tuple[int, int]:
        """Returns (cache_creation_tokens, cache_read_tokens)."""
        key = hashlib.sha256(system_text.encode()).hexdigest()
```

변경 후:
```python
    def record(self, static_text: str, approx_tokens: int) -> tuple[int, int]:
        """Returns (cache_creation_tokens, cache_read_tokens).
        Hashes only the static (user-supplied) part of the system prompt
        so dynamic RAG/KAIROS additions don't break cache hits.
        """
        key = hashlib.sha256(static_text.encode()).hexdigest()
```

- [ ] **Step 3: do_POST 시스템 프롬프트 조립 로직 분리**

`do_POST` 내 현재 system_text 조립 블록(lines 1225~1283)을 리팩터링한다.

기존 패턴:
```python
            system_raw = req.get("system", "")
            system_text = _extract_text(system_raw) if system_raw else ""
            ...
            if config.enable_teammem:
                mem_block = ...
                system_text = system_text + "\n\n" + mem_block ...
            if config.enable_kairos ...:
                ...
                system_text = system_text + "\n\n" + kairos_block ...
            if config.enable_rag ...:
                rag_ctx = ...
                system_text = system_text + "\n\n" + rag_ctx ...
            if config.enable_ultraplan ...:
                ...
                system_text = ultraplan.inject_plan(system_text, plan)
```

변경 후 — static/dynamic 분리:
```python
            system_raw = req.get("system", "")
            static_system = _extract_text(system_raw) if system_raw else ""

            # Build dynamic blocks (change every request — must NOT be part of cache key)
            dynamic_parts: list[str] = []

            if config.enable_teammem:
                mem_block = team_mem.as_context_block()
                if mem_block:
                    dynamic_parts.append(mem_block)

            if config.enable_kairos and kairos:
                findings = kairos.pop_findings()
                if findings:
                    dynamic_parts.append(
                        "## KAIROS Background Findings\n" + "\n".join(f"- {f}" for f in findings)
                    )

            if config.enable_rag and user_text:
                rag_ctx = rag.build_context(user_text)
                if rag_ctx:
                    dynamic_parts.append(rag_ctx)

            if config.enable_ultraplan and ultraplan.is_complex(user_text):
                log.info("ULTRAPLAN: generating plan for complex request")
                plan = ultraplan.generate_plan(user_text)
                if plan:
                    dynamic_parts.append(f"## ULTRAPLAN — Pre-computed Implementation Plan\n{plan}")

            # Assemble: static part + boundary + dynamic part
            if dynamic_parts:
                system_text = static_system + _CACHE_BOUNDARY + "\n\n".join(dynamic_parts)
            else:
                system_text = static_system
```

- [ ] **Step 4: stream_openai_to_anthropic / non_streaming_response 호출부에서 static_system 전달**

`stream_openai_to_anthropic` 및 `non_streaming_response` 의 `system_text` 파라미터는 cache key용이므로, 실제로는 `static_system`을 넘겨야 한다.

`do_POST` 내 두 호출부에서:

기존:
```python
                gen = stream_openai_to_anthropic(
                    config.ollama_host, openai_req, orig_model,
                    cache_layer if config.enable_cache else None,
                    system_text, approx_input,
                )
```
```python
                    resp_obj = non_streaming_response(
                        config.ollama_host, openai_req, orig_model,
                        cache_layer if config.enable_cache else None,
                        system_text, approx_input,
                    )
```

변경 후 (두 곳 모두):
```python
                # Pass static_system for cache key (dynamic parts must not affect cache hash)
                _cache_key_text = static_system if dynamic_parts else system_text
```

그리고 각 호출에서 `system_text` → `_cache_key_text`:
```python
                gen = stream_openai_to_anthropic(
                    config.ollama_host, openai_req, orig_model,
                    cache_layer if config.enable_cache else None,
                    _cache_key_text, approx_input,
                )
```
```python
                    resp_obj = non_streaming_response(
                        config.ollama_host, openai_req, orig_model,
                        cache_layer if config.enable_cache else None,
                        _cache_key_text, approx_input,
                    )
```

---

## Task 4: 병렬 MCP 도구 실행

**Files:**
- Modify: `bridge_proxy_full.py` — McpServerManager + do_POST

### 구현

- [ ] **Step 1: McpServerManager에 per-server 락 추가**

현재 `McpServerManager.__init__` (line 195):
```python
    def __init__(self):
        self._servers: dict[str, subprocess.Popen] = {}
        self._tool_registry: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._req_id = 0
```

변경 후:
```python
    def __init__(self):
        self._servers: dict[str, subprocess.Popen] = {}
        self._tool_registry: dict[str, dict] = {}
        self._lock = threading.Lock()          # protects registry
        self._server_locks: dict[str, threading.Lock] = {}  # per-server send lock
        self._req_id = 0
        self._req_id_lock = threading.Lock()
```

`add_server` 에 락 초기화 추가 (line 201, `self._servers[name] = proc` 직후):
```python
            self._servers[name] = proc
            self._server_locks[name] = threading.Lock()
```

- [ ] **Step 2: _next_id() 스레드 안전하게 변경**

기존 (line 217):
```python
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id
```

변경 후:
```python
    def _next_id(self) -> int:
        with self._req_id_lock:
            self._req_id += 1
            return self._req_id
```

- [ ] **Step 3: _send()에 per-server 락 적용**

기존 `_send` (line 221):
```python
    def _send(self, name: str, method: str, params: Any = None) -> Any:
        proc = self._servers.get(name)
        if proc is None or proc.poll() is not None:
            raise RuntimeError(f"MCP server '{name}' not running")
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        ...
        proc.stdin.write(line.encode())
        proc.stdin.flush()
        raw = proc.stdout.readline()
```

변경 후 — 서버별 락으로 직렬화:
```python
    def _send(self, name: str, method: str, params: Any = None) -> Any:
        proc = self._servers.get(name)
        if proc is None or proc.poll() is not None:
            raise RuntimeError(f"MCP server '{name}' not running")
        server_lock = self._server_locks.get(name, self._lock)
        with server_lock:
            msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
            if params is not None:
                msg["params"] = params
            line = json.dumps(msg) + "\n"
            proc.stdin.write(line.encode())
            proc.stdin.flush()
            raw = proc.stdout.readline()
            if not raw:
                raise RuntimeError(f"MCP server '{name}' closed stdout")
            resp = json.loads(raw.decode())
            if "error" in resp:
                raise RuntimeError(f"MCP error: {resp['error']}")
            return resp.get("result")
```

- [ ] **Step 4: call_tools_parallel() 메서드 추가**

`McpServerManager.call_tool()` 메서드(line 268) 바로 뒤에 추가:

```python
    def call_tools_parallel(
        self, tool_calls: list[tuple[str, dict]]
    ) -> list[tuple[str, str]]:
        """Execute multiple tool calls concurrently.

        Args:
            tool_calls: list of (tool_name, arguments) pairs

        Returns:
            list of (tool_name, result_text) pairs, in same order as input
        """
        import concurrent.futures

        results: list[tuple[str, str]] = [("", "")] * len(tool_calls)

        def _call(idx: int, tool_name: str, arguments: dict):
            result = self.call_tool(tool_name, arguments)
            results[idx] = (tool_name, result)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(tool_calls), 8), thread_name_prefix="mcp-tool"
        ) as executor:
            futures = [
                executor.submit(_call, i, name, args)
                for i, (name, args) in enumerate(tool_calls)
            ]
            for f in concurrent.futures.as_completed(futures):
                exc = f.exception()
                if exc:
                    log.warning("MCP parallel call error: %s", exc)

        return results
```

- [ ] **Step 5: do_POST에서 병렬 MCP 도구 실행 루프 추가**

`non_streaming_response` 결과 처리 블록에서 tool_use 블록이 있으면 MCP 도구를 병렬 실행하는 agentic 루프를 추가한다.

`VERIFICATION_AGENT` 블록(line 1350) 바로 앞에, `resp_obj = non_streaming_response(...)` 호출 직후에 삽입:

```python
                    # --- PARALLEL MCP TOOL EXECUTION LOOP ---
                    # If model returned tool_use blocks for known MCP tools,
                    # execute them in parallel and feed results back for a final answer.
                    if config.enable_mcp:
                        _mcp_tool_calls = [
                            (block["name"], block.get("input", {}), block.get("id", ""))
                            for block in resp_obj.get("content", [])
                            if block.get("type") == "tool_use"
                            and block.get("name", "").startswith("mcp__")
                        ]
                        if _mcp_tool_calls:
                            log.info(
                                "MCP PARALLEL: executing %d tool(s) concurrently: %s",
                                len(_mcp_tool_calls),
                                [t[0] for t in _mcp_tool_calls],
                            )
                            parallel_results = mcp.call_tools_parallel(
                                [(name, args) for name, args, _ in _mcp_tool_calls]
                            )
                            # Build follow-up messages with tool results
                            tool_result_msgs: list[dict] = []
                            for (tool_name, args, tool_id), (_, result_text) in zip(
                                _mcp_tool_calls, parallel_results
                            ):
                                tool_result_msgs.append({
                                    "role": "tool",
                                    "tool_call_id": tool_id,
                                    "content": result_text,
                                })
                            # Re-call model with tool results
                            followup_msgs = list(openai_req["messages"]) + [
                                {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": tool_id,
                                            "type": "function",
                                            "function": {"name": name, "arguments": json.dumps(args)},
                                        }
                                        for name, args, tool_id in _mcp_tool_calls
                                    ],
                                }
                            ] + tool_result_msgs
                            openai_req2 = dict(openai_req)
                            openai_req2["messages"] = followup_msgs
                            openai_req2.pop("tools", None)   # don't offer tools on follow-up
                            resp_obj = non_streaming_response(
                                config.ollama_host, openai_req2, orig_model,
                                None, _cache_key_text, approx_input,
                            )
                            log.info("MCP PARALLEL: follow-up response received")
```

- [ ] **Step 6: argparse에 tool-loop 비활성화 옵션 추가**

```python
    p.add_argument("--no-tool-loop", action="store_true",
                   help="Disable automatic MCP tool execution loop")
```

`ProxyConfig`에 필드 추가:
```python
    enable_tool_loop: bool = True
```

`main()` 의 `ProxyConfig(...)` 생성부에:
```python
        enable_tool_loop=not args.no_tool_loop,
```

핸들러에서 `if config.enable_mcp:` → `if config.enable_mcp and config.enable_tool_loop:` 로 수정.

---

## Task 5: 모듈 상단 docstring 및 기본 모델 업데이트

**Files:**
- Modify: `bridge_proxy_full.py` 상단 docstring (line 3-20)

- [ ] **Step 1: docstring 업데이트**

기존:
```python
"""
bridge_proxy_full.py — Full-featured Anthropic↔Ollama bridge proxy.
...
Features:
  - Prompt caching simulation (SHA256 + keep_alive=-1)
  ...
"""
```

변경 후:
```python
"""
bridge_proxy_full.py — Full-featured Anthropic↔Ollama bridge proxy.

Drop-in replacement for the Anthropic API endpoint. Set:
    export ANTHROPIC_BASE_URL=http://localhost:9099
    export ANTHROPIC_API_KEY=local

Default model: qwen3:8b (supports native thinking/reasoning mode)

Features:
  - Prompt caching simulation (SHA256 + keep_alive=-1, cache boundary marker)
  - MCP server integration (stdio JSON-RPC 2.0, parallel tool execution)
  - Vector RAG via nomic-embed-text (pure-Python, no numpy)
  - KAIROS daemon (background file watcher, PROACTIVE tick)
  - COORDINATOR_MODE (sequential task decomposition)
  - TRANSCRIPT_CLASSIFIER (pattern-based risk scoring, auto-approve)
  - ULTRAPLAN (complexity detection → planning phase injection)
  - VERIFICATION_AGENT (secondary model verification)
  - TEAMMEM (persistent session memory across requests)
  - LocalModelOptimizer (CoT forcing, Qwen3 thinking mode, retry)
  - ConversationCompactor (summarize old turns, keep context within limits)
"""
```

---

## Task 6: run_full_bridge.sh 업데이트

**Files:**
- Modify: `run_full_bridge.sh`

- [ ] **Step 1: 기본 모델 및 features 표시 변경**

line 10 변경:
```bash
PRIMARY_MODEL="${PRIMARY_MODEL:-qwen3:8b}"
```

line 141-149 features 표시 블록 변경:
```bash
echo "Features enabled:"
echo "  ✓ Prompt cache simulation (static/dynamic boundary)"
echo "  ✓ Vector RAG (nomic-embed-text)"
echo "  ✓ KAIROS background watcher"
echo "  ✓ COORDINATOR_MODE (task decomposition)"
echo "  ✓ TRANSCRIPT_CLASSIFIER (safety scoring)"
echo "  ✓ ULTRAPLAN (complexity detection)"
echo "  ✓ TEAMMEM (persistent memory)"
echo "  ✓ Context Compaction (auto-summarize long sessions)"
echo "  ✓ Qwen3 Thinking Mode (/think activation)"
echo "  ✓ Parallel MCP Tool Execution"
echo "  ○ VERIFICATION_AGENT (disabled by default, add --enable-verification)"
echo ""
echo "Model: $PRIMARY_MODEL"
echo "  For smaller RAM: PRIMARY_MODEL=qwen3:4b $0"
echo "  For max quality: PRIMARY_MODEL=qwen3:32b $0"
```

- [ ] **Step 2: 워밍업 시 thinking 모드 확인 안내 추가**

line 85-90 (warm-up 블록) 뒤에 추가:
```bash
# Check if model supports thinking
if echo "$PRIMARY_MODEL" | grep -qi "qwen3\|deepseek-r1\|qwq"; then
    info "Thinking mode: ENABLED (model supports native reasoning)"
    info "  Use /no_think prefix to disable per-request, --no-thinking to disable globally"
else
    info "Thinking mode: DISABLED (model does not support native reasoning)"
fi
```

---

## Task 7: 문서 업데이트

**Files:**
- Modify: `README.md`, `BRIDGE_FULL_GUIDE.md`, `CLAUDE_CODE_LOCAL_OLLAMA_BRIDGE.md`

- [ ] **Step 1: README.md — 기본 모델 및 신규 기능 반영**

`README.md`를 읽고 다음을 찾아 업데이트:
- `qwen2.5-coder` 언급 → `qwen3:8b` (또는 `qwen3`)로 교체
- Features 목록에 4가지 신규 기능 추가:
  - Context Compaction (auto-summarize conversations > 24K tokens)
  - Qwen3 Thinking Mode (native reasoning via /think)
  - Cache Boundary Marker (static/dynamic system prompt split)
  - Parallel MCP Tool Execution

- [ ] **Step 2: BRIDGE_FULL_GUIDE.md — 신규 기능 섹션 추가**

`BRIDGE_FULL_GUIDE.md`를 읽고 각 신규 기능에 대한 설명 섹션 추가:

```markdown
## Qwen3 Thinking Mode

qwen3 계열 모델(qwen3:8b, qwen3:14b, qwen3:32b)은 내장 추론 모드를 지원합니다.
브릿지는 자동으로 `/think` prefix를 삽입해 추론 모드를 활성화하고,
응답에서 `<think>...</think>` 블록을 제거 후 최종 답변만 반환합니다.

| 옵션 | 설명 |
|---|---|
| `--no-thinking` | thinking 모드 비활성화 |
| `--thinking-budget N` | thinking 최대 토큰 (기본: 8192) |

추론 없이 실행: `PRIMARY_MODEL=qwen3:8b-instruct` (`:nothink` 변형 사용)

## Context Compaction

대화 히스토리가 24,000 토큰을 초과하면 자동으로 이전 대화를 요약합니다.
최근 4턴(8메시지)는 원본 그대로 유지하고, 나머지는 요약 블록으로 압축합니다.

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--no-compaction` | — | 비활성화 |
| `--compaction-max-tokens` | 24000 | 트리거 임계값 |

## Cache Boundary Marker

시스템 프롬프트를 정적(사용자 지정) / 동적(RAG, KAIROS, ULTRAPLAN, TEAMMEM) 부분으로 분리합니다.
캐시 해시는 정적 부분만 계산하므로, RAG 컨텍스트가 바뀌어도 캐시 히트가 유지됩니다.

경계 마커: `<!-- BRIDGE:DYNAMIC_START -->`

## Parallel MCP Tool Execution

모델이 한 번에 여러 MCP 도구를 호출할 때, `ThreadPoolExecutor`로 병렬 실행합니다.
도구 결과를 모아 모델에 다시 전달하는 agentic loop를 내장합니다 (1라운드).

| 옵션 | 설명 |
|---|---|
| `--no-tool-loop` | 자동 MCP 도구 실행 루프 비활성화 |
```

- [ ] **Step 3: CLAUDE_CODE_LOCAL_OLLAMA_BRIDGE.md — qwen3 설치 가이드**

`CLAUDE_CODE_LOCAL_OLLAMA_BRIDGE.md`를 읽고 Quick Start 섹션을 qwen3 기준으로 업데이트:

```markdown
## 빠른 시작 (Qwen3)

### 1. Ollama 설치
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### 2. 모델 다운로드

| RAM | 권장 모델 | 명령 |
|---|---|---|
| 8GB | qwen3:4b | `ollama pull qwen3:4b` |
| 16GB | qwen3:8b (기본값) | `ollama pull qwen3:8b` |
| 32GB | qwen3:14b | `ollama pull qwen3:14b` |
| 64GB+ | qwen3:32b | `ollama pull qwen3:32b` |

```bash
# 기본값 (16GB RAM 기준)
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

### 3. 브릿지 실행
```bash
./run_full_bridge.sh
# 또는 모델 지정:
PRIMARY_MODEL=qwen3:14b ./run_full_bridge.sh
```

### 4. Claude Code 연결
```bash
export ANTHROPIC_BASE_URL=http://localhost:9099
export ANTHROPIC_API_KEY=local-ollama-bridge
claude
```
```

---

## Self-Review

### Spec Coverage
- [x] Context Compaction → Task 2
- [x] Qwen3 추론모드 활성화 → Task 1
- [x] 캐시 경계 마커 도입 → Task 3
- [x] 병렬 MCP 도구 실행 → Task 4
- [x] 문서 업데이트 → Tasks 5-7
- [x] 기본 모델 qwen3:8b → Task 1 Step 8, Task 6

### Placeholder Scan
- 모든 코드 블록이 실제 구현 코드를 포함함
- TBD/TODO 없음

### Type Consistency
- `ConversationCompactor` — Task 2 Step 2에서 정의, Step 3-6에서 동일 이름 사용
- `is_thinking_model()` — Task 1 Step 2에서 정의, Step 4에서 호출
- `apply_thinking_mode()` / `strip_thinking_tags()` — Task 1 Step 3에서 정의, Steps 5-6에서 호출
- `call_tools_parallel()` — Task 4 Step 4에서 정의, Step 5에서 호출
- `_CACHE_BOUNDARY` — Task 3 Step 1에서 정의, Step 3에서 사용
- `static_system` / `dynamic_parts` / `_cache_key_text` — Task 3 Step 3-4에서 일관되게 사용
