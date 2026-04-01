#!/usr/bin/env bash
# run_full_bridge.sh — Start the full-featured Anthropic↔Ollama bridge proxy
# Usage: ./run_full_bridge.sh [options]
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — edit these or override via environment variables
# ---------------------------------------------------------------------------
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
PRIMARY_MODEL="${PRIMARY_MODEL:-qwen3:14b}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"
PROXY_PORT="${PROXY_PORT:-9099}"
RAG_DIRS="${RAG_DIRS:-.}"
INDEX_ON_START="${INDEX_ON_START:-false}"   # set to "true" to re-index on each start

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  bridge_proxy_full.py — Full Anthropic Bridge ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ---------------------------------------------------------------------------
# Check dependencies
# ---------------------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || error "python3 not found"
python3 -c "import sys; assert sys.version_info >= (3,9)" 2>/dev/null || error "Python 3.9+ required"
command -v curl >/dev/null 2>&1 || error "curl not found"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_SCRIPT="$SCRIPT_DIR/bridge_proxy_full.py"
RAG_SCRIPT="$SCRIPT_DIR/rag_indexer.py"

[[ -f "$BRIDGE_SCRIPT" ]] || error "bridge_proxy_full.py not found in $SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Check / start Ollama
# ---------------------------------------------------------------------------
info "Checking Ollama at $OLLAMA_HOST..."
if ! curl -sf "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
    warn "Ollama not responding — attempting to start..."
    if command -v ollama >/dev/null 2>&1; then
        ollama serve &>/tmp/ollama.log &
        OLLAMA_PID=$!
        echo "  Ollama PID: $OLLAMA_PID"
        for i in $(seq 1 15); do
            sleep 1
            curl -sf "$OLLAMA_HOST/api/tags" >/dev/null 2>&1 && break
            [[ $i -eq 15 ]] && error "Ollama failed to start after 15s. Check /tmp/ollama.log"
        done
        info "Ollama started"
    else
        error "Ollama not running and 'ollama' binary not found. Install from https://ollama.com"
    fi
else
    info "Ollama is running"
fi

# ---------------------------------------------------------------------------
# Check primary model
# ---------------------------------------------------------------------------
info "Checking primary model: $PRIMARY_MODEL"
if ! ollama list 2>/dev/null | grep -q "${PRIMARY_MODEL%%:*}"; then
    warn "Model '$PRIMARY_MODEL' not found — pulling..."
    ollama pull "$PRIMARY_MODEL" || error "Failed to pull $PRIMARY_MODEL"
fi
info "Primary model ready: $PRIMARY_MODEL"

# ---------------------------------------------------------------------------
# Check embedding model
# ---------------------------------------------------------------------------
info "Checking embedding model: $EMBED_MODEL"
if ! ollama list 2>/dev/null | grep -q "$EMBED_MODEL"; then
    warn "Embedding model '$EMBED_MODEL' not found — pulling..."
    ollama pull "$EMBED_MODEL" || warn "Failed to pull $EMBED_MODEL — RAG will be degraded"
fi

# ---------------------------------------------------------------------------
# Warm up primary model (keep_alive=-1 will handle it, but let's check it loads)
# ---------------------------------------------------------------------------
info "Warming up model in memory..."
curl -sf "$OLLAMA_HOST/api/generate" \
    -d "{\"model\":\"$PRIMARY_MODEL\",\"prompt\":\"hi\",\"stream\":false,\"options\":{\"num_predict\":1,\"keep_alive\":-1}}" \
    >/dev/null 2>&1 || warn "Warm-up ping failed (non-fatal)"

# Inform about thinking mode
if echo "$PRIMARY_MODEL" | grep -qiE "qwen3|deepseek-r1|qwq"; then
    info "Thinking mode: ENABLED (model supports native reasoning)"
else
    info "Thinking mode: not active (add qwen3/deepseek-r1 model to enable)"
fi

# ---------------------------------------------------------------------------
# RAG index
# ---------------------------------------------------------------------------
if [[ -f "$RAG_SCRIPT" ]]; then
    INDEX_FILE=".bridge_rag_index.json"
    if [[ "$INDEX_ON_START" == "true" ]]; then
        info "Indexing codebase for RAG (this may take a few minutes)..."
        python3 "$RAG_SCRIPT" --index "$INDEX_FILE" index --dirs $RAG_DIRS || warn "RAG indexing failed (non-fatal)"
    elif [[ ! -f "$INDEX_FILE" ]]; then
        warn "No RAG index found at $INDEX_FILE"
        echo "  To build it: python3 $RAG_SCRIPT index --dirs ."
        echo "  Or set INDEX_ON_START=true before running this script"
    else
        python3 "$RAG_SCRIPT" --index "$INDEX_FILE" stats 2>/dev/null || true
    fi
