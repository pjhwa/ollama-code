from __future__ import annotations
import os
from datetime import datetime

from reporters.terminal import ProblemRunRecord


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Coding Eval Bench Report</title>
<style>
  body {{ font-family: monospace; background: #1e1e1e; color: #d4d4d4; margin: 2rem; }}
  h1 {{ color: #61dafb; }}
  .summary-cards {{ display: flex; gap: 1rem; margin: 1rem 0; flex-wrap: wrap; }}
  .card {{ background: #252526; padding: 1rem 1.5rem; border-radius: 8px; min-width: 180px; }}
  .card .label {{ color: #888; font-size: 0.8rem; }}
  .card .value {{ font-size: 1.6rem; font-weight: bold; }}
  .ollama {{ color: #f0883e; }}
  .bridge {{ color: #61dafb; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th {{ background: #333; padding: 0.5rem 1rem; text-align: left; }}
  td {{ padding: 0.4rem 1rem; border-bottom: 1px solid #333; }}
  tr:hover {{ background: #2a2a2a; }}
  .pass {{ color: #4ec9b0; }}
  .fail {{ color: #f44747; }}
  .partial {{ color: #dcdcaa; }}
  details summary {{ cursor: pointer; color: #9cdcfe; }}
  pre {{ background: #252526; padding: 1rem; overflow-x: auto; font-size: 0.85rem; }}
  .bar-wrap {{ background: #333; border-radius: 4px; height: 12px; width: 200px; display: inline-block; }}
  .bar {{ height: 12px; border-radius: 4px; }}
  .bar-ollama {{ background: #f0883e; }}
  .bar-bridge {{ background: #61dafb; }}
</style>
</head>
<body>
<h1>Coding Eval Bench — 성능 비교 리포트</h1>
<p style="color:#888">생성: {generated_at}</p>

<h2>전체 요약</h2>
<div class="summary-cards">
  <div class="card">
    <div class="label">ollama-direct 전체 정확도</div>
    <div class="value ollama">{ollama_pass_rate:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">bridge-proxy 전체 정확도</div>
    <div class="value bridge">{bridge_pass_rate:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">ollama-direct 평균 응답시간</div>
    <div class="value ollama">{ollama_avg_time:.1f}s</div>
  </div>
  <div class="card">
    <div class="label">bridge-proxy 평균 응답시간</div>
    <div class="value bridge">{bridge_avg_time:.1f}s</div>
  </div>
</div>

<h2>카테고리별 정확도</h2>
{category_table}

<h2>문제별 상세 결과</h2>
{detail_table}

</body>
</html>
"""


def _pct_bar(rate: float, cls: str) -> str:
    w = int(rate * 200)
    return (
        f'<div class="bar-wrap">'
        f'<div class="bar {cls}" style="width:{w}px"></div>'
        f'</div> {rate*100:.1f}%'
    )


def _pass_class(rate: float) -> str:
    if rate == 1.0:
        return "pass"
    if rate == 0.0:
        return "fail"
    return "partial"


def save(records: list[ProblemRunRecord], output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)

    def _target_rates(target: str):
        rates, times = [], []
        for r in records:
            for t in r.targets:
                if t.target == target:
                    rates.append(t.pass_rate)
                    times.append(t.total_time)
        return rates, times

    o_rates, o_times = _target_rates("ollama-direct")
    b_rates, b_times = _target_rates("bridge-proxy")

    ollama_pass = sum(o_rates) / len(o_rates) * 100 if o_rates else 0
    bridge_pass = sum(b_rates) / len(b_rates) * 100 if b_rates else 0
    ollama_time = sum(o_times) / len(o_times) if o_times else 0
    bridge_time = sum(b_times) / len(b_times) if b_times else 0

    # Category table
    categories = sorted({r.category for r in records})
    cat_rows = ""
    for cat in categories:
        cat_recs = [r for r in records if r.category == cat]
        o = [t for r in cat_recs for t in r.targets if t.target == "ollama-direct"]
        b = [t for r in cat_recs for t in r.targets if t.target == "bridge-proxy"]
        o_rate = sum(t.pass_rate for t in o) / len(o) if o else 0
        b_rate = sum(t.pass_rate for t in b) / len(b) if b else 0
        cat_rows += (
            f"<tr><td>{cat}</td>"
            f"<td>{_pct_bar(o_rate, 'bar-ollama')}</td>"
            f"<td>{_pct_bar(b_rate, 'bar-bridge')}</td></tr>\n"
        )
    category_table = (
        "<table><tr><th>카테고리</th><th>ollama-direct</th><th>bridge-proxy</th></tr>\n"
        + cat_rows
        + "</table>"
    )

    # Detail table
    detail_rows = ""
    for r in records:
        o = next((t for t in r.targets if t.target == "ollama-direct"), None)
        b = next((t for t in r.targets if t.target == "bridge-proxy"), None)

        def _cell(t):
            if t is None:
                return "<td>N/A</td>"
            cls = _pass_class(t.pass_rate)
            return (
                f'<td class="{cls}">{t.passed}/{t.total}'
                f" ({t.total_time:.1f}s, {t.token_count}tok)</td>"
            )

        detail_rows += (
            f"<tr>"
            f"<td><details><summary>{r.problem_id}</summary>"
            f"<b>{r.title}</b><br><b>lang:</b> {r.lang}<br>"
            f"</details></td>"
            f"<td>{r.category}</td>"
            f"{_cell(o)}{_cell(b)}"
            f"</tr>\n"
        )

    detail_table = (
        "<table>"
        "<tr><th>문제</th><th>카테고리</th><th>ollama-direct</th><th>bridge-proxy</th></tr>\n"
        + detail_rows
        + "</table>"
    )

    html = _HTML_TEMPLATE.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ollama_pass_rate=ollama_pass,
        bridge_pass_rate=bridge_pass,
        ollama_avg_time=ollama_time,
        bridge_avg_time=bridge_time,
        category_table=category_table,
        detail_table=detail_table,
    )

    path = os.path.join(output_dir, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
