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
