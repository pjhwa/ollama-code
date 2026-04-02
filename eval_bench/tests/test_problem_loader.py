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
