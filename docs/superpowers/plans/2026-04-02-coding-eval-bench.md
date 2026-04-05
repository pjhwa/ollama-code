# Coding Eval Bench 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ollama qwen3:14b 직접 호출과 브릿지 프록시 경유 호출의 코딩 성능을 자동으로 비교 평가하는 CLI 도구를 `~/dev/ollama-code/eval_bench/`에 구축한다.

**Architecture:** 문제(YAML) → 두 LLM 대상에 동일 요청 → 응답에서 코드 추출 → 언어별 runner로 실행 → 채점 → 터미널/JSON/HTML 리포트. 컴포넌트는 독립 모듈로 분리하여 각각 단위 테스트 가능하다.

**Tech Stack:** Python 3.9+ (stdlib), PyYAML, subprocess (코드 실행), urllib (HTTP)

---

## 파일 구조

```
eval_bench/
├── eval.py                        # CLI 진입점 + Evaluator 오케스트레이터
├── runners/
│   ├── __init__.py                # get_runner(lang) 팩토리
│   ├── base.py                    # RunResult 데이터클래스 + run() 인터페이스
│   ├── python_runner.py           # python3 임시파일 실행
│   ├── js_runner.py               # node 임시파일 실행
│   ├── go_runner.py               # go run 임시디렉터리 실행
│   └── bash_runner.py             # bash 임시파일 실행
├── problems/
│   ├── algorithm/                 # 6개 YAML
│   ├── code_gen/                  # 5개 YAML
│   ├── bug_fix/                   # 4개 YAML
│   ├── unit_test/                 # 3개 YAML
│   └── code_review/               # 2개 YAML
├── clients/
│   ├── __init__.py
│   ├── ollama_client.py           # POST /api/chat (Ollama native, streaming)
│   └── bridge_client.py           # POST /v1/messages (Anthropic API format)
├── extractor.py                   # LLM 응답에서 코드블록 파싱
├── scorer.py                      # 테스트케이스 채점 + 지표 계산
├── reporters/
│   ├── __init__.py
│   ├── terminal.py                # 실시간 터미널 출력 + 최종 요약
│   ├── json_reporter.py           # results.json 저장
│   └── html_reporter.py           # report.html 생성 (차트 + 상세)
├── tests/
│   ├── test_runners.py            # 각 runner 단위테스트
│   ├── test_extractor.py          # 코드 추출 단위테스트
│   ├── test_scorer.py             # 채점 단위테스트
│   └── test_problem_loader.py     # YAML 로딩 단위테스트
└── reports/                       # 실행 결과 저장 (git ignore)
```

---

## Task 1: 프로젝트 스캐폴드 + RunResult 데이터클래스

**Files:**
- Create: `eval_bench/runners/base.py`
- Create: `eval_bench/runners/__init__.py`
- Create: `eval_bench/tests/test_runners.py`

- [ ] **Step 1: 디렉터리 구조 생성**

```bash
cd ~/dev/ollama-code
mkdir -p eval_bench/runners eval_bench/clients eval_bench/reporters \
         eval_bench/tests eval_bench/reports \
         eval_bench/problems/algorithm eval_bench/problems/code_gen \
         eval_bench/problems/bug_fix eval_bench/problems/unit_test \
         eval_bench/problems/code_review
touch eval_bench/runners/__init__.py eval_bench/clients/__init__.py \
      eval_bench/reporters/__init__.py
```

- [ ] **Step 2: base.py — RunResult 데이터클래스 작성**

`eval_bench/runners/base.py`:
```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    elapsed_sec: float

    @property
    def success(self) -> bool:
        return self.exit_code == 0
```

- [ ] **Step 3: RunResult 테스트 작성**

`eval_bench/tests/test_runners.py`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from runners.base import RunResult


def test_run_result_success():
    r = RunResult(stdout="hello\n", stderr="", exit_code=0, elapsed_sec=0.1)
    assert r.success is True


def test_run_result_failure():
    r = RunResult(stdout="", stderr="error", exit_code=1, elapsed_sec=0.2)
    assert r.success is False


def test_run_result_fields():
    r = RunResult(stdout="out", stderr="err", exit_code=0, elapsed_sec=1.5)
    assert r.stdout == "out"
    assert r.stderr == "err"
    assert r.elapsed_sec == 1.5
```

- [ ] **Step 4: 테스트 실행 — PASS 확인**

```bash
cd ~/dev/ollama-code
python -m pytest eval_bench/tests/test_runners.py::test_run_result_success \
                 eval_bench/tests/test_runners.py::test_run_result_failure \
                 eval_bench/tests/test_runners.py::test_run_result_fields -v
```
Expected: 3 PASSED

- [ ] **Step 5: 커밋**

```bash
git add eval_bench/
git commit -m "feat(eval-bench): scaffold project structure and RunResult dataclass"
```

---

## Task 2: Python Runner

**Files:**
- Create: `eval_bench/runners/python_runner.py`
- Modify: `eval_bench/tests/test_runners.py`

- [ ] **Step 1: Python runner 테스트 추가**

`eval_bench/tests/test_runners.py` 끝에 추가:
```python
from runners.python_runner import run as py_run


def test_python_runner_hello():
    result = py_run('print("hello")', "", timeout=5)
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_python_runner_stdin():
    code = "import sys; n = int(sys.stdin.read().strip()); print(n * 2)"
    result = py_run(code, "21", timeout=5)
    assert result.stdout.strip() == "42"


def test_python_runner_syntax_error():
    result = py_run("def foo(:", "", timeout=5)
    assert result.exit_code != 0
    assert result.stderr != ""


def test_python_runner_timeout():
    result = py_run("import time; time.sleep(10)", "", timeout=1)
    assert result.exit_code != 0
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
python -m pytest eval_bench/tests/test_runners.py -k "python" -v
```
Expected: ImportError — `python_runner` not found

- [ ] **Step 3: python_runner.py 구현**

`eval_bench/runners/python_runner.py`:
```python
from __future__ import annotations
import subprocess
import tempfile
import time
import os

from runners.base import RunResult


def run(code: str, stdin_input: str, timeout: int = 10) -> RunResult:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        tmp_path = f.name
    try:
        start = time.time()
        proc = subprocess.run(
            ["python3", tmp_path],
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.time() - start
        return RunResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            elapsed_sec=elapsed,
        )
    except subprocess.TimeoutExpired:
        return RunResult(stdout="", stderr="timeout", exit_code=-1, elapsed_sec=float(timeout))
    finally:
        os.unlink(tmp_path)
```

- [ ] **Step 4: 테스트 실행 — PASS 확인**

```bash
python -m pytest eval_bench/tests/test_runners.py -k "python" -v
```
Expected: 4 PASSED

- [ ] **Step 5: 커밋**

```bash
git add eval_bench/runners/python_runner.py eval_bench/tests/test_runners.py
git commit -m "feat(eval-bench): add Python runner with timeout support"
```

---

## Task 3: JS / Go / Bash Runner

**Files:**
- Create: `eval_bench/runners/js_runner.py`
- Create: `eval_bench/runners/go_runner.py`
- Create: `eval_bench/runners/bash_runner.py`
- Modify: `eval_bench/tests/test_runners.py`

- [ ] **Step 1: JS / Go / Bash runner 테스트 추가**

`eval_bench/tests/test_runners.py` 끝에 추가:
```python
from runners.js_runner import run as js_run
from runners.go_runner import run as go_run
from runners.bash_runner import run as bash_run


def test_js_runner_hello():
    result = js_run('process.stdout.write("hello\\n")', "", timeout=5)
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_js_runner_stdin():
    code = (
        "const lines = [];\n"
        "process.stdin.on('data', d => lines.push(d.toString()));\n"
        "process.stdin.on('end', () => { const n = parseInt(lines.join('').trim()); console.log(n * 3); });\n"
    )
    result = js_run(code, "7", timeout=5)
    assert result.stdout.strip() == "21"


def test_go_runner_hello():
    code = 'package main\nimport "fmt"\nfunc main() { fmt.Println("hello") }'
    result = go_run(code, "", timeout=15)
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_bash_runner_hello():
    result = bash_run('echo "hello"', "", timeout=5)
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_bash_runner_stdin():
    result = bash_run("read n; echo $((n * 4))", "5", timeout=5)
    assert result.stdout.strip() == "20"
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
python -m pytest eval_bench/tests/test_runners.py -k "js or go or bash" -v
```
Expected: ImportError

- [ ] **Step 3: js_runner.py 구현**

`eval_bench/runners/js_runner.py`:
```python
from __future__ import annotations
import subprocess
import tempfile
import time
import os

