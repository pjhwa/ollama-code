# Bridge Proxy — Full Guide

`bridge_proxy_full.py` is a drop-in replacement for the Anthropic Messages API endpoint.  
Claude Code CLI (or any Anthropic SDK client) works unchanged; requests are transparently routed to a local Ollama model.

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Architecture](#2-architecture)
3. [API Translation](#3-api-translation)
4. [Features In Detail](#4-features-in-detail)
5. [Model Selection](#5-model-selection)
6. [CLI Reference](#6-cli-reference)
7. [Environment Variables](#7-environment-variables)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Quick Start

### Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:14b
ollama pull nomic-embed-text   # RAG embedding model
```

### Index your codebase (RAG)

```bash
# Index current directory
python3 rag_indexer.py index --dirs .

# Check index statistics
python3 rag_indexer.py stats
```

### Start the bridge

```bash
./run_full_bridge.sh
```

### Configure Claude Code (separate terminal)

```bash
source ./env.sh
claude
```

`env.sh` sets three environment variables:

```bash
export ANTHROPIC_BASE_URL=http://localhost:9099
export ANTHROPIC_API_KEY=local-ollama-bridge
export ANTHROPIC_CUSTOM_MODEL_OPTION=qwen3:14b   # skips Claude model validation
```

> **Important:** Use `source ./env.sh` (not `./env.sh`).  
> Running directly sets variables in a subshell that exits immediately — the parent shell never sees them.

---

## 2. Architecture

```
Claude Code CLI
      │  Anthropic Messages API (POST /v1/messages)
      ▼
bridge_proxy_full.py  (port 9099)
      │
      ├── PromptCacheLayer    — SHA256 hash, keep_alive=-1
      ├── RagContextInjector  — nomic-embed-text, cosine similarity
      ├── KairosDaemon        — background file watcher
      ├── TranscriptClassifier— risk scoring 0-10
      ├── UltraPlan           — complexity detection → plan injection
      ├── CoordinatorMode     — multi-task decomposition
      ├── ConversationCompactor — context window management
      ├── TeamMemory          — persistent JSON k/v store
      ├── McpServerManager    — stdio MCP tool bridge
      └── LocalModelOptimizer — CoT forcing, retry logic
      │
      │  Ollama native /api/chat
      ▼
Ollama (localhost:11434)
      └── qwen3:14b  (or any model)
```

### Request flow

1. Claude Code CLI sends `POST /v1/messages` (may include `?beta=true` — the query string is stripped).
2. Bridge validates the request. Validation fast-path: if `max_tokens ≤ 3` or `querySource=model_validation`, return a stub immediately without calling Ollama.
3. Context enrichment: RAG injection → KAIROS tick → TEAMMEM block → cache boundary split.
4. TRANSCRIPT_CLASSIFIER scores user text (0–10). Requests above threshold (default 4.0) are rejected.
5. ULTRAPLAN: if user text ≥ 120 chars and matches complex keywords, generate a step-by-step plan and inject it into the system prompt.
6. COORDINATOR_MODE: if user text has ≥ 2 sequencing keywords and length > 200 chars, decompose into subtasks and run sequentially.
7. LocalModelOptimizer builds the final Ollama request (model, options, thinking mode).
8. Response is streamed back in Anthropic SSE format, or returned as a single JSON object.

---

## 3. API Translation

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/messages` | POST | Main chat completion endpoint |
| `/v1/models` | GET | Returns Anthropic-format model list |
| `/v1/health` | GET | Health check (`{"status":"ok"}`) |

`/v1/models` returns the primary model plus these Claude aliases so Claude Code model validation passes:

- `claude-opus-4-6`
- `claude-sonnet-4-6`
- `claude-haiku-4-5-20251001`
- `claude-haiku-4-5`

### Protocol differences

| Anthropic | Ollama | Notes |
|-----------|--------|-------|
| `POST /v1/messages` | `/api/chat` | Bridge translates format |
| `system` field | `messages[0].role=system` | Moved into message array |
| `content: [{type:"text"}]` | `content: "string"` | Blocks flattened to string |
| `tool_use` / `tool_result` | Tool call format | Converted both ways |
| SSE `event: content_block_delta` | `stream:true` JSON lines | Re-chunked |
| `cache_control` blocks | Ignored (simulated) | SHA256 + keep_alive=-1 |

---

## 4. Features In Detail

### Prompt Cache Simulation

The Anthropic API charges less for cached prompt prefixes. The bridge simulates this:

- The system prompt is split at `<!-- BRIDGE:DYNAMIC_START -->` into a **static** part (stable, hashed) and a **dynamic** part (RAG context, KAIROS findings — changes per request).
- The SHA256 hash of the static part is used as a cache key. On a hit, the response reports `cache_read_input_tokens`.
- `keep_alive=-1` keeps the model loaded in memory between requests, which is the actual performance benefit.

### Vector RAG

- Embeddings via `nomic-embed-text` (512-token limit; query text truncated to 1 500 chars before embedding).
- Tries `/api/embed` first (Ollama ≥ 0.1.26, `input:` field), falls back to `/api/embeddings` (legacy, `prompt:` field).
- Cosine similarity in pure Python (no numpy).
- Top-K results (default 5) above similarity threshold (default 0.30) are injected after `<!-- BRIDGE:DYNAMIC_START -->`.
- RAG index is built by `rag_indexer.py` and stored in `.bridge_rag_index.json`.

### KAIROS Daemon

A background thread that watches configured directories every 30 seconds (configurable via `--kairos-interval`).

- Detects modified files since last tick.
- Re-indexes changed files into the RAG store.
- Injects a `## KAIROS Findings` block into the system prompt with up to 5 changed-file summaries.

### TRANSCRIPT_CLASSIFIER

Pattern-based risk scorer (0–10 scale). Runs on the user's text before calling Ollama.

- Safe patterns (e.g., `read`, `list`, `show`) reduce the score.
- Risk patterns (e.g., `delete`, `drop`, `rm -rf`, `sudo`) increase the score.
- Requests with score ≥ threshold (default 4.0) are rejected with HTTP 400.
- Use `--no-classifier` to disable.

### ULTRAPLAN

Detects complex requests and injects a pre-computed plan.

**Triggers when:**
- User text length ≥ 120 characters, AND
- Text matches at least one complex keyword: `refactor`, `migrate`, `implement`, `build`, `create`, `debug`, `fix.*bug`, `write.*tests`, `add.*feature`, etc.

**What it does:**
- Calls Ollama via `_ollama_chat_fast()` (native `/api/chat`, `think:false`) with a 512-token limit.
- Injects the resulting plan under `## ULTRAPLAN — Pre-computed Implementation Plan` in the system prompt.

> Note: ULTRAPLAN checks the full user text including any hook-injected context (e.g., superpowers skill context). This is intentional — hook context that triggers the keywords means the request is genuinely complex.

### COORDINATOR_MODE

For requests that clearly contain multiple sequential tasks:

**Triggers when:**
- ≥ 2 sequencing keywords (`first`, `then`, `after`, `finally`, `next`, `also`, `additionally`), AND
- User text length > 200 characters.

**What it does:**
- Calls Ollama to decompose the request into up to 4 subtasks (JSON array).
- Runs subtasks sequentially, feeding each result as context into the next.
- Returns the accumulated result.

### VERIFICATION_AGENT

Disabled by default (enable with `--enable-verification`).

- After the primary response is generated, calls Ollama a second time to verify quality.
- Uses the same model as primary (or `--verify-model` if specified).
- Only activates for responses ≥ 200 tokens.

### TEAMMEM

Persistent JSON key-value store (`.bridge_memory.json`).

- The model can write memories that persist across sessions.
- The last 20 entries are injected into each system prompt as `## Persistent Memory (TEAMMEM)`.
- Disable with `--no-teammem`.

### Context Compaction

Automatically summarizes old conversation history when the session grows too long.

| Setting | Default | Description |
|---------|---------|-------------|
| `compaction_max_tokens` | 24 000 | Token count that triggers compaction |
| `compaction_target_tokens` | 8 000 | Target token count after compaction |
| `compaction_min_turns` | 6 | Minimum turns before compaction can run |

When triggered:
- Keeps the system prompt and last 8 non-system messages intact.
- Summarizes older turns via `_ollama_chat_fast()`.
- Replaces the summarized turns with a single `## Conversation Summary` message.

### Qwen3 Thinking Mode

For models in the thinking list (`qwen3`, `deepseek-r1`, `qwq`, `marco-o1`):

- Sends requests via `/api/chat` (native Ollama endpoint) with `think:true` and `thinking_budget_tokens: 8192`.
- Strips `<think>...</think>` blocks from the final response before sending to the client.
- Disable with `--no-thinking`.

**All internal meta-calls** (ULTRAPLAN, COORDINATOR, compaction, verification) use `_ollama_chat_fast()` which forces `think:false` at the top level of the native `/api/chat` request. This prevents thinking mode from adding latency to internal operations.

### MCP Tool Integration

- MCP servers configured via `--mcp-server CMD [ARG...]`.
- Tools are discovered at startup via `tools/list` JSON-RPC call.
- When the model returns tool calls, the bridge executes them and feeds results back in an agentic loop (up to 10 iterations by default).
- Multiple MCP tool calls in the same response are executed in parallel via `ThreadPoolExecutor`.
- Per-server locking prevents concurrent requests to the same server process.

---

## 5. Model Selection

| Model | RAM needed | Use case |
|-------|-----------|----------|
| `qwen3:4b` | ~3 GB | Fast responses; 8 GB machines |
| `qwen3:8b` | ~6 GB | Good balance |
| `qwen3:14b` | ~11 GB | **Default** — recommended for coding tasks |
| `qwen3:32b` | ~22 GB | Best quality; needs 64 GB+ RAM |

Override the default model:

```bash
PRIMARY_MODEL=qwen3:8b ./run_full_bridge.sh
```

Or:

```bash
./run_full_bridge.sh --model qwen3:8b
```

---

## 6. CLI Reference

```
python3 bridge_proxy_full.py [OPTIONS]
```

### Server

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Bind host |
| `--port` | `9099` | Bind port |
| `--ollama` | `http://localhost:11434` | Ollama base URL |
| `--model` | `qwen3:14b` | Primary Ollama model |
| `--embed-model` | `nomic-embed-text` | Embedding model |
| `--verify-model` | *(primary model)* | Model for verification pass |
| `--verbose`, `-v` | off | Enable debug logging |

### Feature flags

| Flag | Description |
|------|-------------|
| `--no-cache` | Disable prompt cache simulation |
| `--no-rag` | Disable vector RAG |
| `--no-mcp` | Disable MCP servers |
| `--no-kairos` | Disable KAIROS daemon |
| `--no-coordinator` | Disable COORDINATOR_MODE |
| `--no-classifier` | Disable TRANSCRIPT_CLASSIFIER |
| `--no-ultraplan` | Disable ULTRAPLAN |
| `--enable-verification` | Enable VERIFICATION_AGENT (off by default) |
| `--no-tool-loop` | Disable agentic MCP tool loop |
| `--no-teammem` | Disable persistent team memory |
| `--no-compaction` | Disable context compaction |
| `--no-thinking` | Disable thinking mode for Qwen3/DeepSeek-R1 |

### Tuning

| Flag | Default | Description |
|------|---------|-------------|
| `--compaction-max-tokens` | `24000` | Token count that triggers compaction |
| `--rag-index` | `.bridge_rag_index.json` | RAG index file path |
| `--rag-dirs` | `.` | Directories to watch/index |
| `--rag-top-k` | `5` | Number of RAG results to inject |
| `--rag-threshold` | `0.30` | Minimum cosine similarity for RAG results |
| `--kairos-interval` | `30` | KAIROS tick interval in seconds |
| `--teammem-path` | `.bridge_memory.json` | TeamMemory file path |
| `--thinking-budget` | `8192` | Max thinking tokens |
| `--mcp-server CMD...` | — | Add MCP server (repeatable) |
| `--index-now` | off | Build RAG index on startup |

### `run_full_bridge.sh` environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama URL |
| `PRIMARY_MODEL` | `qwen3:14b` | Model to use |
| `EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `PROXY_PORT` | `9099` | Bridge listen port |
| `RAG_DIRS` | `.` | Directories to index |
| `INDEX_ON_START` | `false` | Set `true` to rebuild RAG index on each start |
| `BRIDGE_LOG` | `/tmp/bridge.log` | Log file path |
| `BRIDGE_VERBOSE` | `false` | Set `true` for debug logging |

---

## 7. Environment Variables

Set these in your Claude Code terminal (or use `source ./env.sh`):

```bash
export ANTHROPIC_BASE_URL=http://localhost:9099
export ANTHROPIC_API_KEY=local-ollama-bridge
export ANTHROPIC_CUSTOM_MODEL_OPTION=qwen3:14b
```

`ANTHROPIC_CUSTOM_MODEL_OPTION` tells Claude Code CLI to skip its model validation check for this exact model name. Without it, the CLI attempts to verify `qwen3:14b` against the Anthropic API and rejects it at session start.

---

## 8. Troubleshooting

### Bridge starts but Claude Code shows "model not found"

Make sure `ANTHROPIC_CUSTOM_MODEL_OPTION` matches the model name exactly:

```bash
export ANTHROPIC_CUSTOM_MODEL_OPTION=qwen3:14b
```

### `source ./env.sh` vs `./env.sh`

`./env.sh` runs in a subshell. All `export` commands disappear when the subshell exits. Always use:

```bash
source ./env.sh
# or
. ./env.sh
```

### RAG embedding fails (HTTP 500)

The bridge tries `/api/embed` first (Ollama ≥ 0.1.26) and falls back to `/api/embeddings` (legacy). If both fail, check that `nomic-embed-text` is pulled:

```bash
ollama pull nomic-embed-text
```

### ULTRAPLAN or COORDINATOR taking too long

Both features use `_ollama_chat_fast()` which calls the native `/api/chat` endpoint with `think:false`. If they are still slow:

1. Check that `--no-thinking` is NOT set (it would route to `/v1/chat/completions` which may not honour `think:false`).
2. Increase the timeout or reduce the model size.
3. Disable them: `--no-ultraplan --no-coordinator`.

### ULTRAPLAN triggers on every request

ULTRAPLAN checks full user text including hook-injected context (e.g., superpowers skill prompts). This is intentional — if hook context contains complex keywords, the request is genuinely complex. To raise the threshold or disable:

```bash
./run_full_bridge.sh --no-ultraplan
```

### Ollama not found / not running

The startup script (`run_full_bridge.sh`) will attempt to start Ollama automatically if the binary is in `PATH`. If it cannot be found, install it:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

To use a remote Ollama instance:

```bash
export OLLAMA_HOST=http://192.168.1.100:11434
./run_full_bridge.sh
```

### BrokenPipeError in logs

This is benign. It means the client disconnected while the bridge was sending a long response (e.g., after a COORDINATOR run). The bridge catches this and logs a warning; no action required.

### Viewing logs

```bash
tail -f /tmp/bridge.log
```

Set `BRIDGE_VERBOSE=true` for debug-level output:

```bash
BRIDGE_VERBOSE=true ./run_full_bridge.sh
```
