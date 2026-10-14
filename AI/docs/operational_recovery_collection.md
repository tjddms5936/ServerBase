# 보정 캠페인 실제 수집과 분할

## 이번 단계의 목적

이전 단계에서 고정한 정상 묶음 오탐 보정 계획을 **실제로 실행하여 새 학습·선정·최종 평가 자료를 확보**한다. 이번에는 모델을 학습하거나 기존 RF로 점수를 계산하지 않는다. 데이터 수집 성공과 모델 탐지 성공은 서로 다른 결과다.

실행 대상은 새 seed의 캠페인 6개, 7개 시나리오, 9회 반복, run마다 세션 4개다. 묶음 크기 1/4/8과 정상 대기 범위 1~4/5~15/20~40ms를 모든 캠페인에서 9조합으로 교차한다. burst는 같은 반복의 묶음·동시성·크기·개수 범위를 맞추고 지정 대기만 0으로 둔다.

범위는 `127.0.0.1:4000`의 로컬 통제된 트래픽이다. 외부 IP에는 송신하지 않는다. 모든 동시성·본문 크기까지 완전 교차한 실험이나 실제 공격 데이터 수집은 아니다.

## 실제 실행 결과

`rec_261014_3878ea96` batch의 6개 캠페인·378/378개 run이 통과했다. 세션 1,512개, 메시지 16,120건, 종료 요약 1,512건, JSONL 레코드 총 17,632건이다. 누락·중복·잘못된 레코드·예상하지 않은 레코드는 모두 0건이었다.

| split | 캠페인 | 메시지 run | 메시지 세션 | 메시지 | 정상 | 통제된 이상 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | 3 | 162 | 648 | 7,976 | 5,782 | 2,194 |
| Validation | 1 | 54 | 216 | 2,737 | 1,974 | 763 |
| Test | 2 | 108 | 432 | 5,407 | 3,964 | 1,443 |

헤더 거부 54개 run·216개 세션은 메시지가 없는 것이 기대값이므로 메시지 모델에서 제외했다. 전체 세션 수 1,512개와 메시지가 있는 세션 수 1,296개를 구분한다. 같은 캠페인·run·복합 session·row 식별자의 split 간 중복은 0건이다.

동일 feature 값은 전체 중복 53그룹, split 간 공유 40그룹·90행, 정답이 상충하는 동일 입력 19그룹이었다. 이는 식별자 중복이나 수집 실패와 다르며 자동으로 행을 삭제하거나 캠페인 역할·정답을 바꾸지 않았다. 동일 관측만으로 항상 정답을 구분할 수 있다는 주장도 하지 않는다.

수집 결과는 `reports/operational_recovery_collection.json/.md`, 분할 결과는 `reports/operational_recovery_dataset.json/.md`에 기록했다. 처리된 데이터는 `data/processed/operational_message/recovery_v1/`에 보관했다. 기존 v1·v2 데이터셋과 혼합하거나 덮어쓰지 않았다.

## 추가한 코드

| 파일 | 역할 |
| --- | --- |
| `scripts/collect_operational_recovery.py` | 준비만 수행하거나 전체 수집·품질 검사·분할을 실행하는 사용자 명령 |
| `data_collection/recovery.py` | 기준 RF의 해시 고정, 실행 잠금, 6개 캠페인 관리, 품질/계획 대조, 완료 근거 저장 |
| `tests/test_operational_recovery_collection.py` | 준비·변조·출력 충돌·실패·중단·재송신 방지·계획 coverage 검사 |

기존 `recovery_design.py`, `campaign.py`, `runtime.py`, `quality.py`, `operational_dataset.py`를 재사용한다. 이전 설계·학습·진단의 보호 해시를 유지하기 위해 이 파일들을 수정하지 않았다. C++ 서버와 FastAPI API 동작도 변경하지 않았다.

## 실행 흐름

```text
고정 보정 계획과 이전 진단 근거 확인
             ↓
6개 캠페인의 역할·실제 id·출력 위치·코드·빌드·기준 RF 해시 잠금
             ↓
378개 run의 설정·독립 정답·프레임 기대값 생성 및 해시 잠금
             ↓
각 run: FastAPI 수집기 시작 → C++ 서버 시작 → 로컬 클라이언트 송신
             ↓
JSONL 저장·flush 확인 → C++ worker 종료 → FastAPI 배출·종료
             ↓
캠페인별 사전 기대값·송신/응답·JSONL·종료 통계 품질 대조
             ↓
6개 모두 통과한 경우에만 캠페인 사전 역할대로 Train/Validation/Test 구성
             ↓
사전 기대 건수·비중복·원본/출력 해시 검증 및 완료 manifest 저장
```

