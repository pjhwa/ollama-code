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

---

## Quick Start

```bash
# 1. Install Ollama — https://ollama.com
ollama pull qwen2.5-coder:14b
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
| `bridge_proxy_full.py` | Main bridge proxy server (~570 lines, stdlib only) |
| `rag_indexer.py` | Standalone RAG indexer CLI |
| `run_full_bridge.sh` | Startup script with dependency checks |
| `BRIDGE_FULL_GUIDE.md` | Full usage guide, CLI reference, troubleshooting |
| `MAC_MINI_M4_OLLAMA_AGENT.md` | Standalone agent (no Claude Code dependency) |
| `CLAUDE_CODE_LOCAL_OLLAMA_BRIDGE.md` | API format deep-dive and bridge analysis |

---

## Requirements

- Python 3.9+ (stdlib only — no pip install needed)
- [Ollama](https://ollama.com) running locally
- Recommended: Mac Mini M4 (32GB) or any machine with 16GB+ RAM

## Recommended Models (Mac Mini M4 32GB)

| Model | RAM | Notes |
|-------|-----|-------|
| `qwen2.5-coder:7b` | ~6 GB | Fast |
| `qwen2.5-coder:14b` | ~11 GB | **Recommended** |
| `qwen2.5-coder:32b-q3_K_M` | ~17 GB | Max quality |

---

## License

MIT
