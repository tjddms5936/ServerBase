# 정상·통제된 이상 트래픽 생성과 다중 실행 수집

## 목적과 범위

직접 구현한 C++ TCP 서버의 메시지 feature를 동일한 정의로 수집하고 학습 전에 품질을 검증한다. 공개 UNSW-NB15 오프라인 실험과는 별도 데이터다. 실제 모델 추론은 여전히 `dummy-v0`이며, 이 단계에서는 모델을 학습하거나 공격 탐지 성능을 주장하지 않는다.

생성기는 **127.0.0.1:4000**에만 연결한다. 외부 주소 지정 옵션은 없다. API는 127.0.0.1:8000에서 실행하며 각 run마다 C++와 API를 새로 시작한다. 이미 포트를 사용하는 프로세스는 종료하지 않고 시작을 거부한다. 종료·강제 정리 대상은 도구가 직접 시작한 자식 프로세스로 제한한다.

## 구현 파일

| 파일 | 역할 |
| --- | --- |
| `data_collection/traffic.py` | 프로토콜 직렬화, 시드별 사전 기대값, 크기·간격·동시성 제한, 클라이언트 송수신 |
| `data_collection/campaign.py` | 여러 run의 준비·프로세스 실행·결과 보관 |
| `data_collection/runtime.py` | 포트 사전 검사, API 상태 대기, 소유 프로세스 정상 종료 |
| `data_collection/api_runner.py` | 표준 입력의 quit로 종료할 수 있는 자동 수집용 API 실행 진입점 |
| `data_collection/quality.py` | 원본 JSONL·manifest·기대값·송신 결과 대조 및 보고서 생성 |
| `configs/traffic_collection_plan.json` | 기본 시나리오, 반복 수, 난수 시드와 제한된 트래픽 설정 |
| `configs/collection_scenarios.json` | 실제 송신 전에 정의한 독립 정답과 실험 의도 |
| `scripts/collect_operational_traffic.py` | 준비 → 다중 run 수집 → 품질 검사 전체 실행 |
| `evaluation/audit_operational_data.py` | 보관한 캠페인의 품질 재검사 |
| `tests/test_operational_traffic.py` | 직렬화·시드·제한·누락·중복·잘못된 정답·관측 범위 검사 |

기존 C++ 코어와 feature 의미 버전, FastAPI 응답 형식은 변경하지 않는다. `api_runner.py`의 종료 제어는 부모 프로세스의 표준 입력을 이용하며 HTTP 관리 엔드포인트를 만들지 않는다.

## 시나리오와 정답

| 시나리오 | label | 송신 방법 | 메시지 관측 |
| --- | ---: | --- | --- |
| normal_chat | 0 | 유효한 프레임을 일정 범위의 간격으로 송신 | 있음 |
| normal_fragmented_chat | 0 | 유효한 프레임을 최대 31바이트 조각으로 나누어 송신 | 완성된 프레임마다 있음 |
| normal_batched_chat | 0 | 유효한 프레임을 최대 4개씩 묶어 송신 | 프레임마다 있음 |
| controlled_burst_chat | 1 | 유효한 요청을 12~16개, 묶음 사이 지정 대기 없이 송신 | 있음 |
| malformed_chat_payload | 1 | 채팅 본문에 1바이트만 넣어 역직렬화 실패 유도 | 거부 전 1개 |
| malformed_length_chat | 1 | 정상과 같은 전체 크기에서 문자열 길이 필드만 실제 본문보다 크게 지정 | 거부 전 1개 |
| invalid_frame_size | 1 | 실제 8바이트 헤더의 크기 필드에 7 지정 | 없음, 종료 요약만 있음 |

분할·묶음 전송은 정상 TCP 동작이다. 하나의 send 호출, 수신 완료, 파싱 메시지가 일대일이라고 가정하지 않는다. TCP_NODELAY와 분할 송신을 사용해도 OS가 실제 완료 경계를 결정한다.

label은 **독립 시나리오 정의**에서 가져온다. 송신 성공, 서버 오류 수, dummy 판단에 따라 사후 변경하지 않는다. 연속 요청 시나리오는 서버 오류가 0이어도 label 1이다. 이는 이 프로젝트의 통제된 행동 정책이며 실제 공격, DoS 성공 또는 정해진 네트워크 요청률을 뜻하지 않는다. 정상 묶음 전송과 빠른 연속 요청은 feature가 겹칠 수 있다.

잘못된 길이 필드 시나리오는 같은 크기의 정상 프레임과 겹치도록 구성한다. 현재 메시지 입력 7개에는 본문 문자열의 의미나 길이 필드의 유효성이 없다. 따라서 이 이상을 모델이 잘 구분할 수 있다고 미리 가정하지 않는다. 이후 오분류 분석에서 현재 관측의 한계와 개선 근거를 검토한다.

