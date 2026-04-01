#!/usr/bin/env python3
"""
rag_indexer.py — Standalone CLI for building and querying a vector RAG index.

Usage:
    python rag_indexer.py index [--dirs DIR...] [--index .bridge_rag_index.json]
    python rag_indexer.py query "how does auth work" [--top-k 5]
    python rag_indexer.py stats
    python rag_indexer.py clear
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

OLLAMA_HOST = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
DEFAULT_INDEX = ".bridge_rag_index.json"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
SIMILARITY_THRESHOLD = 0.25

INDEXABLE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp",
    ".md", ".txt", ".toml", ".yaml", ".yml", ".json",
    ".sh", ".bash", ".zsh", ".fish",
    ".html", ".css", ".scss", ".less",
    ".sql", ".graphql", ".proto",
    ".tf", ".hcl",
}

SKIP_DIRS = {
    ".git", "node_modules", "target", "__pycache__", ".venv", "venv",
    ".next", "dist", "build", ".cache", ".mypy_cache", ".pytest_cache",
    "coverage", ".tox", "eggs", ".eggs",
}


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
def embed(text: str, ollama_host: str = OLLAMA_HOST, model: str = EMBED_MODEL) -> list[float]:
    body = json.dumps({"model": model, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{ollama_host}/api/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        return data.get("embedding", [])
    except Exception as e:
        print(f"[embed error] {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        next_start = start + size - overlap
        if next_start >= len(text):
            break
        start = next_start
    return chunks


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------
def load_index(index_path: str) -> list[dict]:
    p = Path(index_path)
    if not p.exists():
        return []
    with open(p) as f:
        return json.load(f)


def save_index(index_path: str, chunks: list[dict]):
    with open(index_path, "w") as f:
        json.dump(chunks, f)
    print(f"Saved {len(chunks)} chunks to {index_path}")


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------
def index_directories(
    dirs: list[str],
    index_path: str,
    ollama_host: str,
    embed_model: str,
    force: bool = False,
):
    existing = {c["path"]: c for c in load_index(index_path)}
    chunks: list[dict] = []
    total_files = total_chunks = 0
    skipped_unchanged = 0

    for d in dirs:
        base = Path(d)
        if not base.exists():
            print(f"Warning: directory not found: {d}", file=sys.stderr)
            continue

        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in INDEXABLE_EXTENSIONS:
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue

            path_str = str(p)
            mtime = p.stat().st_mtime

            # Check if file unchanged
            if not force and path_str in existing:
                cached = existing[path_str]
                if cached.get("mtime", 0) >= mtime:
                    # Re-use cached chunks for this file
                    file_chunks = [c for c in load_index(index_path) if c["path"] == path_str]
                    chunks.extend(file_chunks)
                    skipped_unchanged += 1
                    continue

            try:
                text = p.read_text(errors="replace")
            except Exception as e:
                print(f"  Skip {path_str}: {e}", file=sys.stderr)
                continue

            if not text.strip():
                continue

            file_chunks_text = chunk_text(text)
            file_chunks: list[dict] = []
            for i, chunk_txt in enumerate(file_chunks_text):
                print(f"  {path_str} chunk {i+1}/{len(file_chunks_text)}", end="\r")
                emb = embed(chunk_txt, ollama_host, embed_model)
                if emb:
                    file_chunks.append({
                        "path": path_str,
                        "chunk_idx": i,
                        "text": chunk_txt,
                        "embedding": emb,
                        "mtime": mtime,
                    })

            print(f"  Indexed {path_str} ({len(file_chunks)} chunks)    ")
            chunks.extend(file_chunks)
            total_files += 1
            total_chunks += len(file_chunks)

    if skipped_unchanged:
        print(f"Skipped {skipped_unchanged} unchanged files (use --force to re-index)")

    print(f"\nIndexed {total_files} new/changed files, {total_chunks} new chunks.")
    save_index(index_path, chunks)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def query_index(
    query_text: str,
    index_path: str,
    ollama_host: str,
    embed_model: str,
    top_k: int = 5,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[tuple[float, dict]]:
    chunks = load_index(index_path)
    if not chunks:
        print("Index is empty. Run: python rag_indexer.py index", file=sys.stderr)
        return []

    print(f"Querying {len(chunks)} chunks...", file=sys.stderr)
    query_emb = embed(query_text, ollama_host, embed_model)
    if not query_emb:
        print("Failed to embed query", file=sys.stderr)
        return []

    scored = []
    for chunk in chunks:
        emb = chunk.get("embedding", [])
        if emb:
            sim = cosine_similarity(query_emb, emb)
            if sim >= threshold:
                scored.append((sim, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def print_stats(index_path: str):
    chunks = load_index(index_path)
    if not chunks:
        print("Index is empty.")
        return

    by_file: dict[str, int] = {}
    for c in chunks:
        by_file[c["path"]] = by_file.get(c["path"], 0) + 1

    total_chars = sum(len(c.get("text", "")) for c in chunks)
    embed_dim = len(chunks[0].get("embedding", [])) if chunks else 0
    index_size = Path(index_path).stat().st_size if Path(index_path).exists() else 0

    print(f"Index: {index_path}")
    print(f"  Files:      {len(by_file)}")
    print(f"  Chunks:     {len(chunks)}")
    print(f"  Total text: {total_chars:,} chars (~{total_chars // 4:,} tokens)")
    print(f"  Embed dim:  {embed_dim}")
    print(f"  Index size: {index_size / 1024 / 1024:.1f} MB")
    print(f"\nTop files by chunk count:")
    for path, count in sorted(by_file.items(), key=lambda x: -x[1])[:10]:
        print(f"  {count:4d}  {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="RAG indexer for bridge_proxy_full.py")
    p.add_argument("--ollama", default=OLLAMA_HOST, help="Ollama host")
    p.add_argument("--embed-model", default=EMBED_MODEL, help="Embedding model")
    p.add_argument("--index", default=DEFAULT_INDEX, help="Index file path")

    sub = p.add_subparsers(dest="cmd", required=True)

    # index
    idx_cmd = sub.add_parser("index", help="Build/update the index")
    idx_cmd.add_argument("--dirs", nargs="+", default=["."], help="Directories to index")
    idx_cmd.add_argument("--force", action="store_true", help="Re-index all files")
    idx_cmd.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    idx_cmd.add_argument("--chunk-overlap", type=int, default=CHUNK_OVERLAP)

    # query
    qry_cmd = sub.add_parser("query", help="Query the index")
    qry_cmd.add_argument("text", help="Query text")
    qry_cmd.add_argument("--top-k", type=int, default=5)
    qry_cmd.add_argument("--threshold", type=float, default=SIMILARITY_THRESHOLD)
    qry_cmd.add_argument("--show-text", action="store_true", help="Show chunk text")

    # stats
    sub.add_parser("stats", help="Print index statistics")

    # clear
    sub.add_parser("clear", help="Delete the index file")

    args = p.parse_args()

    if args.cmd == "index":
        t0 = time.time()
        index_directories(
            args.dirs,
            args.index,
            args.ollama,
            args.embed_model,
            force=args.force,
        )
        print(f"Done in {time.time() - t0:.1f}s")

    elif args.cmd == "query":
        results = query_index(
            args.text,
            args.index,
            args.ollama,
            args.embed_model,
            top_k=args.top_k,
            threshold=args.threshold,
        )
        if not results:
            print("No results above threshold.")
            return
        for score, chunk in results:
            print(f"\n{'='*60}")
            print(f"Score: {score:.4f}  File: {chunk['path']}  Chunk: {chunk['chunk_idx']}")
            if args.show_text:
                print(chunk.get("text", "")[:600])
            else:
                preview = chunk.get("text", "")[:120].replace("\n", " ")
                print(f"Preview: {preview}...")

    elif args.cmd == "stats":
        print_stats(args.index)

    elif args.cmd == "clear":
        p_idx = Path(args.index)
        if p_idx.exists():
            p_idx.unlink()
            print(f"Deleted {args.index}")
        else:
            print(f"{args.index} does not exist")


if __name__ == "__main__":
    main()
