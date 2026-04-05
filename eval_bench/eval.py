#!/usr/bin/env python3
"""
eval.py — Coding performance benchmark: ollama-direct vs bridge-proxy

Usage:
    python eval.py [OPTIONS]

Options:
    --category TEXT      Filter by category
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
    think: bool = True,
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
                think=think,
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

    # 3. Run test cases / score
    if problem.category == "code_review":
        # code_review: score LLM text directly (no code execution)
        from runners.base import RunResult as _RunResult
        run_results = [
            _RunResult(stdout=llm_resp.text, stderr="", exit_code=0, elapsed_sec=0.0)
            for _ in problem.test_cases
        ]
        scored = score_result(problem.id, problem.test_cases, run_results, mode="keyword")
    else:
        runner = get_runner(problem.lang)
        run_results = []
        for tc in problem.test_cases:
            rr = runner(code, tc.input, timeout=problem.timeout_sec)
            run_results.append(rr)
        scored = score_result(problem.id, problem.test_cases, run_results, mode="exact")

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
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--no-html", action="store_true")
    parser.add_argument("--no-think", action="store_true",
                        help="Disable qwen3 thinking mode for faster ollama-direct responses")
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
                think=not args.no_think,
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
