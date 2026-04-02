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
