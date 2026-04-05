# ollama-code

**Local AI coding agent powered by Ollama — drop-in replacement for the Anthropic API.**

Use Claude Code CLI (or any Anthropic SDK client) unchanged, with a local Ollama model instead of paying for API calls.

```bash
export ANTHROPIC_BASE_URL=http://localhost:9099
export ANTHROPIC_API_KEY=local-ollama-bridge
claude   # Claude Code CLI, now running on local Ollama
```

---

## Features

| Feature | Description |
|---------|-------------|
| **Full API bridge** | Translates Anthropic Messages API ↔ Ollama OpenAI-compat format |
| **Prompt cache simulation** | SHA256 hash + `keep_alive=-1` keeps model warm, reports cache tokens |
| **Vector RAG** | nomic-embed-text embeddings, pure-Python cosine similarity, auto-injected context |
| **MCP integration** | stdio JSON-RPC 2.0 MCP servers, tools auto-discovered and forwarded |
| **KAIROS daemon** | Background file watcher, auto re-indexes changed files (PROACTIVE mode) |
| **COORDINATOR_MODE** | Sequential task decomposition for multi-part requests |
| **TRANSCRIPT_CLASSIFIER** | Pattern-based risk scoring (0–10), blocks dangerous requests |
| **ULTRAPLAN** | Generates step-by-step plan before coding complex requests |
| **VERIFICATION_AGENT** | Optional secondary model pass for quality verification |
| **TEAMMEM** | Persistent memory across sessions (`.bridge_memory.json`) |
| **Qwen3 Thinking Mode** | Auto-enables native reasoning for qwen3/deepseek-r1/qwq models; strips `<think>` tags |
| **Context Compaction** | Auto-summarizes history when it exceeds 24K tokens; keeps last 4 turns intact |
| **Cache Boundary Marker** | Splits system prompt into static/dynamic parts; cache hash from static only |
| **Parallel MCP Tool Execution** | Runs multiple `mcp__*` tool calls concurrently via ThreadPoolExecutor |

---

## Quick Start

```bash
# 1. Install Ollama — https://ollama.com
ollama pull qwen3:14b
ollama pull nomic-embed-text

# 2. Index your codebase
python3 rag_indexer.py index --dirs .

# 3. Start the bridge
./run_full_bridge.sh

# 4. Configure Claude Code
export ANTHROPIC_BASE_URL=http://localhost:9099
export ANTHROPIC_API_KEY=local-ollama-bridge
claude
```

---

## Files

| File | Description |
|------|-------------|
| `bridge_proxy_full.py` | Main bridge proxy server (~1800 lines, stdlib only) |
| `rag_indexer.py` | Standalone RAG indexer CLI |
| `run_full_bridge.sh` | Startup script with dependency checks |
| `BRIDGE_FULL_GUIDE.md` | Full usage guide, architecture, CLI reference, troubleshooting |

---

## Requirements

- Python 3.9+ (stdlib only — no pip install needed)
- [Ollama](https://ollama.com) running locally
- Recommended: Mac Mini M4 (32GB) or any machine with 16GB+ RAM

## Recommended Models

| Model | RAM | Notes |
|-------|-----|-------|
| `qwen3:4b` | ~3 GB | Fast, 8GB RAM machines |
| `qwen3:8b` | ~6 GB | Good balance of speed and quality |
| `qwen3:14b` | ~11 GB | **Default** — best quality/speed ratio, 32GB RAM recommended |
| `qwen3:32b` | ~22 GB | Max quality, 64GB RAM recommended |

---

## License

MIT
