from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TargetRunRecord:
    target: str            # "ollama-direct" | "bridge-proxy"
    pass_rate: float
    passed: int
    total: int
    total_time: float
    ttft: float
    token_count: int
    code_extracted: bool
    error: Optional[str] = None


@dataclass
class ProblemRunRecord:
    problem_id: str
    title: str
    category: str
    lang: str
    targets: list[TargetRunRecord] = field(default_factory=list)


def print_problem_result(idx: int, total: int, record: ProblemRunRecord) -> None:
    bar = "━" * max(0, 55 - len(record.problem_id))
    print(f"\n[{idx}/{total}] {record.problem_id} ({record.lang}) {bar}")
    for t in record.targets:
        icon = "✓" if t.pass_rate == 1.0 else ("✗" if t.pass_rate == 0.0 else "~")
        extracted = "" if t.code_extracted else " [no code]"
        err = f" ERROR: {t.error}" if t.error else ""
        print(
            f"  ● {t.target:<16} {icon} {t.passed}/{t.total}"
            f"   {t.total_time:.1f}s   {t.token_count} tok{extracted}{err}"
        )


def print_summary(records: list[ProblemRunRecord]) -> None:
    categories = sorted({r.category for r in records})
    targets = ["ollama-direct", "bridge-proxy"]

    print("\n" + "═" * 70)
    print(" 최종 결과 요약")
    print("═" * 70)
    header = f" {'카테고리':<14}"
    for t in targets:
        header += f"  {t:<22}"
    print(header)
    print(" " + "─" * 68)

    all_stats: dict[str, dict[str, list]] = {t: {} for t in targets}

    for cat in categories:
        cat_records = [r for r in records if r.category == cat]
        line = f" {cat:<14}"
        for t in targets:
            t_results = [
                tr for r in cat_records for tr in r.targets if tr.target == t
            ]
            if not t_results:
                line += f"  {'N/A':<22}"
                continue
            avg_pass = sum(r.pass_rate for r in t_results) / len(t_results) * 100
            avg_time = sum(r.total_time for r in t_results) / len(t_results)
            cell = f"{avg_pass:5.1f}%  {avg_time:.1f}s avg"
            line += f"  {cell:<22}"
            all_stats[t].setdefault("pass", []).extend(r.pass_rate for r in t_results)
            all_stats[t].setdefault("time", []).extend(r.total_time for r in t_results)
        print(line)

    print(" " + "─" * 68)
    total_line = f" {'전체':<14}"
    for t in targets:
        rates = all_stats[t].get("pass", [])
        times = all_stats[t].get("time", [])
        if not rates:
            total_line += f"  {'N/A':<22}"
            continue
        avg_pass = sum(rates) / len(rates) * 100
        avg_time = sum(times) / len(times)
        cell = f"{avg_pass:5.1f}%  {avg_time:.1f}s avg"
        total_line += f"  {cell:<22}"
    print(total_line)
    print("═" * 70)
