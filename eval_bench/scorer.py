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
            actual_lower = actual.lower()
            ok = all(
                kw.lower().replace("_", " ") in actual_lower or kw.lower() in actual_lower
                for kw in keywords
            )
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
