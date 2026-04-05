# 코딩 성능 평가 도구 설계 명세

**날짜:** 2026-04-02  
**대상 프로젝트:** `~/dev/ollama-code`  
**목적:** `ollama qwen3:14b` 직접 호출과 브릿지 프록시 경유 호출의 코딩 성능을 다각도로 비교 분석

---

## 1. 목표

두 대상의 코딩 성능을 동일한 문제셋으로 자동 평가하여 수치 비교:

| 대상 | 엔드포인트 | 설명 |
|------|-----------|------|
| `ollama-direct` | `http://localhost:11434/api/chat` | Ollama API 직접 호출 |
| `bridge-proxy` | `http://localhost:9099/v1/messages` | 브릿지 경유 (thinking 정제, RAG, ULTRAPLAN 등 포함) |

---

## 2. 디렉터리 구조

```
eval_bench/
├── eval.py                  # 메인 실행기 (진입점, stdlib only)
├── problems/                # 문제 정의 (YAML)
│   ├── code_gen/            # 코드 생성 문제
│   ├── bug_fix/             # 버그 수정 문제
│   ├── unit_test/           # 유닛 테스트 작성 문제
│   ├── code_review/         # 코드 설명/리뷰 문제
│   └── algorithm/           # 알고리즘 문제
├── runners/                 # 언어별 코드 실행기
│   ├── python_runner.py     # subprocess로 Python 실행
│   ├── js_runner.py         # Node.js 실행
│   ├── go_runner.py         # go run 실행
│   └── bash_runner.py       # bash 실행
└── reports/                 # 결과 저장
    └── YYYY-MM-DD_HH-MM/
        ├── results.json     # 구조화된 원시 결과
        └── report.html      # 시각화 리포트
```

---

## 3. 문제 정의 형식 (YAML)

```yaml
id: algo_fibonacci              # 고유 ID
category: algorithm             # code_gen | bug_fix | unit_test | code_review | algorithm
lang: python                    # python | javascript | go | bash | any
title: "피보나치 수열 (메모이제이션)"
prompt: |
  피보나치 수열의 n번째 값을 반환하는 함수 fib(n)을 작성하라.
  메모이제이션을 사용해 O(n) 시간복잡도를 만족해야 한다.

test_cases:
  - input: "0"    # runner에 stdin으로 전달
    output: "0"   # 기대 stdout (strip 후 비교)
  - input: "10"
    output: "55"

timeout_sec: 10   # 실행 타임아웃 (기본값: 10)
tags: [dynamic_programming, recursion]
```

**카테고리별 최소 문제 수:**
- `code_gen`: 5문제 (Python 2, JS 1, Go 1, bash 1)
- `bug_fix`: 4문제
- `unit_test`: 3문제
- `code_review`: 2문제 (출력 비교 대신 키워드 포함 여부 체크)
- `algorithm`: 6문제 (다양한 언어)

총 **20문제 이상** 초기 포함.

---

## 4. 실행 흐름

```
eval.py 실행
  │
  ├─ 1. problems/ YAML 로드 및 필터링 (--category, --id, --target 옵션)
  │
  ├─ 2. 각 문제 × 각 대상 순차 실행
  │      ├─ ollama-direct: POST /api/chat  (Ollama native format)
  │      └─ bridge-proxy:  POST /v1/messages (Anthropic Messages API format)
  │
  ├─ 3. 응답에서 코드 추출
  │      └─ ``` 코드블록 파싱 → 없으면 전체 텍스트에서 추측
  │
  ├─ 4. 언어별 runner로 코드 실행
  │      └─ 각 test_case의 input을 stdin으로 주입, stdout 캡처
  │
  ├─ 5. 채점
  │      ├─ pass@1: 통과 테스트 케이스 수 / 전체 테스트 케이스 수
  │      ├─ TTFT: 첫 청크 도착 시간 (스트리밍 방식)
  │      ├─ total_time: 전체 응답 완료 시간
  │      ├─ token_count: 응답 토큰 수 (가능한 경우)
  │      └─ code_extracted: 코드 추출 성공 여부 (bool)
  │
  └─ 6. 결과 저장 및 출력
         ├─ 터미널: 실시간 진행 + 최종 요약표
         └─ reports/YYYY-MM-DD_HH-MM/: results.json + report.html
