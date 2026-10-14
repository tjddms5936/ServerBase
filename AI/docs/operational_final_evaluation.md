# 운영 모델 최종 성능·비용 평가

## 이번 단계의 결과

Validation에서 선정한 세 정책을 그대로 비교했다. Test를 열기 전에 Validation 성능·비용으로 RF를 잠갔지만, Test의 정상 FPR이 16.01%로 사전 상한 10%를 넘었다. 따라서 **평가 실행은 완료했고 배포 승인은 보류**했다. 승인된 모델 묶음은 만들지 않았으며 FastAPI는 계속 dummy다.

Test에서 LSTM의 FPR이 낮았다는 이유로 LSTM으로 바꾸지 않았다. 그것은 Test를 모델 선택에 사용하는 일이 된다. 현재 Test는 사용된 평가 자료이므로 새 설정·threshold를 개발할 때는 더 이상 새로운 독립 Test라고 주장할 수 없다.

## 고정한 비교 대상

| 모델 | 고정 후보 | threshold | 전처리 |
| --- | --- | ---: | --- |
| LR | `lr_c_0_1` | 0.5 | 기존 Train 전용 scaler가 포함된 Pipeline |
| RF | `rf_leaf_5` | 0.3 | 원본 수치, identity |
| LSTM | `lstm_h16` | 0.5 | 기존 Train 전용 scaler, 과거·현재 최대 8개 |

UNSW 모델이 아니라 같은 운영 Train 1,538건에서 직접 학습한 정책들이다. 최종 단계에서는 학습·scaler fit·새 threshold 탐색을 하지 않았다. 현재 판단 대상과 입력 의미 7개는 같지만 LSTM에만 과거 문맥이 있다는 차이를 공개한다.

기존 모델·보고서·입력·schema·행별 Validation 예측을 해시와 재예측으로 대조했다. 이전 단계의 코드는 바꾸지 않아 과거 학습 결과의 보호 해시도 유지했다.

## Test 공개 전 순서

1. Train/Validation과 기존 시계열 입력·모델을 검증한다. Test CSV는 읽지 않는다.
2. 실제 설정·고정 모델·threshold·코드·입력·예상 Test 파일 해시를 `protocol_lock.json`에 저장한다.
3. Validation만으로 모델별 비용을 측정한다.
4. Validation 성능과 비용 제약을 만족하는 정책 중 Recall 최대, F1 최대, FPR 최소를 우선한다. 동률일 때만 p95·peak RSS·파일 크기·id 순으로 비교한다.
5. 선택한 RF를 `candidate_lock.json`에 고정하고 holdout 사용 예약을 기록한다.
6. 그 이후에만 Test의 X/y/metadata를 검증·읽고 고정 모델들을 예측한다.
7. 잠근 RF의 Test 지표로 승인 여부만 확인한다. 실패하면 다른 모델로 대체하지 않는다.

후보가 없거나 worker가 실패하면 Test를 열지 않는다. 같은 holdout 재실행은 사용 이력을 표시하고, 모델·threshold·승인 기준이 바뀌면 재평가를 거부한다. 사용 예약 후 실행이 실패한 경우도 보수적으로 이미 사용한 holdout으로 취급한다.

## 사전 로컬 승인 조건

| 항목 | 조건 |
| --- | --- |
| 정상 FPR | 10% 이하 |
| 이상 Recall | 50% 이상 |
| 전처리 포함 단일 요청 p95 | 각 측정 프로세스 중 가장 높은 p95가 25ms 이하 |
| 네이티브 프로세스 peak RSS | 512MiB 이하 |
| 필수 모델·전처리·명세 파일 합계 | 50MiB 이하 |

같은 성능 조건을 Validation 자격 검사와 잠근 후보의 Test 확인에 적용한다. 이 수치는 현재 로컬 MVP를 위한 임시 예산이지 실제 서비스 SLA나 안전 요구사항을 검증한 것이 아니다. 통과하더라도 advisory 결과 표시만 허용하고 실제 운영 승인·자동 차단은 허용하지 않는다.

## 지연시간·메모리 측정 방법

모델마다 새로운 프로세스를 3개씩 순차 실행했다. 같은 seed 42·Validation 행 300개의 요청 목록, 워밍업 20회, 배치 반복 10회, BLAS/OpenMP·Torch CPU 계산 스레드 1을 사용한다. LR/RF 프로세스에는 Torch를 import하지 않으며 LSTM의 라이브러리 비용을 다른 모델에 섞지 않는다.

단일 요청은 두 경로로 나눈다. 모델 단독 경로는 전처리한 입력을 타이머 밖에서 준비한다. 전처리 포함 경로는 DataFrame/텐서 구성·기존 scaler 변환·모델 확률·고정 threshold 판단을 포함한다. 행 선택·파일 로딩·세션 history 관리·feature 검증·JSON·HTTP·큐는 제외한다.