실제 수집은 기존 C++ `Session → FeatureCollector → AsyncFeatureQueue → AiInferenceWorker → FastAPI` 경로를 사용한다. 학습된 AI 모델을 호출하는 것은 아니며 FastAPI는 dummy 응답을 유지한다. 정답은 이 응답이 아니라 송신 전 독립 시나리오 manifest에서 가져온다.

`SessionClosed`의 종료 요약은 품질 확인용이다. 모델 입력에는 메시지의 원본 v2 feature 7개만 저장하고 종료 요약이나 종료 이후 오류를 합치지 않는다.

## 역할과 수집 전 고정

| 역할 | seed | split |
| --- | ---: | --- |
| recovery_train_01 | 1103 | Train |
| recovery_train_02 | 1201 | Train |
| recovery_train_03 | 1301 | Train |
| recovery_validation_01 | 1409 | Validation |
| recovery_test_01 | 1511 | Test |
| recovery_test_02 | 1601 | Test |

각 캠페인 전체를 같은 split에 넣는다. 같은 연결의 메시지가 여러 split에 섞이지 않는다. 실제 파일 경로에는 `batch_id_c0`부터 `batch_id_c5`까지 짧은 id를 쓰고, 긴 역할 이름과 seed는 registry에 그대로 보관한다. 이는 Windows 경로 길이 제한을 시작 전에 피하기 위한 것이다.

기존 `configs/operational_recovery_plan.json`의 `design_only`는 이전 설계 문서의 상태다. 설계 파일을 결과에 맞춰 고치지 않으며 실제 준비·실행·완료 상태는 새 batch manifest에 별도로 저장한다.

## 실행 방법

자동으로 C++ 서버와 수집용 FastAPI를 실행하므로 먼저 사용자가 켜 둔 포트 4000·8000 서버를 종료한다. 명령은 기존 프로세스를 강제로 종료하지 않고 포트가 사용 중이면 중단한다. 필요한 Python 패키지는 기존 `.deps`와 프로젝트 의존성을 사용한다.

`.deps`가 없는 환경은 기존 `requirements.txt`로 의존성을 준비해야 한다. 이 명령은 이전 단계의 실제 RF 파일·최종 평가·완료 진단 근거를 해시로 확인하므로, Git에서 제외한 해당 산출물도 먼저 확보해야 한다. 소스만 내려받은 새 환경에서 이전 실험을 생략하고 즉시 실행하는 명령은 아니다.

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python -X utf8 .\scripts\collect_operational_recovery.py
```

기본 출력 경로에 이미 결과가 있으면 덮어쓰지 않는다. 새 실제 수집은 세 출력 경로를 모두 바꾸고 새로운 batch id를 사용한다. 같은 seed의 재수집은 재현/반복이며 자동으로 새로운 독립 실험이 되지 않는다.

```powershell
python -X utf8 .\scripts\collect_operational_recovery.py --batch-id rec_repeat_01 --dataset-output .\data\processed\operational_message\recovery_repeat_01 --report-prefix .\reports\operational_recovery_repeat_01 --dataset-report-prefix .\reports\operational_recovery_dataset_repeat_01
```

실제 송신 전에 기대값과 잠금 파일만 확인할 수 있다.

```powershell
python -X utf8 .\scripts\collect_operational_recovery.py --prepare-only --batch-id rec_prepared_01 --dataset-output .\data\processed\operational_message\recovery_prepared_01 --report-prefix .\reports\operational_recovery_prepared_01 --dataset-report-prefix .\reports\operational_recovery_dataset_prepared_01
```

준비만 했고 아직 수집을 시도하지 않은 batch는 잠근 출력 위치로 실행한다.

```powershell
python -X utf8 .\scripts\collect_operational_recovery.py --prepared-batch .\data\raw\operational_recovery\rec_prepared_01
```

수집을 시도한 batch는 실패하거나 중단됐어도 같은 폴더에서 재송신하지 않는다. 실패 원본과 오류 기록을 보존하고 새 batch를 별도로 준비한다. 결과가 나쁜 run만 제거하거나 정답·seed·역할을 결과에 맞춰 바꾸지 않는다.

## 저장 파일

```text
data/raw/operational_recovery/<batch_id>/
  assignment_manifest.json       송신 전 역할·계획·출력 위치·코드/모델/빌드 해시
  prepared_manifest.json         모든 캠페인과 run 설정·기대값의 해시
  batch_runtime.json             실행 중/완료/실패 및 캠페인 진행 기록
  dataset_config.json            원본으로 데이터셋을 재구성할 설정
  completion_manifest.json       모든 원본·보호 입력·완료 출력의 해시
  <batch_id>_c0/ ... _c5/
    campaign_manifest.json       캠페인 계획과 독립 정답·빌드 provenance
    expectations/                송신 전 프레임·개수·응답·송신 제어 기대값
    configs/                     각 run의 수집 API 설정
    runs/<run_id>/               실제 features.jsonl·정답 manifest·종료 통계
    outcomes/                    클라이언트 송수신 및 프로세스 종료 결과
    logs/                        API와 C++ 서버 로그
    quality_report.json/.md      캠페인 품질 검증
