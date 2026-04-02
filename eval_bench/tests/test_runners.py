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