fi

# ---------------------------------------------------------------------------
# Check port availability
# ---------------------------------------------------------------------------
if command -v ss >/dev/null 2>&1; then
    if ss -tlnp 2>/dev/null | grep -q ":$PROXY_PORT "; then
        warn "Port $PROXY_PORT already in use"
        if lsof -ti:"$PROXY_PORT" >/dev/null 2>&1; then
            OLD_PID=$(lsof -ti:"$PROXY_PORT" 2>/dev/null | head -1)
            warn "Killing old process on port $PROXY_PORT (PID $OLD_PID)..."
            kill "$OLD_PID" 2>/dev/null || true
            sleep 1
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Export environment for Claude Code
# ---------------------------------------------------------------------------
export ANTHROPIC_BASE_URL="http://localhost:$PROXY_PORT"
export ANTHROPIC_API_KEY="local-ollama-bridge"
# ANTHROPIC_CUSTOM_MODEL_OPTION tells Claude Code CLI to skip model validation
# for this exact model name (see validateModel.ts in Claude Code source).
# Without this, the CLI tries to verify the model via API and may reject
# non-claude-* model IDs at session startup.
export ANTHROPIC_CUSTOM_MODEL_OPTION="$PRIMARY_MODEL"

echo ""
echo -e "${GREEN}┌─────────────────────────────────────────────┐${NC}"
echo -e "${GREEN}│  Bridge proxy starting on port $PROXY_PORT       │${NC}"
echo -e "${GREEN}│                                             │${NC}"
echo -e "${GREEN}│  Run in your claude terminal:               │${NC}"
echo -e "${GREEN}│  export ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL  │${NC}"
echo -e "${GREEN}│  export ANTHROPIC_API_KEY=local-ollama-bridge │${NC}"
echo -e "${GREEN}│  export ANTHROPIC_CUSTOM_MODEL_OPTION=$PRIMARY_MODEL │${NC}"
echo -e "${GREEN}│                                             │${NC}"
echo -e "${GREEN}│  Then: claude --model $PRIMARY_MODEL      │${NC}"
echo -e "${GREEN}│  Or:   claude  (uses $PRIMARY_MODEL auto)  │${NC}"
echo -e "${GREEN}└─────────────────────────────────────────────┘${NC}"
echo ""
echo "Features enabled:"
echo "  ✓ Prompt cache simulation (static/dynamic boundary)"
echo "  ✓ Vector RAG (nomic-embed-text)"
echo "  ✓ KAIROS background watcher"
echo "  ✓ COORDINATOR_MODE (task decomposition)"
echo "  ✓ TRANSCRIPT_CLASSIFIER (safety scoring)"
echo "  ✓ ULTRAPLAN (complexity detection)"
echo "  ✓ TEAMMEM (persistent memory)"
echo "  ✓ Context Compaction (auto-summarize long sessions)"
echo "  ✓ Qwen3 Thinking Mode (native reasoning via options)"
echo "  ✓ Parallel MCP Tool Execution (agentic loop)"
echo "  ○ VERIFICATION_AGENT (disabled by default, add --enable-verification)"
echo ""
echo "Model: $PRIMARY_MODEL"
echo "  RAM guide: qwen3:4b (8GB) | qwen3:8b (16GB) | qwen3:14b (32GB) | qwen3:32b (64GB+)"
echo ""
echo "Press Ctrl+C to stop."
echo ""

# ---------------------------------------------------------------------------
# Start bridge
# ---------------------------------------------------------------------------
exec python3 "$BRIDGE_SCRIPT" \
    --host 0.0.0.0 \
    --port "$PROXY_PORT" \
    --ollama "$OLLAMA_HOST" \
    --model "$PRIMARY_MODEL" \
    --embed-model "$EMBED_MODEL" \
    --rag-dirs $RAG_DIRS \
    --rag-index ".bridge_rag_index.json" \
    --kairos-interval 30 \
    "$@"
