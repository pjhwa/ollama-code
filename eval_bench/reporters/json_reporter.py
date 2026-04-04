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
