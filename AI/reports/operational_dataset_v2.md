# 운영 메시지 데이터셋 구성 및 분할

데이터셋: `operational_message_v2_campaign_holdout`
저장 위치: `C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI\data\processed\operational_message\v2_campaign_holdout`

의미 버전: `server_application_message_v2`, 입력 7개, 메시지 3122건

## 고정 분할 결과

| 분할 | run | 세션 | 메시지 | 정상 0 | 통제된 이상 1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| train | 36 | 144 | 1538 | 1049 | 489 |
| validation | 12 | 48 | 513 | 349 | 164 |
| test | 24 | 96 | 1071 | 762 | 309 |

송신 전에 역할을 고정한 캠페인 전체를 Train/Validation/Test에 배치한다. 반복·run·세션·메시지를 다른 분할로 옮기지 않는다.

## 분할 사이 식별자 중복

| 비교 | run 중복 | 세션 중복 | 메시지 중복 |
| --- | ---: | ---: | ---: |
| train__validation | 0 | 0 | 0 |
| train__test | 0 | 0 | 0 |
| validation__test | 0 | 0 | 0 |

식별자 비중복과 feature 값 비중복은 다른 검사다.
전체 중복 feature 그룹: 2, 분할 간 같은 feature 그룹: 2, 서로 다른 정답의 같은 feature 그룹: 0

## 입력과 전처리

입력 순서: `packet_size, payload_size, connection_duration_ms, message_interval_ms, bytes_received_total, bytes_sent_total, error_count_total`

정답과 메타데이터는 별도 CSV다. 종료 요약, 오류 문구, 시나리오·세션 ID, timestamp를 모델 입력에 넣지 않는다. 이번 단계에서 scaler나 모델을 fit하지 않았다.
train에서 값이 하나뿐인 입력: `error_count_total`

## 제외한 원본

- `aug_261013_e1cafdde_development_01_r00_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_development_01_r01_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_development_02_r00_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_development_02_r01_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_development_03_r00_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_development_03_r01_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_selection_01_r00_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_selection_01_r01_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_holdout_01_r00_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_holdout_01_r01_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_holdout_02_r00_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']
- `aug_261013_e1cafdde_holdout_02_r01_invalid_frame_size`: ['헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.']

## 저장 및 재현

X/y/metadata 세 파일의 같은 행 위치가 같은 메시지다. 파일 SHA-256, 원본 SHA-256과 split_manifest를 dataset_manifest에 보관한다. 결과를 덮어쓰지 않고 새 경로로 버전을 만든다.

## 해석 범위

- 정답은 통제된 시나리오 의도이며 실제 침입 탐지 정답이 아니다.
- 분할은 사전 정책을 따른다. 독립 묶음·캠페인 수가 작고 충분한 데이터 규모를 보장하지 않는다.
- run/session 비중복은 같은 시나리오·생성기·서버에 대한 편향이나 분포 이동을 해결하지 않는다.
- 동일 feature 벡터는 별도로 보고한다. 같은 feature를 가진 서로 다른 실행을 자동 삭제하거나 정답을 바꾸지 않는다.
- 종료 요약은 원본 품질 확인에만 사용하며 메시지 입력이나 정답 생성에 사용하지 않는다.
- test는 최종 평가용이다. 모델 설정·threshold 선택 및 전처리 fit에는 사용하지 않는다.
- 추후 LSTM 창은 분할 완료 후 각 split의 run/session 내부에서만 구성해야 한다.

## 다음 작업

고정한 기존 LR·threshold 0.7로 새 Validation만 진단한다. 이후 새 Train 전용 scaler·모델 fit과 사전 후보 비교를 수행하며 Test는 최종 비교까지 모델에 넣지 않는다.
