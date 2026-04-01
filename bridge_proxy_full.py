#!/usr/bin/env python3
"""
bridge_proxy_full.py — Full-featured Anthropic↔Ollama bridge proxy.

Drop-in replacement for the Anthropic API endpoint. Set:
    export ANTHROPIC_BASE_URL=http://localhost:9099
    export ANTHROPIC_API_KEY=local

Features:
  - Prompt caching simulation (SHA256 + keep_alive=-1)
  - MCP server integration (stdio JSON-RPC 2.0)
  - Vector RAG via nomic-embed-text (pure-Python, no numpy)
  - KAIROS daemon (background file watcher, PROACTIVE tick)
  - COORDINATOR_MODE (sequential task decomposition)
  - TRANSCRIPT_CLASSIFIER (pattern-based risk scoring, auto-approve)
  - ULTRAPLAN (complexity detection → planning phase injection)
  - VERIFICATION_AGENT (secondary model verification)
  - TEAMMEM (persistent session memory across requests)
  - LocalModelOptimizer (CoT forcing, retry, self-consistency)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Generator, Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bridge")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class ProxyConfig:
    ollama_host: str = "http://localhost:11434"
    primary_model: str = "qwen3:8b"
    embed_model: str = "nomic-embed-text"
    verify_model: str = ""          # defaults to primary_model if empty
    proxy_port: int = 9099
    proxy_host: str = "0.0.0.0"

    # Features
    enable_cache: bool = True
    enable_rag: bool = True
    enable_mcp: bool = True
    enable_kairos: bool = True
    enable_coordinator: bool = True
    enable_classifier: bool = True
    enable_ultraplan: bool = True
    enable_verification: bool = False   # extra latency; off by default
    enable_teammem: bool = True

    # RAG
    rag_index_path: str = ".bridge_rag_index.json"
    rag_top_k: int = 5
    rag_chunk_size: int = 500
    rag_chunk_overlap: int = 100
    rag_threshold: float = 0.30
    rag_watch_dirs: list[str] = field(default_factory=list)

    # KAIROS
    kairos_tick_interval: float = 30.0
    kairos_max_findings: int = 5

    # TEAMMEM
    teammem_path: str = ".bridge_memory.json"

    # COORDINATOR
    coordinator_max_subtasks: int = 4

    # ULTRAPLAN
    ultraplan_min_length: int = 120

    # CLASSIFIER
    classifier_auto_approve_threshold: float = 4.0

    # VERIFICATION
    verification_min_tokens: int = 200   # only verify if response this long

    # Qwen3 / Thinking models
    enable_thinking: bool = True
    thinking_budget_tokens: int = 8192
    thinking_models: list[str] = field(
        default_factory=lambda: ["qwen3", "deepseek-r1", "qwq", "marco-o1"]
    )

    # Retry / backoff
    max_retries: int = 2
    base_backoff_ms: int = 200

    def __post_init__(self):
        if not self.verify_model:
            self.verify_model = self.primary_model
        if not self.rag_watch_dirs:
            self.rag_watch_dirs = ["."]


# ---------------------------------------------------------------------------
# TeamMemory — persistent JSON k/v store
# ---------------------------------------------------------------------------
class TeamMemory:
    def __init__(self, path: str):
        self._path = Path(path)
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path) as f:
                    self._data = json.load(f)
                log.info("TeamMemory: loaded %d entries from %s", len(self._data), self._path)
            except Exception as e:
                log.warning("TeamMemory: load failed: %s", e)

    def _save(self):
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.warning("TeamMemory: save failed: %s", e)

    def set(self, key: str, value: Any):
        with self._lock:
            self._data[key] = value
            self._save()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def as_context_block(self) -> str:
        items = self.all()
        if not items:
            return ""
        lines = ["## Persistent Memory (TEAMMEM)"]
        for k, v in list(items.items())[-20:]:   # last 20 entries
            lines.append(f"- {k}: {json.dumps(v, ensure_ascii=False)}")
        return "\n".join(lines)


def is_thinking_model(model_name: str, thinking_models: list[str]) -> bool:
    """Return True if model supports native thinking/reasoning mode."""
    lower = model_name.lower()
    return any(tm.lower() in lower for tm in thinking_models)


# ---------------------------------------------------------------------------
# PromptCacheLayer — SHA256 hash-based simulation
# ---------------------------------------------------------------------------
@dataclass
class _CacheEntry:
    tokens: int
    hits: int
    last_seen: float


class PromptCacheLayer:
    def __init__(self):
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def record(self, system_text: str, approx_tokens: int) -> tuple[int, int]:
        """Returns (cache_creation_tokens, cache_read_tokens)."""
        key = hashlib.sha256(system_text.encode()).hexdigest()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._cache[key] = _CacheEntry(approx_tokens, 0, time.time())
                return approx_tokens, 0
            entry.hits += 1
            entry.last_seen = time.time()
            return 0, approx_tokens


# ---------------------------------------------------------------------------
# MCP server manager — stdio JSON-RPC 2.0
# ---------------------------------------------------------------------------
class McpServerManager:
    def __init__(self):
        self._servers: dict[str, subprocess.Popen] = {}
        self._tool_registry: dict[str, dict] = {}   # tool_name → {server, schema}
        self._lock = threading.Lock()
        self._req_id = 0

    def add_server(self, name: str, cmd: list[str], env: Optional[dict] = None):
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, **(env or {})},
            )
            self._servers[name] = proc
            self._initialize(name)
            self._list_tools(name)
            log.info("MCP: started server '%s' (pid=%d)", name, proc.pid)
        except Exception as e:
            log.error("MCP: failed to start '%s': %s", name, e)

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _send(self, name: str, method: str, params: Any = None) -> Any:
        proc = self._servers.get(name)
        if proc is None or proc.poll() is not None:
            raise RuntimeError(f"MCP server '{name}' not running")
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

    def _initialize(self, name: str):
        self._send(name, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "bridge_proxy", "version": "1.0"},
        })

    def _list_tools(self, name: str):
        result = self._send(name, "tools/list")
        if not result:
            return
        for tool in result.get("tools", []):
            tool_name = f"mcp__{name}__{tool['name']}"
            with self._lock:
                self._tool_registry[tool_name] = {"server": name, "schema": tool}

    def get_all_tools(self) -> list[dict]:
        """Return tools in Anthropic format."""
        tools = []
        with self._lock:
            for tool_name, info in self._tool_registry.items():
                schema = info["schema"]
                tools.append({
                    "name": tool_name,
                    "description": schema.get("description", ""),
                    "input_schema": schema.get("inputSchema", {"type": "object", "properties": {}}),
                })
        return tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        with self._lock:
            info = self._tool_registry.get(tool_name)
        if info is None:
            return f"[MCP] Unknown tool: {tool_name}"
        server = info["server"]
        # Strip mcp__<server>__ prefix to get raw tool name
        raw_name = tool_name[len(f"mcp__{server}__"):]
        try:
            result = self._send(server, "tools/call", {"name": raw_name, "arguments": arguments})
            if isinstance(result, dict):
                content = result.get("content", [])
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                return "\n".join(parts) if parts else json.dumps(result)
            return str(result)
        except Exception as e:
            return f"[MCP] Error calling {tool_name}: {e}"

    def shutdown(self):
        for name, proc in self._servers.items():
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
            log.info("MCP: stopped server '%s'", name)


# ---------------------------------------------------------------------------
# RAG — vector embedding + cosine similarity (pure Python)
# ---------------------------------------------------------------------------
def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class _RagChunk:
    path: str
    text: str
    embedding: list[float]
    mtime: float


class RagContextInjector:
    def __init__(self, config: ProxyConfig):
        self._cfg = config
        self._chunks: list[_RagChunk] = []
        self._lock = threading.Lock()
        self._embed_cache: dict[str, list[float]] = {}
        self._load_index()

    def _embed(self, text: str) -> list[float]:
        cached = self._embed_cache.get(text)
        if cached:
            return cached
        try:
            body = json.dumps({"model": self._cfg.embed_model, "prompt": text}).encode()
            req = urllib.request.Request(
                f"{self._cfg.ollama_host}/api/embeddings",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            emb = data.get("embedding", [])
            self._embed_cache[text] = emb
            return emb
        except Exception as e:
            log.warning("RAG: embed failed: %s", e)
            return []

    def _chunk_text(self, text: str) -> list[str]:
        size = self._cfg.rag_chunk_size
        overlap = self._cfg.rag_chunk_overlap
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            chunks.append(text[start:end])
            start += size - overlap
            if start >= len(text):
                break
        return chunks

    def index_file(self, path: str):
        p = Path(path)
        if not p.exists() or not p.is_file():
            return
        try:
            mtime = p.stat().st_mtime
            text = p.read_text(errors="replace")
            chunks = self._chunk_text(text)
            new_chunks = []
            for chunk in chunks:
                emb = self._embed(chunk)
                if emb:
                    new_chunks.append(_RagChunk(path=path, text=chunk, embedding=emb, mtime=mtime))
            with self._lock:
                # Remove old chunks for this file
                self._chunks = [c for c in self._chunks if c.path != path]
                self._chunks.extend(new_chunks)
            log.debug("RAG: indexed %s → %d chunks", path, len(new_chunks))
        except Exception as e:
            log.warning("RAG: index_file(%s) failed: %s", path, e)

    def index_directory(self, directory: str, extensions: tuple[str, ...] = (
        ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java",
        ".c", ".cpp", ".h", ".md", ".txt", ".toml", ".yaml", ".yml",
    )):
        d = Path(directory)
        if not d.exists():
            return
        count = 0
        for p in d.rglob("*"):
            if p.is_file() and p.suffix in extensions:
                skip_parts = {".git", "node_modules", "target", "__pycache__", ".venv", "venv"}
                if not any(part in skip_parts for part in p.parts):
                    self.index_file(str(p))
                    count += 1
        log.info("RAG: indexed %d files from %s", count, directory)
        self._save_index()

    def _save_index(self):
        try:
            with self._lock:
                data = [
                    {"path": c.path, "text": c.text, "embedding": c.embedding, "mtime": c.mtime}
                    for c in self._chunks
                ]
            with open(self._cfg.rag_index_path, "w") as f:
                json.dump(data, f)
            log.info("RAG: saved %d chunks to index", len(data))
        except Exception as e:
            log.warning("RAG: save index failed: %s", e)

    def _load_index(self):
        p = Path(self._cfg.rag_index_path)
        if not p.exists():
            return
        try:
            with open(p) as f:
                data = json.load(f)
            with self._lock:
                self._chunks = [
                    _RagChunk(
                        path=d["path"],
                        text=d["text"],
                        embedding=d["embedding"],
                        mtime=d["mtime"],
                    )
                    for d in data
                ]
            log.info("RAG: loaded %d chunks from index", len(self._chunks))
        except Exception as e:
            log.warning("RAG: load index failed: %s", e)

    def update_file_if_changed(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        mtime = p.stat().st_mtime
        with self._lock:
            existing = [c for c in self._chunks if c.path == path]
        if not existing or existing[0].mtime < mtime:
            self.index_file(path)
            return True
        return False

    def query(self, text: str, top_k: Optional[int] = None) -> list[_RagChunk]:
        k = top_k or self._cfg.rag_top_k
        emb = self._embed(text)
        if not emb:
            return []
        with self._lock:
            scored = [(c, _cosine_similarity(emb, c.embedding)) for c in self._chunks]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, score in scored[:k] if score >= self._cfg.rag_threshold]

    def build_context(self, user_text: str) -> str:
        chunks = self.query(user_text)
        if not chunks:
            return ""
        parts = ["## Relevant Code Context (RAG)"]
        seen: set[str] = set()
        for chunk in chunks:
            key = f"{chunk.path}:{chunk.text[:50]}"
            if key in seen:
                continue
            seen.add(key)
            lang = Path(chunk.path).suffix.lstrip(".")
            parts.append(f"\n### {chunk.path}\n```{lang}\n{chunk.text}\n```")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# KAIROS daemon — background file watcher + PROACTIVE tick
# ---------------------------------------------------------------------------
class KairosDaemon(threading.Thread):
    def __init__(self, config: ProxyConfig, rag: RagContextInjector):
        super().__init__(daemon=True, name="kairos")
        self._cfg = config
        self._rag = rag
        self._findings: list[str] = []
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._file_mtimes: dict[str, float] = {}

    def run(self):
        log.info("KAIROS: daemon started (tick=%.1fs)", self._cfg.kairos_tick_interval)
        while not self._stop_evt.wait(self._cfg.kairos_tick_interval):
            self._tick()

    def _tick(self):
        changed = []
        for watch_dir in self._cfg.rag_watch_dirs:
            d = Path(watch_dir)
            if not d.exists():
                continue
            exts = {".py", ".ts", ".tsx", ".js", ".rs", ".go"}
            for p in d.rglob("*"):
                if p.is_file() and p.suffix in exts:
                    skip = {".git", "node_modules", "target", "__pycache__"}
                    if any(part in skip for part in p.parts):
                        continue
                    path_str = str(p)
                    mtime = p.stat().st_mtime
                    old = self._file_mtimes.get(path_str, 0)
                    if mtime > old:
                        self._file_mtimes[path_str] = mtime
                        if old > 0:   # file changed (not first scan)
                            changed.append(path_str)
                            self._rag.index_file(path_str)

        if changed:
            finding = f"KAIROS: {len(changed)} file(s) changed and re-indexed: {', '.join(changed[:3])}"
            if len(changed) > 3:
                finding += f" (+{len(changed) - 3} more)"
            self._add_finding(finding)
            log.info(finding)

    def _add_finding(self, finding: str):
        with self._lock:
            self._findings.append(finding)
            if len(self._findings) > self._cfg.kairos_max_findings:
                self._findings.pop(0)

    def get_findings(self) -> list[str]:
        with self._lock:
            return list(self._findings)

    def pop_findings(self) -> list[str]:
        with self._lock:
            findings = list(self._findings)
            self._findings.clear()
            return findings

    def stop(self):
        self._stop_evt.set()


# ---------------------------------------------------------------------------
# TRANSCRIPT_CLASSIFIER — pattern-based risk scoring
# ---------------------------------------------------------------------------
_RISK_PATTERNS: list[tuple[re.Pattern, float, str]] = [
    (re.compile(r"\brm\s+-rf\b", re.I), 8.0, "rm -rf"),
    (re.compile(r"\bdrop\s+(?:table|database)\b", re.I), 9.0, "SQL DROP"),
    (re.compile(r"\bformat\s+c:\b", re.I), 10.0, "format C:"),
    (re.compile(r"\bos\.system\s*\(", re.I), 5.0, "os.system"),
    (re.compile(r"\bsubprocess\b.*shell\s*=\s*True", re.I), 5.5, "subprocess shell=True"),
    (re.compile(r"\beval\s*\(", re.I), 4.0, "eval()"),
    (re.compile(r"\bexec\s*\(", re.I), 4.0, "exec()"),
    (re.compile(r"\bdelete\b.*\bwhere\b", re.I), 3.5, "DELETE WHERE"),
    (re.compile(r"\btruncate\b", re.I), 6.0, "TRUNCATE"),
    (re.compile(r"\bchmod\s+777\b", re.I), 3.0, "chmod 777"),
    (re.compile(r"\bcurl\b.*\|\s*(?:bash|sh)\b", re.I), 7.0, "curl|bash"),
    (re.compile(r"\bwget\b.*\|\s*(?:bash|sh)\b", re.I), 7.0, "wget|bash"),
    (re.compile(r"\b(?:password|passwd|secret|token|api_key)\s*=\s*['\"][^'\"]{6,}", re.I), 2.5, "hardcoded secret"),
    (re.compile(r"\bgit\s+push\s+--force\b", re.I), 3.0, "git push --force"),
]

_SAFE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bexplain\b", re.I),
    re.compile(r"\bhow\s+(?:do|does|can|to)\b", re.I),
    re.compile(r"\bwhat\s+is\b", re.I),
    re.compile(r"\blist\b", re.I),
    re.compile(r"\bshow\b", re.I),
    re.compile(r"\bdescribe\b", re.I),
]


class TranscriptClassifier:
    def __init__(self, threshold: float):
        self._threshold = threshold

    def score(self, text: str) -> tuple[float, list[str]]:
        """Returns (risk_score 0-10, triggered_reasons)."""
        # Safe patterns lower risk
        safe_hits = sum(1 for p in _SAFE_PATTERNS if p.search(text))
        base = -safe_hits * 0.5

        reasons = []
        for pattern, risk, label in _RISK_PATTERNS:
            if pattern.search(text):
                base += risk
                reasons.append(f"{label}({risk})")

        score = max(0.0, min(10.0, base))
        return score, reasons

    def is_auto_approved(self, text: str) -> tuple[bool, float, list[str]]:
        score, reasons = self.score(text)
        return score < self._threshold, score, reasons


# ---------------------------------------------------------------------------
# ULTRAPLAN — detect complexity and inject planning phase
# ---------------------------------------------------------------------------
_COMPLEX_KEYWORDS = [
    "refactor", "migrate", "redesign", "architecture", "implement.*system",
    "build.*platform", "create.*framework", "integrate.*with", "optimize.*performance",
    "debug.*issue", "fix.*bug.*in", "add.*feature", "implement.*feature",
    "write.*tests", "create.*api", "build.*agent", "create.*pipeline",
]

_COMPLEX_PATTERN = re.compile("|".join(_COMPLEX_KEYWORDS), re.I)


class UltraPlan:
    def __init__(self, config: ProxyConfig, ollama_host: str, model: str):
        self._cfg = config
        self._ollama_host = ollama_host
        self._model = model

    def is_complex(self, text: str) -> bool:
        return (
            len(text) >= self._cfg.ultraplan_min_length
            and bool(_COMPLEX_PATTERN.search(text))
        )

    def generate_plan(self, user_text: str) -> str:
        """Call Ollama to produce a step-by-step plan."""
        plan_prompt = (
            "You are a software architect. Analyze the following request and produce "
            "a concise numbered implementation plan (5-10 steps). Output ONLY the plan, "
            "no preamble.\n\nRequest:\n" + user_text
        )
        try:
            body = json.dumps({
                "model": self._model,
                "messages": [{"role": "user", "content": plan_prompt}],
                "stream": False,
                "options": {"temperature": 0.2, "num_ctx": 4096},
            }).encode()
            req = urllib.request.Request(
                f"{self._ollama_host}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            log.warning("ULTRAPLAN: plan generation failed: %s", e)
            return ""

    def inject_plan(self, system_prompt: str, plan: str) -> str:
        return system_prompt + f"\n\n## ULTRAPLAN — Pre-computed Implementation Plan\n{plan}"


# ---------------------------------------------------------------------------
# COORDINATOR_MODE — sequential task decomposition
# ---------------------------------------------------------------------------
_MULTI_TASK_PATTERN = re.compile(
    r"\b(?:first|then|after(?:ward)?|finally|next|also|additionally|and\s+(?:also|then))\b",
    re.I,
)


class CoordinatorMode:
    def __init__(self, config: ProxyConfig, ollama_host: str, model: str):
        self._cfg = config
        self._ollama_host = ollama_host
        self._model = model

    def is_multi_task(self, text: str) -> bool:
        hits = len(_MULTI_TASK_PATTERN.findall(text))
        return hits >= 2 and len(text) > 200

    def decompose(self, user_text: str) -> list[str]:
        decomp_prompt = (
            "Break the following request into independent subtasks. "
            "Output a JSON array of strings, each being a clear subtask. "
            f"Maximum {self._cfg.coordinator_max_subtasks} subtasks. "
            "Output ONLY the JSON array.\n\nRequest:\n" + user_text
        )
        try:
            body = json.dumps({
                "model": self._model,
                "messages": [{"role": "user", "content": decomp_prompt}],
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 2048},
            }).encode()
            req = urllib.request.Request(
                f"{self._ollama_host}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"].strip()
            # Extract JSON array
            m = re.search(r"\[.*\]", content, re.S)
            if m:
                return json.loads(m.group())
        except Exception as e:
            log.warning("COORDINATOR: decompose failed: %s", e)
        return [user_text]

    def run_sequential(self, subtasks: list[str], system_prompt: str) -> str:
        """Run subtasks sequentially, accumulating context."""
        accumulated = ""
        results = []
        for i, task in enumerate(subtasks):
            context = f"\n\nPrevious results:\n{accumulated}" if accumulated else ""
            prompt = f"Subtask {i+1}/{len(subtasks)}: {task}{context}"
            try:
                body = json.dumps({
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.3, "num_ctx": 8192},
                }).encode()
                req = urllib.request.Request(
                    f"{self._ollama_host}/v1/chat/completions",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read())
                result = data["choices"][0]["message"]["content"]
                results.append(f"### Subtask {i+1}: {task}\n{result}")
                accumulated += f"\nSubtask {i+1} result: {result[:500]}"
                log.info("COORDINATOR: subtask %d/%d completed", i + 1, len(subtasks))
            except Exception as e:
                log.warning("COORDINATOR: subtask %d failed: %s", i + 1, e)
                results.append(f"### Subtask {i+1}: {task}\n[Error: {e}]")
        return "\n\n".join(results)


# ---------------------------------------------------------------------------
# VERIFICATION_AGENT — secondary model verification
# ---------------------------------------------------------------------------
class VerificationAgent:
    def __init__(self, config: ProxyConfig, ollama_host: str):
        self._cfg = config
        self._ollama_host = ollama_host

    def verify(self, user_request: str, response_text: str) -> tuple[bool, str]:
        """Returns (passed, feedback)."""
        verify_prompt = (
            "You are a code reviewer. Evaluate whether the following response correctly "
            "addresses the request. Reply with PASS or FAIL on the first line, "
            "then brief feedback.\n\n"
            f"REQUEST:\n{user_request[:500]}\n\nRESPONSE:\n{response_text[:1000]}"
        )
        try:
            body = json.dumps({
                "model": self._cfg.verify_model,
                "messages": [{"role": "user", "content": verify_prompt}],
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 4096},
            }).encode()
            req = urllib.request.Request(
                f"{self._ollama_host}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            verdict_text = data["choices"][0]["message"]["content"].strip()
            passed = verdict_text.upper().startswith("PASS")
            return passed, verdict_text
        except Exception as e:
            log.warning("VERIFICATION: failed: %s", e)
            return True, ""   # fail open


# ---------------------------------------------------------------------------
# LocalModelOptimizer — CoT, retry, self-consistency
# ---------------------------------------------------------------------------
class LocalModelOptimizer:
    COT_SUFFIX = (
        "\n\nThink step-by-step before answering. Show your reasoning, "
        "then provide the final answer."
    )

    @staticmethod
    def force_cot(messages: list[dict]) -> list[dict]:
        """Append CoT instruction to last user message."""
        msgs = [dict(m) for m in messages]
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == "user":
                content = msgs[i]["content"]
                if isinstance(content, str):
                    msgs[i]["content"] = content + LocalModelOptimizer.COT_SUFFIX
                break
        return msgs

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

    @staticmethod
    def inject_error_context(messages: list[dict], error: str) -> list[dict]:
        msgs = list(messages)
        msgs.append({
            "role": "user",
            "content": f"The previous attempt had an issue: {error}\nPlease try again with a corrected approach.",
        })
        return msgs

    @staticmethod
    def extract_user_text(messages: list[dict]) -> str:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                c = msg.get("content", "")
                return c if isinstance(c, str) else json.dumps(c)
        return ""


# ---------------------------------------------------------------------------
# Anthropic ↔ Ollama format translation
# ---------------------------------------------------------------------------
def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    parts.append(_extract_text(inner))
        return "\n".join(parts)
    return str(content)


def convert_anthropic_to_openai(req: dict, model: str, config: Optional["ProxyConfig"] = None) -> dict:
    """Translate Anthropic Messages API request → OpenAI chat format."""
    messages: list[dict] = []

    # System prompt
    system = req.get("system", "")
    system_text = _extract_text(system) if system else ""
    if system_text:
        messages.append({"role": "system", "content": system_text})

    # Convert messages
    tool_results_pending: list[dict] = []

    for msg in req.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "assistant":
            text_parts: list[str] = []
            tool_calls_out: list[dict] = []

            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        tool_calls_out.append({
                            "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })

            assistant_msg: dict = {"role": "assistant", "content": "\n".join(text_parts)}
            if tool_calls_out:
                assistant_msg["tool_calls"] = tool_calls_out
            messages.append(assistant_msg)

        elif role == "user":
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "tool_result":
                        tool_content = block.get("content", "")
                        tool_results_pending.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": _extract_text(tool_content),
                        })

                # Flush tool results before user text
                messages.extend(tool_results_pending)
                tool_results_pending.clear()

                if text_parts:
                    messages.append({"role": "user", "content": "\n".join(text_parts)})
            else:
                messages.extend(tool_results_pending)
                tool_results_pending.clear()
                messages.append({"role": "user", "content": _extract_text(content)})

    messages.extend(tool_results_pending)

    # Tools
    tools_out = []
    for tool in req.get("tools", []):
        tools_out.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })

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
    if req.get("max_tokens"):
        openai_req["max_tokens"] = req["max_tokens"]
    if tools_out:
        openai_req["tools"] = tools_out

    return openai_req


def _sse(event: str, data: dict) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode()


def stream_openai_to_anthropic(
    ollama_host: str,
    openai_req: dict,
    orig_model: str,
    cache_layer: Optional[PromptCacheLayer],
    system_text: str,
    input_tokens_approx: int,
) -> Generator[bytes, None, None]:
    """Generator: yields Anthropic SSE bytes from Ollama streaming response."""
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"

    # Compute cache simulation tokens
    cache_creation = cache_read = 0
    if cache_layer and system_text:
        cache_creation, cache_read = cache_layer.record(system_text, len(system_text) // 4)

    # message_start
    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": orig_model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens_approx,
                "output_tokens": 1,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
            },
        },
    })

    # content_block_start (text)
    yield _sse("content_block_start", {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    yield b"event: ping\ndata: {\"type\": \"ping\"}\n\n"

    # Stream from Ollama
    body = json.dumps({**openai_req, "stream": True}).encode()
    output_tokens = 0
    stop_reason = "end_turn"
    tool_call_accumulator: dict[int, dict] = {}
    _think_buffer = ""
    _in_think_block = False

    # Retry with backoff
    cfg_retries = 2
    base_ms = 200

    for attempt in range(cfg_retries + 1):
        try:
            req_obj = urllib.request.Request(
                f"{ollama_host}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req_obj, timeout=300) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    finish = chunk.get("choices", [{}])[0].get("finish_reason")

                    if finish == "tool_calls":
                        stop_reason = "tool_use"
                    elif finish in ("stop", "length"):
                        stop_reason = "end_turn" if finish == "stop" else "max_tokens"

                    # Text delta — buffer to strip <think> tags for thinking models
                    text = delta.get("content") or ""
                    if text:
                        if "<think>" in text or "</think>" in text or _in_think_block:
                            _think_buffer += text
                            cleaned = re.sub(r"<think>.*?</think>", "", _think_buffer, flags=re.DOTALL)
                            if cleaned != _think_buffer and not re.search(r"<think>(?!.*</think>)", cleaned, re.DOTALL):
                                text = cleaned
                                _think_buffer = ""
                                _in_think_block = False
                            else:
                                _in_think_block = "<think>" in _think_buffer and "</think>" not in _think_buffer
                                continue
                        if text:
                            output_tokens += len(text) // 4 + 1
                            yield _sse("content_block_delta", {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": text},
                            })

                    # Tool call deltas
                    for tc in delta.get("tool_calls", []):
                        idx = tc.get("index", 0)
                        if idx not in tool_call_accumulator:
                            tool_call_accumulator[idx] = {
                                "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:16]}"),
                                "name": tc.get("function", {}).get("name", ""),
                                "args_json": "",
                            }
                        acc = tool_call_accumulator[idx]
                        if tc.get("function", {}).get("name"):
                            acc["name"] = tc["function"]["name"]
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        acc["args_json"] += tc.get("function", {}).get("arguments", "")
            break  # success
        except (urllib.error.URLError, OSError) as e:
            if attempt < cfg_retries:
                wait = (base_ms * (2 ** attempt)) / 1000.0
                log.warning("Ollama request failed (attempt %d): %s — retry in %.2fs", attempt + 1, e, wait)
                time.sleep(wait)
            else:
                log.error("Ollama request failed after %d retries: %s", cfg_retries + 1, e)
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": f"\n[Bridge Error: {e}]"},
                })

    # content_block_stop for text
    yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})

    # Emit tool_use blocks
    block_idx = 1
    for idx in sorted(tool_call_accumulator):
        acc = tool_call_accumulator[idx]
        try:
            tool_input = json.loads(acc["args_json"]) if acc["args_json"] else {}
        except json.JSONDecodeError:
            tool_input = {"raw": acc["args_json"]}

        yield _sse("content_block_start", {
            "type": "content_block_start",
            "index": block_idx,
            "content_block": {
                "type": "tool_use",
                "id": acc["id"],
                "name": acc["name"],
                "input": {},
            },
        })
        yield _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": block_idx,
            "delta": {
                "type": "input_json_delta",
                "partial_json": acc["args_json"],
            },
        })
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": block_idx})
        block_idx += 1

    # message_delta
    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": max(1, output_tokens)},
    })

    # message_stop
    yield _sse("message_stop", {"type": "message_stop"})


def non_streaming_response(
    ollama_host: str,
    openai_req: dict,
    orig_model: str,
    cache_layer: Optional[PromptCacheLayer],
    system_text: str,
    input_tokens_approx: int,
) -> dict:
    """Blocking call; returns Anthropic Messages response dict."""
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"

    cache_creation = cache_read = 0
    if cache_layer and system_text:
        cache_creation, cache_read = cache_layer.record(system_text, len(system_text) // 4)

    body = json.dumps({**openai_req, "stream": False}).encode()
    req_obj = urllib.request.Request(
        f"{ollama_host}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_obj, timeout=300) as resp:
        data = json.loads(resp.read())

    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    finish = choice.get("finish_reason", "stop")
    stop_reason = "tool_use" if finish == "tool_calls" else (
        "max_tokens" if finish == "length" else "end_turn"
    )

    content_blocks: list[dict] = []
    text = msg.get("content") or ""
    if text:
        text = LocalModelOptimizer.strip_thinking_tags(text)
    if text:
        content_blocks.append({"type": "text", "text": text})

    for tc in msg.get("tool_calls", []):
        try:
            tool_input = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            tool_input = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:16]}"),
            "name": tc["function"]["name"],
            "input": tool_input,
        })

    usage = data.get("usage", {})
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": orig_model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", input_tokens_approx),
            "output_tokens": usage.get("completion_tokens", 0),
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
        },
    }


# ---------------------------------------------------------------------------
# HTTP handler factory
# ---------------------------------------------------------------------------
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

    class BridgeHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args):
            log.debug("HTTP %s", fmt % args)

        def _send_json(self, code: int, obj: dict):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, code: int, msg: str):
            self._send_json(code, {"error": {"type": "bridge_error", "message": msg}})

        def do_GET(self):
            if self.path == "/health":
                self._send_json(200, {"status": "ok", "bridge": "bridge_proxy_full"})
            elif self.path.startswith("/v1/models"):
                self._send_json(200, {
                    "object": "list",
                    "data": [{"id": config.primary_model, "object": "model"}],
                })
            else:
                self._send_error(404, "Not found")

        def do_POST(self):
            if self.path != "/v1/messages":
                self._send_error(404, "Only /v1/messages is supported")
                return

            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                self._send_error(400, "Invalid JSON")
                return

            orig_model = req.get("model", config.primary_model)
            is_streaming = req.get("stream", False)

            # Extract system text
            system_raw = req.get("system", "")
            system_text = _extract_text(system_raw) if system_raw else ""
            user_text = optimizer.extract_user_text(req.get("messages", []))
            approx_input = (len(system_text) + len(user_text)) // 4

            # --- TRANSCRIPT_CLASSIFIER ---
            if config.enable_classifier:
                approved, score, reasons = classifier.is_auto_approved(user_text)
                if not approved:
                    log.warning(
                        "CLASSIFIER: risk=%.1f reasons=%s — REQUEST BLOCKED", score, reasons
                    )
                    self._send_error(
                        403,
                        f"Request blocked by safety classifier (risk={score:.1f}, reasons={reasons}). "
                        "Please rephrase or remove dangerous patterns.",
                    )
                    return
                if score > 0:
                    log.info("CLASSIFIER: risk=%.1f (auto-approved)", score)

            # --- TEAMMEM: inject persistent memory ---
            if config.enable_teammem:
                mem_block = team_mem.as_context_block()
                if mem_block:
                    system_text = system_text + "\n\n" + mem_block if system_text else mem_block

            # --- KAIROS: inject findings ---
            if config.enable_kairos and kairos:
                findings = kairos.pop_findings()
                if findings:
                    kairos_block = "## KAIROS Background Findings\n" + "\n".join(f"- {f}" for f in findings)
                    system_text = system_text + "\n\n" + kairos_block if system_text else kairos_block

            # --- RAG: inject relevant code context ---
            if config.enable_rag and user_text:
                rag_ctx = rag.build_context(user_text)
                if rag_ctx:
                    system_text = system_text + "\n\n" + rag_ctx if system_text else rag_ctx

            # --- ULTRAPLAN: inject plan for complex requests ---
            if config.enable_ultraplan and ultraplan.is_complex(user_text):
                log.info("ULTRAPLAN: generating plan for complex request")
                plan = ultraplan.generate_plan(user_text)
                if plan:
                    system_text = ultraplan.inject_plan(system_text, plan)

            # --- MCP: inject available tools ---
            if config.enable_mcp:
                mcp_tools = mcp.get_all_tools()
                if mcp_tools:
                    existing_tools = req.get("tools", [])
                    req = dict(req)
                    req["tools"] = existing_tools + mcp_tools

            # Patch system back into request
            if system_text:
                req = dict(req)
                req["system"] = system_text

            # --- COORDINATOR_MODE: decompose multi-part tasks ---
            if config.enable_coordinator and coordinator.is_multi_task(user_text):
                log.info("COORDINATOR: decomposing multi-task request")
                subtasks = coordinator.decompose(user_text)
                if len(subtasks) > 1:
                    log.info("COORDINATOR: %d subtasks", len(subtasks))
                    combined_result = coordinator.run_sequential(subtasks, system_text)
                    # Return as a single non-streaming response
                    resp_obj = {
                        "id": f"msg_{uuid.uuid4().hex[:16]}",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": combined_result}],
                        "model": orig_model,
                        "stop_reason": "end_turn",
                        "stop_sequence": None,
                        "usage": {
                            "input_tokens": approx_input,
                            "output_tokens": len(combined_result) // 4,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    }
                    self._send_json(200, resp_obj)
                    return

            # Convert to OpenAI format
            openai_req = convert_anthropic_to_openai(req, config.primary_model, config)
            # Qwen3 thinking mode: activate via /think prefix
            if (config.enable_thinking
                    and is_thinking_model(config.primary_model, config.thinking_models)):
                openai_req["messages"] = optimizer.apply_thinking_mode(openai_req["messages"])
                log.debug("THINKING: activated for model %s", config.primary_model)

            # Streaming path
            if is_streaming:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                gen = stream_openai_to_anthropic(
                    config.ollama_host,
                    openai_req,
                    orig_model,
                    cache_layer if config.enable_cache else None,
                    system_text,
                    approx_input,
                )
                try:
                    for chunk in gen:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except BrokenPipeError:
                    pass

            else:
                # Non-streaming path
                try:
                    resp_obj = non_streaming_response(
                        config.ollama_host,
                        openai_req,
                        orig_model,
                        cache_layer if config.enable_cache else None,
                        system_text,
                        approx_input,
                    )

                    # --- VERIFICATION_AGENT ---
                    if config.enable_verification:
                        resp_text = ""
                        for block in resp_obj.get("content", []):
                            if block.get("type") == "text":
                                resp_text += block.get("text", "")
                        if len(resp_text) >= config.verification_min_tokens:
                            passed, feedback = verifier.verify(user_text, resp_text)
                            if not passed:
                                log.info("VERIFICATION: FAIL — retrying. Feedback: %s", feedback[:100])
                                retry_msgs = optimizer.inject_error_context(
                                    openai_req["messages"], feedback
                                )
                                openai_req2 = dict(openai_req)
                                openai_req2["messages"] = retry_msgs
                                resp_obj = non_streaming_response(
                                    config.ollama_host,
                                    openai_req2,
                                    orig_model,
                                    None,
                                    system_text,
                                    approx_input,
                                )

                    # Auto-save interesting context to TEAMMEM
                    if config.enable_teammem and resp_obj.get("stop_reason") == "end_turn":
                        _try_extract_memory(team_mem, user_text, resp_obj)

                    self._send_json(200, resp_obj)
                except Exception as e:
                    log.error("non-streaming error: %s", e)
                    self._send_error(500, str(e))

    return BridgeHandler


def _try_extract_memory(team_mem: TeamMemory, user_text: str, resp_obj: dict):
    """Heuristically extract facts to persist in TeamMemory."""
    text_parts = [
        b.get("text", "") for b in resp_obj.get("content", []) if b.get("type") == "text"
    ]
    combined = " ".join(text_parts)
    # If the user asked to remember something
    if re.search(r"\b(?:remember|save|note|store)\b", user_text, re.I):
        key = f"mem_{int(time.time())}"
        team_mem.set(key, {"q": user_text[:200], "a": combined[:400]})
        log.info("TEAMMEM: auto-saved entry %s", key)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Full-featured Anthropic↔Ollama bridge proxy")
    p.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=9099, help="Bind port (default: 9099)")
    p.add_argument("--ollama", default="http://localhost:11434", help="Ollama base URL")
    p.add_argument("--model", default="qwen3:8b", help="Primary Ollama model")
    p.add_argument("--embed-model", default="nomic-embed-text", help="Embedding model")
    p.add_argument("--verify-model", default="", help="Verification model (default: same as primary)")

    p.add_argument("--no-cache", action="store_true", help="Disable prompt cache simulation")
    p.add_argument("--no-rag", action="store_true", help="Disable vector RAG")
    p.add_argument("--no-mcp", action="store_true", help="Disable MCP servers")
    p.add_argument("--no-kairos", action="store_true", help="Disable KAIROS daemon")
    p.add_argument("--no-coordinator", action="store_true", help="Disable COORDINATOR_MODE")
    p.add_argument("--no-classifier", action="store_true", help="Disable transcript classifier")
    p.add_argument("--no-ultraplan", action="store_true", help="Disable ULTRAPLAN")
    p.add_argument("--enable-verification", action="store_true", help="Enable VERIFICATION_AGENT")
    p.add_argument("--no-teammem", action="store_true", help="Disable persistent team memory")

    p.add_argument("--rag-index", default=".bridge_rag_index.json", help="RAG index file path")
    p.add_argument("--rag-dirs", nargs="*", default=["."], help="Directories to index for RAG")
    p.add_argument("--rag-top-k", type=int, default=5, help="RAG top-K results")
    p.add_argument("--rag-threshold", type=float, default=0.30, help="RAG similarity threshold")
    p.add_argument("--kairos-interval", type=float, default=30.0, help="KAIROS tick interval (s)")
    p.add_argument("--teammem-path", default=".bridge_memory.json", help="TeamMemory file")

    p.add_argument("--mcp-server", nargs="+", action="append", metavar="CMD",
                   help="MCP server command (can repeat). First arg is server name, rest is command.")
    p.add_argument("--index-now", action="store_true", help="Index RAG directories on startup")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    p.add_argument("--no-thinking", action="store_true", help="Disable thinking mode for Qwen3/DeepSeek-R1")
    p.add_argument("--thinking-budget", type=int, default=8192, help="Max thinking tokens (default: 8192)")
    return p.parse_args()


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cfg = ProxyConfig(
        ollama_host=args.ollama,
        primary_model=args.model,
        embed_model=args.embed_model,
        verify_model=args.verify_model or args.model,
        proxy_port=args.port,
        proxy_host=args.host,
        enable_cache=not args.no_cache,
        enable_rag=not args.no_rag,
        enable_mcp=not args.no_mcp,
        enable_kairos=not args.no_kairos,
        enable_coordinator=not args.no_coordinator,
        enable_classifier=not args.no_classifier,
        enable_ultraplan=not args.no_ultraplan,
        enable_verification=args.enable_verification,
        enable_teammem=not args.no_teammem,
        enable_thinking=not args.no_thinking,
        thinking_budget_tokens=args.thinking_budget,
        rag_index_path=args.rag_index,
        rag_watch_dirs=args.rag_dirs or ["."],
        rag_top_k=args.rag_top_k,
        rag_threshold=args.rag_threshold,
        kairos_tick_interval=args.kairos_interval,
        teammem_path=args.teammem_path,
    )

    log.info("Starting bridge proxy on %s:%d", cfg.proxy_host, cfg.proxy_port)
    log.info("Primary model: %s | Embed: %s", cfg.primary_model, cfg.embed_model)

    # Initialize components
    cache_layer = PromptCacheLayer()
    team_mem = TeamMemory(cfg.teammem_path) if cfg.enable_teammem else TeamMemory("/dev/null")

    mcp = McpServerManager()
    if cfg.enable_mcp and args.mcp_server:
        for spec in args.mcp_server:
            if len(spec) < 2:
                log.warning("MCP server spec needs at least 2 args: <name> <cmd...>")
                continue
            mcp.add_server(spec[0], spec[1:])

    rag = RagContextInjector(cfg)
    if cfg.enable_rag and args.index_now:
        log.info("RAG: indexing directories (this may take a while)...")
        for d in cfg.rag_watch_dirs:
            rag.index_directory(d)

    kairos_daemon: Optional[KairosDaemon] = None
    if cfg.enable_kairos:
        kairos_daemon = KairosDaemon(cfg, rag)
        kairos_daemon.start()
        log.info("KAIROS: daemon started")

    coord = CoordinatorMode(cfg, cfg.ollama_host, cfg.primary_model)
    clf = TranscriptClassifier(cfg.classifier_auto_approve_threshold)
    uplan = UltraPlan(cfg, cfg.ollama_host, cfg.primary_model)
    verif = VerificationAgent(cfg, cfg.ollama_host)
    optim = LocalModelOptimizer()

    handler_class = make_handler_class(
        cfg, cache_layer, mcp, rag, kairos_daemon,
        coord, clf, uplan, verif, team_mem, optim,
    )

    server = HTTPServer((cfg.proxy_host, cfg.proxy_port), handler_class)
    log.info("Bridge proxy ready. Set: ANTHROPIC_BASE_URL=http://localhost:%d", cfg.proxy_port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        if kairos_daemon:
            kairos_daemon.stop()
        mcp.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