from runners.base import RunResult


def run(code: str, stdin_input: str, timeout: int = 10) -> RunResult:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
        f.write(code)
        tmp_path = f.name
    try:
        start = time.time()
        proc = subprocess.run(
            ["node", tmp_path],
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.time() - start
        return RunResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            elapsed_sec=elapsed,
        )
    except subprocess.TimeoutExpired:
        return RunResult(stdout="", stderr="timeout", exit_code=-1, elapsed_sec=float(timeout))
    except FileNotFoundError:
        return RunResult(stdout="", stderr="node not found", exit_code=-2, elapsed_sec=0.0)
    finally:
        os.unlink(tmp_path)
```

- [ ] **Step 4: go_runner.py 구현**

`eval_bench/runners/go_runner.py`:
```python
from __future__ import annotations
import subprocess
import tempfile
import time
import os
import shutil

from runners.base import RunResult


def run(code: str, stdin_input: str, timeout: int = 15) -> RunResult:
    tmp_dir = tempfile.mkdtemp()
    go_file = os.path.join(tmp_dir, "main.go")
    try:
        with open(go_file, "w") as f:
            f.write(code)
        start = time.time()
        proc = subprocess.run(
            ["go", "run", go_file],
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tmp_dir,
        )
        elapsed = time.time() - start
        return RunResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            elapsed_sec=elapsed,
        )
    except subprocess.TimeoutExpired:
        return RunResult(stdout="", stderr="timeout", exit_code=-1, elapsed_sec=float(timeout))
    except FileNotFoundError:
        return RunResult(stdout="", stderr="go not found", exit_code=-2, elapsed_sec=0.0)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 5: bash_runner.py 구현**

`eval_bench/runners/bash_runner.py`:
```python
from __future__ import annotations
import subprocess
import time

from runners.base import RunResult


def run(code: str, stdin_input: str, timeout: int = 10) -> RunResult:
    try:
        start = time.time()
        proc = subprocess.run(
            ["bash", "-c", code],
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.time() - start
        return RunResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            elapsed_sec=elapsed,
        )
    except subprocess.TimeoutExpired:
        return RunResult(stdout="", stderr="timeout", exit_code=-1, elapsed_sec=float(timeout))
```

- [ ] **Step 6: runners/__init__.py — 팩토리 함수**

`eval_bench/runners/__init__.py`:
```python
from runners.python_runner import run as _py_run
from runners.js_runner import run as _js_run
from runners.go_runner import run as _go_run
from runners.bash_runner import run as _bash_run
from runners.base import RunResult


_RUNNERS = {
    "python": _py_run,
    "javascript": _js_run,
    "js": _js_run,
    "go": _go_run,
    "bash": _bash_run,
}


def get_runner(lang: str):
    """Return run(code, stdin_input, timeout) callable for given language."""
    key = lang.lower()
    if key not in _RUNNERS:
        raise ValueError(f"Unsupported language: {lang!r}. Supported: {list(_RUNNERS)}")
    return _RUNNERS[key]
```

- [ ] **Step 7: 테스트 실행 — PASS 확인**

```bash
python -m pytest eval_bench/tests/test_runners.py -v
```
Expected: 전체 PASSED (go/node 미설치 시 go/js 테스트 SKIP 허용)

- [ ] **Step 8: 커밋**

```bash
git add eval_bench/runners/
git commit -m "feat(eval-bench): add JS, Go, Bash runners and factory"
```

---

## Task 4: 코드 추출기 (extractor.py)

**Files:**
- Create: `eval_bench/extractor.py`
- Create: `eval_bench/tests/test_extractor.py`

- [ ] **Step 1: 추출기 테스트 작성**

`eval_bench/tests/test_extractor.py`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from extractor import extract_code


def test_extract_fenced_python():
    text = "Here is the solution:\n```python\nprint('hello')\n```\nDone."
    assert extract_code(text, "python") == "print('hello')"


def test_extract_fenced_no_lang():
    text = "Solution:\n```\nx = 1\nprint(x)\n```"
    assert extract_code(text, "python") == "x = 1\nprint(x)"


def test_extract_multiple_blocks_returns_first():
    text = "```python\nfirst()\n```\nand\n```python\nsecond()\n```"
    assert extract_code(text, "python") == "first()"


def test_extract_no_block_returns_full_text():
    text = "print('hello')"
    result = extract_code(text, "python")
    assert "print" in result


def test_extract_strips_think_tags():
    text = "<think>reasoning here</think>\n```python\nprint(42)\n```"
    assert extract_code(text, "python") == "print(42)"


def test_extract_javascript():
    text = "```javascript\nconsole.log('hi');\n```"
    assert extract_code(text, "javascript") == "console.log('hi');"


def test_extract_go():
    code = 'package main\nimport "fmt"\nfunc main() { fmt.Println("hi") }'
    text = f"```go\n{code}\n```"
    assert extract_code(text, "go") == code
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
python -m pytest eval_bench/tests/test_extractor.py -v
```
Expected: ImportError

- [ ] **Step 3: extractor.py 구현**

`eval_bench/extractor.py`:
```python
from __future__ import annotations
import re


# Language aliases for fenced block matching
_LANG_ALIASES: dict[str, list[str]] = {
    "python": ["python", "py", "python3"],
    "javascript": ["javascript", "js", "node"],
    "go": ["go", "golang"],
    "bash": ["bash", "sh", "shell"],
}


def _aliases(lang: str) -> list[str]:
    lang = lang.lower()
    return _LANG_ALIASES.get(lang, [lang])


