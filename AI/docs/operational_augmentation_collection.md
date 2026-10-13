# 독립 캠페인 생성기 설정·수집·분할 구현

## 이번 구현의 목적

기존 운영 모델의 오분류 분석에서 얻은 가설을 새 실행 조건에서 검증할 수 있도록 데이터를 확보한다. 모델을 새로 학습하거나 threshold를 변경하는 단계는 아니다. 실제 외부 침입 데이터가 아니라 로컬 프로토콜의 정상·통제된 이상 행동을 생성한다.

기존 v1 데이터셋과 선정 모델을 유지하고 새 데이터셋을 `data/processed/operational_message/v2_campaign_holdout/`에 저장한다. 데이터셋 버전은 v2지만 feature 의미 계약은 이전과 같은 `server_application_message_v2`이며 입력은 7개다. 본문 필드나 미래 오류 정보를 새 입력에 추가하지 않았다.

## 실제 완료 결과

완료 batch는 `aug_261013_e1cafdde`다. 6개 캠페인의 84/84 run이 수집·품질 검사를 통과했다. 총 336개 세션에서 메시지 3,122건·종료 요약 336건·JSONL 3,458건을 보관했다. 헤더 거부 12개 run·48개 세션은 메시지가 없으므로 모델 데이터에는 72개 run·288개 세션을 사용했다.

| 분할 | 캠페인 | run | 세션 | 메시지 | 정상 | 통제된 이상 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | 3 | 36 | 144 | 1,538 | 1,049 | 489 |
| Validation | 1 | 12 | 48 | 513 | 349 | 164 |
| Test | 2 | 24 | 96 | 1,071 | 762 | 309 |

잘못된 레코드·레코드 중복·메시지 누락·요약 누락·추가 레코드는 모두 0이다. 분할 간 campaign/run/session/row 중복도 0이다. 서로 다른 실행의 같은 feature 값은 분할 간 2개 그룹·4개 행으로 별도 보고했고 서로 다른 정답의 동일 값 그룹은 0이다. 값 중복을 없애려고 행을 삭제하지 않았다.

완료 원본으로 임시 경로에 데이터셋을 재구성해 12개 CSV/schema/split 출력의 SHA-256 일치를 확인했다. 기존 v1 출력 파일, 고정한 선정 모델·threshold·계약·수집 코드 해시를 검증했고, 종료 후 4000/8000 포트가 비어 있음을 확인했다. 전체 자동 테스트는 175개 통과했다. 기존 캠페인도 새 코드로 재검사해 21/21 run 통과와 메시지 406건 보존을 확인했다.

실제 상세값은 `reports/operational_augmentation_collection.json/.md`, 데이터셋은 `reports/operational_dataset_v2.json/.md`를 참조한다. 이 숫자는 수집 품질·분할 결과이며 새 모델 성능이 아니다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `configs/operational_augmentation_plan.json` | 지난 단계에서 고정한 6개 캠페인 설계. 기존 v1 수집 명령에는 직접 넣지 않는다. |
| `data_collection/traffic.py` | v2의 명시적 묶음·분할 크기, 반복별 프로필, 독립 run seed와 실제 송신 계측. v1 기대값 재생성 규칙은 보존한다. |
| `data_collection/campaign.py` | 송신 전 기대값·설정·빌드·환경 정보를 저장하고, 소유한 C++/API 프로세스만 실행·정상 종료한다. Windows 경로 길이를 사전 검사한다. |
| `data_collection/augmentation.py` | 설계를 실행 가능한 v2 계획으로 변환하고 전체 역할 고정, 모델 해시 보존, 6개 순차 수집·검증·데이터셋 생성을 묶는다. |
| `data_collection/quality.py` | 기존 누락·중복·독립 정답 검사에 v2의 실제 묶음·조각 제어, sendall 호출 수·묶음 기록 검증을 추가한다. |
| `data_preparation/operational_dataset.py` | 기존 시간 순서 반복 분리와 별개로 사전 캠페인 holdout 정책을 지원한다. 동일 캠페인이 여러 split에 들어가면 중단한다. |
| `scripts/collect_operational_augmentation.py` | 6개 캠페인 수집·분할을 실행하는 명령. 준비만 하는 옵션과 새 출력 경로 옵션을 제공한다. |
| `tests/test_operational_augmentation.py` | 재현 seed, 교차 제어, 실제 송신 호출, v1 호환, 캠페인 경계·역할·실패 보호를 검사한다. |
| `reports/operational_augmentation_collection.json/.md` | 실제 수집·품질·역할·정답 분포·송신 관측·범위 한계 보고서. |
| `reports/operational_dataset_v2.json/.md` | 새 메시지 데이터셋 행 수, 분할, 중복, 원본·출력 SHA-256 보고서. |

