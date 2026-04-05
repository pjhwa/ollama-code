#!/bin/bash

ollama pull qwen3:14b
ollama pull nomic-embed-text

#python3 rag_indexer.py index --dirs .
python3 rag_indexer.py stats

ollama ps

PRIMARY_MODEL=qwen3:14b ./run_full_bridge.sh
