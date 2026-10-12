# 운영 트래픽 수집 품질 검증

캠페인: `campaign_20261012T050123_b3017a53`
원본 위치: `C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI\data\raw\operational_campaigns\campaign_20261012T050123_b3017a53`

전체 통과: **True**, run 21/21, 메시지 학습 후보 run 18
세션 84, 메시지 406, 종료 요약 84, JSONL 490건

잘못된 레코드 0, 중복 0, 메시지 누락 0, 요약 누락 0, 추가 레코드 0

## 품질 검증을 통과한 run의 정답 분포

| 집계 단위 | 정상 0 | 통제된 이상 1 |
| --- | ---: | ---: |
| runs | 9 | 12 |
| sessions | 36 | 48 |
| messages | 214 | 192 |
| session_summaries | 36 | 48 |

정답 분포는 통과한 run만 집계한다. 세션 수와 메시지 수의 불균형을 구분하며 자동 균형 조정은 하지 않는다.

## 메시지 학습 후보의 정답 분포

| 집계 단위 | 정상 0 | 통제된 이상 1 |
| --- | ---: | ---: |
| runs | 9 | 9 |
| sessions | 36 | 36 |
| messages | 214 | 192 |

## 시나리오별 분포

| 시나리오 | 정답 | run | 세션 | 메시지 | 종료 요약 |
| --- | ---: | ---: | ---: | ---: | ---: |
| normal_chat | 0 | 3 | 12 | 75 | 12 |
| normal_fragmented_chat | 0 | 3 | 12 | 71 | 12 |
| normal_batched_chat | 0 | 3 | 12 | 68 | 12 |
| controlled_burst_chat | 1 | 3 | 12 | 168 | 12 |
| malformed_chat_payload | 1 | 3 | 12 | 12 | 12 |
| malformed_length_chat | 1 | 3 | 12 | 12 | 12 |
| invalid_frame_size | 1 | 3 | 12 | 0 | 12 |

## run별 결과

| run | 시나리오 정답 | 메시지 | 종료 요약 | 통과 |
| --- | ---: | ---: | ---: | --- |
| campaign_20261012T050123_b3017a53_r00_normal_chat | 0 | 24 | 4 | True |
| campaign_20261012T050123_b3017a53_r00_normal_fragmented_chat | 0 | 23 | 4 | True |
| campaign_20261012T050123_b3017a53_r00_normal_batched_chat | 0 | 17 | 4 | True |
| campaign_20261012T050123_b3017a53_r00_controlled_burst_chat | 1 | 54 | 4 | True |
| campaign_20261012T050123_b3017a53_r00_malformed_chat_payload | 1 | 4 | 4 | True |
| campaign_20261012T050123_b3017a53_r00_malformed_length_chat | 1 | 4 | 4 | True |
| campaign_20261012T050123_b3017a53_r00_invalid_frame_size | 1 | 0 | 4 | True |
| campaign_20261012T050123_b3017a53_r01_normal_chat | 0 | 27 | 4 | True |
| campaign_20261012T050123_b3017a53_r01_normal_fragmented_chat | 0 | 24 | 4 | True |
| campaign_20261012T050123_b3017a53_r01_normal_batched_chat | 0 | 23 | 4 | True |
| campaign_20261012T050123_b3017a53_r01_controlled_burst_chat | 1 | 56 | 4 | True |
| campaign_20261012T050123_b3017a53_r01_malformed_chat_payload | 1 | 4 | 4 | True |
| campaign_20261012T050123_b3017a53_r01_malformed_length_chat | 1 | 4 | 4 | True |
| campaign_20261012T050123_b3017a53_r01_invalid_frame_size | 1 | 0 | 4 | True |
| campaign_20261012T050123_b3017a53_r02_normal_chat | 0 | 24 | 4 | True |
| campaign_20261012T050123_b3017a53_r02_normal_fragmented_chat | 0 | 24 | 4 | True |
| campaign_20261012T050123_b3017a53_r02_normal_batched_chat | 0 | 28 | 4 | True |
| campaign_20261012T050123_b3017a53_r02_controlled_burst_chat | 1 | 58 | 4 | True |
| campaign_20261012T050123_b3017a53_r02_malformed_chat_payload | 1 | 4 | 4 | True |
| campaign_20261012T050123_b3017a53_r02_malformed_length_chat | 1 | 4 | 4 | True |
| campaign_20261012T050123_b3017a53_r02_invalid_frame_size | 1 | 0 | 4 | True |

## 진단과 한계

- `campaign_20261012T050123_b3017a53_r00_invalid_frame_size`: 헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.
- `campaign_20261012T050123_b3017a53_r01_invalid_frame_size`: 헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.
- `campaign_20261012T050123_b3017a53_r02_invalid_frame_size`: 헤더 거부 시나리오: 메시지 snapshot 0개가 정상 기대값이며 메시지 모델 학습에는 사용할 수 없습니다.
- 정답은 통제된 시나리오 의도이며 실제 침입 정답이나 모델 성능이 아니다.
- 클라이언트 인덱스와 C++ 세션 ID의 직접 대응은 없으므로 세션 기대값의 다중집합을 대조한다.
- 누락 검증은 이 생성기가 계획한 로컬 세션에 한정되며 일반 운영 환경의 모든 유실 원인을 특정하지 않는다.
- run 단위 분리·대표성·정답의 탐지 가능성을 추가 검토한 뒤 학습해야 한다.

## 다음 단계

run/session 단위 train·validation·test 분리와 메시지 feature의 탐지 가능성 분석 후 운영 LR/RF 학습을 진행한다.