## 실행 계획과 재현성

기본 계획은 7개 시나리오를 3회씩 반복하는 **21개 run, 84개 세션**이다. run마다 4개 세션을 최대 2개씩 동시에 연결한다. 정상 메시지 수는 세션당 4~8개, 연속 요청은 12~16개이며 오류 시나리오는 세션당 프레임 하나만 전송한다. 문자열 크기는 16/64/256/512바이트에서 선택한다.

설정 상한은 세션 16개, 동시 연결 4개, 정상 또는 연속 요청의 세션당 메시지 32개, 문자열 1024바이트다. 한 묶음은 4개 이하이고 각 정상 문자열은 C++ uint16 길이와 int32 정수 규칙으로 직렬화한다. 거대한 payload나 무제한 요청을 생성하지 않는다. 잘못된 길이 60000은 필드 값일 뿐 실제로 60000바이트를 전송하지 않는다.

각 run의 seed는 기본 seed + 반복 번호 × 100 + 시나리오 순번이다. 세션별 난수도 따로 생성해 스레드 스케줄과 무관하게 프레임·크기·지정 대기 값을 재생성할 수 있다. 지정한 간격과 실제 파싱 간격은 다르며 OS·CPU·응답 대기 때문에 timestamp와 지연시간까지 동일하게 재현되지는 않는다. 이 수집 결과는 latency benchmark가 아니다.

트래픽을 보내기 전에 모든 run의 기대값을 작성한다. 파일에는 원본 송신 바이트의 hex·SHA-256, 클라이언트 인덱스, 기대 프레임 크기·순서, 응답 수, 메시지·종료 요약 수, 관측 가능 여부가 들어간다. 기대값을 수집 결과에 맞춰 낮추거나 실패한 연결을 조용히 제외하지 않는다.

캠페인 ID는 기본적으로 UTC 시각과 임의 ID를 포함한다. 각 run ID에도 캠페인 ID·반복 번호·시나리오를 포함해 여러 캠페인의 식별자 충돌을 줄인다. 사용자가 지정한 ID는 고유해야 하며 기존 캠페인은 덮어쓰지 않는다. run ID 전체는 기존 수집기 규칙에 따라 최대 96자다.

## 실행 방법

기존 FastAPI·C++ 서버·DummyClient를 먼저 종료한다. 빌드한 `Server/BIN/Debug/Server.exe`와 기존 Python 의존성이 필요하다. 수집 도구가 두 서버와 클라이언트를 자동으로 실행하므로 별도로 실행하지 않는다.

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python .\scripts\collect_operational_traffic.py
```

다른 빌드를 사용하려면 `--server-exe`로 지정한다. `--plan`으로 설정 파일을 지정하거나 `--repeats`, `--scenarios`로 제한된 실행을 선택할 수 있다. 빠른 연동 확인은 학습 데이터와 분리한다.

```powershell
python .\scripts\collect_operational_traffic.py --repeats 1 --dataset-purpose integration_test --report-prefix .\data\raw\operational_campaigns\smoke_quality
```

품질 검사 실패는 종료 코드 2다. 실패한 원본·프로세스 로그·클라이언트 결과도 보관한다. 서버 종료 요청 뒤 API를 종료해 C++ HTTP worker와 JSONL 큐를 배출한다. 강제 종료나 저장 오류가 발생한 run을 정상 완료로 간주하지 않는다.

## 저장 구조

```text
data/raw/operational_campaigns/<campaign_id>/
  campaign_manifest.json       계획·시나리오·빌드·schema·코드 해시·Python 버전
  campaign_runtime.json        전체 실행 결과
  expectations/<run_id>.json   송신 전에 작성한 기대값과 해시
  configs/<run_id>.json        각 수집 API 설정
  outcomes/<run_id>_traffic.json  실제 클라이언트 송수신·실패 결과
  outcomes/<run_id>_runtime.json  두 서버의 실행·종료 결과
  logs/                       자식 프로세스 로그
  runs/<run_id>/
    features.jsonl            실제 C++에서 전송한 v2 원본 snapshot
    run_manifest.json         독립 정답과 수집 정의
    collection_summary.json   저장·유실·종료 통계
  quality_report.json          해당 캠페인의 검사 결과
  quality_report.md            해당 캠페인의 한글 보고서