## 캠페인·run·세션 구분

캠페인은 독립 seed를 사용하는 일련의 수집 실험이다. 각 캠페인에는 7개 시나리오 × 2회 반복으로 14개 run이 있고, run마다 C++ 서버와 API를 새로 시작하며 클라이언트 연결 4개를 만든다. 한 연결이 하나의 세션이다.

```text
development_01 캠페인 → 전체 Train
  반복 0의 7개 run → 각 run의 4개 세션 → 모든 메시지
  반복 1의 7개 run → 각 run의 4개 세션 → 모든 메시지

selection_01 캠페인 → 전체 Validation
holdout_01/02 캠페인 → 전체 Test
```

메시지를 무작위로 섞어 나누지 않는다. 같은 캠페인·run·세션에서 나온 기록은 다른 분할로 넘어가지 않는다. 세션 ID는 서버 재시작마다 재사용될 수 있으므로 `(run_id, session_id)` 조합으로 세션을 구분한다.

## 송신 전에 고정하는 정보

1. 설계에서 고정한 Train 3개·Validation 1개·Test 2개 역할과 seed 101/211/307/401/503/607을 읽는다.
2. 기존 선정 모델·schema·label map·selection 파일을 완료 manifest의 SHA-256과 대조한다. threshold 0.7을 기록하지만 모델을 로딩하거나 예측하지 않는다.
3. 모든 캠페인의 구체적 반복별 프로필·순서를 계산하고 `assignment_manifest.json`을 먼저 저장한다.
4. 6개 캠페인의 기대 프레임·정답·수집 설정을 모두 준비한다. 이 시점에는 아직 TCP 트래픽이 없다.
5. `prepared_manifest.json`에 역할 파일과 캠페인 manifest 해시를 저장한다. 수집 중 고정 파일이 바뀌면 중단한다.

수집 결과가 나쁘다는 이유로 역할·seed·정답을 바꾸지 않는다. 교차 배치는 사전 규칙으로 계산하며 모델 점수를 읽지 않는다.

## 명시적 생성기 제어

- 모든 정상·유효 burst 프레임의 문자열 크기는 8/24/96/384/768바이트 후보에서 세션별 난수로 선택한다. 이는 전체 패킷 크기가 아니라 문자열 길이다.
- 정상 메시지 수는 6~24개, burst는 12~24개여서 길이가 겹친다. malformed·잘못된 헤더 시나리오는 오류 후 종료를 기대하므로 실제 프레임은 세션당 1개다.
- 정상 묶음과 burst 묶음은 1/4/8개 프레임을 한 번의 송신 그룹으로 묶는다. 일반 정상·분할 정상·잘못된 본문/헤더는 그룹당 1개다.
- 정상 분할 송신은 그룹을 7/19/31바이트 조각으로 보내며 조각마다 1ms를 의도적으로 기다린다.
- 정상 그룹 사이 지정 대기는 1~4/5~15/20~40ms, burst는 0ms다. 마지막 그룹 뒤에는 추가 대기를 적용하지 않는다. 응답을 읽는 시간과 OS 스케줄 지연은 별도로 발생한다.
- 동시 연결 수준은 1/2/4다. 캠페인·반복·시나리오 인덱스의 사전 순환 규칙으로 수준을 교차 배치하고 시나리오 실행 순서는 seed로 섞는다.

전체와 Train에서는 시나리오별 3수준을 포함한다. Validation은 시나리오별 2개 run만 있으므로 모든 수준·조합을 포함하는 완전 요인 실험은 아니다. JSON 보고서에 실제 계획 coverage를 명시한다.

run 난수는 캠페인 seed·반복·시나리오를 SHA-256으로 조합해 결정한다. 순서를 바꿔도 같은 run의 프레임 난수는 유지된다. 세션별 난수는 별도로 파생하며 84개 run의 seed 충돌을 검사한다. seed는 의도한 프레임을 재현하지만 TCP 수신 경계·실제 시간까지 재현하지는 않는다.

## 실제 동작과 검증

```text
명령 → 설계 변환·기준 모델 해시 고정 → 전체 역할·기대값 준비
     → 캠페인별 순차 실행
       → run별 수집 API 시작 → C++ 서버 시작 → 로컬 클라이언트 송신
       → C++ observer/collector/queue/HTTP → 수집 API → JSONL
       → 기대 응답·저장 수 확인 → C++ worker 종료 → API flush·종료
       → 캠페인 품질 재검사
     → 6개 전체 통과 → 캠페인 역할대로 X/y/metadata 추출 → 완료 manifest
```

