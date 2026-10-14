# 보정 RF 학습과 새 Validation 선정

## 이번에 한 일

이전에 수집한 새 Train 7,976건으로 **RF 모델 4개를 직접 학습**하고 새 Validation 2,737건에서 **20개 모델·threshold 조합을 비교**했다. 기존 RF는 재학습하지 않고 같은 Validation의 기준선으로 사용했다.

새 Test 5,407건은 CSV 읽기와 해시 계산에도 사용하지 않았다. 수집 원본 JSONL·종료 summary·트래픽 송신·FastAPI 모델 교체도 이번 실행에는 없다. 기존 C++와 dummy API 동작을 유지했다.

## 왜 네 후보를 비교했는가

| 후보 | 모델 입력 | 확인할 내용 |
| --- | --- | --- |
| rf_raw7 | 원본 7개 | 새 데이터로 학습했을 때의 대조군 |
| rf_without_io5 | 누적 송수신량을 제외한 5개 | 수신/송신 완료 누적량의 영향 |
| rf_prefix_mean5 | 송수신량 제외 + 평균 파싱 간격, 5개 | 묶음 내부의 순간 간격 대신 앞의 정상 대기도 참고하는 효과 |
| rf_prefix_mean_parsed6 | 평균 간격 + 현재까지 파싱한 바이트 합, 6개 | 수신 완료 누적량과 메시지 경계 누적량의 차이 |

네 종류의 서로 다른 알고리즘을 도입한 것이 아니다. **모두 같은 RF이며 입력 표현만 다르다.** 트리 100개·최소 leaf 5·seed 42·n_jobs=1을 유지하고 다른 기본 설정과 라이브러리 버전도 실제 protocol에 기록했다.

후보마다 Train으로 한 번씩 학습한다. threshold 0.3/0.4/0.5/0.6/0.7을 비교할 때 다시 학습하지 않고 같은 확률 배열에 판단 기준만 적용한다. 따라서 후보 4개를 학습하고 정책 20개를 평가한 것이다. RF는 scaler를 필요로 하지 않으므로 표준화 학습도 하지 않았다.

## 입력 의미와 누출 방지

원본 v2 JSON schema와 모델 입력 schema를 별도 저장한다. 원본 필드 7개는 바꾸지 않고 파생 값에는 `prefix_mean_interval_ms`, `parsed_bytes_prefix_total`라는 이름과 입력 변환 버전을 사용한다.

prefix 후보는 같은 campaign/run/session의 과거·현재만 사용한다. 첫 메시지의 평균은 0이고 누락·중복·counter 역행을 거부한다. `request_index`는 순서·누락 확인과 평균의 분모에 사용하지만 식별자를 숫자 예측 입력에 직접 넣지 않는다. 정답·시나리오·종료 요약·미래 메시지를 사용하지 않는다.

새 Train과 Validation을 합치지 않았고, 기존 모델을 새 Train으로 다시 fit하지 않았다. 기존 RF의 학습 입력은 이전 Train 1,538건이다. 이 provenance를 확인하기 위해 기존 Train/Validation 처리 파일과 저장 모델 근거는 읽었지만 기존 Test는 읽지 않았다.

수집 완료 근거·원본 모델·처리된 Train/Validation·입력 변환·코드·환경·규칙을 확인하고, **새 Validation 예측과 fit 전에** `protocol_lock.json`을 저장했다. 후보·threshold·성능 기준은 더 앞선 수집 전 계획과 정확히 일치한다. 품질 검사에서 새 Validation의 입력 범위를 이미 확인했다는 이력도 공개한다.

## 선정 기준

세 조건을 모두 만족해야 후보가 된다.

1. 전체 정상 FPR 10% 이하.
2. 정상 묶음 FPR 10% 이하.
3. 이상 Recall 50% 이상.

통과한 조합 중 Recall 최대, F1 최대, 전체 FPR 최소, 후보 id와 threshold 오름차순으로 고른다. 한 조건이 조금 넘었다고 반올림해 통과시키지 않는다. 비용과 독립 Test 승인은 이 단계의 선정과 구분한다.

## 실제 결과

20개 중 2개만 세 조건을 모두 만족했다.

| 통과 정책 | FP | FN | 전체 FPR | 묶음 FPR | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| rf_raw7 · 0.7 | 39 | 275 | 1.98% | 5.16% | 63.96% | 75.66% |
| rf_prefix_mean_parsed6 · 0.7 | 65 | 275 | 3.29% | 8.31% | 63.96% | 74.16% |

Recall이 같아서 F1이 높은 **rf_raw7·threshold 0.7**이 선정됐다. 원본 입력 후보가 이겼으므로 **평균 간격을 추가하면 반드시 더 좋아진다는 가설을 확인한 것은 아니다.** 전체 20개 결과와 탈락 사유는 학습 보고서에 보관했다.

원본 RF의 threshold 0.6은 전체 FPR 4.05%·Recall 71.30%였지만 정상 묶음 FPR 10.17%로 탈락했다. 10.17%를 10%로 반올림하거나 계획에 없는 threshold 0.65를 추가하지 않았다.

## 재학습과 threshold 변화 분리

| 같은 Validation의 역할 | threshold | FP | FN | FPR | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 기존 고정 RF | 0.3 | 266 | 71 | 13.48% | 90.69% | 80.42% |
| 새 원본 RF 재학습 | 0.3 | 209 | 80 | 10.59% | 89.52% | 82.54% |
| 새 원본 RF 선정 정책 | 0.7 | 39 | 275 | 1.98% | 63.96% | 75.66% |

