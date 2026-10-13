# 독립 캠페인 수집 및 사전 분할 결과

batch: `aug_261013_e1cafdde`
원본: `C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI\data\raw\operational_augmentation\aug_261013_e1cafdde`

캠페인 6개, run 84/84 통과, 메시지 후보 run 72개.
세션 336개, 메시지 3122건, 종료 요약 336건.

## 송신 전에 고정한 역할

| 슬롯 | seed | 분할 |
| --- | ---: | --- |
| development_01 | 101 | train |
| development_02 | 211 | train |
| development_03 | 307 | train |
| selection_01 | 401 | validation |
| holdout_01 | 503 | test |
| holdout_02 | 607 | test |

한 캠페인의 모든 반복·run·세션·메시지는 같은 분할에 속한다. 정답은 독립 시나리오 manifest에서 가져오며 모델 출력이나 서버 오류로 생성하지 않는다.

## 수집 품질

- `sessions`: 336
- `messages`: 3122
- `summaries`: 336
- `record_count`: 3458
- `invalid_records`: 0
- `duplicate_records`: 0
- `missing_messages`: 0
- `missing_summaries`: 0
- `unexpected_records`: 0

## 메시지 데이터셋

| 분할 | 캠페인 | run | 세션 | 메시지 | 정상 | 통제된 이상 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 3 | 36 | 144 | 1538 | 1049 | 489 |
| validation | 1 | 12 | 48 | 513 | 349 | 164 |
| test | 2 | 24 | 96 | 1071 | 762 | 309 |

데이터셋: `C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI\data\processed\operational_message\v2_campaign_holdout`

헤더 거부 run 12개는 메시지 0개가 기대값이므로 종료 요약만 품질 검사하고 메시지 모델에서는 제외한다.

## 분할 검증

- `train__validation`: {'runs': 0, 'sessions': 0, 'rows': 0, 'campaigns': 0}
- `train__test`: {'runs': 0, 'sessions': 0, 'rows': 0, 'campaigns': 0}
- `validation__test`: {'runs': 0, 'sessions': 0, 'rows': 0, 'campaigns': 0}
- 동일 feature 값 진단: {'duplicate_feature_groups': 2, 'cross_split_feature_groups': 2, 'cross_split_feature_rows': 4, 'conflicting_label_feature_groups': 0, 'pairwise_shared_feature_groups': {'train__validation': 1, 'train__test': 0, 'validation__test': 1}}

같은 값의 feature는 식별자 중복과 구분한다. 행 삭제·정답 교체로 분포를 바꾸지 않는다.

## 송신 제어 및 관측

본문 문자열 8/24/96/384/768바이트, 정상 6~24개·burst 12~24개 메시지, 동시 연결 1/2/4개를 사전 배치했다. 정상 묶음·burst 묶음 1/4/8개, 정상 분할 조각 7/19/31바이트를 실제 sendall 호출 수·묶음 크기와 대조했다.

정상 묶음 사이 지정 대기는 1~4/5~15/20~40ms, burst는 0ms다. 각 시나리오의 모든 수준이 전체 캠페인과 Train에서 교차되지만 Validation의 시나리오별 반복은 2회이므로 완전한 3수준 요인 실험은 아니다.

클라이언트 묶음 시작 간격은 송신·응답·지정 대기를 포함한다. 서버 메시지 파싱 간격과 직접 대응하지 않으며 같다고 검증하지 않는다.

세부 설정 coverage와 Train/Validation의 송신 계측·실제 파싱 간격 범위는 JSON 보고서에 기록했다. Test feature 범위는 분석하지 않았다.

## 수행하지 않은 작업과 한계

scaler fit·모델 학습·모델 예측·Test 성능 평가를 하지 않았다. 기존 LR과 threshold 0.7은 수집 전에 해시로 고정했고 변경하지 않았다. FastAPI 응답은 여전히 dummy다.

새 seed의 독립 캠페인이지만 같은 생성기·프로토콜·Windows 장비·서버 빌드를 사용했다. 실제 외부 공격 데이터나 완전한 요인 실험이 아니다. 송신 대기와 실제 메시지 파싱 간격은 TCP·OS의 영향으로 같지 않다.

## 다음 작업

고정한 기존 모델·threshold로 새 Validation만 진단한 뒤, 새 Train 전용 fit과 사전 후보 비교 실험을 진행한다. Test는 최종 평가까지 모델에 넣지 않는다.
