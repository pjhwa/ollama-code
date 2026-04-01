# Full Bridge Proxy Guide

`bridge_proxy_full.py` is a drop-in replacement for the Anthropic API that routes Claude Code traffic to a local Ollama model, with a suite of engineering features to maximize local model coding performance.

---

## Quick Start

```bash
# 1. Install Ollama and pull models
ollama pull qwen2.5-coder:14b
ollama pull nomic-embed-text

# 2. Build the RAG index for your codebase
python3 rag_indexer.py index --dirs .

# 3. Start the bridge
./run_full_bridge.sh

# 4. In another terminal, configure Claude Code
export ANTHROPIC_BASE_URL=http://localhost:9099
export ANTHROPIC_API_KEY=local-ollama-bridge
claude   # or: claude --model qwen2.5-coder:14b
```

---

## Architecture

```
Claude Code CLI
      │
      │  Anthropic Messages API (HTTP)
      ▼
┌─────────────────────────────────────────────────────────┐
│                  bridge_proxy_full.py                    │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │PromptCache   │  │ TeamMemory   │  │ TranscriptClf │ │
│  │(SHA256+warm) │  │(persist JSON)│  │(risk scoring) │ │
│  └──────────────┘  └──────────────┘  └───────────────┘ │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │ RAGInjector  │  │ KairosDaemon │  │ UltraPlan     │ │
│  │(nomic-embed) │  │(file watcher)│  │(plan phase)   │ │
│  └──────────────┘  └──────────────┘  └───────────────┘ │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │ McpManager   │  │ Coordinator  │  │ Verification  │ │
│  │(stdio JSONRPC│  │(decompose)   │  │(2nd model)    │ │
│  └──────────────┘  └──────────────┘  └───────────────┘ │
│                                                          │
│               format translation                         │
│         Anthropic ↔ OpenAI (Ollama-compat)              │
└─────────────────────────────────────────────────────────┘
                        │
                        │  OpenAI /v1/chat/completions
                        ▼
                   Ollama (local)
              qwen2.5-coder:14b (or any)
```

---

## Features

### 1. Prompt Cache Simulation

Tracks repeated system prompts via SHA256 hash. Uses `keep_alive=-1` to keep the model loaded in RAM (Mac Mini M4 UMA). Reports `cache_creation_input_tokens` and `cache_read_input_tokens` in usage — Claude Code's cost display will reflect this correctly.

**Effect:** Eliminates model reload latency on repeated calls. ~0ms overhead.

---

### 2. Vector RAG (nomic-embed-text)

Indexes your codebase into 500-char overlapping chunks, embedded via `nomic-embed-text`. On each request, the user's message is embedded and the top-K most similar chunks are injected into the system prompt.

**Pure Python** — no numpy, no external dependencies beyond Ollama.

```bash
# Build index
python3 rag_indexer.py index --dirs . src/ tests/

# Check index stats
python3 rag_indexer.py stats

# Test a query
python3 rag_indexer.py query "how does authentication work" --top-k 5 --show-text

# Clear and rebuild
python3 rag_indexer.py clear
python3 rag_indexer.py index --force
```

**Config flags:**
- `--rag-top-k 5` — number of chunks to inject
- `--rag-threshold 0.30` — minimum cosine similarity
- `--rag-dirs . src/` — directories to search
- `--no-rag` — disable entirely

---

### 3. MCP Server Integration

Connects to MCP (Model Context Protocol) servers via stdio JSON-RPC 2.0. Tools are auto-discovered and injected into every request as Anthropic-format tool definitions.

```bash
# Start with an MCP server (filesystem server example)
./run_full_bridge.sh \
    --mcp-server filesystem npx @modelcontextprotocol/server-filesystem /path/to/dir

# Multiple servers
./run_full_bridge.sh \
    --mcp-server files npx @modelcontextprotocol/server-filesystem . \
    --mcp-server git npx @modelcontextprotocol/server-git .
```

MCP tool names are exposed as `mcp__<server>__<tool>` (e.g. `mcp__files__read_file`).

---

### 4. KAIROS Daemon

Background thread (30s tick) that watches indexed directories for file changes and automatically re-indexes modified files. Changed files are reported to the model in the next request via a `## KAIROS Background Findings` block in the system prompt.

This implements the PROACTIVE awareness loop: the model always has up-to-date context about recent edits.

**Config:**
- `--kairos-interval 30` — tick interval in seconds
- `--no-kairos` — disable

---

### 5. COORDINATOR_MODE

Detects multi-part requests (by counting temporal connective words: "first", "then", "also", "additionally", etc.). Decomposes complex requests into sequential subtasks, runs them one by one, accumulating context between subtasks, then combines results.

**Effect:** Better performance on "do X, then Y, then Z" requests. Slower (multiple model calls) but higher quality.

**Config:** `--no-coordinator` to disable.

---

### 6. TRANSCRIPT_CLASSIFIER

Scores every request 0–10 for risk using pattern matching (rm -rf, DROP TABLE, curl|bash, eval(), hardcoded secrets, etc.). Requests above threshold 4.0 are blocked with HTTP 403.

Safe-intent patterns (explain, how, what is, list, show) reduce the risk score.

**Config:** `--no-classifier` to disable (not recommended for shared deployments).

Threshold is set at compile time via `ProxyConfig.classifier_auto_approve_threshold = 4.0`.

---

### 7. ULTRAPLAN

Detects complex requests by keyword matching (refactor, migrate, implement, build, optimize, etc.) plus minimum length (120 chars). For matching requests, generates a numbered implementation plan via a separate model call, then injects it as `## ULTRAPLAN — Pre-computed Implementation Plan` in the system prompt.