같은 0.3 재학습 비교에서는 FP가 57건 줄고 FN이 9건 늘었다. 새 모델에서 threshold를 0.3→0.7로 바꾸면 FP가 170건 줄지만 FN이 195건 늘었다. 데이터 수·분포·타이밍 조건도 함께 달라졌으므로 데이터 양만의 인과적 효과라고 주장하지 않는다.

기존 모델과 최종 선정 정책을 직접 비교하면 FP 266→39건, FN 71→275건이다. 경고를 줄이는 대신 놓치는 이상이 늘었으므로 **전체 탐지 능력이 좋아졌다고 단정하지 않는다.** 같은 행의 수정 FP/FN과 새 FP/FN을 별도 집계하고 변경 row_id와 확률을 기록했다.

## 남은 관측 한계

선정 정책의 정상 묶음은 698건 중 FP 36건, 일반 정상은 651건 중 FP 3건, 조각 정상은 FP 0건이다. burst는 691건 중 FN 242건이다.

`malformed_chat_payload` 36건은 모두 잡았지만 `malformed_length_chat` 36건 중 33건은 놓쳤다. 후자의 Recall은 8.33%다. 입력에 내부 문자열 길이/본문 유효성이 없으므로 단순한 RF 재학습이나 평균 간격으로 없는 정보를 복원할 수 없다.

첫 메시지 216건 중 정상 FP 5건·이상 FN 63건이다. 첫 메시지에는 이전 흐름이 없다는 한계를 유지하고 해당 메시지를 성능 분모에서 제외하지 않았다. burst 세션의 최초 양성 위치와 prefix 분모도 기록했다.

## 추가한 파일

| 파일 | 역할 |
| --- | --- |
| training/run_operational_recovery_experiment.py | provenance 확인·protocol 잠금·후보 학습·저장 재예측·선정 실행 |
| evaluation/compare_operational_recovery.py | 세 제약·사전 순위·첫 메시지/시나리오·동일 행 비교·한글 보고서 |
| configs/operational_recovery_experiment.json | 데이터·기준 모델·실험 출력 위치와 기대 행 수 |
| tests/test_operational_recovery_experiment.py | Train 전용 fit·잠금 순서·탈락/동률·저장/행 일치·Test 금지·변조/덮어쓰기 검증 |

이전 수집·입력 변환·학습·평가 코드는 수정하지 않았다. 앞 단계 수집 변경이 아직 커밋되지 않은 경우 이번 학습 단계의 추가 파일과 공유 문서 변경을 구분한다.

## 실행과 산출물

```powershell
cd C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI
python -X utf8 .\training\run_operational_recovery_experiment.py
```

기본 결과가 이미 있으므로 재실행할 때는 세 출력 경로를 바꾼다.

```powershell
python -X utf8 .\training\run_operational_recovery_experiment.py --artifact-dir .\artifacts\operational_message\recovery_replay_01 --report-prefix .\reports\operational_recovery_replay_01 --comparison-report-prefix .\reports\operational_recovery_replay_comparison_01
```

재실행은 동일 자료의 재현 확인이지 새 독립 성능 검증이 아니다. 자료·규칙·기존 모델은 임의로 바꾸지 않는다.

```text
artifacts/operational_message/recovery_v1/validation_selection/
  protocol_lock.json                 학습·새 Validation 예측 전 잠금
  raw_feature_schema.json            원본 입력 계약
  label_map.json                     독립 정답 정의
  candidates/<candidate_id>/
    pipeline.joblib                  직접 학습한 RF
    model_input_schema.json          원본/파생 입력과 순서·상태 요구사항
    default_decision_policy.json     기본 0.5 정책, 선정 threshold와 구분
    training_summary.json            실제 트리·시간·중요도·기본 지표·입력 해시
    validation_predictions.csv       동일 Validation 행의 확률·기본 판단
  selection.json                     선정 모델·입력 변환·threshold, 비용/Test 미확인
  comparison_predictions.csv         같은 Validation 2,737행의 비교 역할별 확률·판단
  experiment_manifest.json            모든 저장·보고서 검증 후 완료 표시
```

보고서는 `reports/operational_recovery_validation.json/.md`와 `reports/operational_recovery_comparison.json/.md`다. 실제 모델과 비교 CSV는 기존 Git 제외 규칙을 유지한다.

## 검증과 다음 작업

신규 테스트 15개와 전체 테스트 304개가 통과했다. 실제 fit 4회에 각각 새 Train의 변환 배열·정답·7,976행이 전달됐음을 확인했다. Test CSV·raw JSONL·종료 summary·네트워크 송신·scaler fit을 금지한 상태로 실행했다. 후보 4개를 재로딩해 20개 지표·원본 순서·선정·비교 CSV를 대조하고 산출물 25개·보고서 4개·보호 파일 49개의 해시를 검증했다.

이번 선정은 `selected_pending_cost_and_test`다. **배포 승인이나 최종 모델 확정은 아니다.** 현재 선정 입력은 `raw7_v1`이므로 추가 Python prefix 상태가 필요 없다. 상태형 후보를 채택할 때만 온라인 prefix·순서·누락·수명·메모리 관리가 추가로 필요하다.

다음 작업은 **선정 raw7·0.7 정책의 Validation 기반 비용 측정**이다. 단일 요청 p95·프로세스 peak RSS·필수 파일 크기를 기존 한도로 확인하고 비용까지 통과한 후보를 새 Test 공개 전에 잠근다. 그 뒤 별도 단계에서 새 Test의 승인 여부를 확인한다. Test 결과로 다른 후보나 threshold로 교체하지 않는다.

보정 묶음은 비용·새 독립 승인이 남아 있어 큰 작업 묶음은 여전히 **4개**다. 승인 확인까지 마치면 FastAPI·TCP 통합·최종 문서화 3개로 진행한다. 자동 차단과 실제 외부 침입 일반화는 보장하지 않는다.
