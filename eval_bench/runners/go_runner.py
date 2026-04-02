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
