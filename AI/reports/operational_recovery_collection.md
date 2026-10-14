# 보정 캠페인 실제 수집·품질 검증·분할

## 완료 결과

batch: `rec_261014_3878ea96`
원본: `C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI\data\raw\operational_recovery\rec_261014_3878ea96`

독립 캠페인 6개, run 378/378개 통과, 세션 1512개를 실제 수집했다.
메시지 16120건·종료 요약 1512건이며 정답은 송신 전 독립 시나리오 manifest를 유지했다.

기존 RF는 파일·threshold를 해시로 고정했지만 로드하거나 예측하지 않았다. FastAPI는 dummy 응답이며 이 수치는 탐지 성능이 아니라 수집 품질이다.

## 송신 전에 고정한 역할

| 슬롯 | 실제 캠페인 | seed | 분할 |
| --- | --- | ---: | --- |
| recovery_train_01 | rec_261014_3878ea96_c0 | 1103 | train |
| recovery_train_02 | rec_261014_3878ea96_c1 | 1201 | train |
| recovery_train_03 | rec_261014_3878ea96_c2 | 1301 | train |
| recovery_validation_01 | rec_261014_3878ea96_c3 | 1409 | validation |
| recovery_test_01 | rec_261014_3878ea96_c4 | 1511 | test |
| recovery_test_02 | rec_261014_3878ea96_c5 | 1601 | test |

한 캠페인의 모든 반복·run·세션·메시지는 같은 split에 속한다. 파일 경로용 짧은 캠페인 id와 설계의 역할 이름은 registry로 연결한다.

## 수집 품질

- `sessions`: 1512
- `messages`: 16120
- `summaries`: 1512
- `record_count`: 17632
- `invalid_records`: 0
- `duplicate_records`: 0
- `missing_messages`: 0
- `missing_summaries`: 0
- `unexpected_records`: 0

## 메시지 데이터셋

| 분할 | 캠페인 | 메시지 run | 메시지 세션 | 메시지 | 정상 | 통제된 이상 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 3 | 162 | 648 | 7976 | 5782 | 2194 |
| validation | 1 | 54 | 216 | 2737 | 1974 | 763 |
| test | 2 | 108 | 432 | 5407 | 3964 | 1443 |

데이터셋: `C:\Users\mypc\Desktop\NetworkDetectionAI\mymy\AI\data\processed\operational_message\recovery_v1`

헤더 거부 run 54개는 기대 메시지가 0건이므로 별도 coverage로 남겼다. 종료 요약을 가짜 메시지나 학습 정답으로 바꾸지 않았다.

## 분할과 중복 검증

- `train__validation`: {'runs': 0, 'sessions': 0, 'rows': 0, 'campaigns': 0}
- `train__test`: {'runs': 0, 'sessions': 0, 'rows': 0, 'campaigns': 0}
- `validation__test`: {'runs': 0, 'sessions': 0, 'rows': 0, 'campaigns': 0}
- 동일 feature 값 진단: {'duplicate_feature_groups': 53, 'cross_split_feature_groups': 40, 'cross_split_feature_rows': 90, 'conflicting_label_feature_groups': 19, 'pairwise_shared_feature_groups': {'train__validation': 13, 'train__test': 25, 'validation__test': 8}}

동일 feature 값은 같은 세션·행 식별자 중복과 구분하며 결과를 보고 행 삭제·정답 교체를 하지 않았다.

## 제어 조건과 관측 범위

각 캠페인의 정상 묶음 크기 1/4/8 × 정상 pacing 1~4/5~15/20~40ms를 9조합으로 실행했다. 동일 반복 burst는 같은 크기·개수 범위·묶음·동시성으로 지정 대기만 0ms다.

실제 sendall 호출 수·묶음 구성·송신 바이트·응답·종료 및 수집 누락을 run별 품질 검사로 확인했다. 이 교차는 묶음 크기와 pacing에 대한 것이며 모든 동시성·크기까지 완전 교차한 실험은 아니다.

클라이언트 묶음 시작 간격은 송신·응답·지정 대기를 포함한다. 서버 메시지 파싱 간격과 직접 대응하지 않으며 같다고 검증하지 않는다.

Train/Validation의 송신·파싱 범위만 진단했다. 새 Test 원본은 수집 품질·추출·분할·해시 검사를 위해 읽었지만 Test feature 범위 분석·모델 예측·threshold 선정에는 사용하지 않았다.

## 보호와 한계

계획·독립 정답·역할·출력 경로·모델·빌드·실행 코드·각 run 설정/기대값을 송신 전에 잠갔다. 전체 원본과 출력의 해시를 완료 manifest에 보관했다. 실패 batch는 보존하고 자동 재송신하거나 통과 run만 골라 데이터셋을 승인하지 않는다.

이전 보정 설계·진단·최종 평가·모델·데이터셋은 덮어쓰지 않았다. 기존 Test는 이미 노출된 진단 자료이며 이번 새 Test와 구분한다. 새 seed의 같은 생성기·장비 실험이므로 실제 외부 침입이나 운영 일반화를 보장하지 않는다.

## 다음 작업과 남은 단계

다음은 새 Train 전용 RF 후보 4개 학습과 새 Validation의 20개 조합 비교다. 이후 비용 확인·후보 잠금·새 Test 승인이 끝나야 보정 묶음이 완료된다.

보정 묶음은 수집·분할까지 진행 중이므로 FastAPI 실제 모델 연결·TCP 통합·최종 문서화를 포함한 큰 작업 묶음은 아직 4개 남아 있다.
