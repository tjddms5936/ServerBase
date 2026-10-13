# 선정 운영 모델 오분류·관측 한계 분석

## 분석 범위

선정 후보 `lr_c_1`, threshold=0.7의 Validation 138건을 재예측했다.
TN=71, FP=3, FN=24, TP=40. 모델·threshold를 변경하거나 재학습하지 않았다.
Test는 읽지 않았다. 원본 JSONL이나 종료 summary를 모델 입력에 합치지 않고 보존한 row_id·run/session·원본 행 번호로 추적한다.

## 시나리오별 오분류

| 시나리오 | 메시지 | FP | FN |
| --- | ---: | ---: | ---: |
| controlled_burst_chat | 56 | 0 | 16 |
| malformed_chat_payload | 4 | 0 | 4 |
| malformed_length_chat | 4 | 0 | 4 |
| normal_batched_chat | 23 | 3 | 0 |
| normal_chat | 27 | 0 | 0 |
| normal_fragmented_chat | 24 | 0 | 0 |

## burst prefix 진단

| 메시지 번호 | 관측 수 | FN |
| --- | ---: | ---: |
| 1 | 4 | 4 |
| 2 | 4 | 4 |
| 3 | 4 | 4 |
| 4 | 4 | 4 |
| 5 | 4 | 0 |
| 6 | 4 | 0 |
| 7 | 4 | 0 |
| 8 | 4 | 0 |
| 9 | 4 | 0 |
| 10 | 4 | 0 |
| 11 | 4 | 0 |
| 12 | 4 | 0 |
| 13 | 3 | 0 |
| 14 | 2 | 0 |
| 15 | 2 | 0 |
| 16 | 1 | 0 |

## 모든 오분류 추적

| 종류 | 시나리오 | 세션 | 메시지 | 이상 확률 | payload 바이트 |
| --- | --- | ---: | ---: | ---: | ---: |
| false_positive | normal_batched_chat | 42 | 6 | 0.865740 | 262 |
| false_positive | normal_batched_chat | 42 | 7 | 0.865889 | 262 |
| false_positive | normal_batched_chat | 42 | 8 | 0.853404 | 518 |
| false_negative | controlled_burst_chat | 39 | 1 | 0.546882 | 70 |
| false_negative | controlled_burst_chat | 39 | 2 | 0.500348 | 22 |
| false_negative | controlled_burst_chat | 39 | 3 | 0.534046 | 22 |
| false_negative | controlled_burst_chat | 39 | 4 | 0.510208 | 262 |
| false_negative | controlled_burst_chat | 40 | 1 | 0.498626 | 22 |
| false_negative | controlled_burst_chat | 40 | 2 | 0.442853 | 518 |
| false_negative | controlled_burst_chat | 40 | 3 | 0.445021 | 518 |
| false_negative | controlled_burst_chat | 40 | 4 | 0.487830 | 70 |
| false_negative | controlled_burst_chat | 42 | 1 | 0.553562 | 518 |
| false_negative | controlled_burst_chat | 42 | 2 | 0.548133 | 518 |
| false_negative | controlled_burst_chat | 42 | 3 | 0.548675 | 518 |
| false_negative | controlled_burst_chat | 42 | 4 | 0.591601 | 70 |
| false_negative | controlled_burst_chat | 43 | 1 | 0.462799 | 262 |
| false_negative | controlled_burst_chat | 43 | 2 | 0.458009 | 262 |
| false_negative | controlled_burst_chat | 43 | 3 | 0.483734 | 22 |
| false_negative | controlled_burst_chat | 43 | 4 | 0.459496 | 262 |
| false_negative | malformed_chat_payload | 39 | 1 | 0.499816 | 1 |
| false_negative | malformed_chat_payload | 40 | 1 | 0.483292 | 1 |
| false_negative | malformed_chat_payload | 41 | 1 | 0.500994 | 1 |
| false_negative | malformed_chat_payload | 42 | 1 | 0.370027 | 1 |
| false_negative | malformed_length_chat | 39 | 1 | 0.517883 | 518 |
| false_negative | malformed_length_chat | 40 | 1 | 0.501901 | 70 |
| false_negative | malformed_length_chat | 41 | 1 | 0.503249 | 70 |
| false_negative | malformed_length_chat | 42 | 1 | 0.500940 | 22 |

