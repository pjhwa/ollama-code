#!/usr/bin/env bash
# env.sh — Source this file to configure Claude Code CLI for local Ollama bridge.
#
# Usage:
#   source ./env.sh
#   claude                          # uses qwen3:14b via bridge automatically
#   claude --model qwen3:14b        # explicit (ANTHROPIC_CUSTOM_MODEL_OPTION bypasses validation)
#
# The bridge must already be running:
#   ./run_full_bridge.sh &          # background, or in a separate terminal

PRIMARY_MODEL="${PRIMARY_MODEL:-qwen3:14b}"
PROXY_PORT="${PROXY_PORT:-9099}"

export ANTHROPIC_BASE_URL="http://localhost:${PROXY_PORT}"
export ANTHROPIC_API_KEY="local-ollama-bridge"

# Claude Code CLI's validateModel() skips API validation when the requested
# model name exactly matches ANTHROPIC_CUSTOM_MODEL_OPTION.  Without this,
# the CLI rejects non-claude-* model IDs at SessionStart.
export ANTHROPIC_CUSTOM_MODEL_OPTION="${PRIMARY_MODEL}"

echo "Claude Code → Local Ollama bridge env set:"
echo "  ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL"
echo "  ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
echo "  ANTHROPIC_CUSTOM_MODEL_OPTION=$ANTHROPIC_CUSTOM_MODEL_OPTION"
echo ""
echo "Run: claude --model $PRIMARY_MODEL"
