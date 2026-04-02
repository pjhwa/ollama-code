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