**Effect:** Guides the model to think before coding. Adds ~5-15s latency on complex requests.

**Config:** `--no-ultraplan` to disable.

---

### 8. VERIFICATION_AGENT

*(Off by default — adds latency)*

After generating a response, calls the same model with a reviewer prompt asking PASS/FAIL on whether the response correctly addresses the request. On FAIL, retries the request with the feedback injected. Only triggers for responses ≥ 200 tokens.

**Config:** `--enable-verification` to activate.

---

### 9. TEAMMEM (Persistent Memory)

Persists key/value pairs to `.bridge_memory.json`. Auto-saves context when the user asks to "remember" something. The last 20 entries are injected into every system prompt.

**Effect:** Memory survives process restarts. The model has context about past sessions.

```bash
# In a Claude Code session:
# "remember that we use poetry for package management"
# → saved to .bridge_memory.json
# Next session: injected automatically
```

**Config:** `--no-teammem`, `--teammem-path custom.json`

---

## Model Recommendations (Mac Mini M4 32GB)

| Model | Size | Use Case |
|-------|------|----------|
| `qwen2.5-coder:7b` | ~5.8 GB | Fast, good for small files |
| `qwen2.5-coder:14b` | ~10.6 GB | **Recommended** — best speed/quality |
| `qwen2.5-coder:32b-q3_K_M` | ~17.1 GB | Max quality, slower |
| `deepseek-coder-v2:16b` | ~11 GB | Alternative, strong at reasoning |

Leave ~12 GB for macOS + other processes. `qwen2.5-coder:14b` + `nomic-embed-text` (~274 MB) = ~11 GB total.

---

## Full CLI Reference

```
python3 bridge_proxy_full.py [OPTIONS]

Core:
  --host HOST              Bind host (default: 0.0.0.0)
  --port PORT              Bind port (default: 9099)
  --ollama URL             Ollama base URL (default: http://localhost:11434)
  --model MODEL            Primary model (default: qwen2.5-coder:14b)
  --embed-model MODEL      Embedding model (default: nomic-embed-text)
  --verify-model MODEL     Verification model (default: same as primary)

Feature toggles:
  --no-cache               Disable prompt cache simulation
  --no-rag                 Disable vector RAG
  --no-mcp                 Disable MCP server integration
  --no-kairos              Disable KAIROS file watcher
  --no-coordinator         Disable COORDINATOR_MODE
  --no-classifier          Disable TRANSCRIPT_CLASSIFIER
  --no-ultraplan           Disable ULTRAPLAN
  --enable-verification    Enable VERIFICATION_AGENT (slow)
  --no-teammem             Disable persistent memory

RAG options:
  --rag-index PATH         Index file (default: .bridge_rag_index.json)
  --rag-dirs DIR...        Watch directories (default: .)
  --rag-top-k N            Top-K chunks to inject (default: 5)
  --rag-threshold FLOAT    Min cosine similarity (default: 0.30)
  --index-now              Re-index on startup

KAIROS:
  --kairos-interval SECS   Tick interval (default: 30)

Memory:
  --teammem-path PATH      Memory file (default: .bridge_memory.json)

MCP:
  --mcp-server NAME CMD... Add MCP server (repeatable)

Other:
  -v, --verbose            Enable debug logging
```

---

## Troubleshooting

**"Connection refused" from Claude Code:**
```bash
curl http://localhost:9099/health
# Should return: {"status": "ok", "bridge": "bridge_proxy_full"}
```

**Model not found:**
```bash
ollama list
ollama pull qwen2.5-coder:14b
```

**RAG returning no results:**
```bash
python3 rag_indexer.py stats          # check index size
python3 rag_indexer.py query "test"   # try a simple query
python3 rag_indexer.py index --force  # rebuild from scratch
```

**Out of memory:**
Switch to a smaller model or use Q3_K_M quantization:
```bash
PRIMARY_MODEL=qwen2.5-coder:7b ./run_full_bridge.sh
```

**Request blocked by classifier:**
The request contains dangerous patterns. Check the risk score:
```python
# In Python:
from bridge_proxy_full import TranscriptClassifier
clf = TranscriptClassifier(4.0)
score, reasons = clf.score("your request here")
print(score, reasons)
```

---

## Environment Variables

```bash
export OLLAMA_HOST=http://localhost:11434
export PRIMARY_MODEL=qwen2.5-coder:14b
export EMBED_MODEL=nomic-embed-text
export PROXY_PORT=9099
export RAG_DIRS=". src/ tests/"
export INDEX_ON_START=true    # re-index every time run_full_bridge.sh starts

# For Claude Code:
export ANTHROPIC_BASE_URL=http://localhost:9099
export ANTHROPIC_API_KEY=local-ollama-bridge
```

---

## Files Created

| File | Description |
|------|-------------|
| `bridge_proxy_full.py` | Main bridge proxy (~900 lines) |
| `rag_indexer.py` | Standalone RAG indexer CLI |
| `run_full_bridge.sh` | Startup script with dependency checks |
| `.bridge_rag_index.json` | Vector index (created on first index run) |
| `.bridge_memory.json` | TEAMMEM persistent store (created at first save) |

---

## Related Documents

- `MAC_MINI_M4_OLLAMA_AGENT.md` — standalone agent without Claude Code CLI
- `CLAUDE_CODE_LOCAL_OLLAMA_BRIDGE.md` — simpler bridge without advanced features
