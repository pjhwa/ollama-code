# Coding Eval Bench

ollama qwen3:14b 직접 호출과 브릿지 프록시 경유 호출의 코딩 성능을 비교 평가하는 CLI 도구.

## 설치

```bash
pip install pyyaml
```

## 사전 요구사항

- Ollama 실행 중 (`ollama serve`)
- qwen3:14b 모델 설치 (`ollama pull qwen3:14b`)
- 브릿지 프록시 실행 중 (비교 시, `../run_full_bridge.sh`)

## 사용법

```bash
# 전체 비교 실행
python eval.py

# 특정 카테고리만
python eval.py --category algorithm

# Ollama만 테스트
python eval.py --target ollama

# 브릿지만 테스트
python eval.py --target bridge

# 특정 문제
python eval.py --id algo_py_fibonacci

# 상세 출력 (원본 LLM 응답 포함)
python eval.py -v
```

## 평가 지표

| 지표 | 설명 |
|------|------|
| pass_rate | 테스트 케이스 통과율 (메인 지표) |
| TTFT | Time To First Token (초) |
| total_time | 전체 응답 완료 시간 (초) |
| token_count | 응답 토큰 수 |

## 문제 추가

`problems/<category>/` 아래 YAML 파일 추가:

```yaml
id: my_problem_id
category: algorithm
lang: python
title: "문제 제목"
prompt: |
  문제 설명
test_cases:
  - input: "입력"
    output: "기대출력"
timeout_sec: 10
tags: []
```

## 지원 언어

python, javascript, go, bash

## 카테고리

| 카테고리 | 설명 | 문제 수 |
|---------|------|--------|
| algorithm | 알고리즘 구현 | 6 |
| code_gen | 코드 생성 | 5 |
| bug_fix | 버그 수정 | 4 |
| unit_test | 유닛 테스트 작성 | 3 |
| code_review | 코드 리뷰 (키워드 채점) | 2 |