def extract_code(text: str, lang: str) -> str:
    """Extract first code block from LLM response.

    Priority:
    1. Fenced block with matching language tag (```python ... ```)
    2. Fenced block with no language tag (``` ... ```)
    3. Full text stripped of <think> tags
    """
    # Strip <think> tags (Qwen3 thinking output)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Try fenced block with matching language
    for alias in _aliases(lang):
        pattern = rf"```{re.escape(alias)}\n(.*?)```"
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()

    # Try any fenced block
    m = re.search(r"```\w*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Fallback: return stripped text
    return text.strip()
```

- [ ] **Step 4: 테스트 실행 — PASS 확인**

```bash
python -m pytest eval_bench/tests/test_extractor.py -v
```
Expected: 7 PASSED

- [ ] **Step 5: 커밋**

```bash
git add eval_bench/extractor.py eval_bench/tests/test_extractor.py
git commit -m "feat(eval-bench): add code extractor with think-tag stripping"
```

---

## Task 5: 문제 로더 (YAML → Problem 데이터클래스)

**Files:**
- Create: `eval_bench/problem_loader.py`
- Create: `eval_bench/tests/test_problem_loader.py`
- Create: `eval_bench/problems/algorithm/py_fibonacci.yaml` (테스트용)

- [ ] **Step 1: 테스트용 YAML 문제 파일 작성**

`eval_bench/problems/algorithm/py_fibonacci.yaml`:
```yaml
id: algo_py_fibonacci
category: algorithm
lang: python
title: "피보나치 수열"
prompt: |
  표준입력으로 정수 n을 받아 피보나치 수열의 n번째 값을 출력하라.
  (0-indexed: fib(0)=0, fib(1)=1, fib(10)=55)
test_cases:
  - input: "0"
    output: "0"
  - input: "1"
    output: "1"
  - input: "10"
    output: "55"
  - input: "30"
    output: "832040"
timeout_sec: 10
tags: [dynamic_programming]
```

- [ ] **Step 2: 문제 로더 테스트 작성**

`eval_bench/tests/test_problem_loader.py`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from problem_loader import load_problems, Problem


PROBLEMS_DIR = os.path.join(os.path.dirname(__file__), "..", "problems")


def test_load_single_problem():
    problems = load_problems(PROBLEMS_DIR, category="algorithm", problem_id="algo_py_fibonacci")
    assert len(problems) == 1
    p = problems[0]
    assert p.id == "algo_py_fibonacci"
    assert p.lang == "python"
    assert p.category == "algorithm"
    assert len(p.test_cases) == 4


def test_test_case_fields():
    problems = load_problems(PROBLEMS_DIR, category="algorithm", problem_id="algo_py_fibonacci")
    tc = problems[0].test_cases[2]
    assert tc.input == "10"
    assert tc.output == "55"


def test_load_by_category():
    problems = load_problems(PROBLEMS_DIR, category="algorithm")
    assert all(p.category == "algorithm" for p in problems)
    assert len(problems) >= 1


def test_load_all():
    problems = load_problems(PROBLEMS_DIR)
    assert len(problems) >= 1


def test_missing_id_returns_empty():
    problems = load_problems(PROBLEMS_DIR, problem_id="nonexistent_id")
    assert problems == []
```

- [ ] **Step 3: 테스트 실행 — FAIL 확인**

```bash
python -m pytest eval_bench/tests/test_problem_loader.py -v
```
Expected: ImportError

- [ ] **Step 4: problem_loader.py 구현**

`eval_bench/problem_loader.py`:
```python
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class TestCase:
    input: str
    output: str


@dataclass
class Problem:
    id: str
    category: str
    lang: str
    title: str
    prompt: str
    test_cases: list[TestCase]
    timeout_sec: int = 10
    tags: list[str] = field(default_factory=list)


def _parse_yaml(path: str) -> Problem:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    test_cases = [
        TestCase(input=str(tc["input"]), output=str(tc["output"]))
        for tc in data.get("test_cases", [])
    ]
    return Problem(
        id=data["id"],
        category=data["category"],
        lang=data["lang"],
        title=data["title"],
        prompt=data["prompt"].strip(),
        test_cases=test_cases,
        timeout_sec=int(data.get("timeout_sec", 10)),
        tags=data.get("tags", []),
    )


def load_problems(
    problems_dir: str,
    category: Optional[str] = None,
    problem_id: Optional[str] = None,
) -> list[Problem]:
    """Load all problems from problems_dir, with optional filters."""
    results: list[Problem] = []

    search_dirs = []
    if category:
        cat_dir = os.path.join(problems_dir, category)
        if os.path.isdir(cat_dir):
            search_dirs.append(cat_dir)
    else:
        for entry in sorted(os.listdir(problems_dir)):
            full = os.path.join(problems_dir, entry)
            if os.path.isdir(full):
                search_dirs.append(full)

    for d in search_dirs:
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".yaml"):
                continue
            try:
                p = _parse_yaml(os.path.join(d, fname))
                if problem_id and p.id != problem_id:
                    continue
                results.append(p)
            except Exception as e:
                print(f"[WARN] Failed to load {fname}: {e}")

    return results
```

- [ ] **Step 5: 테스트 실행 — PASS 확인**

```bash
python -m pytest eval_bench/tests/test_problem_loader.py -v
```
Expected: 5 PASSED

- [ ] **Step 6: 커밋**

```bash
git add eval_bench/problem_loader.py eval_bench/tests/test_problem_loader.py \
        eval_bench/problems/algorithm/py_fibonacci.yaml
git commit -m "feat(eval-bench): add problem loader with YAML parsing"
```

---

## Task 6: LLM 클라이언트 — Ollama Direct

**Files:**
- Create: `eval_bench/clients/ollama_client.py`

> **참고:** 이 Task는 실제 Ollama 서버가 필요하므로 단위테스트 대신 수동 smoke test로 검증한다.

- [ ] **Step 1: ollama_client.py 구현**

`eval_bench/clients/ollama_client.py`:
```python
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str            # 최종 응답 텍스트 (think 태그 포함)
    ttft: float          # Time To First Token (초)
    total_time: float    # 전체 응답 완료 시간 (초)
    token_count: int     # 응답 토큰 수 (eval_count 기준)
    raw: str             # 원본 응답 전체 (디버그용)


def call(
    prompt: str,
    model: str = "qwen3:14b",
    ollama_url: str = "http://localhost:11434",
    timeout: int = 120,
    system: str = "You are an expert programmer. Provide only the requested code.",
) -> LLMResponse:
    """Call Ollama /api/chat with streaming. Returns LLMResponse."""
    url = f"{ollama_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0.1},
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})

    chunks: list[str] = []
    ttft: float = 0.0
    token_count: int = 0
    raw_lines: list[str] = []

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                line = line.decode("utf-8").strip()
                if not line:
                    continue
                raw_lines.append(line)
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                content = obj.get("message", {}).get("content", "")
                if content:
                    if not ttft:
                        ttft = time.time() - start
                    chunks.append(content)

                if obj.get("done"):
                    token_count = obj.get("eval_count", 0)
                    break

    except urllib.error.URLError as e:
        raise ConnectionError(f"Cannot reach Ollama at {ollama_url}: {e}")

    total_time = time.time() - start
    return LLMResponse(
        text="".join(chunks),
        ttft=ttft,
        total_time=total_time,
        token_count=token_count,
        raw="\n".join(raw_lines),
    )
```

- [ ] **Step 2: Smoke test (Ollama 실행 중일 때)**

```bash
cd ~/dev/ollama-code/eval_bench
python -c "
from clients.ollama_client import call
r = call('Write a Python function that returns the sum of two numbers.')
print('TTFT:', r.ttft)
print('Total:', r.total_time)
print('Tokens:', r.token_count)
print('---')
print(r.text[:300])
"
```
Expected: 코드가 포함된 응답 출력, ttft > 0

- [ ] **Step 3: 커밋**

```bash
git add eval_bench/clients/ollama_client.py
git commit -m "feat(eval-bench): add Ollama direct client with streaming TTFT"
```

---

## Task 7: LLM 클라이언트 — Bridge Proxy

**Files:**
- Create: `eval_bench/clients/bridge_client.py`

- [ ] **Step 1: bridge_client.py 구현**

`eval_bench/clients/bridge_client.py`:
```python
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass

from clients.ollama_client import LLMResponse  # reuse dataclass


def call(
    prompt: str,
    model: str = "qwen3:14b",
    bridge_url: str = "http://localhost:9099",
    timeout: int = 180,
    system: str = "You are an expert programmer. Provide only the requested code.",
) -> LLMResponse:
    """Call bridge proxy /v1/messages (Anthropic Messages API format) with streaming."""
    url = f"{bridge_url.rstrip('/')}/v1/messages"
    payload = {
        "model": model,
        "max_tokens": 4096,
        "stream": True,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": "local-ollama-bridge",
            "anthropic-version": "2023-06-01",
        },
    )

    chunks: list[str] = []
    ttft: float = 0.0
    token_count: int = 0
    raw_lines: list[str] = []

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                line = line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                raw_lines.append(data_str)
                if data_str == "[DONE]":
                    break
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # Anthropic SSE event types
                event_type = obj.get("type", "")
                if event_type == "content_block_delta":
                    delta = obj.get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        if not ttft:
                            ttft = time.time() - start
                        chunks.append(text)
                elif event_type == "message_delta":
                    usage = obj.get("usage", {})
                    token_count = usage.get("output_tokens", token_count)
                elif event_type == "message_stop":
                    break

    except urllib.error.URLError as e:
        raise ConnectionError(f"Cannot reach bridge at {bridge_url}: {e}")

    total_time = time.time() - start
    return LLMResponse(
        text="".join(chunks),
        ttft=ttft,
        total_time=total_time,
        token_count=token_count,
        raw="\n".join(raw_lines),
    )
```

- [ ] **Step 2: Smoke test (브릿지 실행 중일 때)**

```bash
cd ~/dev/ollama-code/eval_bench
python -c "
from clients.bridge_client import call
r = call('Write a Python function that returns the sum of two numbers.')
print('TTFT:', r.ttft)
print('Total:', r.total_time)
print('Tokens:', r.token_count)
print('---')
print(r.text[:300])
"
```
Expected: 코드가 포함된 응답 출력

- [ ] **Step 3: 커밋**

```bash
git add eval_bench/clients/bridge_client.py
git commit -m "feat(eval-bench): add bridge proxy client (Anthropic SSE format)"
```

---

## Task 8: 채점기 (scorer.py)

**Files:**
- Create: `eval_bench/scorer.py`
- Create: `eval_bench/tests/test_scorer.py`

- [ ] **Step 1: 채점기 테스트 작성**

`eval_bench/tests/test_scorer.py`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scorer import score_result, ProblemResult, TestCaseResult
from problem_loader import TestCase
from runners.base import RunResult


def _tc(inp, out):
    return TestCase(input=inp, output=out)


def test_all_pass():
    test_cases = [_tc("1", "1"), _tc("10", "55")]
    run_results = [
        RunResult(stdout="1\n", stderr="", exit_code=0, elapsed_sec=0.1),
        RunResult(stdout="55\n", stderr="", exit_code=0, elapsed_sec=0.1),
    ]
    result = score_result("p1", test_cases, run_results)
    assert result.pass_rate == 1.0
    assert result.passed == 2
    assert result.total == 2


def test_partial_pass():
    test_cases = [_tc("1", "1"), _tc("10", "WRONG")]
    run_results = [
        RunResult(stdout="1\n", stderr="", exit_code=0, elapsed_sec=0.1),
        RunResult(stdout="55\n", stderr="", exit_code=0, elapsed_sec=0.1),
    ]
    result = score_result("p1", test_cases, run_results)
    assert result.pass_rate == 0.5
    assert result.passed == 1


def test_exit_code_nonzero_fails():
    test_cases = [_tc("0", "0")]
    run_results = [RunResult(stdout="", stderr="error", exit_code=1, elapsed_sec=0.1)]
    result = score_result("p1", test_cases, run_results)
    assert result.pass_rate == 0.0


def test_output_stripped_comparison():
    """Trailing newlines and spaces should not affect comparison."""
    test_cases = [_tc("0", "hello")]
    run_results = [RunResult(stdout="hello\n  ", stderr="", exit_code=0, elapsed_sec=0.1)]
    result = score_result("p1", test_cases, run_results)
    assert result.pass_rate == 1.0


def test_keyword_check_for_code_review():
    """code_review category: check keywords in output instead of exact match."""
    test_cases = [_tc("", "sql_injection,parameterized")]
    run_results = [
        RunResult(
            stdout="This code is vulnerable to SQL injection. Use parameterized queries.",
            stderr="", exit_code=0, elapsed_sec=0.1,
        )
    ]
    result = score_result("p1", test_cases, run_results, mode="keyword")
    assert result.pass_rate == 1.0
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
python -m pytest eval_bench/tests/test_scorer.py -v
```
Expected: ImportError

- [ ] **Step 3: scorer.py 구현**

`eval_bench/scorer.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field

from problem_loader import TestCase
from runners.base import RunResult


@dataclass
class TestCaseResult:
    input: str
    expected: str
    actual: str
    passed: bool
    exit_code: int


@dataclass
class ProblemResult:
    problem_id: str
    passed: int
    total: int
    test_case_results: list[TestCaseResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0


def score_result(
    problem_id: str,
    test_cases: list[TestCase],
    run_results: list[RunResult],
    mode: str = "exact",  # "exact" | "keyword"
) -> ProblemResult:
    """Score run results against expected test case outputs."""
    passed = 0
    tc_results: list[TestCaseResult] = []

    for tc, rr in zip(test_cases, run_results):
        actual = rr.stdout.strip()
        expected = tc.output.strip()

        if rr.exit_code != 0:
            ok = False
        elif mode == "keyword":
            keywords = [kw.strip() for kw in expected.split(",")]
            ok = all(kw.lower() in actual.lower() for kw in keywords)
        else:
            ok = actual == expected

        if ok:
            passed += 1
        tc_results.append(
            TestCaseResult(
                input=tc.input,
                expected=expected,
                actual=actual,
                passed=ok,
                exit_code=rr.exit_code,
            )
        )

    return ProblemResult(
        problem_id=problem_id,
        passed=passed,
        total=len(test_cases),
        test_case_results=tc_results,
    )
```

- [ ] **Step 4: 테스트 실행 — PASS 확인**

```bash
python -m pytest eval_bench/tests/test_scorer.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: 커밋**

```bash
git add eval_bench/scorer.py eval_bench/tests/test_scorer.py
git commit -m "feat(eval-bench): add scorer with exact and keyword modes"
```

---

## Task 9: 리포터 — 터미널 + JSON

**Files:**
- Create: `eval_bench/reporters/terminal.py`
- Create: `eval_bench/reporters/json_reporter.py`

- [ ] **Step 1: terminal.py 구현**

`eval_bench/reporters/terminal.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TargetRunRecord:
    target: str            # "ollama-direct" | "bridge-proxy"
    pass_rate: float
    passed: int
    total: int
    total_time: float
    ttft: float
    token_count: int
    code_extracted: bool
    error: Optional[str] = None


@dataclass
class ProblemRunRecord:
    problem_id: str
    title: str
    category: str
    lang: str
    targets: list[TargetRunRecord] = field(default_factory=list)


def print_problem_result(idx: int, total: int, record: ProblemRunRecord) -> None:
    bar = "━" * max(0, 55 - len(record.problem_id))
    print(f"\n[{idx}/{total}] {record.problem_id} ({record.lang}) {bar}")
    for t in record.targets:
        icon = "✓" if t.pass_rate == 1.0 else ("✗" if t.pass_rate == 0.0 else "~")
        extracted = "" if t.code_extracted else " [no code]"
        err = f" ERROR: {t.error}" if t.error else ""
        print(
            f"  ● {t.target:<16} {icon} {t.passed}/{t.total}"
            f"   {t.total_time:.1f}s   {t.token_count} tok{extracted}{err}"
        )


def print_summary(records: list[ProblemRunRecord]) -> None:
    categories = sorted({r.category for r in records})
    targets = ["ollama-direct", "bridge-proxy"]

    print("\n" + "═" * 70)
    print(" 최종 결과 요약")
    print("═" * 70)
    header = f" {'카테고리':<14}"
    for t in targets:
        header += f"  {t:<22}"
    print(header)
    print(" " + "─" * 68)

    all_stats: dict[str, dict[str, list]] = {t: {} for t in targets}

    for cat in categories:
        cat_records = [r for r in records if r.category == cat]
        line = f" {cat:<14}"
        for t in targets:
            t_results = [
                tr for r in cat_records for tr in r.targets if tr.target == t
            ]
            if not t_results:
                line += f"  {'N/A':<22}"
                continue
            avg_pass = sum(r.pass_rate for r in t_results) / len(t_results) * 100
            avg_time = sum(r.total_time for r in t_results) / len(t_results)
            cell = f"{avg_pass:5.1f}%  {avg_time:.1f}s avg"
            line += f"  {cell:<22}"
            all_stats[t].setdefault("pass", []).extend(r.pass_rate for r in t_results)
            all_stats[t].setdefault("time", []).extend(r.total_time for r in t_results)
        print(line)

    print(" " + "─" * 68)
    total_line = f" {'전체':<14}"
    for t in targets:
        rates = all_stats[t].get("pass", [])
        times = all_stats[t].get("time", [])
        if not rates:
            total_line += f"  {'N/A':<22}"
            continue
        avg_pass = sum(rates) / len(rates) * 100
        avg_time = sum(times) / len(times)
        cell = f"{avg_pass:5.1f}%  {avg_time:.1f}s avg"
        total_line += f"  {cell:<22}"
    print(total_line)
    print("═" * 70)
```

- [ ] **Step 2: json_reporter.py 구현**

`eval_bench/reporters/json_reporter.py`:
```python
from __future__ import annotations
import json
import os
from datetime import datetime

from reporters.terminal import ProblemRunRecord


def save(records: list[ProblemRunRecord], output_dir: str) -> str:
    """Save results.json to output_dir. Returns file path."""
    os.makedirs(output_dir, exist_ok=True)
    data = {
        "generated_at": datetime.now().isoformat(),
        "problems": [
            {
                "problem_id": r.problem_id,
                "title": r.title,
                "category": r.category,
                "lang": r.lang,
                "targets": [
                    {
                        "target": t.target,
                        "pass_rate": round(t.pass_rate, 4),
                        "passed": t.passed,
                        "total": t.total,
                        "total_time": round(t.total_time, 3),
                        "ttft": round(t.ttft, 3),
                        "token_count": t.token_count,
                        "code_extracted": t.code_extracted,
                        "error": t.error,
                    }
                    for t in r.targets
                ],
            }
            for r in records
        ],
    }
    path = os.path.join(output_dir, "results.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
```

- [ ] **Step 3: 커밋**

```bash
git add eval_bench/reporters/terminal.py eval_bench/reporters/json_reporter.py
git commit -m "feat(eval-bench): add terminal and JSON reporters"
```

---

## Task 10: HTML 리포터

**Files:**
- Create: `eval_bench/reporters/html_reporter.py`

- [ ] **Step 1: html_reporter.py 구현**

`eval_bench/reporters/html_reporter.py`:
```python
from __future__ import annotations
import json
import os
from datetime import datetime

from reporters.terminal import ProblemRunRecord


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Coding Eval Bench Report</title>
<style>
  body { font-family: monospace; background: #1e1e1e; color: #d4d4d4; margin: 2rem; }
  h1 { color: #61dafb; }
  .summary-cards { display: flex; gap: 1rem; margin: 1rem 0; flex-wrap: wrap; }
  .card { background: #252526; padding: 1rem 1.5rem; border-radius: 8px; min-width: 180px; }
  .card .label { color: #888; font-size: 0.8rem; }
  .card .value { font-size: 1.6rem; font-weight: bold; }
  .ollama { color: #f0883e; }
  .bridge { color: #61dafb; }
  table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
  th { background: #333; padding: 0.5rem 1rem; text-align: left; }
  td { padding: 0.4rem 1rem; border-bottom: 1px solid #333; }
  tr:hover { background: #2a2a2a; }
  .pass { color: #4ec9b0; }
  .fail { color: #f44747; }
  .partial { color: #dcdcaa; }
  details summary { cursor: pointer; color: #9cdcfe; }
  pre { background: #252526; padding: 1rem; overflow-x: auto; font-size: 0.85rem; }
  .bar-wrap { background: #333; border-radius: 4px; height: 12px; width: 200px; display: inline-block; }
  .bar { height: 12px; border-radius: 4px; }
  .bar-ollama { background: #f0883e; }
  .bar-bridge { background: #61dafb; }
</style>
</head>
<body>
<h1>Coding Eval Bench — 성능 비교 리포트</h1>
<p style="color:#888">생성: {generated_at}</p>

<h2>전체 요약</h2>
<div class="summary-cards">
  <div class="card">
    <div class="label">ollama-direct 전체 정확도</div>
    <div class="value ollama">{ollama_pass_rate:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">bridge-proxy 전체 정확도</div>
    <div class="value bridge">{bridge_pass_rate:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">ollama-direct 평균 응답시간</div>
    <div class="value ollama">{ollama_avg_time:.1f}s</div>
  </div>
  <div class="card">
    <div class="label">bridge-proxy 평균 응답시간</div>
    <div class="value bridge">{bridge_avg_time:.1f}s</div>
  </div>
</div>

<h2>카테고리별 정확도</h2>
{category_table}

<h2>문제별 상세 결과</h2>
{detail_table}

</body>
</html>
"""


def _pct_bar(rate: float, cls: str) -> str:
    w = int(rate * 200)
    return (
        f'<div class="bar-wrap">'
        f'<div class="bar {cls}" style="width:{w}px"></div>'
        f'</div> {rate*100:.1f}%'
    )


def _pass_class(rate: float) -> str:
    if rate == 1.0:
        return "pass"
    if rate == 0.0:
        return "fail"
    return "partial"


def save(records: list[ProblemRunRecord], output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)

    def _target_rates(target: str):
        rates, times = [], []
        for r in records:
            for t in r.targets:
                if t.target == target:
                    rates.append(t.pass_rate)
                    times.append(t.total_time)
        return rates, times

    o_rates, o_times = _target_rates("ollama-direct")
    b_rates, b_times = _target_rates("bridge-proxy")

    ollama_pass = sum(o_rates) / len(o_rates) * 100 if o_rates else 0
    bridge_pass = sum(b_rates) / len(b_rates) * 100 if b_rates else 0
    ollama_time = sum(o_times) / len(o_times) if o_times else 0
    bridge_time = sum(b_times) / len(b_times) if b_times else 0

    # Category table
    categories = sorted({r.category for r in records})
    cat_rows = ""
    for cat in categories:
        cat_recs = [r for r in records if r.category == cat]
        o = [t for r in cat_recs for t in r.targets if t.target == "ollama-direct"]
        b = [t for r in cat_recs for t in r.targets if t.target == "bridge-proxy"]
        o_rate = sum(t.pass_rate for t in o) / len(o) if o else 0
        b_rate = sum(t.pass_rate for t in b) / len(b) if b else 0
        cat_rows += (
            f"<tr><td>{cat}</td>"
            f"<td>{_pct_bar(o_rate, 'bar-ollama')}</td>"
            f"<td>{_pct_bar(b_rate, 'bar-bridge')}</td></tr>\n"
        )
    category_table = (
        "<table><tr><th>카테고리</th><th>ollama-direct</th><th>bridge-proxy</th></tr>\n"
        + cat_rows
        + "</table>"
    )

    # Detail table
    detail_rows = ""
    for r in records:
        o = next((t for t in r.targets if t.target == "ollama-direct"), None)
        b = next((t for t in r.targets if t.target == "bridge-proxy"), None)

        def _cell(t):
            if t is None:
                return "<td>N/A</td>"
            cls = _pass_class(t.pass_rate)
            return (
                f'<td class="{cls}">{t.passed}/{t.total}'
                f" ({t.total_time:.1f}s, {t.token_count}tok)</td>"
            )

        detail_rows += (
            f"<tr>"
            f"<td><details><summary>{r.problem_id}</summary>"
            f"<b>{r.title}</b><br><b>lang:</b> {r.lang}<br>"
            f"</details></td>"
            f"<td>{r.category}</td>"
            f"{_cell(o)}{_cell(b)}"
            f"</tr>\n"
        )

    detail_table = (
        "<table>"
        "<tr><th>문제</th><th>카테고리</th><th>ollama-direct</th><th>bridge-proxy</th></tr>\n"
        + detail_rows
        + "</table>"
    )

    html = _HTML_TEMPLATE.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ollama_pass_rate=ollama_pass,
        bridge_pass_rate=bridge_pass,
        ollama_avg_time=ollama_time,
        bridge_avg_time=bridge_time,
        category_table=category_table,
        detail_table=detail_table,
    )

    path = os.path.join(output_dir, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
```

- [ ] **Step 2: 커밋**

```bash
git add eval_bench/reporters/html_reporter.py
git commit -m "feat(eval-bench): add HTML reporter with category bar charts"
```

---

## Task 11: 메인 오케스트레이터 + CLI (eval.py)

**Files:**
- Create: `eval_bench/eval.py`

- [ ] **Step 1: eval.py 구현**

`eval_bench/eval.py`:
```python
#!/usr/bin/env python3
"""
eval.py — Coding performance benchmark: ollama-direct vs bridge-proxy

Usage:
    python eval.py [OPTIONS]

Options:
    --category TEXT      Filter by category (code_gen|bug_fix|unit_test|code_review|algorithm)
    --id TEXT            Filter by problem ID
    --target TEXT        ollama|bridge|both (default: both)
    --ollama-url TEXT    default: http://localhost:11434
    --bridge-url TEXT    default: http://localhost:9099
    --model TEXT         default: qwen3:14b
    --timeout INT        LLM response timeout seconds (default: 120)
    --output-dir TEXT    Report output directory (default: reports/)
    --no-html            Skip HTML report
    -v, --verbose        Print raw LLM responses
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

# Allow running from eval_bench/ directory
sys.path.insert(0, os.path.dirname(__file__))

from problem_loader import load_problems, Problem
from extractor import extract_code
from scorer import score_result
from runners import get_runner
from reporters.terminal import (
    ProblemRunRecord,
    TargetRunRecord,
    print_problem_result,
    print_summary,
)
from reporters.json_reporter import save as save_json
from reporters.html_reporter import save as save_html


PROBLEMS_DIR = os.path.join(os.path.dirname(__file__), "problems")


def _run_target(
    problem: Problem,
    target: str,
    ollama_url: str,
    bridge_url: str,
    model: str,
    timeout: int,
    verbose: bool,
) -> TargetRunRecord:
    """Call LLM, extract code, run tests, return TargetRunRecord."""
    # 1. Call LLM
    try:
        if target == "ollama-direct":
            from clients.ollama_client import call
            llm_resp = call(
                problem.prompt,
                model=model,
                ollama_url=ollama_url,
                timeout=timeout,
            )
        else:
            from clients.bridge_client import call
            llm_resp = call(
                problem.prompt,
                model=model,
                bridge_url=bridge_url,
                timeout=timeout,
            )
    except Exception as e:
        return TargetRunRecord(
            target=target,
            pass_rate=0.0, passed=0, total=len(problem.test_cases),
            total_time=0.0, ttft=0.0, token_count=0,
            code_extracted=False, error=str(e),
        )

    if verbose:
        print(f"\n    [RAW {target}]\n{llm_resp.text[:500]}\n")

    # 2. Extract code
    code = extract_code(llm_resp.text, problem.lang)
    code_extracted = bool(code and len(code) > 5)

    # 3. Run test cases
    runner = get_runner(problem.lang)
    mode = "keyword" if problem.category == "code_review" else "exact"

    run_results = []
    for tc in problem.test_cases:
        rr = runner(code, tc.input, timeout=problem.timeout_sec)
        run_results.append(rr)

    # 4. Score
    scored = score_result(problem.id, problem.test_cases, run_results, mode=mode)

    return TargetRunRecord(
        target=target,
        pass_rate=scored.pass_rate,
        passed=scored.passed,
        total=scored.total,
        total_time=llm_resp.total_time,
        ttft=llm_resp.ttft,
        token_count=llm_resp.token_count,
        code_extracted=code_extracted,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Coding eval bench: ollama vs bridge")
    parser.add_argument("--category", default=None)
    parser.add_argument("--id", default=None, dest="problem_id")
    parser.add_argument("--target", default="both", choices=["ollama", "bridge", "both"])
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--bridge-url", default="http://localhost:9099")
    parser.add_argument("--model", default="qwen3:14b")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--no-html", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    problems = load_problems(PROBLEMS_DIR, category=args.category, problem_id=args.problem_id)
    if not problems:
        print("No problems found. Check --category / --id filters.")
        sys.exit(1)

    targets = []
    if args.target in ("ollama", "both"):
        targets.append("ollama-direct")
    if args.target in ("bridge", "both"):
        targets.append("bridge-proxy")

    run_dir = os.path.join(
        args.output_dir,
        datetime.now().strftime("%Y-%m-%d_%H-%M"),
    )

    records: list[ProblemRunRecord] = []

    for idx, problem in enumerate(problems, 1):
        target_records: list[TargetRunRecord] = []
        for target in targets:
            tr = _run_target(
                problem, target,
                args.ollama_url, args.bridge_url,
                args.model, args.timeout, args.verbose,
            )
            target_records.append(tr)

        record = ProblemRunRecord(
            problem_id=problem.id,
            title=problem.title,
            category=problem.category,
            lang=problem.lang,
            targets=target_records,
        )
        records.append(record)
        print_problem_result(idx, len(problems), record)

    print_summary(records)

    json_path = save_json(records, run_dir)
    print(f"\n JSON: {json_path}")

    if not args.no_html:
        html_path = save_html(records, run_dir)
        print(f" HTML: {html_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 커밋**

```bash
git add eval_bench/eval.py
git commit -m "feat(eval-bench): add main orchestrator and CLI (eval.py)"
```

---

## Task 12: 문제셋 작성 — algorithm (6문제)

**Files:** `eval_bench/problems/algorithm/` 아래 YAML 6개

- [ ] **Step 1: py_fibonacci.yaml** — 이미 Task 5에서 작성됨 (건너뜀)

- [ ] **Step 2: py_binary_search.yaml**

`eval_bench/problems/algorithm/py_binary_search.yaml`:
```yaml
id: algo_py_binary_search
category: algorithm
lang: python
title: "이진 탐색"
prompt: |
  표준입력으로 공백으로 구분된 정렬된 정수 배열과 그 다음 줄에 찾을 값 target을 받는다.
  target이 배열에 있으면 인덱스(0-based)를, 없으면 -1을 출력하라.
  이진 탐색 알고리즘을 직접 구현해야 한다 (bisect 모듈 사용 불가).
  
  입력 예시:
  1 3 5 7 9 11
  7
  
  출력 예시:
  3
test_cases:
  - input: "1 3 5 7 9 11\n7"
    output: "3"
  - input: "1 3 5 7 9 11\n1"
    output: "0"
  - input: "1 3 5 7 9 11\n11"
    output: "5"
  - input: "1 3 5 7 9 11\n4"
    output: "-1"
  - input: "2\n2"
    output: "0"
timeout_sec: 10
tags: [binary_search, arrays]
```

- [ ] **Step 3: js_two_sum.yaml**

`eval_bench/problems/algorithm/js_two_sum.yaml`:
```yaml
id: algo_js_two_sum
category: algorithm
lang: javascript
title: "Two Sum"
prompt: |
  표준입력으로 첫째 줄에 공백으로 구분된 정수 배열, 둘째 줄에 target을 받는다.
  합이 target이 되는 두 수의 인덱스를 오름차순으로 공백 구분하여 출력하라.
  정답은 정확히 하나 존재한다.
  
  입력:
  2 7 11 15
  9
  
  출력:
  0 1
test_cases:
  - input: "2 7 11 15\n9"
    output: "0 1"
  - input: "3 2 4\n6"
    output: "1 2"
  - input: "3 3\n6"
    output: "0 1"
timeout_sec: 10
tags: [hash_map, arrays]
```

- [ ] **Step 4: go_merge_sort.yaml**

`eval_bench/problems/algorithm/go_merge_sort.yaml`:
```yaml
id: algo_go_merge_sort
category: algorithm
lang: go
title: "병합 정렬 (Merge Sort)"
prompt: |
  표준입력으로 공백으로 구분된 정수 배열을 받아 병합 정렬로 오름차순 정렬 후 출력하라.
  내장 sort 패키지를 사용하지 말고 직접 구현해야 한다.
  
  입력: 5 2 4 6 1 3
  출력: 1 2 3 4 5 6
test_cases:
  - input: "5 2 4 6 1 3"
    output: "1 2 3 4 5 6"
  - input: "3 1"
    output: "1 3"
  - input: "42"
    output: "42"
  - input: "9 8 7 6 5 4 3 2 1"
    output: "1 2 3 4 5 6 7 8 9"
timeout_sec: 15
tags: [sorting, divide_and_conquer]
```

- [ ] **Step 5: py_lru_cache.yaml**

`eval_bench/problems/algorithm/py_lru_cache.yaml`:
```yaml
id: algo_py_lru_cache
category: algorithm
lang: python
title: "LRU Cache 구현"
prompt: |
  LRU(Least Recently Used) 캐시를 구현하라.
  표준입력 첫 줄: capacity (정수)
  이후 각 줄: "get key" 또는 "put key value" 명령
  get 명령은 해당 키가 있으면 값을, 없으면 -1을 출력한다.
  
  입력:
  2
  put 1 1
  put 2 2
  get 1
  put 3 3
  get 2
  get 3
  
  출력:
  1
  -1
  3
test_cases:
  - input: "2\nput 1 1\nput 2 2\nget 1\nput 3 3\nget 2\nget 3"
    output: "1\n-1\n3"
  - input: "1\nput 1 1\nget 1\nput 2 2\nget 1\nget 2"
    output: "1\n-1\n2"
timeout_sec: 10
tags: [design, hash_map, linked_list]
```

- [ ] **Step 6: bash_find_duplicates.yaml**

`eval_bench/problems/algorithm/bash_find_duplicates.yaml`:
```yaml
id: algo_bash_find_duplicates
category: algorithm
lang: bash
title: "중복 단어 찾기 (bash)"
prompt: |
  표준입력으로 공백으로 구분된 단어 목록을 받아, 2회 이상 등장하는 단어를
  알파벳 오름차순으로 한 줄씩 출력하라. 없으면 아무것도 출력하지 않는다.
  
  입력: apple banana apple cherry banana apple
  출력:
  apple
  banana
test_cases:
  - input: "apple banana apple cherry banana apple"
    output: "apple\nbanana"
  - input: "one two three"
    output: ""
  - input: "z a z b a z"
    output: "a\nz"
timeout_sec: 10
tags: [string, sorting]
```

- [ ] **Step 7: 커밋**

```bash
git add eval_bench/problems/algorithm/
git commit -m "feat(eval-bench): add 6 algorithm problems (py/js/go/bash)"
```

---

## Task 13: 문제셋 작성 — code_gen (5문제)

**Files:** `eval_bench/problems/code_gen/` 아래 YAML 5개

- [ ] **Step 1: py_list_flatten.yaml**

`eval_bench/problems/code_gen/py_list_flatten.yaml`:
```yaml
id: gen_py_list_flatten
category: code_gen
lang: python
title: "중첩 리스트 평탄화"
prompt: |
  표준입력으로 JSON 배열(중첩 가능)을 받아 평탄화(flatten)한 결과를
  공백으로 구분하여 출력하라.
  
  입력: [[1, [2, 3]], [4, [5, [6]]]]
  출력: 1 2 3 4 5 6
test_cases:
  - input: "[[1, [2, 3]], [4, [5, [6]]]]"
    output: "1 2 3 4 5 6"
  - input: "[1, 2, 3]"
    output: "1 2 3"
  - input: "[[[[1]]]]"
    output: "1"
timeout_sec: 10
tags: [recursion, lists]
```

- [ ] **Step 2: py_word_count.yaml**

`eval_bench/problems/code_gen/py_word_count.yaml`:
```yaml
id: gen_py_word_count
category: code_gen
lang: python
title: "단어 빈도 카운터"
prompt: |
  표준입력으로 문자열을 받아 각 단어의 등장 횟수를 세어
  알파벳 오름차순으로 "단어:횟수" 형식으로 한 줄씩 출력하라.
  대소문자는 무시하고 소문자로 처리한다.
  
  입력: Hello world hello
  출력:
  hello:2
  world:1
test_cases:
  - input: "Hello world hello"
    output: "hello:2\nworld:1"
  - input: "a b c a b a"
    output: "a:3\nb:2\nc:1"
  - input: "one"
    output: "one:1"
timeout_sec: 10
tags: [string, hash_map]
```

- [ ] **Step 3: js_debounce.yaml**

`eval_bench/problems/code_gen/js_debounce.yaml`:
```yaml
id: gen_js_debounce
category: code_gen
lang: javascript
title: "Debounce 함수 구현"
prompt: |
  debounce(fn, delay) 함수를 구현하라. debounce된 함수를 연속 호출하면
  마지막 호출 후 delay ms가 지난 뒤에만 fn이 실행된다.
  
  아래 테스트 코드를 실행하면 정확히 아래 출력이 나와야 한다:
  
  const log = [];
  const debounced = debounce((x) => log.push(x), 50);
  debounced(1);
  debounced(2);
  debounced(3);
  setTimeout(() => {
    console.log(log.join(','));
  }, 200);
  
  기대 출력: 3
test_cases:
  - input: ""
    output: "3"
timeout_sec: 10
tags: [closures, async]
```

- [ ] **Step 4: go_word_freq.yaml**

`eval_bench/problems/code_gen/go_word_freq.yaml`:
```yaml
id: gen_go_word_freq
category: code_gen
lang: go
title: "단어 빈도 (Go)"
prompt: |
  표준입력 한 줄을 공백으로 나눠 각 단어의 빈도를 세어
  알파벳 오름차순으로 "단어:횟수" 형식으로 출력하라.
  
  입력: go is great go
  출력:
  go:2
  great:1
  is:1
test_cases:
  - input: "go is great go"
    output: "go:2\ngreat:1\nis:1"
  - input: "a b a"
    output: "a:2\nb:1"
timeout_sec: 15
tags: [string, map]
```

- [ ] **Step 5: bash_csv_column.yaml**

`eval_bench/problems/code_gen/bash_csv_column.yaml`:
```yaml
id: gen_bash_csv_column
category: code_gen
lang: bash
title: "CSV 두 번째 컬럼 추출 (bash)"
prompt: |
  표준입력으로 CSV 데이터(헤더 포함)를 받아 두 번째 컬럼 값만
  헤더를 제외하고 한 줄씩 출력하라.
  
  입력:
  name,age,city
  alice,30,seoul
  bob,25,busan
  
  출력:
  30
  25
test_cases:
  - input: "name,age,city\nalice,30,seoul\nbob,25,busan"
    output: "30\n25"
  - input: "a,b\n1,2\n3,4"
    output: "2\n4"
timeout_sec: 10
tags: [text_processing, csv]
```

- [ ] **Step 6: 커밋**

```bash
git add eval_bench/problems/code_gen/
git commit -m "feat(eval-bench): add 5 code_gen problems (py/js/go/bash)"
```

---

## Task 14: 문제셋 작성 — bug_fix / unit_test / code_review (9문제)

**Files:** `eval_bench/problems/bug_fix/`, `unit_test/`, `code_review/`

- [ ] **Step 1: bug_fix/py_off_by_one.yaml**

`eval_bench/problems/bug_fix/py_off_by_one.yaml`:
```yaml
id: fix_py_off_by_one
category: bug_fix
lang: python
title: "Off-by-one 버그 수정"
prompt: |
  아래 코드는 1부터 n까지의 합을 구하려고 하지만 버그가 있다.
  버그를 수정하여 올바른 코드를 작성하라.
  표준입력으로 n을 받아 1부터 n까지의 합을 출력한다.
  
  버그 있는 코드:
  ```python
  import sys
  n = int(sys.stdin.read().strip())
  total = 0
  for i in range(1, n):   # 버그: n이 포함되지 않음
      total += i
  print(total)
  ```
test_cases:
  - input: "5"
    output: "15"
  - input: "10"
    output: "55"
  - input: "1"
    output: "1"
  - input: "100"
    output: "5050"
timeout_sec: 10
tags: [loops, off_by_one]
```

- [ ] **Step 2: bug_fix/py_mutable_default.yaml**

`eval_bench/problems/bug_fix/py_mutable_default.yaml`:
```yaml
id: fix_py_mutable_default
category: bug_fix
lang: python
title: "가변 기본 인자 버그 수정"
prompt: |
  아래 Python 함수에는 가변 기본 인자(mutable default argument) 버그가 있다.
  버그를 수정하여 올바르게 동작하는 전체 프로그램을 작성하라.
  표준입력으로 공백으로 구분된 두 숫자를 받아, 각각 append 후 리스트를 출력한다.
  두 호출의 리스트는 독립적이어야 한다.
  
  버그 있는 코드:
  ```python
  def add_item(item, lst=[]):  # 버그: 기본 인자가 공유됨
      lst.append(item)
      return lst
  ```
  
  입력: 1 2
  출력:
  [1]
  [2]
test_cases:
  - input: "1 2"
    output: "[1]\n[2]"
  - input: "10 20"
    output: "[10]\n[20]"
timeout_sec: 10
tags: [python_gotchas, functions]
```

- [ ] **Step 3: bug_fix/js_closure_loop.yaml**

`eval_bench/problems/bug_fix/js_closure_loop.yaml`:
```yaml
id: fix_js_closure_loop
category: bug_fix
lang: javascript
title: "클로저 루프 버그 수정 (JS)"
prompt: |
  아래 코드는 0~4를 순서대로 출력하려 하지만 버그가 있다.
  버그를 수정하여 0 1 2 3 4를 한 줄씩 출력하는 코드를 작성하라.
  
  버그 있는 코드:
  ```javascript
  for (var i = 0; i < 5; i++) {
    setTimeout(() => console.log(i), i * 10);
  }
  ```
test_cases:
  - input: ""
    output: "0\n1\n2\n3\n4"
timeout_sec: 10
tags: [closures, var_vs_let]
```

- [ ] **Step 4: bug_fix/py_recursion_missing_base.yaml**

`eval_bench/problems/bug_fix/py_recursion_missing_base.yaml`:
```yaml
id: fix_py_recursion_base
category: bug_fix
lang: python
title: "재귀 기저 조건 버그 수정"
prompt: |
  아래 팩토리얼 함수에 버그가 있다. 수정하여 올바른 프로그램을 작성하라.
  표준입력으로 n을 받아 n!을 출력한다.
  
  버그 있는 코드:
  ```python
  def factorial(n):
      return n * factorial(n - 1)  # 버그: 기저 조건 없음
  ```
test_cases:
  - input: "0"
    output: "1"
  - input: "1"
    output: "1"
  - input: "5"
    output: "120"
  - input: "10"
    output: "3628800"
timeout_sec: 10
tags: [recursion, base_case]
```

- [ ] **Step 5: unit_test/py_stack.yaml**

`eval_bench/problems/unit_test/py_stack.yaml`:
```yaml
id: test_py_stack
category: unit_test
lang: python
title: "Stack 유닛테스트 작성"
prompt: |
  아래 Stack 클래스에 대한 pytest 유닛테스트를 작성하라.
  push, pop, peek, is_empty, size 메서드를 모두 테스트해야 한다.
  빈 스택에서 pop/peek 호출 시 IndexError가 발생하는 것도 테스트하라.
  테스트 파일을 실행하면 모든 테스트가 통과해야 한다.
  
  ```python
  class Stack:
      def __init__(self): self._items = []
      def push(self, item): self._items.append(item)
      def pop(self): 
          if not self._items: raise IndexError("pop from empty stack")
          return self._items.pop()
      def peek(self):
          if not self._items: raise IndexError("peek from empty stack")
          return self._items[-1]
      def is_empty(self): return len(self._items) == 0
      def size(self): return len(self._items)
  ```
  
  위 Stack 클래스를 포함한 완전한 테스트 파일을 작성하라.
  파일을 실행하면 "X passed" 형태의 pytest 출력에서 passed 개수를 출력한다.
  최소 6개 테스트가 통과해야 한다.
  힌트: subprocess로 pytest를 실행하고 통과한 테스트 수를 파싱하여 출력하라.
test_cases:
  - input: ""
    output: "PASS"
timeout_sec: 30
tags: [testing, data_structures]
```

- [ ] **Step 6: unit_test/py_calculator.yaml**

`eval_bench/problems/unit_test/py_calculator.yaml`:
```yaml
id: test_py_calculator
category: unit_test
lang: python
title: "Calculator 유닛테스트 작성"
prompt: |
  아래 Calculator 클래스에 대한 완전한 pytest 테스트를 작성하라.
  add, subtract, multiply, divide를 테스트하고,
  0으로 나누기 시 ZeroDivisionError 발생도 테스트하라.
  
  ```python
  class Calculator:
      def add(self, a, b): return a + b
      def subtract(self, a, b): return a - b
      def multiply(self, a, b): return a * b
      def divide(self, a, b):
          if b == 0: raise ZeroDivisionError("division by zero")
          return a / b
  ```
  
  위 클래스를 포함한 완전한 테스트 파일을 작성하라.
  subprocess로 pytest를 실행하고 결과를 파싱해 모든 테스트가 통과하면 PASS를 출력하라.
test_cases:
  - input: ""
    output: "PASS"
timeout_sec: 30
tags: [testing, oop]
```

- [ ] **Step 7: unit_test/js_array_utils.yaml**

`eval_bench/problems/unit_test/js_array_utils.yaml`:
```yaml
id: test_js_array_utils
category: unit_test
lang: javascript
title: "Array 유틸리티 테스트 작성 (JS)"
prompt: |
  아래 배열 유틸리티 함수들에 대한 테스트를 작성하고 실행 결과를 출력하라.
  최소 5개 이상의 테스트 케이스를 직접 구현하고(assert 사용),
  모두 통과하면 PASS를 출력하라.
  
  ```javascript
  const unique = arr => [...new Set(arr)];
  const flatten = arr => arr.flat(Infinity);
  const chunk = (arr, size) => {
    const result = [];
    for (let i = 0; i < arr.length; i += size) result.push(arr.slice(i, i + size));
    return result;
  };
  ```
test_cases:
  - input: ""
    output: "PASS"
timeout_sec: 10
tags: [testing, arrays, javascript]
```

- [ ] **Step 8: code_review/py_sql_injection.yaml**

`eval_bench/problems/code_review/py_sql_injection.yaml`:
```yaml
id: review_py_sql_injection
category: code_review
lang: python
title: "SQL Injection 취약점 리뷰"
prompt: |
  아래 코드의 보안 취약점을 분석하고 개선 방법을 설명하라.
  
  ```python
  def get_user(username):
      query = f"SELECT * FROM users WHERE username = '{username}'"
      return db.execute(query)
  ```
  
  어떤 취약점이 있으며, 어떻게 수정해야 하는지 설명하라.
test_cases:
  - input: ""
    output: "sql_injection,parameterized"
timeout_sec: 30
tags: [security, sql]
```

- [ ] **Step 9: code_review/py_performance.yaml**

`eval_bench/problems/code_review/py_performance.yaml`:
```yaml
id: review_py_performance
category: code_review
lang: python
title: "성능 이슈 코드 리뷰"
prompt: |
  아래 코드의 성능 문제를 분석하고 개선 방법을 설명하라.
  
  ```python
  def find_duplicates(lst):
      duplicates = []
      for i in range(len(lst)):
          for j in range(i + 1, len(lst)):
              if lst[i] == lst[j] and lst[i] not in duplicates:
                  duplicates.append(lst[i])
      return duplicates
  ```
  
  시간 복잡도 문제와 개선된 O(n) 구현 방법을 설명하라.
test_cases:
  - input: ""
    output: "O(n),set,hash"
timeout_sec: 30
tags: [performance, complexity]
```

- [ ] **Step 10: 커밋**

```bash
git add eval_bench/problems/bug_fix/ eval_bench/problems/unit_test/ \
        eval_bench/problems/code_review/
git commit -m "feat(eval-bench): add bug_fix(4) unit_test(3) code_review(2) problems"
```

---

## Task 15: 통합 연기 테스트 + README

**Files:**
- Create: `eval_bench/README.md`

- [ ] **Step 1: 단위 테스트 전체 실행 확인**

```bash
cd ~/dev/ollama-code
python -m pytest eval_bench/tests/ -v
```
Expected: 모든 테스트 PASSED (go runner 테스트는 go 미설치 시 skip 허용)

- [ ] **Step 2: --help 출력 확인**

```bash
cd ~/dev/ollama-code/eval_bench
python eval.py --help
```
Expected: 옵션 목록 출력 (에러 없음)

- [ ] **Step 3: 단일 문제 smoke test**

```bash
# Ollama만 실행 중인 경우
python eval.py --id algo_py_fibonacci --target ollama -v
```
Expected: 문제 1개 실행, 결과 출력, reports/ 디렉터리 생성

- [ ] **Step 4: README.md 작성**

`eval_bench/README.md`:
```markdown
# Coding Eval Bench

ollama qwen3:14b 직접 호출과 브릿지 프록시 경유 호출의 코딩 성능을 비교 평가하는 CLI 도구.

## 설치

\```bash
pip install pyyaml
\```

## 사전 요구사항

- Ollama 실행 중 (`ollama serve`)
- qwen3:14b 모델 설치 (`ollama pull qwen3:14b`)
- 브릿지 프록시 실행 중 (비교 시, `../run_full_bridge.sh`)

## 사용법

\```bash
# 전체 비교 실행
python eval.py

# 특정 카테고리만
python eval.py --category algorithm

# Ollama만 테스트
python eval.py --target ollama

# 브릿지만 테스트
python eval.py --target bridge

# 특정 문제
python eval.py --id algo_py_fibonacci

# 상세 출력 (원본 LLM 응답 포함)
python eval.py -v
\```

## 문제 추가

`problems/<category>/` 아래 YAML 파일 추가:

\```yaml
id: my_problem_id
category: algorithm
lang: python
title: "문제 제목"
prompt: |
  문제 설명
test_cases:
  - input: "입력"
    output: "기대출력"
timeout_sec: 10
tags: []
\```

## 지원 언어

python, javascript, go, bash
```

- [ ] **Step 5: 최종 커밋**

```bash
git add eval_bench/README.md
git commit -m "feat(eval-bench): add README and complete 20-problem eval bench"
```

---

## 자체 검토 (Self-Review)

**스펙 커버리지 확인:**
- ✅ 두 대상 비교 (ollama-direct, bridge-proxy)
- ✅ 5가지 카테고리 (code_gen, bug_fix, unit_test, code_review, algorithm)
- ✅ 다언어 지원 (python, js, go, bash)
- ✅ 자동 실행 채점 (test_cases pass@1)
- ✅ TTFT, total_time, token_count 측정
- ✅ code_review: keyword 모드 채점
- ✅ 터미널 실시간 출력 + 최종 요약
- ✅ results.json 저장
- ✅ report.html 저장 (바 차트, 상세 테이블)
- ✅ CLI 옵션 (--category, --id, --target, --ollama-url, --bridge-url, --model, --timeout, --no-html, -v)
- ✅ 20문제 이상 (6 algo + 5 code_gen + 4 bug_fix + 3 unit_test + 2 code_review = 20)
- ✅ PyYAML 의존성

**타입 일관성:**
- `RunResult` → `runners/base.py` → 모든 runner에서 사용
- `LLMResponse` → `clients/ollama_client.py` → bridge_client에서 재사용
- `ProblemRunRecord`, `TargetRunRecord` → `reporters/terminal.py` → json/html reporter에서 사용
- `score_result()` → `scorer.py` → eval.py에서 호출

**플레이스홀더 없음** — 모든 task에 실제 코드 포함.