```

---

## 5. 평가 지표

| 지표 | 단위 | 설명 |
|------|------|------|
| `pass_rate` | % | 테스트 케이스 통과율 (메인 지표) |
| `ttft` | 초 | Time To First Token |
| `total_time` | 초 | 전체 응답 완료 시간 |
| `token_count` | 개 | 응답 토큰 수 |
| `code_extracted` | bool | 코드 블록 추출 성공 여부 |
| `category_pass_rate` | % | 카테고리별 통과율 집계 |

---

## 6. 언어별 Runner 인터페이스

모든 runner는 동일한 인터페이스를 구현:

```python
def run(code: str, stdin_input: str, timeout: int) -> RunResult:
    """
    Returns:
        RunResult(stdout, stderr, exit_code, elapsed_sec)
    """
```

| Runner | 실행 방법 | 전제조건 |
|--------|-----------|---------|
| `python_runner` | `python3 -c <code>` 또는 임시 파일 | Python 3.9+ |
| `js_runner` | `node -e <code>` | Node.js 설치 |
| `go_runner` | 임시 디렉터리 + `go run` | Go 설치 |
| `bash_runner` | `bash -c <code>` | bash |

---

## 7. CLI 인터페이스

```
python eval.py [OPTIONS]

옵션:
  --category TEXT      특정 카테고리만 실행 (code_gen, bug_fix, unit_test, code_review, algorithm)
  --id TEXT            특정 문제 ID만 실행
  --target TEXT        ollama | bridge | both (기본: both)
  --ollama-url TEXT    Ollama 서버 URL (기본: http://localhost:11434)
  --bridge-url TEXT    브릿지 서버 URL (기본: http://localhost:9099)
  --model TEXT         Ollama 모델명 (기본: qwen3:14b)
  --timeout INT        전역 타임아웃 초 (기본: 30)
  --output-dir TEXT    리포트 저장 경로 (기본: reports/)
  --no-html            HTML 리포트 생성 생략
  -v, --verbose        상세 출력 (모델 원본 응답 포함)
```

---

## 8. 터미널 출력 형식

```
[1/20] algo_fibonacci (python) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ● ollama-direct   ✓ 4/4   2.3s   312 tok
  ● bridge-proxy    ✓ 4/4   4.1s   289 tok

[2/20] bug_fix_off_by_one (python) ━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ● ollama-direct   ✗ 1/3   3.1s   445 tok
  ● bridge-proxy    ✓ 3/3   5.2s   398 tok

══════════════════════════════════════════════════════════════════
 최종 결과 요약
══════════════════════════════════════════════════════════════════
 카테고리       ollama-direct        bridge-proxy
 ─────────────────────────────────────────────────────────────
 code_gen       80.0%  3.2s avg      85.0%  5.5s avg
 bug_fix        62.5%  3.8s avg      75.0%  6.1s avg
 unit_test      66.7%  4.0s avg      83.3%  5.9s avg
 code_review    50.0%  2.1s avg      70.0%  4.3s avg
 algorithm      58.3%  4.5s avg      66.7%  7.2s avg
 ─────────────────────────────────────────────────────────────
 전체           63.5%  3.5s avg      76.0%  5.8s avg
══════════════════════════════════════════════════════════════════
 리포트 저장: reports/2026-04-02_14-30/report.html
```

---

## 9. HTML 리포트 구성

- 상단: 전체 요약 카드 (두 대상 pass_rate, 평균 응답시간, 토큰 효율)
- 중단: 카테고리별 수평 바 차트 (두 대상 나란히)
- 하단: 문제별 상세 테이블 (클릭하면 모델 원본 응답·추출된 코드·테스트 결과 펼쳐보기)
- 모든 렌더링은 순수 HTML/CSS/JS (외부 라이브러리 없음)

---

## 10. 의존성

- Python 3.9+ (`pip install pyyaml` 필요 — 유일한 외부 의존성)
- Ollama 실행 중
- 브릿지 프록시 실행 중 (`bridge_proxy_full.py`)
- 언어별 런타임 (Node.js, Go 등 — 해당 언어 문제 실행 시에만 필요)

---

## 11. 범위 외 (비구현)

- CI/CD 자동화 연동
- 분산 실행 / 병렬 문제 실행
- 외부 벤치마크(HumanEval, MBPP) 통합
- GPU 사용률 등 시스템 리소스 측정