data/processed/operational_message/recovery_v1/
  X_train.csv / X_validation.csv / X_test.csv
  y_train.csv / y_validation.csv / y_test.csv
  metadata_train.csv / metadata_validation.csv / metadata_test.csv
  feature_schema.json / label_map.json / split_manifest.json / dataset_manifest.json
```

원본·실행 로그·처리된 CSV는 기존 Git 제외 규칙을 따른다. 소스·한글 문서·수집 및 데이터셋 요약 보고서는 커밋 대상으로 남긴다. 대용량 원본 보관과 재수집 명령은 재현 절차에서 구분한다.

## 품질·분할 검증

- 클라이언트 송신 프레임·바이트·실제 sendall 호출 수·묶음 구성·응답과 기대값을 대조한다.
- JSONL의 필드·v2 계약·식별자·독립 정답 출처·누락·중복·예상하지 않은 레코드를 확인한다.
- 서버와 API의 graceful shutdown, 최종 flush, 큐 유실·저장 오류·미처리 작업을 확인한다.
- 모든 캠페인이 통과한 경우에만 split을 구성하며 일부 통과 run만 골라 승인하지 않는다.
- 사전 계획 378개 run·1,512개 세션·16,120개 메시지 및 split별 기대 건수를 대조한다.
- campaign/run/복합 session/row 식별자의 split 간 비중복을 확인한다.
- 같은 feature 값의 반복은 별도로 보고하며 그 이유만으로 데이터를 삭제하거나 정답을 바꾸지 않는다.
- X/y/metadata의 같은 행 대응과 원본·출력 SHA-256을 저장한다.

새 Test 원본도 수집 품질과 분할을 위해 읽는다. 하지만 모델 예측, threshold 선정, 오분류 분석, Test feature 범위 탐색에는 사용하지 않는다. 기존에 노출된 Test는 이번 수집의 정답이나 새 승인 자료로 사용하지 않는다.

## 완료 후 재검증

- 신규 테스트 12개, 전체 테스트 289개 통과.
- 실제 수집 명령의 부모 프로세스에서 학습 함수·학습 모델 로딩/예측·기존 Test CSV 접근을 금지한 상태로 완료.
- 새 Train/Validation 로더로 행 수·schema·입력 fingerprint·정답·연속 세션 prefix 검증. fit·예측은 미실행.
- 원본을 다시 품질 검사·추출해 세 split의 모든 X/y/metadata 행과 대조. 새 Test는 품질 대조에만 사용.
- 잠근 입력 814개, 원본 파일 2,276개, 출력 파일 17개의 SHA-256 재검증.
- 원본 batch의 완료 상태와 수집 종료 후 포트 4000·8000이 비어 있음을 확인.
- 기존 C++·FastAPI·학습 코드·보정 설계·완료 진단·최종 평가·모델을 보존.

전체 테스트 명령은 다음과 같다. 테스트의 소형 모의 학습 fixture는 실제 수집에서 모델을 학습했다는 뜻이 아니다.

```powershell
python -X utf8 -m unittest discover -s tests -p 'test_*.py'
```

## 다음 작업과 남은 단계

다음은 **새 Train 전용 RF 후보 4개 학습과 새 Validation의 20개 조합 비교**다. 원본 대조군·송수신량 제외·prefix 평균·평균+파싱 바이트 합을 사전 규칙대로 비교한다. 이후 온라인 prefix 상태 비용까지 확인하고 후보를 잠근 뒤 새 Test 승인 여부를 확인한다.

큰 작업 묶음은 아직 **4개**다.

1. 오탐 보정과 새 독립 승인: 수집·분할 다음에 학습·선정·비용·승인 작업이 남음.
2. 승인된 모델의 FastAPI 연결.
3. C++ TCP 종단 연동과 장애·부하 테스트.
4. 최종 문서·재현·GitHub 포트폴리오 마무리.

수집 완료만으로 보정 묶음이나 탐지 시스템 전체가 완료되는 것은 아니다. 새 독립 승인까지 마치면 남은 큰 묶음은 3개가 된다.