따라서 아래 시간은 실시간 API/TCP 종단 지연이 아니다. 배치 전체 시간을 행 수로 나누는 값도 단일 요청 응답시간과 다르다.

psutil로 입력·라이브러리 로딩·측정 캐시까지 포함한 프로세스 RSS, Windows 네이티브 peak working set, 가능한 USS·private commit을 기록했다. 모델 로딩 전/후 RSS 차이는 관측값이지 정확한 모델 메모리가 아니다. 주요 학습 배열 합계와 파일 크기는 별도 부분 추정·디스크 비용이다. [psutil 메모리 API](https://psutil.io/7.2/#psutil.Process.memory_info), [Windows 메모리 카운터](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters_ex)의 정의를 사용한다.

| 모델 | 단일 중앙값 ms | 가장 높은 프로세스 p95 ms | 모델 단독 중앙값 ms | 프로세스 peak RSS MiB | 필수 파일 KiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| LR | 0.6696 | 0.9260 | 0.0626 | 150.11 | 3.05 |
| RF | 4.6750 | 6.8599 | 4.5559 | 152.61 | 191.62 |
| LSTM | 0.9475 | 1.3316 | 0.2761 | 380.93 | 14.29 |

전체 프로세스 메모리에서 LSTM은 큰 라이브러리 비용이, RF에서는 100개 트리의 호출 시간이 드러난다. 모델 가중치 파일만 작다고 프로세스 RAM도 작다고 해석하지 않는다. 현재 장치·실행 시점의 결과이며 다른 환경이나 고부하 상태까지 보장하지 않는다.

## 사전 수치 점검 실패와 보존

첫 `v1/` 실행은 LSTM의 단일/배치 확률 비교 오차 상한 1e-6 때문에 비용 측정 중 종료했다. 최대 차이는 약 1.788e-6였고 판단이 바뀐 요청은 없었다. 이때 후보 잠금·holdout 기록·Test 읽기·완료 보고서는 없었다. 부분 측정 폴더는 삭제하거나 덮어쓰지 않았다.

float32의 배치/슬라이스 계산은 비트 단위로 항상 동일하지 않다. [PyTorch 수치 정확성 문서](https://docs.pytorch.org/docs/2.14/notes/numerical_accuracy.html#batched-computations-or-slice-computations)의 제한에 맞춰 확률 허용오차 1e-5를 명시하고 실제 threshold 판단은 반드시 정확히 같도록 유지했다. 차이의 최댓값도 기록했다. 모델·threshold·FPR/Recall·비용 승인 기준은 변경하지 않았다.

Test 공개 전에 이를 수정한 새 `v2/` 실행이 현재의 완료 결과다. 이는 Test에서 더 좋은 점수를 얻으려고 정책을 바꾼 재실험이 아니다.

## Test 데이터와 고정 결과

Test는 캠페인 2개·run 24개·세션 96개, 정상 762건·통제된 이상 309건의 메시지 1,071건이다. Test CSV 해시·X/y/metadata 행 정렬·독립 정답·run 배치·Train/Validation 식별자 비중복을 확인했다.

같은 세션의 과거·현재만 묶어 창 `(1071, 8, 7)`을 만들었다. 길이 8 창 593개·부분 창 478개·길이 1 창 96개이며 malformed 두 종류 각 16건도 유지했다. 저장 후 NPZ를 pickle 없이 재로딩하고 원본 값·위치·정답·mask·metadata를 대조했다. 이 최종 평가 전용 창은 기존 Train/Validation 데이터셋에 덮어쓰지 않았다.

| 모델 | FP | FN | FPR | Recall | F1 | ROC-AUC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 고정 LR | 113 | 131 | 14.83% | 57.61% | 59.33% | 0.869446 |
| 잠근 RF | 122 | 9 | 16.01% | 97.09% | 82.08% | 0.970944 |
| 고정 LSTM | 60 | 104 | 7.87% | 66.34% | 71.43% | 0.945965 |

RF는 이상을 많이 잡았지만 정상 오탐 기준을 통과하지 못했다. LSTM은 오탐이 낮고 이상 누락이 많았다. 이 결과를 보고 LSTM을 새 배포 후보로 선택하지 않는다. Test는 고정 정책의 비교·확인 자료로 사용했고 이후 독립 평가라고 다시 주장할 수 없다.

RF의 FP 122건 중 정상 묶음 103건·정상 일반 15건·정상 분할 4건이었다. 정상 묶음의 FPR은 103/270=38.15%다. burst 277건은 FN 9·Recall 96.75%, malformed 32건은 모두 탐지했지만 입력에 없는 본문 의미를 복원했다는 증거는 아니다.

이번에는 관측 결과와 승인 보류를 남겼다. 정상 묶음 오탐의 원인을 단정하거나 Test 오분류를 새 정답·학습 데이터로 자동 추가하지 않았다.

## 파일 역할

| 파일 | 역할 |
| --- | --- |
| `configs/operational_final_evaluation.json` | 고정 모델·threshold·예산·측정 규칙·출력 경로를 지정한다. |
| `requirements-evaluation.txt` | psutil 7.2.2를 로컬 `.deps`에 추가한다. |
| `evaluation/operational_final_data.py` | 후보 잠금·holdout 사용 기록 뒤 Test를 검증하고 같은 규칙의 최종 창을 만든다. |
| `evaluation/operational_final_models.py` | 기존 학습 provenance·정책·재예측을 검증하고 fit 없는 추론을 제공한다. |
| `evaluation/operational_cost_worker.py` | 모델 하나만 로드한 독립 프로세스에서 시간·메모리·수치 일치를 측정한다. |
| `evaluation/run_operational_final_evaluation.py` | 전체 순서·선정·확인·승인 보류·보고서·통과 시 배포 묶음 생성을 담당한다. |
| `tests/test_operational_final_evaluation.py` | 접근 순서·누출·창 정합성·비용 조건·승인 보류·재실행·worker 실패를 검사한다. |

```text
artifacts/operational_final_evaluation/
  v1/                              Test 전 비용 점검 실패 기록
  v2/
    protocol_lock.json
    benchmark/<model_id>/          Validation 입력·고정 job·프로세스별 비용 JSON
    candidate_lock.json
    test_windows/test.npz
    test_windows/metadata_test.csv
    evaluation_manifest.json
  holdout_usage/<파일해시>.json

reports/
  operational_final_evaluation.json
  operational_final_evaluation.md
  operational_final_evaluation.predictions.csv
```

현재 승인 보류이므로 `deployment_bundle/`은 없다. 통과하는 경우에만 모델·전처리·명세·label map·잠근 threshold·manifest를 별도 묶음으로 생성하는 구현은 테스트했다. 후보의 과거 기본 0.5 정책을 그대로 복사하지 않고 실제 선정 threshold를 새 정책에 기록한다.

모델·Test 창·비용 원본은 Git에서 제외하며 코드·설정·한글 문서·작은 비교 보고서를 커밋한다. 실제 원본과 이전 모델·보고서는 보존했다.

## 실행과 재실행 주의

신규 최종 평가 테스트 17개를 포함해 전체 262개가 통과했다. 실제 실행에서는 fit·새 트래픽·원본 JSONL·종료 summary를 차단하고 Test 접근이 후보 잠금·사용 기록 이후인지 검사했다. 저장된 고정 모델을 다시 로드해 같은 Test 행·확률·지표·CSV 판단이 일치했고 최종 산출물 19개와 원본·모델·코드·lock 해시도 보존됐다. 이 재예측은 이미 사용된 holdout의 고정 정책 검증이지 새 독립 평가가 아니다.

AI 폴더에서 실행하며 C++ 서버·FastAPI를 켤 필요는 없다.

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python -m pip install --target .deps -r requirements-evaluation.txt
python -X utf8 .\evaluation\run_operational_final_evaluation.py
python -X utf8 -m unittest discover -s tests -p "test_*.py"
```

기본 결과는 이미 있으므로 첫 명령을 다시 실행하면 덮어쓰기 없이 종료한다. 새 `--artifact-dir`와 `--report-prefix`로 같은 고정 정책을 확인할 수는 있지만 비용 측정을 다시 수행하며, 같은 Test 사용 이력이 표시된다. 이를 새 독립 Test 결과로 취급하지 않는다. 변경한 정책으로 현재 holdout을 재사용하려 하면 거부한다.

프로그램 종료 코드 0은 평가 실행 완료를 뜻하지 모델 승인 성공을 뜻하지 않는다. 반드시 `approval.status`를 확인한다. 실패·미선정·승인 보류도 정상적인 연구 결과로 보관한다.

## 다음 작업과 남은 단계

이번 최종 비교·승인 판단 작업은 완료했다. 그러나 승인 조건을 통과한 배포 모델은 아직 없다. 바로 FastAPI의 dummy를 실제 모델로 바꾸지 않는다.

기본 남은 큰 묶음은 실제 FastAPI·TCP 통합·포트폴리오의 3개지만, **오탐 보정과 새 독립 검증 1개를 추가해 현재 실제 남은 묶음은 4개**로 정리한다.

1. 정상 묶음 오탐 보정 설계·제한된 Train/Validation 개발·새 독립 holdout 확인
2. 승인된 모델의 실제 FastAPI 연결
3. C++ TCP 종단 통합·장애·부하 검증
4. 최종 재현·보고서·GitHub 포트폴리오 마무리

바로 다음 작업은 정상 묶음 전송의 오탐 단서를 Train/Validation 개발 관점에서 분석하고, 보정 후보·threshold·판정 시점·새 평가 캠페인의 역할을 사전에 설계하는 것이다. 현재 Test는 진단 근거로만 인용하며 새 정책의 독립 승인 점수로 재사용하지 않는다. 이 과정도 결과에 따라 반복될 수 있어 4번의 수정으로 무조건 끝난다는 일정 약속은 하지 않는다.