```

기본 실행은 원본을 지우지 않고 보관한다. 원본 경로는 기존 `.gitignore`로 Git에서 제외하고, 포트폴리오용 최신 결과는 `reports/operational_data_quality.json`과 `.md`에 저장한다. 보고서에 연결된 원본 경로, 캠페인 manifest와 검증기 해시를 남긴다. 새 수집 시 최신 보고서는 갱신하지만 캠페인별 보고서는 각각 남는다.

이전의 2개 시나리오 E2E 검사는 임시 원본을 제거한다. 이번 기본 수집은 `controlled_experiment` 목적으로 원본을 보관하는 별도 단계다. `integration_test` 실행은 검증이 통과해도 학습 후보에서 제외한다.

## 누락·중복·정답 분포 검증

- 계획의 모든 run이 존재하는지, 추가·중복 run 또는 잘못된 반복 번호·seed가 없는지 확인한다.
- 사전 기대값 SHA-256을 검사하고 계획·seed로 다시 생성한 값과 완전히 대조한다.
- 독립 시나리오 정답·정답 출처·manifest 복사본·빌드·schema가 run별로 일치하는지 확인한다.
- 실제 연결·송신·응답 결과와 프로세스 정상 종료를 검사한다.
- 수집 유실·저장 오류·미완료 수가 0이고 통계와 실제 JSONL 줄 수가 일치하는지 검사한다.
- 각 줄의 v2 payload와 envelope를 검증하고 중복 JSON 필드·NaN·불완전 마지막 줄을 거부한다.
- collection_sequence_id, C++ queue_sequence_id와 세션/메시지 식별 키의 중복·누락을 검사한다.
- 세션의 request_index 연속성, 누적 바이트·지속 시간의 비감소, 종료 요약 수·request_count·평균 간격을 검사한다.
- 세션별 프레임 크기·순서·수신 바이트·예상 오류의 다중집합을 송신 계획과 대조한다.
- 정상/통제된 이상 분포를 run·세션·메시지·종료 요약 및 시나리오별로 따로 집계한다.

C++ 세션 ID와 생성기의 클라이언트 인덱스는 프로토콜로 직접 교환하지 않는다. 따라서 동일한 기대 프레임을 가진 세션 사이의 정확한 일대일 대응은 주장하지 않고, 전체 세션 패턴의 다중집합을 비교한다. 세션 키는 `(run_id, session_id)`, 메시지 키는 `(run_id, session_id, request_index)`다.

헤더 거부 run은 메시지 0개가 사전 기대값이다. 이 경우를 누락 메시지로 세지 않지만 **메시지 모델의 학습 후보에서는 제외**한다. 품질 검증 통과와 메시지 학습 후보 수가 다른 이유다. 요약의 사후 오류 정보를 메시지 입력에 넣어 미래 정보 누출로 보정하지 않는다.

불완전 run은 파일을 삭제하지 않고 `passed: false`, `eligible_for_training: false`와 진단을 남긴다. 정답 분포는 품질 검증 통과 run만 집계하고 메시지 학습 후보 분포를 별도로 제공한다. 메시지가 많은 연속 요청은 run 수보다 메시지 비중이 커질 수 있다. 자동 undersampling 등으로 이를 숨기지 않는다.

재검사에는 원본 캠페인의 경로를 지정한다.

```powershell
python .\evaluation\audit_operational_data.py --campaign-dir "원본_캠페인_절대_경로"
python -m unittest discover -s tests -p "test_*.py"
```

## 한계와 다음 단계

이번 검증은 **생성기가 계획한 로컬 세션의 기대 레코드**에 한정된다. 일반 운영 환경에서 생성기가 모르는 트래픽, 전원 차단 영속성, 모든 TCP 손실 원인이나 공격 정답을 증명하지 않는다. 해시는 파일 변경 대조용이며 송신자 인증이나 전자 서명이 아니다.

초기 수집 규모는 파이프라인과 분할 실험을 준비하는 용도다. 품질 검증 통과가 충분한 학습 규모, 대표성 또는 탐지 가능성을 보장하지 않는다. 7개 숫자형 feature로 관측할 수 없는 본문 이상이 있고, 생성기 구현·세션 길이·크기·간격에만 적응하는 편향이 생길 수 있다.

운영 데이터셋 구성과 run/session 단위 train·validation·test 분리는 구현했으며 `docs/operational_dataset.md`에서 실행 및 한계를 확인한다. 종료 요약과 식별 메타데이터를 메시지 입력에서 제외하고 실제 수집된 7개 feature만 저장했다.

다음 작업은 train 전용 전처리와 운영 LR/RF 학습·validation 선정이다. 가능하면 후속 독립 캠페인을 확보해 추가 평가하고 시나리오별 coverage·분포·관측 한계를 분석한다. 실제 FastAPI 모델 연결과 LSTM 비교까지의 전체 단계는 `docs/project_roadmap.md`에 있다.
