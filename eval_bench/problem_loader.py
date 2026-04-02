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
