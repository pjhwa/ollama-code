# Bridge Proxy 완전 가이드

`bridge_proxy_full.py`는 Anthropic Messages API의 drop-in 대체 서버입니다.  
Claude Code CLI(또는 Anthropic SDK 클라이언트)를 **수정 없이** 그대로 사용하면서,  
요청을 로컬 Ollama 모델로 투명하게 라우팅합니다.

---

## 목차

1. [빠른 시작](#1-빠른-시작)
2. [아키텍처](#2-아키텍처)
3. [API 프로토콜 변환 원리](#3-api-프로토콜-변환-원리)
4. [기능 상세](#4-기능-상세)
5. [모델 선택 가이드](#5-모델-선택-가이드)
6. [성능 분석](#6-성능-분석)
7. [CLI 레퍼런스](#7-cli-레퍼런스)
8. [환경 변수](#8-환경-변수)
9. [문제 해결](#9-문제-해결)

---

## 1. 빠른 시작

### 의존성 설치

```bash
# Ollama 설치 (아직 없다면)
curl -fsSL https://ollama.com/install.sh | sh

# 모델 다운로드 (기본값: qwen3:14b)
ollama pull qwen3:14b
ollama pull nomic-embed-text   # RAG용 임베딩 모델
```

### 코드베이스 인덱싱 (RAG)

```bash
# 현재 디렉터리 인덱싱
python3 rag_indexer.py index --dirs .

# 인덱스 통계 확인
python3 rag_indexer.py stats
```

### 브릿지 실행

```bash
./run_full_bridge.sh

# 모델 지정
PRIMARY_MODEL=qwen3:8b ./run_full_bridge.sh

# Thinking 모드 끄기
./run_full_bridge.sh --no-thinking
```

### Claude Code 연결

```bash
export ANTHROPIC_BASE_URL=http://localhost:9099
export ANTHROPIC_API_KEY=local-ollama-bridge
claude
```

헬스체크:

```bash
curl http://localhost:9099/health
# → {"status": "ok", "bridge": "bridge_proxy_full"}
```

---

## 2. 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  Claude Code CLI / Anthropic SDK 클라이언트                   │
│  (변경 없음)                                                  │
│  ANTHROPIC_BASE_URL=http://localhost:9099                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ Anthropic Messages API
                           │ POST /v1/messages
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   bridge_proxy_full.py                       │
│                                                              │
│  요청 전처리 레이어 (순서대로 실행)                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 1. TRANSCRIPT_CLASSIFIER  (위험 패턴 차단, 0-10 점수)  │  │
│  │ 2. COMPACTION              (히스토리 24K 초과 시 압축)  │  │
│  │ 3. TEAMMEM                 (영속 메모리 주입)           │  │
│  │ 4. KAIROS                  (파일 변경 감지 결과 주입)   │  │
│  │ 5. RAG                     (관련 코드 청크 주입)        │  │
│  │ 6. ULTRAPLAN               (복잡 요청 → 계획 선생성)    │  │
│  │ 7. MCP Tools               (도구 목록 주입)             │  │
│  │ 8. COORDINATOR_MODE        (다단계 작업 분해)           │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  포맷 변환: Anthropic → OpenAI (Ollama 호환)                 │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ - system 필드 → messages[0] (system role)             │  │
│  │ - content 블록 배열 → 문자열                            │  │
│  │ - tool_use / tool_result ↔ tool_calls / tool msg      │  │
│  │ - input_schema → parameters                           │  │
│  │ - Qwen3 think 옵션 주입                                │  │
│  │ - Cache Boundary 마커로 static/dynamic 분리            │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  응답 후처리 레이어                                            │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ - <think>...</think> 태그 제거 (스트리밍/논스트리밍)    │  │
│  │ - MCP 병렬 도구 실행 루프 (1라운드)                     │  │
│  │ - VERIFICATION_AGENT (선택, 재시도)                    │  │
│  │ - TEAMMEM 자동 저장                                    │  │
│  │ - OpenAI SSE → Anthropic SSE 이벤트 변환               │  │
│  └───────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │ OpenAI /v1/chat/completions
                           ▼
                    Ollama (localhost:11434)
                    qwen3:14b (기본값)
```

### 컴포넌트 맵

| 클래스 / 함수 | 역할 |
|---|---|
| `ProxyConfig` | 전체 설정 (dataclass) |
| `PromptCacheLayer` | SHA256 캐시 시뮬레이션 |
| `McpServerManager` | MCP 서버 stdio JSON-RPC 2.0 |
| `RagContextInjector` | 벡터 RAG (nomic-embed-text) |
| `KairosDaemon` | 파일 감시 백그라운드 스레드 |
| `TranscriptClassifier` | 위험 패턴 스코어링 |
| `UltraPlan` | 복잡도 감지 + 계획 생성 |
| `CoordinatorMode` | 다단계 작업 분해 |
| `VerificationAgent` | 보조 모델 검증 |
| `TeamMemory` | JSON 영속 메모리 |
| `LocalModelOptimizer` | CoT 강제, Qwen3 thinking |
| `ConversationCompactor` | 히스토리 압축 |
| `convert_anthropic_to_openai` | 포맷 변환 (요청) |
| `stream_openai_to_anthropic` | 포맷 변환 (스트리밍 응답) |
| `non_streaming_response` | 포맷 변환 (논스트리밍 응답) |

---

## 3. API 프로토콜 변환 원리

Claude Code는 Anthropic Messages API를, Ollama는 OpenAI 호환 API를 사용합니다.  
브릿지가 두 형식을 투명하게 변환합니다.

### 주요 변환 차이점 (14가지)

| # | 항목 | Anthropic (Claude Code) | OpenAI/Ollama |
|---|------|------------------------|---------------|
| 1 | 시스템 프롬프트 | `"system"` top-level 필드 | `messages[0].role="system"` |
| 2 | Content 형식 | 블록 배열 `[{"type":"text","text":"..."}]` | 문자열 `"..."` |
| 3 | 도구 스키마 키 | `input_schema` | `parameters` |
| 4 | 도구 호출 응답 | content 내 `{"type":"tool_use","id":...}` | `tool_calls` 배열 |
| 5 | 도구 결과 | `role:"user"` + `{"type":"tool_result","tool_use_id":...}` | `role:"tool"` + `tool_call_id` |
| 6 | 인증 헤더 | `x-api-key: sk-ant-xxx` | `Authorization: Bearer xxx` |
| 7 | SSE 이벤트명 | `event: message_start` 등 6종 | `data: {...}` only |
| 8 | 스트리밍 구조 | message_start → content_block_start → delta × N → stop | 단순 delta 스트림 |
| 9 | Stop reason | `"end_turn"`, `"tool_use"`, `"max_tokens"` | `"stop"`, `"tool_calls"`, `"length"` |
| 10 | 도구 입력 스트리밍 | `input_json_delta` (점진적) | 최종 전체 JSON |
| 11 | Usage 구조 | `cache_creation_input_tokens` 등 4개 필드 | `prompt_tokens`, `completion_tokens` |
| 12 | 도구 타입 래퍼 | `{"type":"function","function":{...}}` | Anthropic에는 없음 |
| 13 | 다중 content 블록 | 인덱스 기반 (`index: 0`, `index: 1`) | 인덱스 없음 |
| 14 | Thinking 블록 | 미지원 | Ollama qwen3의 `<think>...</think>` 출력 |

### SSE 이벤트 흐름 (Anthropic 형식, 브릿지 출력)

```
event: message_start         ← msg ID, 모델명, usage (cache 토큰 포함)
event: content_block_start   ← 텍스트 블록 시작 (index: 0)
event: ping
event: content_block_delta   ← 텍스트 델타 (반복)
event: content_block_stop    ← 텍스트 블록 종료
event: content_block_start   ← 도구 호출 블록 시작 (index: 1, type: tool_use)
event: content_block_delta   ← input_json_delta (도구 인자)
event: content_block_stop    ← 도구 블록 종료
event: message_delta         ← stop_reason, final usage
event: message_stop
```

---

## 4. 기능 상세

### 4.1 Prompt Cache Simulation + Cache Boundary Marker

**역할:** 반복 요청에서 모델 재로드를 방지하고, 캐시 히트율을 최대화합니다.

**Prompt Cache Simulation:**
- 시스템 프롬프트를 SHA256 해시로 추적
- `keep_alive: -1` 옵션으로 모델을 RAM에 상시 유지
- 응답 usage에 `cache_creation_input_tokens`, `cache_read_input_tokens` 보고
  (Claude Code의 비용 표시에 반영됨)

**Cache Boundary Marker:**
- 시스템 프롬프트를 **정적 파트** (사용자 지정 시스템 프롬프트)와 **동적 파트** (RAG, KAIROS, ULTRAPLAN, TEAMMEM)로 분리
- 캐시 해시는 정적 파트만 계산 → RAG 컨텍스트가 바뀌어도 캐시 히트 유지
- 경계 마커: `<!-- BRIDGE:DYNAMIC_START -->`

```
시스템 프롬프트 구조:
┌──────────────────────────────────────────────┐
│  정적 파트 (캐시 키 대상)                       │
│  - 사용자가 보낸 system 필드 원문               │
├──────────────────────────────────────────────┤
│  <!-- BRIDGE:DYNAMIC_START -->               │
│  동적 파트 (캐시 키 제외)                      │
│  ## Persistent Memory (TEAMMEM)              │
│  ## KAIROS Background Findings               │
│  ## Relevant Code Context (RAG)              │
│  ## ULTRAPLAN — Pre-computed Plan            │
└──────────────────────────────────────────────┘
```

---

### 4.2 Vector RAG (nomic-embed-text)

**역할:** 코드베이스의 관련 코드 청크를 자동으로 시스템 프롬프트에 주입합니다.

**동작 방식:**
1. 코드 파일을 500자 청크로 분할 (100자 오버랩)
2. `nomic-embed-text` 모델로 각 청크를 임베딩 벡터화
3. 요청마다 사용자 메시지를 임베딩 → 코사인 유사도 계산 → Top-K 청크 선택
4. 선택된 청크를 `## Relevant Code Context (RAG)` 블록으로 동적 파트에 주입

**순수 Python** — numpy 없음, 외부 의존성 없음.

```bash
# 인덱스 빌드
python3 rag_indexer.py index --dirs . src/ tests/

# 통계 확인
python3 rag_indexer.py stats

# 쿼리 테스트
python3 rag_indexer.py query "how does authentication work" --top-k 5 --show-text

# 재빌드
python3 rag_indexer.py clear && python3 rag_indexer.py index
```

**설정:**
- `--rag-top-k 5` — 주입할 최대 청크 수
- `--rag-threshold 0.30` — 최소 코사인 유사도 (0~1)
- `--rag-dirs . src/` — 인덱싱 디렉터리
- `--no-rag` — 비활성화

---

### 4.3 KAIROS Daemon (프로액티브 파일 감시)

**역할:** 30초마다 감시 디렉터리의 파일 변경을 감지하고, 변경된 파일을 자동 재인덱싱합니다.

**동작 방식:**
- 백그라운드 스레드로 실행 (데몬 스레드)
- `.py`, `.ts`, `.tsx`, `.js`, `.rs`, `.go` 파일의 mtime 변경 감지
- 변경 감지 시 해당 파일을 RAG 인덱스에 자동 업데이트
- 다음 요청 시 `## KAIROS Background Findings` 블록으로 변경 내용을 모델에 알림

**효과:** 모델이 항상 최신 코드 상태를 인식합니다. 파일을 수정한 후 Claude Code에게 물어보면, 수정 내용을 이미 알고 있습니다.

**설정:**
- `--kairos-interval 30` — 감시 주기 (초)
- `--no-kairos` — 비활성화

---

### 4.4 Qwen3 Thinking Mode (네이티브 추론)

**역할:** qwen3, DeepSeek-R1, QwQ 등 추론 모델의 내장 사고 모드를 자동 활성화합니다.

**지원 모델:** `qwen3`, `deepseek-r1`, `qwq`, `marco-o1` (모델명 포함 여부로 자동 감지)

**동작 방식:**
1. 모델명에서 thinking 지원 여부 자동 감지
2. Ollama options에 `"think": true` 설정
3. `num_ctx` 확장 (65,536 토큰 — thinking 블록이 컨텍스트 소비)
4. 응답에서 `<think>...</think>` 블록 제거 후 최종 답변만 클라이언트에 반환
5. 스트리밍 경로: 실시간으로 think 태그를 버퍼링하며 제거

**주의:** `--thinking-budget` 설정 시 Ollama `num_predict`를 제한합니다.  
클라이언트가 `max_tokens`를 지정하면 thinking mode에서는 무시됩니다 (thinking budget 우선).

**설정:**
- `--no-thinking` — thinking 모드 비활성화
- `--thinking-budget 8192` — 최대 thinking 토큰 수

---

### 4.5 Context Compaction (컨텍스트 압축)

**역할:** 대화 히스토리가 너무 길어지면 오래된 턴을 자동으로 요약합니다.

**트리거 조건:**
- 비-system 메시지 수 ≥ `compaction_min_turns * 2` (기본: 12개)
- 추정 토큰 수 > `compaction_max_tokens` (기본: 24,000)

**동작 방식:**
1. 최근 4턴 (8메시지)은 원본 그대로 보존
2. 나머지 이전 메시지를 요약 프롬프트로 모델에 전달 (temperature 0.1)
3. 요약 결과를 `[Prior Conversation Summary]` 블록으로 대체
4. 요약 실패 시 원본 메시지 그대로 반환 (fail-safe)

**효과:** 긴 코딩 세션에서도 컨텍스트 윈도우를 넘지 않습니다.

**설정:**
- `--no-compaction` — 비활성화
- `--compaction-max-tokens 24000` — 압축 트리거 임계값

---

### 4.6 COORDINATOR_MODE (다단계 작업 분해)

**역할:** 여러 단계가 포함된 요청을 독립적인 서브태스크로 분해하여 순차 실행합니다.

**트리거:** 200자 이상이고, 시간적 연결어("first", "then", "also", "additionally", "next" 등)가 2개 이상 포함된 요청

**동작 방식:**
1. 분해 프롬프트로 서브태스크 JSON 배열 생성 (temperature 0.1)
2. 각 서브태스크를 순차 실행 (temperature 0.3, num_ctx 8192)
3. 이전 서브태스크 결과를 다음 서브태스크 컨텍스트에 축적
4. 모든 결과를 합쳐 단일 응답으로 반환

**설정:**
- `--no-coordinator` — 비활성화
- `coordinator_max_subtasks: 4` (코드 내 기본값)

---

### 4.7 ULTRAPLAN (복잡도 감지 + 계획 선생성)

**역할:** 복잡한 구현 요청을 받으면, 먼저 아키텍처 계획을 생성하고 시스템 프롬프트에 주입합니다.

**트리거:** 120자 이상이고, 다음 키워드 중 하나 포함:
`refactor`, `migrate`, `redesign`, `architecture`, `implement...system`, `build...platform`,  
`create...framework`, `integrate...with`, `optimize...performance`, `debug...issue`,  
`fix...bug...in`, `add...feature`, `implement...feature`, `write...tests`, `create...api`,  
`build...agent`, `create...pipeline`

**동작 방식:**
1. "소프트웨어 아키텍트" 역할로 5-10단계 구현 계획 생성 (temperature 0.2)
2. `## ULTRAPLAN — Pre-computed Implementation Plan` 블록으로 동적 파트에 주입
3. 실제 구현 모델 호출이 구조적 계획을 따라 진행

**효과:** 로컬 모델이 큰 구현 작업에서 방향을 잃지 않도록 합니다.

**설정:**
- `--no-ultraplan` — 비활성화

---

### 4.8 TRANSCRIPT_CLASSIFIER (위험 패턴 차단)

**역할:** 요청의 위험도를 0-10으로 스코어링하고, 임계값(4.0) 초과 시 HTTP 403 차단합니다.

**위험 패턴 (점수 높을수록 위험):**

| 패턴 | 점수 |
|------|------|
| `format c:` | 10.0 |
| SQL `DROP TABLE/DATABASE` | 9.0 |
| `rm -rf` | 8.0 |
| `curl\|bash`, `wget\|bash` | 7.0 |
| `TRUNCATE` | 6.0 |
| `subprocess(shell=True)` | 5.5 |
| `os.system()` | 5.0 |
| `eval()`, `exec()` | 4.0 |
| `DELETE WHERE` | 3.5 |
| `git push --force` | 3.0 |
| `chmod 777` | 3.0 |
| 하드코딩된 시크릿 | 2.5 |

**안전 패턴** ("explain", "how", "what is", "list", "show", "describe")은 각각 -0.5점 적용.

**설정:**
- `--no-classifier` — 비활성화 (공유 배포 환경에서는 비권장)
- `classifier_auto_approve_threshold: 4.0` (코드 내 기본값)

---

### 4.9 TEAMMEM (영속 메모리)

**역할:** 세션 간에 유지되는 키-값 메모리 저장소입니다.

**동작 방식:**
- 사용자가 "remember", "save", "note", "store" 키워드를 포함한 요청을 하면 자동 저장
- `.bridge_memory.json` 파일에 영속 저장 (프로세스 재시작 후에도 유지)
- 최근 20개 항목을 매 요청마다 `## Persistent Memory (TEAMMEM)` 블록으로 동적 파트에 주입

```bash
# Claude Code 세션 중:
# "remember that we use poetry for dependency management"
# → .bridge_memory.json에 저장됨
# 다음 세션: 자동으로 시스템 프롬프트에 주입됨
```

**설정:**
- `--no-teammem` — 비활성화
- `--teammem-path custom.json` — 저장 파일 경로

---

### 4.10 MCP 서버 통합 + 병렬 도구 실행

**역할:** MCP (Model Context Protocol) 서버의 도구를 자동 발견하여 모델에 노출하고, 병렬로 실행합니다.

**MCP 연결:**
- stdio JSON-RPC 2.0 프로토콜
- 서버별 독립 스레드 락 (동시 요청 안전)
- 도구 이름: `mcp__<server>__<tool>` 형식

```bash
# 파일시스템 MCP 서버 추가
./run_full_bridge.sh \
    --mcp-server filesystem npx @modelcontextprotocol/server-filesystem .

# 여러 서버
./run_full_bridge.sh \
    --mcp-server files npx @modelcontextprotocol/server-filesystem . \
    --mcp-server git npx @modelcontextprotocol/server-git .
```

**병렬 도구 실행 (Agentic Loop):**
1. 모델이 여러 `mcp__*` 도구를 동시에 호출하면 `ThreadPoolExecutor`로 병렬 실행
2. 총 레이턴시 ≈ max(개별 도구 레이턴시) — N개 직렬 대비 최대 N배 빠름
3. 모든 결과를 수집해 follow-up 모델 호출로 최종 답변 생성 (1라운드)
4. 스트리밍 요청에서는 tool loop 미실행 (클라이언트가 직접 처리)

**설정:**
- `--no-tool-loop` — 자동 MCP 도구 실행 루프 비활성화
- `--no-mcp` — MCP 통합 전체 비활성화

---

### 4.11 VERIFICATION_AGENT (선택적 품질 검증)

> **기본 비활성화** — 레이턴시 두 배 증가

**역할:** 모델 응답을 보조 모델로 검증하고, 실패 시 재시도합니다.

**동작 방식:**
1. 응답 토큰이 200개 이상이면 검증 활성화
2. 같은 모델로 "코드 리뷰어" 역할 프롬프트 전달
3. `PASS` / `FAIL` 판정 + 피드백 수신
4. `FAIL` 시: 피드백을 메시지에 주입하고 재시도

**설정:**
- `--enable-verification` — 활성화

---

## 5. 모델 선택 가이드

### Qwen3 계열 (권장)

| 모델 | 크기 | 권장 RAM | 특징 |
|------|------|----------|------|
| `qwen3:4b` | ~2.6 GB | 8 GB | 빠른 응답, 단순 작업 |
| `qwen3:8b` | ~5.2 GB | 12 GB | 균형잡힌 성능 |
| **`qwen3:14b`** | ~9.3 GB | **20 GB** | **기본값** — 고품질 코딩 |
| `qwen3:32b` | ~21 GB | 36 GB | 최고 품질 |
| `qwen3:30b-a3b` | ~19 GB | 32 GB | MoE 아키텍처, 효율적 |

### 추론 모델 (Thinking Mode 지원)

| 모델 | 크기 | 특징 |
|------|------|------|
| `qwen3:14b` | ~9.3 GB | 하이브리드 추론 (기본값) |
| `deepseek-r1:8b` | ~5 GB | 대안 추론 모델 |
| `qwq:32b` | ~21 GB | 수학/논리 특화 추론 |

### 임베딩 모델 (RAG용)

| 모델 | 크기 | 설명 |
|------|------|------|
| `nomic-embed-text` | ~274 MB | 기본값, 코드 임베딩 적합 |

### 메모리 사용량 예시 (qwen3:14b 기준)

```
qwen3:14b        ~9.3 GB
nomic-embed-text ~0.3 GB
OS / 기타        ~2 GB
─────────────────────────
총 권장          ~12 GB (20GB 시스템에서 여유 있음)
```

---

## 6. 성능 분석

### Claude 3.7 Sonnet 대비 추정 성능 (qwen3:14b 기준)

| 작업 유형 | 추정 성능 | 주요 요인 |
|---|---|---|
| 단순 코드 완성/수정 | 80-88% | RAG + Thinking Mode |
| 버그 분석/디버깅 | 72-80% | Thinking Mode 효과 큼 |
| 멀티파일 리팩토링 | 70-78% | Coordinator + RAG |
| 아키텍처 설계 | 68-76% | UltraPlan + Thinking |
| 장기 세션 (20+ 턴) | 65-75% | Compaction 효과 |
| 단발성 코드 생성 | 85-92% | 최적 케이스 |

### 기법별 성능 기여

| 기법 | 기여도 | 대상 작업 |
|---|---|---|
| Qwen3 Thinking Mode | +15~25% | 복잡한 추론, 버그 분석 |
| RAG 컨텍스트 주입 | +15~20% | 코드베이스 인식 작업 |
| COORDINATOR_MODE | +10~20% | 다단계 구현 |
| ULTRAPLAN | +10~15% | 아키텍처/설계 |
| Context Compaction | +10~15% | 장기 세션 |
| Cache Boundary Marker | +5~10% | 반복 요청 (레이턴시 감소) |
| TEAMMEM | +3~8% | 세션 연속성 |

### 레이턴시 프로파일 (qwen3:14b, M4 Mac Mini 기준)

| 상황 | 예상 레이턴시 |
|---|---|
| 첫 요청 (모델 로드) | 3~8초 |
| 이후 요청 (캐시 히트) | 0.1초 이내 |
| RAG 임베딩 (요청당) | 0.5~1초 |
| ULTRAPLAN 계획 생성 | 5~15초 |
| Thinking Mode (qwen3) | +3~20초 (추론 깊이에 따라) |
| Context Compaction (트리거 시) | 15~60초 |
| VERIFICATION_AGENT | 응답 레이턴시 × 2배 |

---

## 7. CLI 레퍼런스

```
python3 bridge_proxy_full.py [OPTIONS]

기본 설정:
  --host HOST              바인드 호스트 (기본: 0.0.0.0)
  --port PORT              바인드 포트 (기본: 9099)
  --ollama URL             Ollama 서버 URL (기본: http://localhost:11434)
  --model MODEL            기본 모델 (기본: qwen3:14b)
  --embed-model MODEL      임베딩 모델 (기본: nomic-embed-text)
  --verify-model MODEL     검증 모델 (기본: 기본 모델과 동일)

기능 토글:
  --no-cache               프롬프트 캐시 시뮬레이션 비활성화
  --no-rag                 벡터 RAG 비활성화
  --no-mcp                 MCP 서버 통합 비활성화
  --no-kairos              KAIROS 파일 감시 비활성화
  --no-coordinator         COORDINATOR_MODE 비활성화
  --no-classifier          TRANSCRIPT_CLASSIFIER 비활성화
  --no-ultraplan           ULTRAPLAN 비활성화
  --enable-verification    VERIFICATION_AGENT 활성화 (느림)
  --no-teammem             영속 메모리 비활성화
  --no-compaction          컨텍스트 압축 비활성화
  --no-tool-loop           MCP 도구 자동 실행 루프 비활성화
  --no-thinking            Thinking mode 비활성화

RAG 옵션:
  --rag-index PATH         인덱스 파일 경로 (기본: .bridge_rag_index.json)
  --rag-dirs DIR...        인덱싱 디렉터리 (기본: .)
  --rag-top-k N            주입할 최대 청크 수 (기본: 5)
  --rag-threshold FLOAT    최소 코사인 유사도 (기본: 0.30)
  --index-now              시작 시 즉시 인덱싱

KAIROS:
  --kairos-interval SECS   감시 주기 초 (기본: 30)

Thinking:
  --thinking-budget N      최대 thinking 토큰 (기본: 8192)

Compaction:
  --compaction-max-tokens N  압축 트리거 토큰 수 (기본: 24000)

메모리:
  --teammem-path PATH      메모리 파일 경로 (기본: .bridge_memory.json)

MCP:
  --mcp-server NAME CMD... MCP 서버 추가 (반복 가능)

기타:
  -v, --verbose            디버그 로깅 활성화
```

---

## 8. 환경 변수

`run_full_bridge.sh`에서 사용하는 환경 변수:

```bash
# Ollama 설정
export OLLAMA_HOST=http://localhost:11434
export PRIMARY_MODEL=qwen3:14b
export EMBED_MODEL=nomic-embed-text
export PROXY_PORT=9099
export RAG_DIRS=". src/ tests/"
export INDEX_ON_START=false     # true: 시작 시마다 재인덱싱

# Claude Code 연결 (브릿지 시작 후 설정)
export ANTHROPIC_BASE_URL=http://localhost:9099
export ANTHROPIC_API_KEY=local-ollama-bridge
```

---

## 9. 문제 해결

### Claude Code가 브릿지에 연결 안 됨

```bash
# 브릿지 상태 확인
curl http://localhost:9099/health
# 기대 응답: {"status": "ok", "bridge": "bridge_proxy_full"}

# 포트 사용 확인
ss -tlnp | grep 9099

# 브릿지 재시작
./run_full_bridge.sh
```

### 모델을 찾을 수 없음

```bash
ollama list                    # 설치된 모델 목록
ollama pull qwen3:14b          # 모델 다운로드
```

### RAG 결과 없음

```bash
python3 rag_indexer.py stats              # 인덱스 크기 확인
python3 rag_indexer.py query "test"       # 간단한 쿼리 테스트
python3 rag_indexer.py index --force      # 강제 재빌드
```

### 메모리 부족 (OOM)

더 작은 모델로 전환:
```bash
PRIMARY_MODEL=qwen3:8b ./run_full_bridge.sh
# 또는
PRIMARY_MODEL=qwen3:4b ./run_full_bridge.sh
```

### 요청이 차단됨 (HTTP 403)

TRANSCRIPT_CLASSIFIER가 위험 패턴을 감지한 경우. 점수 확인:

```bash
python3 - <<'EOF'
from bridge_proxy_full import TranscriptClassifier
clf = TranscriptClassifier(4.0)
score, reasons = clf.score("여기에 요청 텍스트 입력")
print(f"Score: {score}, Reasons: {reasons}")
EOF
```

분류기 비활성화 (주의):
```bash
./run_full_bridge.sh --no-classifier
```

### Thinking 태그가 응답에 그대로 나타남

스트리밍 응답에서 `<think>` 태그가 청크 경계에 걸쳐 분리된 경우 발생할 수 있습니다.
논스트리밍 모드로 전환하거나 `--no-thinking`으로 비활성화하세요:

```bash
./run_full_bridge.sh --no-thinking
```

### Compaction이 매 요청마다 실행됨

최근 8개 메시지만으로도 24K 토큰을 초과하는 경우입니다. 임계값을 높이거나 더 큰 컨텍스트 모델을 사용하세요:

```bash
./run_full_bridge.sh --compaction-max-tokens 40000
```

---

## 파일 구조

```
ollama-code/
├── bridge_proxy_full.py       # 메인 브릿지 프록시 서버 (stdlib only)
├── rag_indexer.py             # 독립 실행형 RAG 인덱서 CLI
├── run_full_bridge.sh         # 의존성 체크 + 시작 스크립트
├── BRIDGE_FULL_GUIDE.md       # 이 문서
├── README.md                  # 프로젝트 개요
├── .bridge_rag_index.json     # RAG 벡터 인덱스 (자동 생성)
└── .bridge_memory.json        # TEAMMEM 영속 저장소 (자동 생성)
```