CSV에는 전체 식별자·원본 행 번호·입력 7개·정답·예측·확률·threshold를 저장한다. JSON은 LR 가중치 기여도와 반대 정답의 가까운 Train 예시도 보관한다. 기여도와 거리는 인과관계나 프로토콜 동등성 증거가 아니다.

## 관측 한계

- malformed_chat_payload의 payload 1바이트는 CS_CHAT의 최소 고정 필드 6바이트보다 짧다. 크기 단서는 현재 입력에 있지만 모델의 누락과 프로토콜 검증 실패는 별개다.
- malformed_length_chat은 같은 크기의 정상 본문에서 문자열 길이 필드만 바꿀 수 있다. 그 필드·본문 유효성은 현재 입력 7개에 없다.
- PacketParsed와 FeatureCollector의 snapshot은 ServerSession의 본문 역직렬화 전에 생성된다. 이후 오류나 종료 summary를 현재 error_count_total에 가져오면 판단 시점 누출이다.
- 첫 메시지 간격은 0이고 짧은 이상 세션은 메시지 하나뿐이다. 시계열 모델도 없는 과거 정보나 본문 필드를 복원할 수 없다.
- 묶음 전송의 파싱 간격은 실제 TCP 패킷 도착 간격과 다르다. 정상 묶음도 짧은 간격·동일 수신 누적값을 보일 수 있다.
- 같은 시나리오의 Train/Validation 시간 범위를 별도로 비교한다. 전체 Train 범위 안에 있어도 시나리오별 분포 차이는 남을 수 있다.
- invalid_frame_size는 메시지 snapshot이 없으므로 메시지 모델 평가 대상 밖이다. 이미 적용한 헤더 거부 경로와 별도의 coverage로 구분한다.

## 본문 표현의 구성 반례

정상 프레임의 문자열 길이 필드만 60000으로 바꿨다. 전체 프레임 크기는 같고, 시각·카운터를 같게 고정하면 입력 7개와 모델 출력도 같다.
이것은 모델에 전달되지 않는 정보의 구성 반례다. 실제 두 수집 행이 완전히 같거나 모델이 모든 malformed 메시지를 탐지할 수 없다는 뜻은 아니다.

## 독립 데이터 보강 설계

계획은 캠페인 6개, run 84개, 세션 336개(헤더 거부 포함)다. 새로 수집한 캠페인은 0개다.
캠페인 전체를 사전에 Train 3개·Validation 1개·Test 2개로 배치한다. 새 seed·긴 정상 세션·타이밍·묶음/분할 크기·동시 연결 수준을 교차 배치한다.
현재 생성기와 데이터셋 분리기에 새 설정·캠페인 holdout 구현이 필요하다. 이 JSON을 기존 수집기에 바로 전달해 실행하는 계획이 아니다.
프로토콜 유효성은 기존 C++ 검증 경로로 다루고 AI의 행동 이상 판단과 구분한다. 새 관측 필드가 필요하면 의미 버전·판단 시점·재수집을 별도 설계한다.

## 해석 한계

- 이미 선정에 사용한 Validation의 사후 진단이다. 독립적인 최종 성능 또는 원인 확정으로 해석하지 않는다.
- 시나리오별 표본과 독립 세션 수가 작다. 요약 범위 차이는 통계적 분포 이동 검정이 아니다.
- LR 기여도는 표준화 입력·현재 가중치의 계산 분해이며 중복 입력과 상관관계를 포함한다. 기여도가 실제 원인이라는 뜻은 아니다.
- 같은 feature의 실제 다른 정답 관측 여부와 구성 반례를 구분한다. 실제 feature 충돌이 0이어도 충분한 관측 정보를 보장하지 않는다.
- 보강 계획은 아직 실행하지 않았다. 새 캠페인·seed 분리만으로 실제 공격 도메인이나 생성기 편향이 해결되지는 않는다.
- 프로토콜 검증 규칙으로 본문 오류를 검출하는 것을 AI 모델 개선 성과로 대신 주장하지 않는다.

## 다음 작업

설계한 생성기 설정과 캠페인 단위 분리를 구현하고 새 캠페인을 수집·검증한다. 이후 새 Validation에서 고정 baseline을 진단하고 새 Train 전용 보강 실험을 진행한다. Test는 최종 평가까지 보류한다.