실제 `sendall` 호출 수, 프레임 수·바이트, 묶음별 크기·지정 대기와 실제 그룹 시작 간격·송신 시간을 기록한다. 기대 그룹과 실제 호출·기록을 검사한다. 그룹 시작 간격에는 송신·응답·지정 대기가 포함되고, C++ 파싱 간격은 수신·버퍼 처리 시각이므로 두 시간을 같다고 요구하지 않는다.

JSONL schema, 순번 연속성, 세션별 프레임 크기·순서, 누적 바이트·오류 기대값, 종료 요약, 독립 정답, 수집 큐 유실과 정상 종료를 검사한다. 식별자 중복과 서로 다른 실행의 동일 feature 값은 따로 보고한다. 동일 값 때문에 자동으로 행을 삭제하지 않는다.

실패 캠페인은 원본·로그를 남기고 전체 batch 승인을 중단한다. 통과한 run만 골라 같은 캠페인을 승인하지 않는다. 이미 실행한 batch는 재송신하지 않고, 원인을 수정한 뒤 새 batch로 다시 실험한다.

헤더 거부 12개 run은 메시지 0개·세션 종료 요약 4개씩이 정상 기대값이다. 이를 누락으로 취급하거나 요약으로 메시지를 만들지 않는다. 전체 84개 run을 품질 검사하지만 메시지 모델 데이터셋에는 관측 가능한 72개 run·288개 세션만 넣는다.

## 실행과 재구성

AI 폴더에서 실행하며 빌드된 `Server/BIN/Debug/Server.exe`와 Python `.deps`가 필요하다. 기존 4000/8000 포트의 서버는 사용자가 먼저 종료한다. 도구는 다른 프로세스를 강제로 종료하지 않는다.

```powershell
python .\scripts\collect_operational_augmentation.py
```

기존 새 데이터셋·보고서가 있으면 명령이 덮어쓰기 없이 종료한다. 재수집은 세 출력 경로를 모두 새 버전으로 지정한다.

```powershell
python .\scripts\collect_operational_augmentation.py --dataset-output data\processed\operational_message\v2_repeat --report-prefix reports\augmentation_repeat --dataset-report-prefix reports\dataset_repeat
```

계획과 기대값만 확인하려면 `--prepare-only`를 사용한다. 이 옵션은 송신하지 않으며 만든 준비 batch를 재송신하기 위한 자동 resume 명령은 제공하지 않는다. 실제 수집은 새 batch로 시작한다.

원본 batch의 `dataset_config.json`에는 정확한 6개 입력 경로와 역할 registry가 들어 있다. 재구성은 새 출력 경로와 새 보고서 경로를 지정한다.

```powershell
python .\scripts\prepare_operational_dataset.py --config data\raw\operational_augmentation\<batch_id>\dataset_config.json --output-dir data\processed\operational_message\v2_rebuild --report-prefix reports\operational_dataset_v2_rebuild
```

`<batch_id>`는 실제 수집 명령이 출력한 값이다. Windows 경로 제한 때문에 기본 ID를 짧게 발급하며, 긴 출력 경로는 파일 생성 전에 거부한다. 첫 개발 실행에서 경로 제한으로 준비만 실패한 폴더는 실제 송신·수집 0개인 준비 산출물이다. 완료 batch와 섞지 않는다.

원본·가공 데이터는 기존 Git 제외 정책을 따른다. 보고서·코드·설계·테스트는 Git에 포함한다. 새 checkout은 먼저 실제 수집으로 원본을 만들어야 하며 CSV 없이 보고서 숫자만으로 학습할 수 없다.

## 수행하지 않은 작업과 다음 순서

이번에는 전처리 fit, 모델 학습, 선정 모델의 새 데이터 예측, Test 성능 평가를 하지 않았다. 새 Test는 schema·누락·정답 분포·분할 품질 확인과 CSV 구성에만 사용하며 feature 범위 탐색은 Train/Validation으로 제한한다. 기존 v1 Test를 새 데이터에 합치지 않는다.

수집 직후의 고정 기존 LR 진단과 새 Train 전용 보강 학습·선정·비교는 후속 작업에서 모두 완료했다. 기존 LR은 FP 92·FN 46건, 선정 보강 RF leaf=5·0.3은 FP 30·FN 9건이다. 설명은 `docs/operational_frozen_baseline_audit.md`, `docs/operational_augmentation_experiment.md`에 있다. 다음은 세션 prefix 창 구성·LSTM 실험이며 큰 작업 묶음은 5개가 남는다.

같은 장비·서버·생성기의 반복이므로 다른 네트워크·실제 공격으로의 일반화를 보장하지 않는다. 원래 입력에 없는 본문 길이 필드는 데이터를 더 모으거나 LSTM을 사용한다고 새로 관측되지 않는다. 구조 개선 가설과 관측 한계를 계속 구분한다.
